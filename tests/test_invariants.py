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
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from nesttidy import planner
from nesttidy.model import OperationPlan
from nesttidy.planner import build_plan, scan_residual_dirs, snapshot_digest


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


class PlannerResidualDirEquivalenceTests(unittest.TestCase):
    """目录归属判定优化的行为一致性回归。

    build_plan 末尾的残留目录判定由 O(目录数 x 文件数) 改为父路径集合查表。
    这里把优化前的循环原样内联为对照实现，通过临时替换 planner 模块里的
    新函数，跑同一份真实深嵌套夹具生成两份完整计划，逐条比对全部字段。
    """

    @staticmethod
    def legacy_scan(scanned_dirs: list[Path], local_targets: list[Path], root: Path,
                    removed_dirs: set[Path], resolve=None) -> None:
        """优化前的实现，仅用于行为对照，不要在生产代码中复活。

        resolve 是 P0-3 注入的同语义记忆化解析，与 scanned.resolve() 结果一致；
        接受它是为了兼容 build_plan 的新签名，对照的仍是残留目录判定算法。
        """
        if resolve is None:
            resolve = lambda path: path.resolve()
        for scanned in scanned_dirs:
            resolved = resolve(scanned)
            if resolved == root or resolved in removed_dirs:
                continue
            if not any(resolved in target.parents for target in local_targets):
                removed_dirs.add(resolved)

    @staticmethod
    def normalized(plan: OperationPlan) -> dict:
        """抹掉每次生成都不同的 id/时间戳，其余字段全量参与比对。"""
        data = plan.to_dict()
        data["plan_id"] = data["created_at"] = "<volatile>"
        for key, field in (("operations", "op_id"), ("mappings", "entry_id"), ("issues", "issue_id")):
            for row in data[key]:
                row[field] = "<volatile>"
                if "created_at" in row:
                    row["created_at"] = "<volatile>"
        return data

    def test_deep_nested_plan_matches_legacy_loop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "资源库" / "专题A"
            # 真实深嵌套 + 中文目录名：单链壳、空壳、R3 全提、R3 父级保护同时出现。
            files = (
                "总览.txt",
                "电影/下载源/解压目录/第一季/第01集.mkv", "电影/下载源/解压目录/第一季/第02集.mkv",
                "电影/下载源/解压目录/海报/封面.jpg",
                "合集/甲辑/a.txt", "合集/乙辑/b.txt",
                "保留分区/正文/保留1.txt", "保留分区/正文/保留2.txt", "保留分区/附录/保留3.txt",
            )
            for item in files:
                touch(root / Path(item), item)
            (root / "电影" / "下载源" / "空缓存").mkdir(parents=True, exist_ok=True)

            with mock.patch.object(planner, "scan_residual_dirs", self.legacy_scan):
                legacy_plan = build_plan(str(root), "single")
            current_plan = build_plan(str(root), "single")

            self.assertEqual(self.normalized(current_plan), self.normalized(legacy_plan),
                             "优化改变了 mappings / operations / issues 的任一字段")

            rmdirs = {
                Path(op.source_path).resolve()
                for op in current_plan.operations if op.op_type == "rmdir"
            }
            # 无文件落入的目录必须删除；R2 在资源根处把整条壳链提到「电影」下，原中间路径全变空。
            for relative in ("电影/下载源/空缓存", "电影/下载源", "电影/下载源/解压目录",
                             "电影/下载源/解压目录/第一季", "电影/下载源/解压目录/海报",
                             "合集/甲辑", "合集/乙辑"):
                self.assertIn((root / Path(relative)).resolve(), rmdirs, f"{relative} 应进入 rmdir")
            # 仍有文件落入的目录（含 R3 父级保护、R2 提升后的落点）不得删除。
            for relative in ("保留分区", "保留分区/正文", "保留分区/附录",
                             "电影", "电影/第一季", "电影/海报"):
                self.assertNotIn((root / Path(relative)).resolve(), rmdirs, f"{relative} 不应进入 rmdir")
            self.assertNotIn(root.resolve(), rmdirs, "资源根本身不得进入 rmdir")

    def test_residual_scan_scale_comparison(self) -> None:
        """同规模对照两种实现的耗时；只打印数字，不做机器相关的阈值断言。
        默认规模保持单测速度；NESTTIDY_BENCH_FULL=1 可跑 2000 目录 x 5000 文件完整量级。
        """
        full = bool(os.environ.get("NESTTIDY_BENCH_FULL"))
        dir_count, file_count = (2000, 5000) if full else (200, 600)
        base = Path("D:/基准/资源库")  # 纯路径运算，不访问文件系统
        scanned = [base / f"分支{i // 20:03d}" / f"层级{i % 20:02d}" for i in range(dir_count)]
        targets = [
            base / f"分支{i // 200:03d}" / f"层级{i % 20:02d}" / f"第{i:05d}集.mkv"
            for i in range(file_count)
        ]

        legacy_removed: set[Path] = set()
        started = time.perf_counter()
        self.legacy_scan(scanned, targets, base, legacy_removed)
        legacy_elapsed = time.perf_counter() - started

        current_removed: set[Path] = set()
        started = time.perf_counter()
        scan_residual_dirs(scanned, targets, base, current_removed)
        current_elapsed = time.perf_counter() - started

        self.assertEqual(current_removed, legacy_removed, "优化在规模场景下改变了删除目录集合")
        print(f"\n[耗时对照 {dir_count} 目录 x {file_count} 文件] "
              f"原实现 {legacy_elapsed:.3f}s / 优化后 {current_elapsed:.3f}s "
              f"/ 加速 {legacy_elapsed / max(current_elapsed, 1e-9):.1f}x")


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
