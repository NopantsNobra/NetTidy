"""0.5.1 · 执行层接缝用例

规则层「输出什么」由 test_rule_seams 覆盖；这里只覆盖「执行时会不会丢、会不会越界、
报告是否说真话、撤销能不能回溯」。这四条都属于「计划正确但执行失真」，
历史上一条测试都没有——42 个用例全绿时它们全部未被覆盖。
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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


class CopyOutputBoundaryTests(unittest.TestCase):
    """copy 输出根在计划期只 resolve 一次；执行期若子目录被换成 junction，
    写入会落到用户选择边界之外且仍报成功。必须逐文件复查。
    """

    @staticmethod
    def _junction(link: Path, target: Path) -> bool:
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
        )
        return result.returncode == 0

    def test_normal_output_root_is_not_rejected(self) -> None:
        """防 junction 不能把正常路径误拒。"""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            touch(base / "src" / "R" / "a.txt", "payload")
            out = base / "out"
            plan = build_plan(str(base / "src"), "batch", str(out))
            result = execute_plan(plan, "copy")
            self.assertEqual((result.completed, result.failed), (1, 0))
            self.assertTrue((out / "R" / "a.txt").exists())

    def test_junction_escape_is_refused_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            touch(base / "src" / "R" / "a.txt", "payload")
            out = base / "out"
            escape = base / "escape"
            escape.mkdir()
            plan = build_plan(str(base / "src"), "batch", str(out))
            target = next(Path(op.target_path) for op in plan.operations if op.op_type == "copy")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.parent.rmdir()
            if not self._junction(target.parent, escape):
                self.skipTest("本机无法创建 junction，跳过此 Windows 专有场景")

            result = execute_plan(plan, "copy")

            self.assertEqual(result.completed, 0)
            self.assertGreater(result.failed, 0)
            self.assertEqual([p for p in escape.rglob("*") if p.is_file()], [])
            self.assertIn("OUTPUT_BOUNDARY_ESCAPED", [row.code for row in result.issues])


class FrozenPlanTests(unittest.TestCase):
    """不变量 6：未冻结计划必须在任何磁盘写入之前被拒绝，且零副作用。"""

    @staticmethod
    def _relative_files(root: Path) -> list[str]:
        """源目录下所有文件的相对路径快照（大小写无关、已排序），供前后比对。"""
        return sorted(
            p.relative_to(root).as_posix().casefold()
            for p in root.rglob("*")
            if p.is_file()
        )

    def test_unfrozen_plan_is_rejected_without_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "资源包" / "课程A"
            touch(root / "外壳" / "正文" / "电影.mkv", "x")
            plan = build_plan(str(root), "single")
            before = self._relative_files(root)
            self.assertGreater(len(plan.operations), 0, "夹具未生成任何操作，用例会假绿")

            plan.frozen = False
            with self.assertRaisesRegex(ValueError, "未冻结"):
                execute_plan(plan, "move", confirm_move=True, transaction_log=str(base / "tx.jsonl"))

            self.assertEqual(self._relative_files(root), before, "拒绝执行后源目录文件发生变化")
            self.assertFalse((base / "tx.jsonl").exists(), "未冻结计划仍创建了事务日志")
            self.assertEqual({op.status for op in plan.operations}, {"planned"}, "计划状态被提前回写")


class PrecheckTests(unittest.TestCase):
    """不变量 7：执行前必须复核源状态。计划生成后源文件被改动，不得按过期计划执行。

    只断言操作状态与问题文本，不锁定 issue.code —— 当前 size/mtime 变化被
    classify_exception 归为 EXECUTION_FAILED 而非 SOURCE_CHANGED，此口径待后续裁决。
    """

    def test_size_change_rejects_stale_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "资源包" / "课程A"
            source = root / "外壳" / "正文" / "电影.mkv"
            touch(source, "x")
            plan = build_plan(str(root), "single")
            self.assertGreater(len(plan.operations), 0, "夹具未生成任何操作，用例会假绿")
            move_op = next(op for op in plan.operations if op.op_type == "move")

            expected_mtime = move_op.precheck["mtime_ns"]
            source.write_text("x" * 20, encoding="utf-8")
            os.utime(source, ns=(source.stat().st_atime_ns, expected_mtime))
            self.assertNotEqual(source.stat().st_size, move_op.precheck["size"], "夹具没有改变文件大小")
            self.assertEqual(
                source.stat().st_mtime_ns, expected_mtime, "夹具必须保持 mtime 不变，否则测到的是 mtime 分支"
            )

            result = execute_plan(plan, "move", confirm_move=True, transaction_log=str(base / "tx.jsonl"))

            self.assertGreaterEqual(result.failed, 1)
            self.assertEqual(move_op.status, "failed", "过期计划的操作状态未回写")
            self.assertIn("源文件大小已变化", " ".join(row.message for row in result.issues))
            self.assertTrue(source.exists(), "源文件被按过期计划搬走")
            self.assertEqual(source.read_text(encoding="utf-8"), "x" * 20, "源文件内容被改动")
            self.assertFalse((root / "电影.mkv").exists(), "拒绝执行后仍产生了目标文件")

    def test_mtime_change_rejects_stale_plan_with_same_size(self) -> None:
        # Windows NTFS 的 st_mtime_ns 是 100ns 粒度：加 1ns 不生效（实测 8/8 假绿风险），
        # 故用 +1 秒。先断言 size 未变，避免误测到 size 分支。
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "资源包" / "课程A"
            source = root / "外壳" / "正文" / "电影.mkv"
            touch(source, "x")
            plan = build_plan(str(root), "single")
            self.assertGreater(len(plan.operations), 0, "夹具未生成任何操作，用例会假绿")
            move_op = next(op for op in plan.operations if op.op_type == "move")

            stat = source.stat()
            os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
            self.assertEqual(source.stat().st_size, move_op.precheck["size"], "本用例必须保持 size 不变")
            self.assertNotEqual(
                source.stat().st_mtime_ns, move_op.precheck["mtime_ns"], "NTFS 未反映 mtime 变化，用例会假绿"
            )

            result = execute_plan(plan, "move", confirm_move=True, transaction_log=str(base / "tx.jsonl"))

            self.assertGreaterEqual(result.failed, 1)
            self.assertEqual(move_op.status, "failed", "过期计划的操作状态未回写")
            self.assertIn("源文件修改时间已变化", " ".join(row.message for row in result.issues))
            self.assertTrue(source.exists(), "源文件被按过期计划搬走")
            self.assertFalse((root / "电影.mkv").exists(), "拒绝执行后仍产生了目标文件")


class ExecutorFailureTests(unittest.TestCase):
    """executor 失败路径：权限拒绝必须分类留痕，且不得留下半完成的搬移。"""

    def test_permission_denied_is_classified_and_source_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "资源包" / "课程A"
            source = root / "外壳" / "正文" / "电影.mkv"
            touch(source, "payload")
            plan = build_plan(str(root), "single")
            self.assertGreater(len(plan.operations), 0, "夹具未生成任何操作，用例会假绿")
            move_op = next(op for op in plan.operations if op.op_type == "move")

            with mock.patch("nesttidy.executor.shutil.move", side_effect=PermissionError("拒绝访问")):
                result = execute_plan(plan, "move", confirm_move=True, transaction_log=str(base / "tx.jsonl"))

            self.assertGreaterEqual(result.failed, 1)
            self.assertEqual(move_op.status, "failed", "权限失败的操作状态未回写")
            self.assertIn("PERMISSION_DENIED", [row.code for row in result.issues])
            self.assertTrue(source.exists(), "权限失败后源文件仍被搬走")
            self.assertEqual(source.read_text(encoding="utf-8"), "payload", "源文件内容被改动")
            self.assertFalse((root / "电影.mkv").exists(), "权限失败后仍产生了目标文件")


if __name__ == "__main__":
    unittest.main()
