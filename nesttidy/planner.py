from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .config import INCOMPLETE_SUFFIXES, OUTPUT_DIR_PREFIX, REPORT_DIR_NAME, RULE_VERSION
from .model import ExecutionIssue, MappingEntry, Operation, OperationPlan
from .rules import build_tree, final_files, process_node, prune_empty_dirs, apply_root_chain_penetration, resolve_names


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
        for raw in scan_issues:
            code = str(raw["code"]).split(":", 1)[0]
            issues.append(issue(str(raw["path"]), "scan", code, str(raw["message"])))
        local_removed: list[Path] = []
        prune_empty_dirs(tree, local_removed)
        if apply_root_chain_penetration(tree):
            resolve_names(tree)
        else:
            process_node(tree, local_removed)
        removed_dirs.update(path.resolve() for path in local_removed if path.resolve() != root)
        for file_item, target in final_files(tree, root):
            estimated_space += file_item.size
            if file_item.original_path.resolve() == target.resolve() and not file_item.name_cleaning_trace:
                op_type = "keep"
            else:
                op_type = "copy" if output_root else "move"
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

    for dir_path in sorted(removed_dirs, key=lambda p: str(p).casefold(), reverse=True):
        operations.append(
            Operation(
                op_id=str(uuid.uuid4()),
                resource_root_id="directory",
                op_type="rmdir",
                source_path=str(dir_path),
                target_path=None,
                rule_trace=["R0/R1/R3/R4 产生的空目录清理"],
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
