"""0.5.1 · 执行层接缝用例

规则层「输出什么」由 test_rule_seams 覆盖；这里只覆盖「执行时会不会丢、会不会越界、
报告是否说真话、撤销能不能回溯」。这四条都属于「计划正确但执行失真」，
历史上一条测试都没有——42 个用例全绿时它们全部未被覆盖。
"""
from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

import nesttidy.executor as executor
from nesttidy.executor import execute_plan
from nesttidy.planner import build_plan
from nesttidy.recovery import undo_transaction
from nesttidy.reports import write_plan_report


def touch(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class CopyCompletenessTests(unittest.TestCase):
    """copy 模式的契约是「交出一份完整副本」，不是「把需要移动的搬过去」。"""

    def test_copy_mode_keeps_in_place_files_in_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source_root = base / "src" / "Resource"
            output_root = base / "out"
            touch(source_root / "already.txt", "keep me")
            touch(source_root / "shell" / "nested.txt", "move me")

            plan = build_plan(str(base / "src"), "batch", str(output_root))
            result = execute_plan(plan, "copy")

            produced = sorted(p.relative_to(output_root).as_posix() for p in output_root.rglob("*") if p.is_file())
            self.assertEqual(produced, ["Resource/already.txt", "Resource/nested.txt"], "输出目录缺文件")
            self.assertEqual(result.failed, 0)
            self.assertEqual(result.completed, 2)
            self.assertEqual(result.skipped, 0, "copy 计划不应再出现被跳过的原位文件")


class PlanModeConsistencyTests(unittest.TestCase):
    """含 output_root 的计划是 copy 专用，按 move 执行等于把文件搬出资源根。"""

    def test_copy_plan_rejects_move_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source_root = base / "R"
            source = source_root / "shell" / "a.txt"
            touch(source, "source")

            plan = build_plan(str(source_root), "single", str(base / "out"))
            with self.assertRaises(ValueError):
                execute_plan(plan, "move", confirm_move=True, transaction_log=str(base / "tx.jsonl"))

            self.assertTrue(source.exists(), "源文件被移出资源根")
            self.assertFalse((base / "out" / "R" / "a.txt").exists(), "拒绝执行后仍产生了输出")


class ExecutionReportTests(unittest.TestCase):
    """报告是唯一的回溯凭证，执行失败必须在里面留下痕迹。"""

    def test_execution_failure_lands_in_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "R"
            touch(root / "shell" / "a.txt", "x")
            plan = build_plan(str(root), "single")
            touch(root / "a.txt", "已存在的外部文件")

            result = execute_plan(plan, "move", confirm_move=True, transaction_log=str(base / "tx.jsonl"))
            self.assertGreaterEqual(result.failed, 1)

            report = write_plan_report(plan, base / "report")
            with (report / "issues.csv").open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertTrue(rows, "执行期问题没有进入 issues.csv")

            move_op = next(op for op in plan.operations if op.op_type == "move")
            self.assertEqual(move_op.status, "failed", "操作状态没有回写执行结果")
            mapping = next(m for m in plan.mappings if m.entry_id == move_op.op_id)
            self.assertEqual(mapping.status, "failed", "映射状态没有回写执行结果")

    def test_successful_execution_marks_operations_completed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "R"
            touch(root / "shell" / "a.txt", "x")
            plan = build_plan(str(root), "single")

            result = execute_plan(plan, "move", confirm_move=True, transaction_log=str(base / "tx.jsonl"))
            self.assertEqual(result.failed, 0)
            self.assertEqual({op.status for op in plan.operations}, {"completed"})


class UndoCoverageTests(unittest.TestCase):
    """done 日志写失败时文件已经移动了，撤销不能因此把这条操作丢掉。"""

    def test_undo_recovers_started_rows_after_move(self) -> None:
        real_write = executor.write_tx
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "R"
            source = root / "shell" / "a.txt"
            touch(source, "payload")
            plan = build_plan(str(root), "single")
            tx = base / "tx.jsonl"

            def flaky(tx_path, row):
                if row.get("status") == "done":
                    raise OSError("磁盘写入失败（注入）")
                return real_write(tx_path, row)

            executor.write_tx = flaky
            try:
                result = execute_plan(plan, "move", confirm_move=True, transaction_log=str(tx))
            finally:
                executor.write_tx = real_write

            self.assertGreaterEqual(result.failed, 1)
            moved_target = root / "a.txt"
            self.assertFalse(source.exists())
            self.assertTrue(moved_target.exists())

            rows = undo_transaction(str(tx))
            moved_back = [row for row in rows if row.status == "ok"]
            self.assertTrue(moved_back, "已移动的文件没有进入可恢复集合")
            self.assertTrue(source.exists(), "撤销后文件没有回到原路径")
            self.assertFalse(moved_target.exists())

    def test_undo_skips_started_rows_that_never_took_effect(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "origin" / "a.txt"
            touch(source, "still here")
            tx = base / "tx.jsonl"
            tx.write_text(
                json.dumps({"action": "move", "source": str(source), "target": str(base / "gone" / "a.txt"), "status": "started"}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            rows = undo_transaction(str(tx))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].status, "skipped")
            self.assertTrue(source.exists())


if __name__ == "__main__":
    unittest.main()
