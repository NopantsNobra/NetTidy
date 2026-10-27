from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class UndoResult:
    action: str
    source: str
    target: str
    status: str
    message: str


def undo_transaction(transaction_log: str) -> list[UndoResult]:
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
        item = json.loads(line)
        if item.get("status") not in {"started", "done"}:
            continue
        key = (item.get("action"), item.get("source"), item.get("target"))
        if key not in collected:
            collected[key] = item
            order.append(key)
        elif item.get("status") == "done":
            collected[key] = item
    results: list[UndoResult] = []
    for key in reversed(order):
        item = collected[key]
        if item.get("action") != "move":
            results.append(UndoResult(item.get("action", ""), item.get("source", ""), item.get("target") or "", "skipped", "仅 move 操作可自动撤销"))
            continue
        source = Path(item["source"])
        target = Path(item["target"])
        source_exists = source.exists()
        target_exists = target.exists()
        if source_exists and target_exists:
            results.append(UndoResult("move", str(source), str(target), "failed", "原路径与目标同时存在，需人工确认"))
            continue
        if not target_exists:
            results.append(UndoResult("move", str(source), str(target), "skipped", "现场未见搬移结果，跳过"))
            continue
        source.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target), str(source))
        message = "已移动回原路径" if item.get("status") == "done" else "日志缺 done，按现场状态回收"
        results.append(UndoResult("move", str(source), str(target), "ok", message))
    return results


def rows_as_dicts(rows: list[UndoResult]) -> list[dict]:
    return [asdict(row) for row in rows]

