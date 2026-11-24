from __future__ import annotations

import ctypes
import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import REPORT_DIR_NAME
from .duplicate import full_sha256
from .model import ExecutionIssue, Operation, OperationPlan
from .planner import directory_identity, issue, should_skip_path, validate_copy_output
from .rules import stable_key


@dataclass
class ExecutionResult:
    completed: int
    skipped: int
    failed: int
    issues: list[ExecutionIssue]
    transaction_log: str | None
    swept: int = 0


@dataclass
class DeleteEntry:
    """一条删除请求：删 path，保留 keep_path；expected_sha256 可选，用于防止诊断后被改动。"""

    path: str
    keep_path: str
    expected_sha256: str = ""


@dataclass
class DeleteResult:
    deleted: int = 0
    recycled: int = 0
    skipped: int = 0
    failed: int = 0
    freed_bytes: int = 0
    recycled_bytes: int = 0
    transaction_log: str | None = None
    rows: list[dict] = field(default_factory=list)
    issues: list[ExecutionIssue] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "deleted": self.deleted,
            "recycled": self.recycled,
            "skipped": self.skipped,
            "failed": self.failed,
            "freed_bytes": self.freed_bytes,
            "recycled_bytes": self.recycled_bytes,
            "transaction_log": self.transaction_log,
            "rows": self.rows,
            "issues": [row.to_dict() for row in self.issues],
        }


class OutputBoundaryError(ValueError):
    """copy 写入前复查发现目标越过用户选择的输出根边界。"""


def ensure_target_within_output(target: Path, output_root: Path) -> None:
    """copy 写入前复查目标是否仍在用户选择的输出根内。"""
    root_abs = Path(os.path.abspath(output_root))
    if output_root.resolve() != root_abs:
        raise OutputBoundaryError(f"copy 输出根已被重定向: {output_root}")
    resolved_parent = target.parent.resolve()
    if not resolved_parent.is_relative_to(root_abs):
        raise OutputBoundaryError(f"目标越过 copy 输出边界: {target} -> {resolved_parent}")


class MoveBoundaryError(ValueError):
    """原地执行的路径已偏离冻结计划的资源根。"""


def ensure_move_path(path: Path, root: Path) -> None:
    """拒绝越界和重解析点，允许尚不存在的目标目录；不消除 TOCTOU。"""
    if not path.is_absolute() or not path.is_relative_to(root) or path == root:
        raise MoveBoundaryError(f"路径越过资源根或指向资源根本身: {path}")
    try:
        if path.resolve() != path or root.resolve() != root:
            raise MoveBoundaryError(f"路径已被重定向: {path}")
        for current in (path, *path.parents):
            try:
                attrs = current.lstat()
            except FileNotFoundError:
                continue
            if current.is_symlink() or getattr(attrs, "st_file_attributes", 0) & 0x400:
                raise MoveBoundaryError(f"拒绝符号链接或重解析点: {current}")
            if current == root:
                break
    except (OSError, RuntimeError) as exc:
        raise MoveBoundaryError(f"无法安全复核路径: {path}: {exc}") from exc


def verify_root_identity(plan: OperationPlan, root: Path) -> None:
    """新计划固定 selected/root 的目录对象；旧的内部构造计划保留路径保护。"""
    identities = plan.config_snapshot.get("root_identities")
    if identities is None:
        return
    for folder in dict.fromkeys([Path(plan.selected_root), root]):
        expected = identities.get(str(folder))
        try:
            current = directory_identity(folder)
        except (OSError, ValueError) as exc:
            raise MoveBoundaryError(f"资源根身份无法复核: {folder}: {exc}") from exc
        if not expected or not expected[1] or current != expected:
            raise MoveBoundaryError(f"资源根身份已变化或不可验证: {folder}")


def move_resource_root(plan: OperationPlan, op: Operation, roots: set[Path]) -> Path:
    if not op.source_path:
        raise MoveBoundaryError("缺少源路径")
    selected = Path(plan.selected_root)
    source = Path(op.source_path)
    if not source.is_absolute() or not source.is_relative_to(selected) or source == selected:
        raise MoveBoundaryError(f"源路径越过选中根: {source}")
    if selected in roots:
        root = selected
    else:
        root = selected / source.relative_to(selected).parts[0]
    if root not in roots:
        raise MoveBoundaryError(f"源路径不属于冻结资源根: {source}")
    verify_root_identity(plan, root)
    ensure_move_path(source, root)
    if op.op_type in {"move", "copy"}:
        if not op.target_path:
            raise MoveBoundaryError("缺少目标路径")
        ensure_move_path(Path(op.target_path), root)
    return root


def execute_plan(plan: OperationPlan, mode: str, confirm_move: bool = False, transaction_log: str | None = None, progress=None) -> ExecutionResult:
    if not plan.frozen:
        raise ValueError("OperationPlan 未冻结，禁止执行")
    if mode not in {"copy", "move"}:
        raise ValueError("执行模式必须是 copy 或 move")
    # 计划类型与执行模式必须一致。含 output_root 的计划是 copy 专用：
    # 它的 target 指向输出目录，按 move 执行会把文件搬出资源根，直接突破 P0 边界。
    if plan.output_root and mode != "copy":
        raise ValueError("该计划包含输出目录（copy 专用），禁止按 move 执行")
    if mode == "copy":
        if not plan.output_root:
            raise ValueError("copy 模式必须提供 output_root")
        validate_copy_output(plan.selected_root, plan.output_root)
    if mode == "move" and not confirm_move:
        raise ValueError("move 模式必须显式确认")

    issues: list[ExecutionIssue] = []
    completed = 0
    skipped = 0
    failed = 0
    statuses: dict[str, str] = {}
    output_root_path: Path | None = Path(plan.output_root) if (mode == "copy" and plan.output_root) else None
    tx_path: Path | None = Path(transaction_log).resolve() if transaction_log else None
    if mode == "move" and tx_path is None:
        tx_path = Path.cwd() / "transaction_log.jsonl"
    if tx_path:
        tx_path.parent.mkdir(parents=True, exist_ok=True)

    roots = {Path(root) for root in plan.resource_roots}
    ops = deterministic_ops(plan.operations)
    for index, op in enumerate(ops, 1):
        if progress:
            progress(index, len(ops), op.op_type)
        status = "failed"
        try:
            move_root = move_resource_root(plan, op, roots) if mode == "move" and op.op_type in {"move", "copy", "rmdir"} else None
            precheck(op)
            if op.op_type == "keep":
                status = "skipped"
            elif mode == "copy":
                if op.op_type in {"copy", "move"}:
                    do_copy(op, output_root_path)
                    status = "completed"
                else:
                    status = "skipped"
            else:  # move
                if op.op_type in {"move", "copy"}:
                    do_move(op, tx_path, move_root, plan)
                    status = "completed"
                elif op.op_type == "rmdir":
                    if do_rmdir(op, tx_path, move_root, plan):
                        status = "completed"
                    else:
                        status = "skipped"
                        issues.append(
                            issue(str(op.source_path), "execute", "RMDIR_SKIPPED_NON_EMPTY", "目录非空，未删除")
                        )
                else:
                    status = "skipped"
        except Exception as exc:
            status = "failed"
            issues.append(issue(op.source_path or op.target_path or "", "execute", classify_exception(exc), str(exc)))
        statuses[op.op_id] = status
        if status == "completed":
            completed += 1
        elif status == "skipped":
            skipped += 1
        else:
            failed += 1
    swept = sweep_empty_dirs(plan, tx_path, issues) if mode == "move" else 0
    apply_result_to_plan(plan, statuses, issues)
    return ExecutionResult(completed=completed, skipped=skipped, failed=failed, issues=issues, transaction_log=str(tx_path) if tx_path else None, swept=swept)


def apply_result_to_plan(plan: OperationPlan, statuses: dict[str, str], issues: list[ExecutionIssue]) -> None:
    """把执行事实回写进计划：否则 issues.csv 只含计划期问题、状态永远停在 planned。"""
    for op in plan.operations:
        op.status = statuses.get(op.op_id, "not_run")
        if op.status == "failed":
            op.result = {"status": "failed"}
    for mapping in plan.mappings:
        mapping.status = statuses.get(mapping.entry_id, mapping.status)
    seen = {(row.path, row.code, row.message) for row in plan.issues}
    for row in issues:
        key = (row.path, row.code, row.message)
        if key in seen:
            continue
        plan.issues.append(row)
        seen.add(key)


def deterministic_ops(ops: list[Operation]) -> list[Operation]:
    files = [op for op in ops if op.op_type != "rmdir"]
    dirs = [op for op in ops if op.op_type == "rmdir"]
    files.sort(key=lambda op: ((op.source_path or "").casefold(), op.target_path or ""))
    dirs.sort(key=lambda op: (op.source_path or "").casefold(), reverse=True)
    return files + dirs


def precheck(op: Operation) -> None:
    if op.op_type == "rmdir":
        return
    if not op.source_path:
        raise ValueError("缺少源路径")
    source = Path(op.source_path)
    if not source.exists():
        raise FileNotFoundError(f"源路径不存在: {source}")
    stat = source.stat()
    expected_size = op.precheck.get("size")
    expected_mtime = op.precheck.get("mtime_ns")
    if expected_size is not None and stat.st_size != expected_size:
        raise RuntimeError(f"源文件大小已变化: {source}")
    if expected_mtime is not None and stat.st_mtime_ns != expected_mtime:
        raise RuntimeError(f"源文件修改时间已变化: {source}")
    if not op.target_path:
        raise ValueError("缺少目标路径")
    target = Path(op.target_path)
    if target.exists() and target.resolve() != source.resolve():
        raise FileExistsError(f"目标已存在，禁止覆盖: {target}")


def do_copy(op: Operation, output_root: Path | None = None) -> None:
    assert op.source_path and op.target_path
    source = Path(op.source_path)
    target = Path(op.target_path)
    if source.resolve() == target.resolve():
        return
    # 边界复查必须先于任何写入，mkdir 也算写入：目标链里若被换成 junction / 目录符号链接，
    # 先 mkdir 会把空目录建到输出边界之外（junction 逃逸要求「一个字节都不写」）。
    if output_root is not None:
        ensure_target_within_output(target, output_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    if target.stat().st_size != source.stat().st_size:
        raise RuntimeError(f"复制后大小校验失败: {target}")


def missing_dirs(path: Path) -> list[Path]:
    """返回从浅到深、当前尚不存在的目录链：整理时新建的文件夹必须记账，撤销才能一起收掉。"""
    missing: list[Path] = []
    current = path
    while not current.exists() and current.parent != current:
        missing.append(current)
        current = current.parent
    return list(reversed(missing))


def _move_without_replace(source: Path, target: Path) -> None:
    """Windows 同卷原生 rename：目标存在就失败，不做 shutil.move 的复制/删除回退。"""
    if os.name == "nt":
        os.rename(source, target)
        return
    # 非交付平台仅供开发测试；仍保留显式冲突检查，不宣称竞态安全。
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"目标已存在，禁止覆盖: {target}")
    shutil.move(str(source), str(target))


def do_move(op: Operation, tx_path: Path | None, root: Path | None = None, plan: OperationPlan | None = None) -> None:
    assert op.source_path and op.target_path
    source = Path(op.source_path)
    target = Path(op.target_path)
    if source.resolve() == target.resolve():
        return
    for created in missing_dirs(target.parent):
        if root is not None:
            if plan is not None:
                verify_root_identity(plan, root)
            ensure_move_path(created, root)
        created.mkdir()
        write_tx(tx_path, {"action": "mkdir", "source": str(created), "status": "done"})
    if root is not None:
        if plan is not None:
            verify_root_identity(plan, root)
        ensure_move_path(source, root)
        ensure_move_path(target, root)
        precheck(op)
    write_tx(tx_path, {"action": "move", "source": str(source), "target": str(target), "status": "started"})
    _move_without_replace(source, target)
    write_tx(tx_path, {"action": "move", "source": str(source), "target": str(target), "status": "done"})


def do_rmdir(op: Operation, tx_path: Path | None, root: Path | None = None, plan: OperationPlan | None = None) -> bool:
    """返回是否达成「目录已不存在」这一目的。非空目录返回 False，上层须计入跳过而非成功。"""
    assert op.source_path
    path = Path(op.source_path)
    if not path.exists():
        return True
    if any(path.iterdir()):
        return False
    if root is not None:
        if plan is not None:
            verify_root_identity(plan, root)
        ensure_move_path(path, root)
    write_tx(tx_path, {"action": "rmdir", "source": str(path), "status": "started"})
    path.rmdir()
    write_tx(tx_path, {"action": "rmdir", "source": str(path), "status": "done"})
    return True


def sweep_empty_dirs(
    plan: OperationPlan, tx_path: Path | None, issues: list[ExecutionIssue] | None = None
) -> int:
    """R6：只清扫资源根内未排除的空目录；不跟随重新定向的目录。"""
    selected = Path(plan.selected_root)
    removed = 0

    def report(path: Path, code: str, message: str) -> None:
        if issues is not None:
            issues.append(issue(str(path), "execute", code, message))

    def excluded(path: Path) -> bool:
        reason = should_skip_path(path)
        if reason and reason.startswith("ACCESS_ERROR"):
            report(path, "R6_ACCESS_ERROR", reason)
        return reason is not None

    def eligible(path: Path, base: Path) -> bool:
        # 比较冻结计划中的原路径，绝不把新 resolve 的结果当成新的资源根。
        try:
            verify_root_identity(plan, base)
            if not path.is_relative_to(base) or path.resolve() != path:
                report(path, "R6_PATH_REFUSED", "清扫路径已重定向或越过资源根，拒绝删除")
                return False
            # 删除前复查祖先：目录可能在枚举后被替换，或被设置为排除属性。
            current = path
            while current != selected:
                if excluded(current):
                    return False
                current = current.parent
            return True
        except MoveBoundaryError as exc:
            report(path, "R6_PATH_REFUSED", str(exc))
            return False
        except (OSError, RuntimeError) as exc:
            report(path, "R6_ACCESS_ERROR", str(exc))
            return False

    def walk_error(exc: OSError) -> None:
        report(Path(exc.filename or str(selected)), "R6_ACCESS_ERROR", str(exc))

    for root in sorted(set(plan.resource_roots), key=lambda value: stable_key(Path(value), "dir")):
        base = Path(root)
        if base != selected and base.parent != selected:
            report(base, "R6_PATH_REFUSED", "资源根不在选中根的直接边界内")
            continue
        if not eligible(base, base) or not base.is_dir():
            continue
        dirs: list[Path] = []
        # topdown 剪枝：Windows junction 也必须按扫描排除规则处理，
        # 不能只依靠 os.walk 的 followlinks=False。
        for current, dirnames, _ in os.walk(base, topdown=True, followlinks=False, onerror=walk_error):
            folder = Path(current)
            if not eligible(folder, base):
                dirnames[:] = []
                continue
            dirnames[:] = [name for name in dirnames if not excluded(folder / name)]
            if folder != selected:
                dirs.append(folder)
        for path in sorted(dirs, key=lambda item: (-len(item.parts), stable_key(item, "dir"))):
            if not eligible(path, base):
                continue
            try:
                if any(path.iterdir()):
                    continue
                write_tx(tx_path, {"action": "rmdir", "source": str(path), "status": "started"})
                path.rmdir()
            except OSError as exc:
                report(path, "R6_ACCESS_ERROR", str(exc))
                continue
            write_tx(tx_path, {"action": "rmdir", "source": str(path), "status": "done"})
            removed += 1
    return removed


def write_tx(tx_path: Path | None, row: dict) -> None:
    if not tx_path:
        return
    with tx_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def classify_exception(exc: Exception) -> str:
    if isinstance(exc, MoveBoundaryError):
        return "MOVE_BOUNDARY_REFUSED"
    if isinstance(exc, OutputBoundaryError):
        return "OUTPUT_BOUNDARY_ESCAPED"
    if isinstance(exc, PermissionError):
        return "PERMISSION_DENIED"
    if isinstance(exc, FileNotFoundError):
        return "SOURCE_CHANGED"
    if isinstance(exc, FileExistsError):
        return "TARGET_CONFLICT"
    if isinstance(exc, OSError) and getattr(exc, "winerror", None) in {32, 33}:
        return "FILE_LOCKED"
    return "EXECUTION_FAILED"


def send_to_recycle_bin(path: Path) -> bool:
    """把单个文件交给 Windows 回收站（FOF_ALLOWUNDO + 不弹窗），回收站里可自行还原。"""
    if os.name != "nt":
        # 非 Windows 没有回收站：必须抛 OSError 让上层按行记失败，
        # 不许让 ctypes.windll 的 AttributeError 半路炸掉整批删除（下层只捕获 OSError）。
        raise OSError("回收站仅在 Windows 可用；本机请改用 purge 彻底删除")
    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", ctypes.c_void_p),
            ("wFunc", ctypes.c_uint),
            ("pFrom", ctypes.c_wchar_p),
            ("pTo", ctypes.c_wchar_p),
            ("fFlags", ctypes.c_ushort),
            ("fAnyOperationsAborted", ctypes.c_bool),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", ctypes.c_wchar_p),
        ]

    flags = 0x0004 | 0x0010 | 0x0040 | 0x0400  # SILENT | NOCONFIRMATION | ALLOWUNDO | NOERRORUI
    op = SHFILEOPSTRUCTW(None, 3, str(path) + "\0", None, flags, False, None, None)
    code = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    return code == 0 and not op.fAnyOperationsAborted and not path.exists()


def delete_duplicates(
    root: str,
    entries: list[DeleteEntry],
    mode: str = "recycle",
    confirm: bool = False,
    transaction_log: str | None = None,
    progress=None,
) -> DeleteResult:
    """删除重复副本：执行层的唯一入口，所有前置校验在此，不在 UI。"""
    mode = "recycle" if mode == "quarantine" else mode
    if mode not in {"recycle", "purge"}:
        raise ValueError("删除方式必须是 recycle（移入回收站）或 purge（彻底删除）")
    if mode == "purge" and not confirm:
        raise ValueError("彻底删除必须显式确认")
    base = Path(root).resolve()
    if not base.is_dir():
        raise ValueError(f"目录不存在或不是文件夹: {base}")
    if not entries:
        raise ValueError("没有要删除的文件")
    victims = {str(Path(entry.path).resolve()) for entry in entries}
    keeps = {str(Path(entry.keep_path).resolve()) for entry in entries}
    if keeps & victims:
        raise ValueError("保留项出现在待删除列表里：每组必须至少保留一个副本")

    result = DeleteResult()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    # 日志落在被整理目录旁的报告目录里，不落 cwd：否则 undo 依据会落在「启动目录」这种不确定位置
    tx_path = Path(transaction_log).resolve() if transaction_log else base.parent / REPORT_DIR_NAME / f"删除日志_{stamp}.jsonl"
    tx_path.parent.mkdir(parents=True, exist_ok=True)
    result.transaction_log = str(tx_path)

    handled: set[str] = set()
    for index, entry in enumerate(entries, 1):
        victim = Path(entry.path).resolve()
        keep = Path(entry.keep_path).resolve()
        key = str(victim)
        if progress:
            progress(index, len(entries), victim.name)
        if key in handled:
            result.skipped += 1
            result.rows.append(_delete_row(victim, "skipped", "DUPLICATE_REQUEST", "同一文件在本批请求里出现多次", 0))
            continue
        handled.add(key)
        code, message = _delete_precheck(victim, keep, base, entry.expected_sha256)
        if code:
            result.skipped += 1
            result.issues.append(issue(key, "delete", code, message))
            result.rows.append(_delete_row(victim, "skipped", code, message, 0))
            continue
        size = victim.stat().st_size
        try:
            if mode == "purge":
                write_tx(tx_path, {"action": "delete", "source": str(victim), "mode": "purge", "status": "started"})
                victim.unlink()
                write_tx(tx_path, {"action": "delete", "source": str(victim), "mode": "purge", "status": "done"})
                result.deleted += 1
                result.freed_bytes += size
                result.rows.append(_delete_row(victim, "deleted", "OK", "已彻底删除", size))
            elif send_to_recycle_bin(victim):
                write_tx(tx_path, {"action": "recycle", "source": str(victim), "status": "done"})
                result.recycled += 1
                result.recycled_bytes += size
                result.rows.append(_delete_row(victim, "recycled", "OK", "已移入回收站（可在回收站还原；清空回收站才真正释放空间）", size))
            else:
                raise OSError("回收站操作未生效")
        except OSError as exc:
            result.failed += 1
            code = classify_exception(exc)
            result.issues.append(issue(key, "delete", code, str(exc)))
            result.rows.append(_delete_row(victim, "failed", code, str(exc), 0))
    return result


def _delete_precheck(victim: Path, keep: Path, base: Path, expected_sha256: str) -> tuple[str, str]:
    """返回 (错误码, 说明)；空字符串表示通过。"""
    if str(victim) == str(keep):
        return "KEEP_IS_VICTIM", "保留项不能同时是删除目标"
    if not victim.is_relative_to(base):
        return "OUT_OF_ROOT", f"不在选定目录内，拒绝删除: {victim}"
    if victim.is_symlink():
        return "SYMLINK_REFUSED", "符号链接不参与删除"
    if not keep.is_file():
        return "KEEP_MISSING", f"保留副本已不存在，拒绝删除: {keep}"
    if not victim.is_file():
        return "SOURCE_MISSING", "文件已不存在，无需删除"
    if keep.samefile(victim):
        return "SAME_FILE", "待删文件与保留项指向同一物理文件"
    actual = full_sha256(victim)
    if actual != full_sha256(keep):
        return "NOT_DUPLICATE", "与保留副本内容不一致，拒绝删除"
    if expected_sha256 and expected_sha256 != actual:
        return "CONTENT_CHANGED", "诊断之后文件已变化，拒绝删除"
    return "", ""


def _delete_row(victim: Path, status: str, code: str, message: str, size: int) -> dict:
    return {"path": str(victim), "status": status, "code": code, "message": message, "bytes": size}


