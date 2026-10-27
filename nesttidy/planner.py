from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .config import INCOMPLETE_SUFFIXES, OUTPUT_DIR_PREFIX, REPORT_DIR_NAME, RULE_VERSION
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
        if lower in {REPORT_DIR_NAME.lower()} or lower.startswith(OUTPUT_DIR_PREFIX.lower()):
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
    for child in sorted(selected_root.iterdir(), key=lambda p: (str(p).casefold(), str(p))):
        skip = should_skip_path(child)
        if skip:
            issues.append(issue(str(child), "scan", skip.split(":", 1)[0], skip))
            continue
        if child.is_dir():
            roots.append(child.resolve())
        elif child.is_file():
            issues.append(issue(str(child), "scan", "IGNORED_ROOT_FILE", "批量模式下运行根目录文件原地保留"))
    return roots, issues


def snapshot_digest(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for root in sorted(paths, key=lambda p: str(p).casefold()):
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort(key=str.casefold)
            filenames.sort(key=str.casefold)
            h.update(str(Path(dirpath)).encode("utf-8", "surrogatepass"))
            for name in filenames:
                path = Path(dirpath) / name
                try:
                    stat = path.stat()
                except OSError:
                    continue
                h.update(str(path).encode("utf-8", "surrogatepass"))
                h.update(str(stat.st_size).encode())
                h.update(str(stat.st_mtime_ns).encode())
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


def build_plan(selected_root: str, mode: str = "batch", output_root: str | None = None) -> OperationPlan:
    selected = Path(selected_root).resolve()
    roots, root_issues = resource_roots(selected, mode)
    operations: list[Operation] = []
    mappings: list[MappingEntry] = []
    issues: list[ExecutionIssue] = list(root_issues)
    removed_dirs: set[Path] = set()
    estimated_space = 0

    for root in roots:
        resource_id = hashlib.sha1(str(root).encode("utf-8", "surrogatepass")).hexdigest()[:12]
        tree, scan_issues = build_tree(root, should_skip_path)
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
            removed_dirs.update(path.resolve() for path in local_removed if path.resolve() != root)
        local_targets: list[Path] = []
        for file_item, target in final_files(tree, root):
            local_targets.append(target)
            estimated_space += file_item.size
            if output_root:
                # copy 模式必须交出完整副本：原位文件也要写进输出目录。
                # 曾经沿用「无需移动 → keep」的判定，执行器跳过后输出缺文件却报 0 失败。
                op_type = "copy"
            elif file_item.original_path.resolve() == target.resolve() and not file_item.name_cleaning_trace:
                op_type = "keep"
            else:
                op_type = "move"
            actual_target = target
            if output_root:
                actual_target = Path(output_root).resolve() / root.name / target.relative_to(root)
            op = Operation(
                op_id=str(uuid.uuid4()),
                resource_root_id=resource_id,
                op_type=op_type,
                source_path=str(file_item.original_path),
                target_path=str(actual_target),
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
                    original_path=str(file_item.original_path),
                    current_path=str(file_item.original_path),
                    planned_target_path=str(actual_target),
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
        if not output_root:
            for scanned in scanned_dirs:
                resolved = scanned.resolve()
                if resolved == root or resolved in removed_dirs:
                    continue
                if not any(resolved in target.parents for target in local_targets):
                    removed_dirs.add(resolved)

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
        output_root=str(Path(output_root).resolve()) if output_root else None,
        config_snapshot={"mode": mode, "output_root": output_root},
        source_snapshot_digest=snapshot_digest(roots),
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
