from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import QUARANTINE_DIR_PREFIX, REPORT_DIR_NAME
from .duplicate import full_sha256
from .model import ExecutionIssue, Operation, OperationPlan
from .planner import issue, validate_copy_output


@dataclass
class ExecutionResult:
    completed: int
    skipped: int
    failed: int
    issues: list[ExecutionIssue]
    transaction_log: str | None


@dataclass
class DeleteEntry:
    """一条删除请求：删 path，保留 keep_path；expected_sha256 可选，用于防止诊断后被改动。"""

    path: str
    keep_path: str
    expected_sha256: str = ""


@dataclass
class DeleteResult:
    deleted: int = 0
    quarantined: int = 0
    skipped: int = 0
    failed: int = 0
    freed_bytes: int = 0
    quarantined_bytes: int = 0
    trash_dir: str | None = None
    transaction_log: str | None = None
    rows: list[dict] = field(default_factory=list)
    issues: list[ExecutionIssue] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "deleted": self.deleted,
            "quarantined": self.quarantined,
            "skipped": self.skipped,
            "failed": self.failed,
            "freed_bytes": self.freed_bytes,
            "quarantined_bytes": self.quarantined_bytes,
            "trash_dir": self.trash_dir,
            "transaction_log": self.transaction_log,
            "rows": self.rows,
            "issues": [row.to_dict() for row in self.issues],
        }


class OutputBoundaryError(ValueError):
    """copy 写入前复查发现目标越过用户选择的输出根边界。"""


def ensure_target_within_output(target: Path, output_root: Path) -> None:
    """copy 写入前复查目标是否仍在用户选择的输出根内。

    计划期 `validate_copy_output()` 只 resolve 一次。若输出根或其子目录在计划生成后
    被替换成 junction / symlink，`shutil.copy2` 会把文件写到边界之外，执行仍报成功
    （实测：目标父目录换成 junction 后，文件落到 escape/a.txt，completed=1 failed=0）。

    复查两条：① 输出根自身未被重解析点重定向；② 目标父目录解析后仍落在根内。

    局限：检查与 copy2 之间仍有 TOCTOU 窗口，此处只缩小窗口，**不消除竞态**。
    完全消除需要目录句柄 + 相对路径打开，标准库在 Windows 上做不到。
    """
    root_abs = Path(os.path.abspath(output_root))
    if output_root.resolve() != root_abs:
        raise OutputBoundaryError(f"copy 输出根已被重定向: {output_root}")
    resolved_parent = target.parent.resolve()
    if not resolved_parent.is_relative_to(root_abs):
        raise OutputBoundaryError(f"目标越过 copy 输出边界: {target} -> {resolved_parent}")


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

    ops = deterministic_ops(plan.operations)
    for index, op in enumerate(ops, 1):
        if progress:
            progress(index, len(ops), op.op_type)
        status = "failed"
        try:
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
                    do_move(op, tx_path)
                    status = "completed"
                elif op.op_type == "rmdir":
                    if do_rmdir(op, tx_path):
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
    apply_result_to_plan(plan, statuses, issues)
    return ExecutionResult(completed=completed, skipped=skipped, failed=failed, issues=issues, transaction_log=str(tx_path) if tx_path else None)


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
    target.parent.mkdir(parents=True, exist_ok=True)
    if output_root is not None:
        ensure_target_within_output(target, output_root)
    shutil.copy2(source, target)
    if target.stat().st_size != source.stat().st_size:
        raise RuntimeError(f"复制后大小校验失败: {target}")


def do_move(op: Operation, tx_path: Path | None) -> None:
    assert op.source_path and op.target_path
    source = Path(op.source_path)
    target = Path(op.target_path)
    if source.resolve() == target.resolve():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    write_tx(tx_path, {"action": "move", "source": str(source), "target": str(target), "status": "started"})
    shutil.move(str(source), str(target))
    write_tx(tx_path, {"action": "move", "source": str(source), "target": str(target), "status": "done"})


def do_rmdir(op: Operation, tx_path: Path | None) -> bool:
    """返回是否达成「目录已不存在」这一目的。非空目录返回 False，上层须计入跳过而非成功。"""
    assert op.source_path
    path = Path(op.source_path)
    if not path.exists():
        return True
    if any(path.iterdir()):
        return False
    write_tx(tx_path, {"action": "rmdir", "source": str(path), "status": "started"})
    path.rmdir()
    write_tx(tx_path, {"action": "rmdir", "source": str(path), "status": "done"})
    return True


def write_tx(tx_path: Path | None, row: dict) -> None:
    if not tx_path:
        return
    with tx_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def classify_exception(exc: Exception) -> str:
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


def delete_duplicates(
    root: str,
    entries: list[DeleteEntry],
    mode: str = "quarantine",
    confirm: bool = False,
    trash_dir: str | None = None,
    transaction_log: str | None = None,
) -> DeleteResult:
    """删除重复副本：执行层的唯一入口，所有前置校验在此，不在 UI。

    校验顺序不可调换：① 保留项不得在待删集合里 ② 待删文件必须在资源根内
    ③ 不是链接/目录 ④ 与保留项全文哈希一致 ⑤ 与诊断时的哈希一致。
    任何一条不成立只跳过该条，绝不「按过期诊断删文件」。

    mode=quarantine 移到资源根之外的隔离区（可撤销，同盘 move 很快，但不立即释放空间）；
    mode=purge 直接删除（释放空间，不可撤销，需 confirm）。
    """
    if mode not in {"quarantine", "purge"}:
        raise ValueError("删除方式必须是 quarantine（移入隔离区）或 purge（彻底删除）")
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
    trash = Path(trash_dir).resolve() if trash_dir else base.parent / f"{QUARANTINE_DIR_PREFIX}{stamp}"
    if mode == "quarantine":
        result.trash_dir = str(trash)

    handled: set[str] = set()
    for entry in entries:
        victim = Path(entry.path).resolve()
        keep = Path(entry.keep_path).resolve()
        key = str(victim)
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
            else:
                target = _quarantine_target(trash, base, victim)
                target.parent.mkdir(parents=True, exist_ok=True)
                write_tx(tx_path, {"action": "quarantine", "source": str(victim), "target": str(target), "status": "started"})
                shutil.move(str(victim), str(target))
                write_tx(tx_path, {"action": "quarantine", "source": str(victim), "target": str(target), "status": "done"})
                result.quarantined += 1
                result.quarantined_bytes += size
                result.rows.append(_delete_row(victim, "quarantined", "OK", f"已移入隔离区: {target}", size))
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


def _quarantine_target(trash: Path, base: Path, victim: Path) -> Path:
    target = trash / victim.relative_to(base)
    if not target.exists():
        return target
    for index in range(1, 100):
        candidate = target.with_name(f"{target.stem}__{index}{target.suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"隔离区内同名文件过多: {target}")


def _delete_row(victim: Path, status: str, code: str, message: str, size: int) -> dict:
    return {"path": str(victim), "status": status, "code": code, "message": message, "bytes": size}


