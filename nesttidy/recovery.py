from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import REPORT_DIR_NAME


@dataclass
class UndoResult:
    action: str
    source: str
    target: str
    status: str
    message: str


def undo_transaction(transaction_log: str, progress=None) -> list[UndoResult]:
    path = Path(transaction_log).resolve()
    if not path.exists():
        raise FileNotFoundError(f"事务日志不存在: {path}")
    # started 行必须参与撤销：move 已完成但 done 日志写失败（磁盘满、进程被杀）时，
    # 文件已经在新位置，只认 done 会把这条操作从可恢复集合里彻底丢掉。
    collected: dict[tuple, dict] = {}
    order: list[tuple] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            # 末行截断（写一半崩溃）恰是 undo 最需要的场景：跳过该行，其余完整行照常回收；只捕获解析错误，不吞其他异常。
            continue
        if item.get("status") not in {"started", "done"}:
            continue
        key = (item.get("action"), item.get("source"), item.get("target"))
        if key not in collected:
            collected[key] = item
            order.append(key)
        elif item.get("status") == "done":
            collected[key] = item
    results: list[UndoResult] = []
    for index, key in enumerate(reversed(order), 1):
        if progress:
            progress(index, len(order), str(key[1] or ""), "撤销")
        item = collected[key]
        action = item.get("action", "")
        if action == "recycle":
            results.append(UndoResult(action, str(item.get("source", "")), "", "skipped", "已移入回收站：在回收站里右键「还原」即可放回原位置"))
            continue
        if action == "mkdir":
            # 整理时新建的空文件夹：文件都已搬回原处后再收掉，非空则一律不动（里面还有别人的东西）。
            folder = Path(str(item.get("source", "")))
            if not folder.exists():
                results.append(UndoResult(action, str(folder), "", "skipped", "目录已不存在"))
            elif any(folder.iterdir()):
                results.append(UndoResult(action, str(folder), "", "skipped", "目录里还有别的内容，未删除"))
            else:
                folder.rmdir()
                results.append(UndoResult(action, str(folder), "", "ok", "已删除整理时新建的空文件夹"))
            continue
        if action == "rmdir":
            # M2 裁决不变：空目录删除不重建。重建出来的空壳和用户当初看到的目录树并非同一件事，
            # 而且会让「撤销」凭空多出目录。这里只如实说明，不重放。
            results.append(UndoResult(action, str(item.get("source", "")), "", "skipped", "空目录删除不重建（该操作不支持自动撤销）"))
            continue
        if action not in {"move", "quarantine"}:
            # delete（彻底删除）没有现场可回收，必须显式说清，不能让它静默混在 skipped 里。
            note = "彻底删除无法撤销（未进入隔离区）" if action == "delete" else "该操作不支持自动撤销"
            results.append(UndoResult(action, item.get("source", ""), item.get("target") or "", "skipped", note))
            continue
        source = Path(item["source"])
        target = Path(item["target"])
        source_exists = source.exists()
        target_exists = target.exists()
        if source_exists and target_exists:
            results.append(UndoResult(action, str(source), str(target), "failed", "原路径与隔离副本同时存在，需人工确认"))
            continue
        if not target_exists:
            results.append(UndoResult(action, str(source), str(target), "skipped", "现场未见隔离副本，跳过"))
            continue
        source.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target), str(source))
        if action == "quarantine":
            message = "已从隔离区放回原路径" if item.get("status") == "done" else "日志缺 done，按现场状态放回"
        else:
            message = "已移动回原路径" if item.get("status") == "done" else "日志缺 done，按现场状态回收"
        results.append(UndoResult(action, str(source), str(target), "ok", message))
    _retire_log(path, results)
    return results


def _retire_log(path: Path, results: list[UndoResult]) -> None:
    """撤销全部到位后收掉这次执行留下的记录物，别把「整理产生的文件夹」留在用户目录里。

    只在没有任何失败行时动手（失败行还要靠日志再撤一次）；只删本次日志，并且仅当
    `理巢报告` 目录已经空了才连带删掉它——里面还有别的日志就原样保留。
    """
    if any(row.status == "failed" for row in results):
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return
    parent = path.parent
    if parent.name.casefold() != REPORT_DIR_NAME.casefold():
        return
    try:
        if not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        return


def rows_as_dicts(rows: list[UndoResult]) -> list[dict]:
    return [asdict(row) for row in rows]

