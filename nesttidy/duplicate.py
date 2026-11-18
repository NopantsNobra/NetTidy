from __future__ import annotations

import csv
import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

from .planner import should_skip_path
from .rules import stable_key

CHUNK = 1024 * 1024
LEVELS = ("fast", "standard", "deep")
# 第一遍只做初筛；standard/fast 都会漏掉「首尾相同、中段不同」的文件，
# 所以默认再跑一遍全文件哈希复核（两阶段），把误判组剔出去。
PHASE1_METHOD = {"fast": "大小 + 文件名", "standard": "大小 + 首尾各 1MB 哈希", "deep": "大小 + 全文件 SHA-256"}
VERIFY_METHOD = "全文件 SHA-256"


@dataclass
class DuplicateRow:
    duplicate_group_id: str
    path: str
    size: int
    mtime: float
    hash_method: str
    hash_value: str
    is_hardlink: bool
    suggested_keep: bool
    reason: str
    phase1_method: str = ""
    verify_method: str = ""
    verify_value: str = ""
    verified: bool = False
    conflict_note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DuplicateGroup:
    """按组汇总：只有 verified 组里的非保留项才允许删除。"""

    duplicate_group_id: str
    size: int
    verified: bool
    file_count: int
    keep_path: str
    deletable_paths: list[str]
    verified_hash: str
    reclaimable_bytes: int
    note: str

    def to_dict(self) -> dict:
        return asdict(self)


def detect_duplicates(root: str, level: str = "standard", workers: int = 4, verify: bool = True) -> list[DuplicateRow]:
    """两阶段重复检测：第一遍按 level 初筛，第二遍默认对候选组做全文件哈希复核。

    第一遍用 fast 只比文件名、standard 只比首尾 1MB——这两种口径都会把
    「大小相同、首尾相同、中段不同」的文件误判成重复。verify=True 时自动补一遍
    全文件 SHA-256，复核不一致的成员标 conflict_note 且永不进入可删集合。
    level="deep" 时第一遍已是全量哈希，不再重复计算。
    """
    if level not in LEVELS:
        raise ValueError("重复文件检测级别必须是 fast、standard 或 deep")
    base = Path(root).resolve()
    if not base.is_dir():
        raise ValueError(f"目录不存在或不是文件夹: {base}")
    files = collect_files(base)
    by_size: dict[int, list[Path]] = {}
    for path in files:
        try:
            by_size.setdefault(path.stat().st_size, []).append(path)
        except OSError:
            continue
    rows: list[DuplicateRow] = []
    group_index = 1
    for size in sorted(by_size):
        group = sorted(by_size[size], key=lambda p: stable_key(p, "file"))
        if len(group) < 2:
            if size == 0:
                rows.append(_singleton_row(group[0], group_index))
                group_index += 1
            continue
        for cluster in _clusters(group, level, workers):
            if len(cluster) < 2:
                continue
            confirmed, rejected, verify_hash, verified = _verify_cluster(cluster, level, workers, verify)
            if confirmed:
                rows.extend(_group_rows(confirmed, size, level, f"dup-{group_index}", verify_hash, verified, ""))
            if rejected:
                rows.extend(
                    _group_rows(
                        rejected,
                        size,
                        level,
                        f"dup-{group_index}-recheck",
                        verify_hash,
                        False,
                        "复核剔除：首尾相同但全文哈希不同，不是重复文件",
                    )
                )
            group_index += 1
    return rows


def collect_files(base: Path) -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames.sort(key=str.casefold)
        filenames.sort(key=str.casefold)
        for name in filenames:
            path = Path(dirpath) / name
            if should_skip_path(path):
                continue
            try:
                if path.is_file():
                    files.append(path)
            except OSError:
                continue
    return files


def _singleton_row(path: Path, index: int) -> DuplicateRow:
    try:
        stat = path.stat()
        size, mtime = stat.st_size, stat.st_mtime
    except OSError:
        size, mtime = 0, 0.0
    return DuplicateRow(
        duplicate_group_id=f"zero-{index}",
        path=str(path),
        size=size,
        mtime=mtime,
        hash_method="size",
        hash_value="0",
        is_hardlink=False,
        suggested_keep=True,
        reason="0 字节文件，没有可删副本",
        phase1_method="大小",
        verify_method="不适用",
        verified=False,
        conflict_note="",
    )


def _clusters(group: list[Path], level: str, workers: int) -> list[list[Path]]:
    signatures: dict[str, list[Path]] = {}
    for path, value in _hash_map(group, level, workers):
        signatures.setdefault(value, []).append(path)
    return [sorted(paths, key=lambda p: stable_key(p, "file")) for _, paths in sorted(signatures.items())]


def _verify_cluster(
    cluster: list[Path], level: str, workers: int, verify: bool
) -> tuple[list[Path], list[Path], str, bool]:
    """第二遍复核：返回（确认重复的成员、复核剔除的成员、确认哈希、是否已复核确认）。

    只有拿到「至少两个成员全文哈希一致」才算确认；初筛出来的组如果全文哈希
    两两不同，说明整组都是误判，全体进剔除集合，谁都拿不到可删资格。
    """
    if not verify:
        return cluster, [], "", False
    if level == "deep":
        # 第一遍已全量哈希，直接采信，避免对同一批大文件重复读盘。
        return cluster, [], file_signature(cluster[0], "deep"), True
    hashes: dict[str, list[Path]] = {}
    for path, value in _hash_map(cluster, "deep", workers):
        hashes.setdefault(value, []).append(path)
    ordered = sorted(hashes.items(), key=lambda item: (-len(item[1]), min(stable_key(p, "file") for p in item[1])))
    confirmed_hash, best = ordered[0]
    best = sorted(best, key=lambda p: stable_key(p, "file"))
    if len(best) < 2:
        # 每个哈希都是独一份：初筛整体误判。
        rejected = sorted([p for _, paths in ordered for p in paths], key=lambda p: stable_key(p, "file"))
        return [], rejected, confirmed_hash, False
    rejected = sorted([p for _, paths in ordered[1:] for p in paths], key=lambda p: stable_key(p, "file"))
    return best, rejected, confirmed_hash, True


def _group_rows(
    paths: list[Path],
    size: int,
    level: str,
    group_id: str,
    verify_hash: str,
    verified: bool,
    conflict_note: str,
) -> list[DuplicateRow]:
    keep = choose_keep(paths) if paths else None
    if verified:
        reason, verify_method = "全文哈希一致，确认重复", VERIFY_METHOD
    elif conflict_note:
        reason, verify_method = "复核未通过，不参与删除", f"{VERIFY_METHOD}（不一致）"
    else:
        reason = f"未做第二遍复核，仅按{PHASE1_METHOD[level]}初筛（删除前仍会校验全文哈希）"
        verify_method = "未启用"
    rows: list[DuplicateRow] = []
    for path in paths:
        try:
            stat = path.stat()
            mtime = stat.st_mtime
        except OSError:
            mtime = 0.0
        rows.append(
            DuplicateRow(
                duplicate_group_id=group_id,
                path=str(path),
                size=size,
                mtime=mtime,
                hash_method=level,
                hash_value=verify_hash,
                is_hardlink=is_hardlink(path),
                suggested_keep=path == keep,
                reason=reason,
                phase1_method=PHASE1_METHOD[level],
                verify_method=verify_method,
                verify_value=verify_hash,
                verified=verified,
                conflict_note=conflict_note,
            )
        )
    return rows


def _hash_map(paths: list[Path], level: str, workers: int) -> list[tuple[Path, str]]:
    def one(path: Path) -> tuple[Path, str]:
        try:
            return path, file_signature(path, level)
        except OSError:
            return path, f"__error__{path}"

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return list(pool.map(one, paths))


def group_rows(rows: list[DuplicateRow]) -> list[DuplicateGroup]:
    """把行聚成组：可删集合 = 组内没被复核否决、且不是保留项的文件（删除时 executor 仍会重算全文哈希）。"""
    buckets: dict[str, list[DuplicateRow]] = {}
    for row in rows:
        buckets.setdefault(row.duplicate_group_id, []).append(row)
    groups: list[DuplicateGroup] = []
    for group_id in sorted(buckets):
        items = sorted(buckets[group_id], key=lambda r: (bool(r.conflict_note), not r.suggested_keep, r.path.casefold(), r.path))
        usable = [row for row in items if not row.conflict_note]
        rejected = not usable
        verified = bool(usable) and all(row.verified for row in usable)
        keep_row = usable[0] if usable else items[0]
        deletable = [row.path for row in usable if row is not keep_row]
        if rejected:
            # 复核剔除的组不是重复组：不标保留项，避免界面暗示「留住这个、删那个」
            for row in items:
                row.suggested_keep = False
        note = items[0].conflict_note
        if not note:
            if deletable and verified:
                note = "确认重复，可删除其余副本"
            elif deletable:
                note = "未做第二遍复核，可删除；删除前会校验全文哈希"
            else:
                note = "无可删副本"
        groups.append(
            DuplicateGroup(
                duplicate_group_id=group_id,
                size=keep_row.size,
                verified=verified,
                file_count=len(items),
                keep_path=keep_row.path,
                deletable_paths=deletable,
                verified_hash=keep_row.verify_value,
                reclaimable_bytes=keep_row.size * len(deletable),
                note=note,
            )
        )
    return groups


def summarize(rows: list[DuplicateRow]) -> dict:
    groups = group_rows(rows)
    return {
        "files_reported": len(rows),
        "groups": len(groups),
        "confirmed_groups": sum(1 for group in groups if group.verified and group.deletable_paths),
        "recheck_rejected_groups": sum(1 for group in groups if not group.verified and "复核剔除" in group.note),
        "deletable_files": sum(len(group.deletable_paths) for group in groups),
        "reclaimable_bytes": sum(group.reclaimable_bytes for group in groups),
    }


def file_signature(path: Path, level: str) -> str:
    stat = path.stat()
    if level == "fast":
        return f"{stat.st_size}:{path.name.casefold()}"
    h = hashlib.sha256()
    h.update(str(stat.st_size).encode())
    with path.open("rb") as f:
        if level == "standard":
            h.update(f.read(CHUNK))
            if stat.st_size > CHUNK:
                f.seek(max(0, stat.st_size - CHUNK))
                h.update(f.read(CHUNK))
        else:
            while True:
                chunk = f.read(CHUNK)
                if not chunk:
                    break
                h.update(chunk)
    return h.hexdigest()


def full_sha256(path: Path) -> str:
    """全文件 SHA-256：删除前的最后一道校验，与 file_signature(level="deep") 同口径。"""
    h = hashlib.sha256()
    size = path.stat().st_size
    h.update(str(size).encode())
    with path.open("rb") as f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def is_hardlink(path: Path) -> bool:
    try:
        return path.stat().st_nlink > 1
    except Exception:
        return False


def choose_keep(paths: list[Path]) -> Path:
    return sorted(paths, key=lambda p: (len(p.parts), str(p).casefold(), str(p)))[0]


def write_duplicate_report(rows: list[DuplicateRow], csv_path: str | Path) -> Path:
    out = Path(csv_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(rows[0]).keys()) if rows else list(DuplicateRow.__dataclass_fields__.keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))
    return out
