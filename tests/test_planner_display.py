"""R0 合并标签行为锁：planner 给所有 rmdir 写死合并标签，plan_display_stats 的 R0
计数（= rmdir 条数）依赖该口径。锁标签原文与计数，防止展示层静默失真。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nesttidy.planner import build_plan
from nesttidy.reports import plan_display_stats

MERGED_TAG = "R0/R1/R2/R3/R4 产生的空目录清理"


def touch(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class RmdirTagLockTests(unittest.TestCase):
    """构造 R0/R1/R2 混合来源的空目录，锁合并标签原文与展示层计数。"""

    def test_all_rmdir_ops_carry_merged_rule_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # R0 来源：嵌套空目录（无任何文件）
            (root / "R0来源" / "空1" / "空2").mkdir(parents=True)
            # R1 来源：资源根内单链壳，根无文件 → 直链穿透
            touch(root / "R1来源" / "壳1" / "壳2" / "内层" / "a.txt")
            # R2 来源：根有散落文件，合集/A/B/C 为壳链 → R2 折叠 B、C
            touch(root / "R2来源" / "说明.txt")
            touch(root / "R2来源" / "合集" / "A" / "B" / "C" / "file.txt")

            plan = build_plan(str(root), "batch")
            rmdirs = [op for op in plan.operations if op.op_type == "rmdir"]

            self.assertGreaterEqual(len(rmdirs), 4, "夹具未产生足够 rmdir，用例会假绿")
            for op in rmdirs:
                self.assertEqual(
                    op.rule_trace,
                    [MERGED_TAG],
                    f"rmdir {op.source_path} 的 rule_trace 不是合并标签原文",
                )
            self.assertEqual(
                plan_display_stats(plan)["rule_counts"]["R0"],
                len(rmdirs),
                "展示层 R0 计数口径（= rmdir 条数）被破坏",
            )


if __name__ == "__main__":
    unittest.main()
