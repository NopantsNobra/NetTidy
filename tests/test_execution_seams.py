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
from nesttidy.model import Operation, OperationPlan
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

    def test_nested_link_escape_creates_no_directory_outside_output(self) -> None:
        """边界复查必须先于 mkdir：父链被换成链接时，拒绝前连空目录都不许建到输出根之外。

        构造必须是两层：R 下的子目录各放两个文件，否则 R 规则会把「只有一个文件的子目录」
        拍平，target.parent 直接等于链接本身，mkdir(exist_ok=True) 成为空操作，测不出缺陷。
        """
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            for rel in ("keep1/a.txt", "keep1/a2.txt", "keep2/b.txt", "keep2/b2.txt"):
                touch(base / "src" / "R" / rel, "payload")
            out = base / "out"
            out.mkdir()
            escape = base / "escape"
            escape.mkdir()
            plan = build_plan(str(base / "src"), "batch", str(out))
            try:
                (out / "R").symlink_to(escape, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("本机无法创建目录链接，跳过此边界场景（Windows 由 junction 用例覆盖）")

            result = execute_plan(plan, "copy")

            self.assertEqual(result.completed, 0)
            self.assertGreater(result.failed, 0)
            self.assertEqual(
                sorted(p.name for p in escape.iterdir()), [], "边界拒绝之前已把目录建到 copy 输出根之外"
            )
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


class UndoCreatedFolderTests(unittest.TestCase):
    """整理时新建的文件夹必须随撤销一起收掉。

    真实现场：move 会为目标路径 mkdir，撤销只搬回文件、不记账新建的目录，
    于是「撤销」之后磁盘上多出一串空壳——用户以为复原了，其实没复原干净。
    """

    @staticmethod
    def _plan(root: Path, source: Path, target: Path) -> OperationPlan:
        return OperationPlan(
            plan_id="p", app_version="t", rule_version="r", mode="move",
            selected_root=str(root), output_root=None, config_snapshot={},
            source_snapshot_digest="d", resource_roots=[str(root)],
            operations=[Operation("o1", "rid", "move", str(source), str(target), ["R4 单文件提级"])],
            issues=[], mappings=[], created_at="t",
        )

    def test_undo_removes_folders_created_by_move(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "R"
            source = root / "a" / "b" / "deep.txt"
            touch(source, "payload")
            target = root / "新分类" / "深层" / "deep.txt"
            tx = base / "tx.jsonl"

            result = execute_plan(self._plan(root, source, target), "move", confirm_move=True, transaction_log=str(tx))
            self.assertEqual(result.completed, 1)
            self.assertTrue(target.exists(), "夹具未真正搬动文件，用例会假绿")

            rows = undo_transaction(str(tx))
            self.assertTrue(source.exists(), "文件没有搬回原路径")
            self.assertFalse((root / "新分类" / "深层").exists(), "撤销后仍留着整理时新建的最深层文件夹")
            self.assertFalse((root / "新分类").exists(), "撤销后仍留着整理时新建的文件夹")
            self.assertIn(("mkdir", "ok"), {(row.action, row.status) for row in rows})

    def test_undo_keeps_created_folder_that_gained_other_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "R"
            source = root / "a" / "deep.txt"
            touch(source, "payload")
            target = root / "新分类" / "deep.txt"
            tx = base / "tx.jsonl"
            execute_plan(self._plan(root, source, target), "move", confirm_move=True, transaction_log=str(tx))
            stranger = root / "新分类" / "用户自己放的文件.txt"
            touch(stranger, "别动我")

            rows = undo_transaction(str(tx))
            self.assertTrue(source.exists())
            mkdir_rows = [row for row in rows if row.action == "mkdir"]
            self.assertTrue(mkdir_rows, "新建目录没有记账")
            self.assertEqual(mkdir_rows[0].status, "skipped", "目录非空却被删了")
            self.assertIn("还有别的内容", mkdir_rows[0].message)
            self.assertTrue(stranger.exists(), "撤销删掉了不属于本次整理的文件")
            self.assertTrue((root / "新分类").is_dir())


    def test_undo_rebuilds_removed_empty_shell(self) -> None:
        """v1.8.0（R6）起空目录删除可还原：撤销把壳重建回来，并逐条说明。"""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "R"
            touch(root / "外壳" / "正文" / "电影.mkv", "payload")
            (root / "外壳" / "原本就是空的").mkdir(parents=True)
            plan = build_plan(str(root), "single")
            tx = base / "tx.jsonl"

            result = execute_plan(plan, "move", confirm_move=True, transaction_log=str(tx))
            self.assertEqual(result.failed, 0)
            self.assertFalse((root / "外壳" / "原本就是空的").exists(), "夹具未触发空目录清理，用例会假绿")

            rows = undo_transaction(str(tx))
            self.assertTrue((root / "外壳" / "正文" / "电影.mkv").exists(), "文件没有搬回原路径")
            self.assertTrue((root / "外壳" / "原本就是空的").is_dir(), "撤销应把删掉的空目录重建回来")
            rmdir_rows = [row for row in rows if row.action == "rmdir"]
            self.assertTrue(rmdir_rows, "空目录删除没有出现在撤销结果里")
            self.assertTrue(all(row.status == "ok" for row in rmdir_rows), f"rmdir 行应记为已还原：{rmdir_rows}")
            self.assertIn("重建", rmdir_rows[0].message)

    def test_undo_retires_log_and_empty_report_folder(self) -> None:
        """撤销到位后，本次执行留下的日志与空掉的「理巢报告」目录一起收掉。"""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "R"
            source = root / "a" / "deep.txt"
            touch(source, "payload")
            target = root / "新分类" / "deep.txt"
            report = base / "理巢报告"
            report.mkdir()
            tx = report / "事务日志_1.jsonl"

            execute_plan(self._plan(root, source, target), "move", confirm_move=True, transaction_log=str(tx))
            self.assertTrue(tx.exists(), "事务日志没有写出来，用例会假绿")
            undo_transaction(str(tx))
            self.assertTrue(source.exists(), "文件没有搬回原路径")
            self.assertFalse(tx.exists(), "撤销后仍留着事务日志")
            self.assertFalse(report.exists(), "撤销后仍留着空掉的理巢报告目录")

    def test_undo_keeps_report_folder_that_still_holds_other_logs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "R"
            source = root / "a" / "deep.txt"
            touch(source, "payload")
            target = root / "新分类" / "deep.txt"
            report = base / "理巢报告"
            report.mkdir()
            tx = report / "事务日志_1.jsonl"
            other = report / "事务日志_0.jsonl"
            other.write_text("{}\n", encoding="utf-8")

            execute_plan(self._plan(root, source, target), "move", confirm_move=True, transaction_log=str(tx))
            undo_transaction(str(tx))
            self.assertFalse(tx.exists())
            self.assertTrue(other.exists(), "撤销连带删掉了别人的日志")
            self.assertTrue(report.is_dir(), "目录里还有别的日志却被删了")

    def test_undo_keeps_log_when_any_row_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "R"
            source = root / "a" / "deep.txt"
            touch(source, "payload")
            target = root / "新分类" / "deep.txt"
            tx = base / "tx.jsonl"
            execute_plan(self._plan(root, source, target), "move", confirm_move=True, transaction_log=str(tx))
            touch(source, "用户又手动放了一份")  # 原路径被占，撤销必然失败

            rows = undo_transaction(str(tx))
            self.assertIn("failed", {row.status for row in rows})
            self.assertTrue(tx.exists(), "有失败行还删了日志，用户没法再撤一次")



class MoveResourceBoundaryTests(unittest.TestCase):
    def test_move_refuses_cross_resource_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            source = base / "源" / "甲" / "壳" / "文件.txt"
            touch(source, "payload")
            other = base / "源" / "乙"
            other.mkdir()
            plan = build_plan(str(base / "源"), "batch")
            op = next(op for op in plan.operations if op.op_type == "move")
            target = other / "不应创建" / "文件.txt"
            op.target_path = str(target)

            result = execute_plan(plan, "move", confirm_move=True, transaction_log=str(base / "tx.jsonl"))

            self.assertEqual(source.read_text(encoding="utf-8"), "payload")
            self.assertFalse(target.parent.exists())
            self.assertTrue(any(row.code == "MOVE_BOUNDARY_REFUSED" for row in result.issues))

    def test_move_refuses_redirected_source_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            root = base / "资源包"
            source = root / "壳" / "文件.txt"
            touch(source, "payload")
            plan = build_plan(str(root), "single")
            outside = base / "外部"
            source.parent.rename(outside)
            link = root / "壳"
            if os.name == "nt":
                if not CopyOutputBoundaryTests._junction(link, outside):
                    self.skipTest("本机无法创建 junction")
            else:
                try:
                    link.symlink_to(outside, target_is_directory=True)
                except OSError:
                    self.skipTest("本机无法创建目录符号链接")
            try:
                result = execute_plan(plan, "move", confirm_move=True, transaction_log=str(base / "tx.jsonl"))
                self.assertEqual((outside / "文件.txt").read_text(encoding="utf-8"), "payload")
                self.assertFalse((root / "文件.txt").exists())
                self.assertTrue(any(row.code == "MOVE_BOUNDARY_REFUSED" for row in result.issues))
            finally:
                if os.name == "nt":
                    link.rmdir()
                else:
                    link.unlink()

    def test_planned_rmdir_refuses_redirected_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            root = base / "资源包"
            root.mkdir()
            empty = root / "空壳"
            empty.mkdir()
            plan = build_plan(str(root), "single")
            self.assertTrue(any(op.op_type == "rmdir" for op in plan.operations))
            outside = base / "外部"
            outside.mkdir()
            empty.rmdir()
            if os.name == "nt":
                if not CopyOutputBoundaryTests._junction(empty, outside):
                    self.skipTest("本机无法创建 junction")
            else:
                try:
                    empty.symlink_to(outside, target_is_directory=True)
                except OSError:
                    self.skipTest("本机无法创建目录符号链接")
            try:
                result = execute_plan(plan, "move", confirm_move=True, transaction_log=str(base / "tx.jsonl"))
                self.assertTrue(outside.is_dir())
                self.assertTrue(empty.is_dir())
                self.assertTrue(any(row.code == "MOVE_BOUNDARY_REFUSED" for row in result.issues))
            finally:
                if os.name == "nt":
                    empty.rmdir()
                else:
                    empty.unlink()



if __name__ == "__main__":
    unittest.main()
