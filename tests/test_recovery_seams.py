"""撤销层加固用例：末行截断容错（任务 1）+ rmdir 永不重放策略锁（M2 裁决）。"""
from __future__ import annotations

import json
import os
import subprocess
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
    """v1.8.0（R6）裁决：undo 重建空目录，rmdir 行记 ok 并逐条说明。"""

    def test_rmdir_rows_are_replayed_by_rebuilding_the_dir(self) -> None:
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
            self.assertEqual(rows[0].status, "ok")
            self.assertIn("重建", rows[0].message)
            self.assertTrue(removed_dir.is_dir(), "rmdir 行必须被重放，把目录重建回来")

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



class UndoBoundaryTests(unittest.TestCase):
    @staticmethod
    def _link(link: Path, target: Path) -> bool:
        if os.name == "nt":
            return subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True).returncode == 0
        try:
            link.symlink_to(target, target_is_directory=True)
            return True
        except OSError:
            return False

    def test_undo_refuses_redirected_original_parent_and_retains_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            root = base / "资源包"
            root.mkdir()
            outside = base / "外部"
            outside.mkdir()
            link = root / "原位置"
            if not self._link(link, outside):
                self.skipTest("本机无法创建目录链接")
            try:
                source = link / "文件.txt"
                target = root / "文件.txt"
                touch(target, "payload")
                tx = base / "tx.jsonl"
                tx.write_text(json.dumps({"action": "move", "source": str(source), "target": str(target), "resource_root": str(root), "status": "done"}, ensure_ascii=False) + "\n", encoding="utf-8")

                rows = undo_transaction(str(tx))

                self.assertEqual(rows[0].status, "failed")
                self.assertTrue(tx.exists(), "失败后不得退役日志")
                self.assertEqual(target.read_text(encoding="utf-8"), "payload")
                self.assertFalse((outside / "文件.txt").exists())
            finally:
                if os.name == "nt":
                    link.rmdir()
                else:
                    link.unlink()

    def test_undo_refuses_recorded_root_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            root = base / "资源包"
            root.mkdir()
            target = root / "文件.txt"
            touch(target, "payload")
            outside = base / "外部" / "文件.txt"
            tx = base / "tx.jsonl"
            tx.write_text(json.dumps({"action": "move", "source": str(outside), "target": str(target), "resource_root": str(root), "status": "done"}, ensure_ascii=False) + "\n", encoding="utf-8")

            rows = undo_transaction(str(tx))

            self.assertEqual(rows[0].status, "failed")
            self.assertTrue(tx.exists())
            self.assertFalse(outside.parent.exists())
            self.assertEqual(target.read_text(encoding="utf-8"), "payload")

    def test_undo_refuses_legacy_rmdir_through_link(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            outside = base / "外部"
            outside.mkdir()
            link = base / "链接"
            if not self._link(link, outside):
                self.skipTest("本机无法创建目录链接")
            try:
                tx = base / "tx.jsonl"
                tx.write_text(json.dumps({"action": "rmdir", "source": str(link / "空壳"), "status": "done"}, ensure_ascii=False) + "\n", encoding="utf-8")

                rows = undo_transaction(str(tx))

                self.assertEqual(rows[0].status, "failed")
                self.assertTrue(tx.exists())
                self.assertFalse((outside / "空壳").exists())
            finally:
                if os.name == "nt":
                    link.rmdir()
                else:
                    link.unlink()



if __name__ == "__main__":
    unittest.main()
