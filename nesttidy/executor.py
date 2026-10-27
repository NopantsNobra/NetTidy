from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from .model import ExecutionIssue, Operation, OperationPlan
from .planner import issue, validate_copy_output


@dataclass
class ExecutionResult:
    completed: int
    skipped: int
    failed: int
    issues: list[ExecutionIssue]
    transaction_log: str | None


def execute_plan(plan: OperationPlan, mode: str, confirm_move: bool = False, transaction_log: str | None = None) -> ExecutionResult:
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
    tx_path: Path | None = Path(transaction_log).resolve() if transaction_log else None
    if mode == "move" and tx_path is None:
        tx_path = Path.cwd() / "transaction_log.jsonl"
    if tx_path:
        tx_path.parent.mkdir(parents=True, exist_ok=True)

    for op in deterministic_ops(plan.operations):
        status = "failed"
        try:
            precheck(op)
            if op.op_type == "keep":
                status = "skipped"
            elif mode == "copy":
                if op.op_type in {"copy", "move"}:
                    do_copy(op)
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
    """把执行事实回写进计划：操作状态、映射状态、执行期问题。

    不回写的话，报告里的 issues.csv 只含计划期问题、状态永远停在 planned，
    执行失败在唯一的回溯凭证上完全消失。
    """
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


def do_copy(op: Operation) -> None:
    assert op.source_path and op.target_path
    source = Path(op.source_path)
    target = Path(op.target_path)
    if source.resolve() == target.resolve():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
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
    if isinstance(exc, PermissionError):
        return "PERMISSION_DENIED"
    if isinstance(exc, FileNotFoundError):
        return "SOURCE_CHANGED"
    if isinstance(exc, FileExistsError):
        return "TARGET_CONFLICT"
    if isinstance(exc, OSError) and getattr(exc, "winerror", None) in {32, 33}:
        return "FILE_LOCKED"
    return "EXECUTION_FAILED"

