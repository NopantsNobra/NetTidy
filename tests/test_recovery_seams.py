"""撤销层加固用例：末行截断容错（任务 1）+ rmdir 永不重放策略锁（M2 裁决）。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from nesttidy.recovery import undo_transaction


def touch(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TruncatedLogTests(unittest.TestCase):
    """事务日志末行截断时，已完成的 move 仍必须被回收。"""

    def test_truncated_last_line_does_not_abandon_completed_moves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "origin" / "a.txt"
            target = base / "moved" / "a.txt"
            touch(target, "payload")
            tx = base / "tx.jsonl"
            # 1 条完整 started 行 + 1 条写一半崩溃的截断末行。
            tx.write_text(
                json.dumps(
                    {"action": "move", "source": str(source), "target": str(target), "status": "started"},
                    ensure_ascii=False,
                )
                + "\n"
                + '{"action": "move", "sour',
                encoding="utf-8",
            )

            rows = undo_transaction(str(tx))  # 修复前此处抛 JSONDecodeError

            self.assertEqual(len(rows), 1, "截断行必须被跳过，完整行照常处理")
            self.assertEqual(rows[0].status, "ok", "started 行必须被回收")
            self.assertTrue(source.exists(), "已搬移的文件没有回到原路径")
            self.assertFalse(target.exists())
            self.assertEqual(source.read_text(encoding="utf-8"), "payload")


class UndoRmdirPolicyTests(unittest.TestCase):
    """M2 裁决：undo 不重建空目录，rmdir 行只报 skipped，绝不重放。"""

    def test_rmdir_rows_are_never_replayed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            removed_dir = base / "壳"
            tx = base / "tx.jsonl"
            tx.write_text(
                json.dumps({"action": "rmdir", "source": str(removed_dir), "status": "done"}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            rows = undo_transaction(str(tx))

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].status, "skipped")
            self.assertIn("不支持自动撤销", rows[0].message)
            self.assertFalse(removed_dir.exists(), "rmdir 行被重放，违反 M2 裁决")

    def test_purge_delete_rows_are_reported_as_unrecoverable(self) -> None:
        """0.6.0 新增 purge 删除：undo 必须明说不可撤销，不能静默 skipped 了事。"""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            tx = base / "tx.jsonl"
            tx.write_text(
                json.dumps({"action": "delete", "source": str(base / "gone.txt"), "mode": "purge", "status": "done"}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            rows = undo_transaction(str(tx))

            self.assertEqual(rows[0].status, "skipped")
            self.assertIn("彻底删除无法撤销", rows[0].message)


if __name__ == "__main__":
    unittest.main()
