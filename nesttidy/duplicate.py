from __future__ import annotations

import csv
import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

from .planner import should_skip_path

CHUNK = 1024 * 1024


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


def detect_duplicates(root: str, level: str = "standard", workers: int = 4) -> list[DuplicateRow]:
    base = Path(root).resolve()
    if level not in {"fast", "standard", "deep"}:
        raise ValueError("重复文件检测级别必须是 fast、standard 或 deep")
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
    by_size: dict[int, list[Path]] = {}
    for path in files:
        try:
            by_size.setdefault(path.stat().st_size, []).append(path)
        except OSError:
            continue
    candidates = [group for size, group in by_size.items() if size == 0 or len(group) > 1]
    rows: list[DuplicateRow] = []
    group_index = 1
    for group in candidates:
        if len(group) == 1:
            path = group[0]
            stat = path.stat()
            rows.append(DuplicateRow(f"zero-{group_index}", str(path), stat.st_size, stat.st_mtime, "size", "0", False, True, "0 字节文件"))
            group_index += 1
            continue
        hash_groups: dict[str, list[Path]] = {}
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for path, value in pool.map(lambda p: (p, file_signature(p, level)), group):
                hash_groups.setdefault(value, []).append(path)
        for value, paths in hash_groups.items():
            if len(paths) < 2:
                continue
            keep = choose_keep(paths)
            group_id = f"dup-{group_index}"
            for path in sorted(paths, key=lambda p: (str(p).casefold(), str(p))):
                stat = path.stat()
                rows.append(
                    DuplicateRow(
                        duplicate_group_id=group_id,
                        path=str(path),
                        size=stat.st_size,
                        mtime=stat.st_mtime,
                        hash_method=level,
                        hash_value=value,
                        is_hardlink=is_hardlink(path),
                        suggested_keep=path == keep,
                        reason="同大小且签名一致；仅报告，不自动删除",
                    )
                )
            group_index += 1
    return rows


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

