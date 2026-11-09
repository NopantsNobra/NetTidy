"""M2 · 九条安全不变量的结构性测试。

不变量 5（计划生成与执行分离）、6（冻结计划）、7（过期计划拒绝）、
9（多线程不改变结果）此前只有代码约定没有断言，见 docs/规则对照表.md 第二节。
不变量 6/7 的断言在 test_execution_seams.py，本文件覆盖 5、9 与事务日志防回归。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from nesttidy.model import OperationPlan
from nesttidy.planner import build_plan, snapshot_digest


def touch(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def resource_root(base: Path) -> Path:
    """真实形态夹具：资源根有散落文件 + 并列子目录，不靠占位文件屏蔽 R1。"""
    root = base / "资源包" / "课程A"
    touch(root / "说明.txt", "根文件")
    touch(root / "合集" / "A" / "封面.jpg", "a")
    touch(root / "合集" / "b" / "封面.jpg", "b")
    touch(root / "其他" / "备注.txt", "c")
    return root


def source_state(root: Path) -> tuple[str, dict[str, str], list[str]]:
    """源目录快照：digest + 文件内容 + 目录节点。

    snapshot_digest 只覆盖路径/size/mtime，补上内容与空目录维度，
    避免「计划生成阶段原地改写了文件却没被发现」的假绿。
    """
    files = {
        p.relative_to(root).as_posix().casefold(): p.read_text(encoding="utf-8")
        for p in root.rglob("*")
        if p.is_file()
    }
    dirs = sorted(
        p.relative_to(root).as_posix().casefold()
        for p in root.rglob("*")
        if p.is_dir()
    )
    return snapshot_digest([root.resolve()]), files, dirs


class PlannerReadOnlyTests(unittest.TestCase):
    """不变量 5：计划生成不得触碰被整理目录的任何内容。"""

    def test_build_plan_leaves_source_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = resource_root(Path(tmp))
            before = source_state(root)
            plan = build_plan(str(root), "single")

            self.assertGreater(len(plan.operations), 0, "夹具未生成任何操作，用例会假绿")
            self.assertEqual(source_state(root), before, "规划阶段改动了源目录")
            self.assertEqual(plan.source_snapshot_digest, before[0], "计划记录的快照与实际不符")


class ThreadConsistencyTests(unittest.TestCase):
    """不变量 9：多线程不得影响最终整理结果。"""

    @staticmethod
    def structure(plan: OperationPlan) -> list[tuple[str, str | None, str | None]]:
        """规范化结构决策，排除 plan_id / op_id / created_at 等每次不同的字段。"""
        return sorted(
            (
                op.op_type,
                Path(op.source_path).name.casefold() if op.source_path else None,
                Path(op.target_path).name.casefold() if op.target_path else None,
            )
            for op in plan.operations
        )

    def test_concurrent_plans_match_single_thread(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = resource_root(Path(tmp))
            before = source_state(root)
            baseline_plan = build_plan(str(root), "single")
            baseline = self.structure(baseline_plan)
            self.assertGreater(len(baseline), 0, "夹具未生成任何操作，用例会假绿")

            with ThreadPoolExecutor(max_workers=4) as pool:
                plans = list(pool.map(lambda _: build_plan(str(root), "single"), range(8)))

            for plan in plans:
                self.assertEqual(self.structure(plan), baseline, "并发规划改变了结构决策")
                self.assertEqual(plan.source_snapshot_digest, baseline_plan.source_snapshot_digest)
            # 反假绿：确认 8 次确实是各自独立的规划，而非复用同一对象。
            self.assertEqual(len({plan.plan_id for plan in plans}), 8)
            self.assertEqual(
                len({op.op_id for plan in plans for op in plan.operations}), 8 * len(baseline)
            )
            self.assertEqual(source_state(root), before, "并发规划期间源目录被改动")


class TransactionLogTests(unittest.TestCase):
    """ROADMAP 风险台账：executor 默认把事务日志写到 cwd，app.py 必须显式传参防回归。"""

    def test_cli_move_keeps_transaction_log_out_of_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            cwd = base / "工作目录"
            cwd.mkdir()
            root = base / "源" / "课程A"
            touch(root / "外壳" / "正文" / "电影.mkv", "payload")
            report = base / "报告"

            env = dict(os.environ)
            env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            proc = subprocess.run(
                [
                    sys.executable, "-B", "-m", "nesttidy.app", "move", str(root),
                    "--mode", "single", "--confirm", "--no-elevate", "--report-dir", str(report),
                ],
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )

            self.assertEqual(proc.returncode, 0, f"CLI move 失败: {proc.stderr}")
            payload = json.loads(proc.stdout)
            self.assertGreater(payload["completed"], 0, "CLI 没有真正执行，用例会假绿")
            self.assertEqual(list(cwd.iterdir()), [], "事务日志或缓存写进了进程工作目录")
            self.assertTrue((report / "transaction_log.jsonl").exists(), "事务日志没有落到报告目录")


if __name__ == "__main__":
    unittest.main()
