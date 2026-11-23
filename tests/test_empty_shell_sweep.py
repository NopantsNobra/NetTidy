"""1.8.0 · R6「移动完成后变空的目录一并删除」

历史缺陷：整理只删「计划里列出的空目录」。执行过程中目录变空（例如另一个程序在
计划冻结后删掉/搬走了它唯一的文件、或用户手工清过），这个目录就永远留成空壳。
现场证据：`V:\\食色\\海角社区` 整理后剩余 3 处残留，其中 `SDFVBN` 只剩一个
`desktop.ini`，执行结束也没有任何机制回头再看一眼。

本文件先于实现写就，改前必须失败。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from nesttidy.executor import execute_plan
from nesttidy.planner import build_plan
from nesttidy.recovery import undo_transaction


def touch(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class EmptyShellSweepTests(unittest.TestCase):
    """R6：move 结束后，资源根以外的空目录必须被收掉，并且可撤销。"""

    def _plan_with_emptied_dir(self, base: Path) -> tuple:
        package = base / "src" / "包"
        touch(package / "视频" / "a.mp4", "video")
        touch(package / "图集" / "b.jpg", "photo")
        touch(package / "图集" / "c.jpg", "photo")
        plan = build_plan(str(base / "src"), "batch")
        tx = base / "理巢报告" / "事务日志_test.jsonl"
        # 计划冻结后，图集目录里的两个文件被外部搬走 —— 这个目录不在计划的 rmdir 清单里
        (package / "图集" / "b.jpg").unlink()
        (package / "图集" / "c.jpg").unlink()
        return plan, package, tx

    def test_sweep_removes_dir_emptied_while_executing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            plan, package, tx = self._plan_with_emptied_dir(base)

            execute_plan(plan, "move", confirm_move=True, transaction_log=str(tx))

            self.assertFalse((package / "图集").exists(), "执行后变空的目录应被一并删除")
            self.assertTrue((package / "视频").is_dir(), "不该动还有内容的目录")

    def test_sweep_removes_empty_resource_root(self) -> None:
        """整理完就空了的资源根，连目录一起删（2026-11-24 管理员裁决）。"""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            package = base / "src" / "空包"
            touch(package / "壳" / "x.mp4", "video")
            plan = build_plan(str(base / "src"), "batch")
            tx = base / "理巢报告" / "事务日志_test.jsonl"
            # 计划冻结后唯一的文件被外部搬走：执行完这个资源根必然为空
            (package / "壳" / "x.mp4").unlink()

            execute_plan(plan, "move", confirm_move=True, transaction_log=str(tx))

            self.assertFalse(package.exists(), "整理后为空的资源根应一并删除")

    def test_sweep_keeps_selected_root_even_when_empty(self) -> None:
        """单包模式下资源根就是选中的那个目录，它是用户自己的容器，不删。"""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            package = base / "包"
            touch(package / "壳" / "x.mp4", "video")
            plan = build_plan(str(package), "single")
            tx = base / "理巢报告" / "事务日志_test.jsonl"
            (package / "壳" / "x.mp4").unlink()

            execute_plan(plan, "move", confirm_move=True, transaction_log=str(tx))

            self.assertTrue(package.is_dir(), "选中的目录本身保留")

    def test_undo_rebuilds_removed_resource_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            package = base / "src" / "空包"
            touch(package / "壳" / "x.mp4", "video")
            plan = build_plan(str(base / "src"), "batch")
            tx = base / "理巢报告" / "事务日志_test.jsonl"
            (package / "壳" / "x.mp4").unlink()
            execute_plan(plan, "move", confirm_move=True, transaction_log=str(tx))
            self.assertFalse(package.exists())

            rows = undo_transaction(str(tx))

            self.assertTrue(package.is_dir(), "撤销应把删掉的资源根重建回来")
            self.assertTrue(all(row.status == "ok" for row in rows if row.action == "rmdir"))

    def test_swept_dirs_are_recorded_in_transaction_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            plan, package, tx = self._plan_with_emptied_dir(base)

            execute_plan(plan, "move", confirm_move=True, transaction_log=str(tx))

            rows = [json.loads(line) for line in tx.read_text(encoding="utf-8").splitlines() if line.strip()]
            swept = [row for row in rows if row.get("action") == "rmdir" and row.get("status") == "done"]
            self.assertTrue(
                any(Path(row["source"]).name == "图集" for row in swept),
                f"清扫的目录要进事务日志才能还原，实际：{[row.get('source') for row in swept]}",
            )

    def test_undo_rebuilds_swept_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            plan, package, tx = self._plan_with_emptied_dir(base)
            execute_plan(plan, "move", confirm_move=True, transaction_log=str(tx))
            self.assertFalse((package / "图集").exists())

            rows = undo_transaction(str(tx))

            self.assertTrue((package / "图集").is_dir(), "撤销应把清扫掉的目录重建回来")
            rebuilt = [row for row in rows if row.action == "rmdir"]
            self.assertTrue(rebuilt, "撤销结果里应出现 rmdir 行")
            self.assertTrue(all(row.status == "ok" for row in rebuilt), f"rmdir 行应记为已还原：{rebuilt}")


if __name__ == "__main__":
    unittest.main()
