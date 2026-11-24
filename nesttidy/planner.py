from __future__ import annotations

import hashlib
import os
import stat
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .config import INCOMPLETE_SUFFIXES, OUTPUT_DIR_PREFIX, QUARANTINE_DIR_PREFIX, REPORT_DIR_NAME, RULE_VERSION
from .model import ExecutionIssue, MappingEntry, Operation, OperationPlan
from .rules import (
    VDir,
    apply_root_chain_penetration,
    build_tree,
    final_files,
    process_node,
    prune_empty_dirs,
)


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def issue(path: str, stage: str, code: str, message: str) -> ExecutionIssue:
    return ExecutionIssue(
        issue_id=str(uuid.uuid4()),
        path=path,
        stage=stage,
        code=code,
        message=message,
        created_at=utc_now(),
    )


def should_skip_path(path: Path) -> str | None:
    try:
        if path.is_symlink():
            return "SKIPPED_SYMLINK"
        lower = path.name.lower()
        if lower in {REPORT_DIR_NAME.lower()} or lower.startswith(OUTPUT_DIR_PREFIX.lower()) or lower.startswith(
            QUARANTINE_DIR_PREFIX.lower()
        ):
            return "SKIPPED_NESTTIDY_OUTPUT"
        if path.suffix.lower() in INCOMPLETE_SUFFIXES or lower.endswith(".!qb"):
            return "DOWNLOAD_IN_PROGRESS"
        if os.name == "nt":
            attrs = path.stat().st_file_attributes  # type: ignore[attr-defined]
            if attrs & 0x400:  # FILE_ATTRIBUTE_REPARSE_POINT
                return "SKIPPED_REPARSE_POINT"
            if attrs & 0x4:  # FILE_ATTRIBUTE_SYSTEM
                return "SKIPPED_SYSTEM_FILE"
    except OSError as exc:
        return f"ACCESS_ERROR:{exc}"
    return None


def resource_roots(selected_root: Path, mode: str) -> tuple[list[Path], list[ExecutionIssue]]:
    selected_root = selected_root.resolve()
    issues: list[ExecutionIssue] = []
    if not selected_root.exists() or not selected_root.is_dir():
        raise ValueError(f"目录不存在或不是文件夹: {selected_root}")
    if mode == "single":
        return [selected_root], issues
    if mode != "batch":
        raise ValueError("mode 必须是 batch 或 single")
    roots: list[Path] = []
    with os.scandir(selected_root) as iterator:
        entries = list(iterator)
    for entry in sorted(entries, key=lambda e: (str(Path(e.path)).casefold(), str(Path(e.path)))):
        child = Path(entry.path)
        skip = should_skip_path(child)
        if skip:
            issues.append(issue(str(child), "scan", skip.split(":", 1)[0], skip))
            continue
        if entry.is_dir():
            roots.append(child.resolve())
        elif entry.is_file():
            issues.append(issue(str(child), "scan", "IGNORED_ROOT_FILE", "批量模式下运行根目录文件原地保留"))
    return roots, issues


def snapshot_digest(paths: list[Path], progress=None) -> str:
    # 与 os.walk(topdown=True, followlinks=False) 等价的前序遍历，但用 DirEntry
    # 复用枚举时的 stat；dirnames/filenames 的 casefold 排序与递归顺序逐字复刻，
    # 保证 digest 字节序列与原实现完全一致（见 P0-2 基线对照测试）。
    h = hashlib.sha256()
    done = 0

    def visit(top: str) -> None:
        nonlocal done
        try:
            with os.scandir(top) as iterator:
                entries = list(iterator)
        except OSError:
            return
        dirs: list[str] = []
        files: dict[str, os.DirEntry] = {}
        for entry in entries:
            try:
                is_dir = entry.is_dir()
            except OSError:
                is_dir = False
            if is_dir:
                dirs.append(entry.name)
            else:
                files[entry.name] = entry
        dirs.sort(key=str.casefold)
        names = sorted(files, key=str.casefold)
        h.update(str(Path(top)).encode("utf-8", "surrogatepass"))
        for name in names:
            try:
                stat = files[name].stat()
            except OSError:
                continue
            done += 1
            if progress:
                progress(done, 0, name, "计划")
            path = Path(top) / name
            h.update(str(path).encode("utf-8", "surrogatepass"))
            h.update(str(stat.st_size).encode())
            h.update(str(stat.st_mtime_ns).encode())
        for dirname in dirs:
            new_path = os.path.join(top, dirname)
            if not os.path.islink(new_path):
                visit(new_path)

    for root in sorted(paths, key=lambda p: str(p).casefold()):
        visit(os.fspath(root))
    return h.hexdigest()


def collect_dir_paths(tree: VDir) -> list[Path]:
    """在树被规则改写之前，记下所有被扫描到的目录原路径。"""
    paths: list[Path] = []

    def walk(node: VDir) -> None:
        for child in node.dirs:
            paths.append(child.original_path)
            walk(child)

    walk(tree)
    return paths


def collect_parent_paths(targets: list[Path]) -> set[Path]:
    """一次性收集所有目标路径的祖先目录。"""
    parents: set[Path] = set()
    for target in targets:
        parents.update(target.parents)
    return parents


def _path_resolver():
    """单次规划内的 resolve() 记忆化：键为原始路径字符串，命中返回同一 Path。

    缓存随闭包生命周期丢弃，绝不跨规划复用——避免陈旧路径判定（ROADMAP P0-3）。
    只做同语义记忆化，resolved 语义（含符号链接）完整保留。
    """
    cache: dict[str, Path] = {}

    def resolve(path: Path) -> Path:
        key = str(path)
        result = cache.get(key)
        if result is None:
            result = path.resolve()
            cache[key] = result
        return result

    return resolve


def scan_residual_dirs(
    scanned_dirs: list[Path],
    local_targets: list[Path],
    root: Path,
    removed_dirs: set[Path],
    resolve=None,
) -> None:
    """登记「执行后不再有文件落入」的扫描目录（原地修改 removed_dirs）。"""
    occupied = collect_parent_paths(local_targets)
    if resolve is None:
        resolve = lambda path: path.resolve()
    for scanned in scanned_dirs:
        resolved = resolve(scanned)
        if resolved == root or resolved in removed_dirs:
            continue
        if resolved not in occupied:
            removed_dirs.add(resolved)


def directory_identity(path: Path) -> list[int]:
    """冻结目录对象身份；不跟随最终分量的链接或重解析点。"""
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError(f"资源根不是普通目录: {path}")
    return [info.st_dev, info.st_ino]


def build_plan(
    selected_root: str,
    mode: str = "batch",
    output_root: str | None = None,
    progress: Callable[[int, int, str, str | None], None] | None = None,
) -> OperationPlan:
    """扫描 + 冻结计划。`progress(done, total, label, phase)` 只上报进度：total 为 0 表示总量未定。"""
    resolve = _path_resolver()
    selected = resolve(Path(selected_root))
    roots, root_issues = resource_roots(selected, mode)
    root_identities = {str(root): directory_identity(root) for root in dict.fromkeys([selected, *roots])}
    operations: list[Operation] = []
    mappings: list[MappingEntry] = []
    issues: list[ExecutionIssue] = list(root_issues)
    removed_dirs: set[Path] = set()
    estimated_space = 0
    done = 0

    for root in roots:
        resource_id = hashlib.sha1(str(root).encode("utf-8", "surrogatepass")).hexdigest()[:12]
        tree, scan_issues = build_tree(root, should_skip_path, progress)
        scanned_dirs = collect_dir_paths(tree)
        for raw in scan_issues:
            code = str(raw["code"]).split(":", 1)[0]
            issues.append(issue(str(raw["path"]), "scan", code, str(raw["message"])))
        local_removed: list[Path] = []
        prune_empty_dirs(tree, local_removed)
        # R1 是前置步骤，不是终态：穿透只把内容提到资源根，穿透点以下仍需
        # R0-R4 继续整理。曾经写成 if/else 二选一，导致「下载→解压→合集」
        # 这类最常见形态整棵子树一条规则都不跑（0.5.1 修复）。
        apply_root_chain_penetration(tree)
        process_node(tree, local_removed)
        # copy 模式不修改源目录：任何 rmdir 都不该进计划。
        # 旧实现只在「扫描残留空目录」那一段做了 output_root 判断，
        # R0-R4 登记的目录仍然生成 rmdir 操作，混进计划与报告里。
        if not output_root:
            removed_dirs.update(resolve(path) for path in local_removed if resolve(path) != root)
        local_targets: list[Path] = []
        files = list(final_files(tree, root))
        for index, (file_item, target) in enumerate(files, 1):
            if progress:
                progress(done + index, 0, root.name, "计划")
            local_targets.append(target)
            estimated_space += file_item.size
            if output_root:
                # copy 模式必须交出完整副本：原位文件也要写进输出目录。
                # 曾经沿用「无需移动 → keep」的判定，执行器跳过后输出缺文件却报 0 失败。
                op_type = "copy"
            elif resolve(file_item.original_path) == resolve(target) and not file_item.name_cleaning_trace:
                op_type = "keep"
            else:
                op_type = "move"
            actual_target = target
            if output_root:
                actual_target = resolve(Path(output_root)) / root.name / target.relative_to(root)
            source_text = str(file_item.original_path)
            target_text = str(actual_target)
            if source_text == target_text:
                target_text = source_text
            op = Operation(
                op_id=str(uuid.uuid4()),
                resource_root_id=resource_id,
                op_type=op_type,
                source_path=source_text,
                target_path=target_text,
                rule_trace=file_item.traces or ["保留原位"],
                risk_level="low" if op_type in {"keep", "copy"} else "medium",
                precheck={"size": file_item.size, "mtime_ns": file_item.mtime_ns},
            )
            operations.append(op)
            mappings.append(
                MappingEntry(
                    entry_id=op.op_id,
                    resource_root_id=resource_id,
                    entry_type="file",
                    original_path=source_text,
                    current_path=source_text,
                    planned_target_path=target_text,
                    operation_type=op_type,
                    rule_trace=op.rule_trace,
                    risk_level=op.risk_level,
                    status="planned",
                    source_size=file_item.size,
                    source_mtime=file_item.mtime_ns,
                    name_cleaning_trace=file_item.name_cleaning_trace,
                    conflict_resolution_trace=file_item.conflict_trace,
                )
            )

        # 剥壳（R1/R2）只重挂内存树，被丢弃的壳节点不会自己进 local_removed。
        # 统一按「执行后是否还有文件落在该目录里」判定：没有就必然变空，必须登记删除。
        # 这条同时覆盖穿透点下方被重新挂载的子目录——它们的旧路径同样会变空。
        done += len(files)
        if not output_root:
            scan_residual_dirs(scanned_dirs, local_targets, root, removed_dirs, resolve=resolve)

    for dir_path in sorted(removed_dirs, key=lambda p: str(p).casefold(), reverse=True):
        operations.append(
            Operation(
                op_id=str(uuid.uuid4()),
                resource_root_id="directory",
                op_type="rmdir",
                source_path=str(dir_path),
                target_path=None,
                rule_trace=["R0/R1/R2/R3/R4 产生的空目录清理"],
                risk_level="medium",
                precheck={},
            )
        )

    return OperationPlan(
        plan_id=str(uuid.uuid4()),
        app_version=__version__,
        rule_version=RULE_VERSION,
        mode=mode,
        selected_root=str(selected),
        output_root=str(resolve(Path(output_root))) if output_root else None,
        config_snapshot={"mode": mode, "output_root": output_root, "root_identities": root_identities},
        source_snapshot_digest=snapshot_digest(roots, progress),
        resource_roots=[str(root) for root in roots],
        operations=operations,
        issues=issues,
        mappings=mappings,
        created_at=utc_now(),
        frozen=True,
        estimated_space=estimated_space,
    )


def validate_copy_output(selected_root: str, output_root: str) -> None:
    selected = Path(selected_root).resolve()
    output = Path(output_root).resolve()
    try:
        output.relative_to(selected)
    except ValueError:
        pass
    else:
        raise ValueError("copy 输出目录不得位于源目录内部")
    try:
        selected.relative_to(output)
    except ValueError:
        pass
    else:
        raise ValueError("copy 输出目录不得包含源目录")
