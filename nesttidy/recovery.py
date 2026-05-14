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
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if item.get("status") == "done":
            rows.append(item)
    results: list[UndoResult] = []
    for item in reversed(rows):
        if item.get("action") != "move":
            results.append(UndoResult(item.get("action", ""), item.get("source", ""), item.get("target", ""), "skipped", "仅 move 操作可自动撤销"))
            continue
        source = Path(item["source"])
        target = Path(item["target"])
        if not target.exists():
            results.append(UndoResult("move", str(source), str(target), "skipped", "目标已不存在"))
            continue
        if source.exists():
            results.append(UndoResult("move", str(source), str(target), "failed", "原路径已存在，禁止覆盖"))
            continue
        source.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target), str(source))
        results.append(UndoResult("move", str(source), str(target), "ok", "已移动回原路径"))
    return results


def rows_as_dicts(rows: list[UndoResult]) -> list[dict]:
    return [asdict(row) for row in rows]

