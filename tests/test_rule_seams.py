"""M1 · 规则接缝用例（对应 docs/规则对照表.md 第三节缺口 1–5）

接缝 = 两条规则同时作用于同一棵树时的先后与边界。
历史复盘结论：这个项目真正出错的地方从来不是单条规则，而是规则交接处。

本文件只覆盖「规则输出的结构是否正确」，不含执行阶段（executor）行为。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nesttidy.planner import build_plan
from nesttidy.rules import VDir, VFile, stable_key


def touch(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def rel_targets(plan, root: Path) -> list[str]:
    base = root.resolve()
    return sorted(str(Path(m.planned_target_path).resolve().relative_to(base)) for m in plan.mappings)


def rel_dirs(plan, root: Path) -> list[str]:
    base = root.resolve()
    return sorted(str(Path(op.source_path).resolve().relative_to(base)) for op in plan.operations if op.op_type == "rmdir")


class P0R1SeamTests(unittest.TestCase):
    """缺口 1–2：P0 边界 × R1 直链穿透 同时生效时的行为。"""

    def test_root_file_and_single_shell_chain_coexist(self) -> None:
        """运行根散落文件 + 子目录单壳链：R1 只在资源根内穿透，运行根文件原地不动。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            touch(root / "说明.txt")
            touch(root / "电影A" / "百度网盘" / "解压后" / "正文" / "01.mp4")
            plan = build_plan(str(root), "batch")

            # 穿透结果：内容落到资源根下，中间三层壳名全部消失
            self.assertEqual(rel_targets(plan, root), [str(Path("电影A") / "01.mp4")])
            # 边界：资源根只有一个，且就是 电影A
            self.assertEqual(plan.resource_roots, [str((root / "电影A").resolve())])
            # 运行根散落文件必须被显式忽略，而不是被当成资源提到某处
            self.assertTrue(any(i.code == "IGNORED_ROOT_FILE" for i in plan.issues))
            self.assertTrue((root / "说明.txt").exists(), "规划阶段不得改动磁盘")
            self.assertFalse(
                any("说明.txt" in m.planned_target_path for m in plan.mappings),
                "运行根散落文件不得进入映射表",
            )

    def test_parallel_shells_inside_resource_root(self) -> None:
        """资源根内多壳并联：R1 不触发（不是单链），由 R2 各自吸收后再 R3 全提。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            touch(root / "电影A" / "壳1" / "内层1" / "a.txt")
            touch(root / "电影A" / "壳2" / "内层2" / "b.txt")
            plan = build_plan(str(root), "batch")

            self.assertEqual(
                rel_targets(plan, root),
                sorted([str(Path("电影A") / "a.txt"), str(Path("电影A") / "b.txt")]),
            )
            traces = " ".join(" ".join(m.rule_trace) for m in plan.mappings)
            self.assertIn("R2", traces, "多壳并联应由 R2 吸收")
            self.assertIn("R3", traces, "两个单文件壳应被 R3 全提")
            self.assertNotIn("R1", traces, "两壳并联不是单链，R1 不得触发")

    def test_selected_dir_is_itself_a_shell(self) -> None:
        """单资源模式下所选目录本身是壳（A/A/file.txt）：只穿透一层，资源根名保留。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "A"
            touch(root / "A" / "file.txt")
            plan = build_plan(str(root), "single")

            self.assertEqual(rel_targets(plan, root), ["file.txt"])
            self.assertEqual(plan.resource_roots, [str(root.resolve())])
            self.assertTrue(all("R1" in " ".join(m.rule_trace) for m in plan.mappings))
            # 资源根自身名不得被替换（不变量 3）
            self.assertEqual(Path(plan.resource_roots[0]).name, "A")


class ShellChainTests(unittest.TestCase):
    """缺口 3：R2 多层壳链折叠后保留最外层名字。"""

    def test_r2_multi_layer_chain_keeps_outer_name(self) -> None:
        """A/B/C/file.txt：B、C 折叠掉，只保留 A，且 A 的名字不得被改成 C 或 file 所在层。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "资源A"
            touch(root / "marker.txt")
            touch(root / "合集" / "A" / "B" / "C" / "file.txt")
            touch(root / "合集" / "其他" / "x.txt")
            touch(root / "合集" / "其他" / "y.txt")
            plan = build_plan(str(root), "single")
            targets = rel_targets(plan, root)

            self.assertEqual(
                targets,
                sorted([
                    "marker.txt",
                    str(Path("合集") / "A" / "file.txt"),
                    str(Path("合集") / "其他" / "x.txt"),
                    str(Path("合集") / "其他" / "y.txt"),
                ]),
            )
            segments = {part for target in targets for part in Path(target).parts}
            self.assertNotIn("B", segments, "中间壳 B 应被折叠")
            self.assertNotIn("C", segments, "中间壳 C 应被折叠")


class MultiRoundConflictTests(unittest.TestCase):
    """缺口 4：R5 需要多轮才能解决的撞名。"""

    @unittest.expectedFailure
    def test_r5_multi_round_conflict_uses_ancestor_chain(self) -> None:
        """Group1/A/cover.jpg + Group2/A/cover.jpg：两轮冲突后应为 Group1-A-cover.jpg。

        当前实现输出 Group1-A-A-cover.jpg —— 第二轮把「A-」又贴到了已带前缀的名字前面。
        规则表述是「按 original_path 由近及远加祖先名前缀」，第二轮的前缀应**替换**
        第一轮前缀（祖先链 Group1→A），而不是在 current_name 上叠加。
        判定：实现与规则表述不符，需裁决（升 RULE_VERSION）后再改，故此处标记为预期失败。
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "资源A"
            touch(root / "marker.txt")
            touch(root / "合集" / "Group1" / "A" / "cover.jpg")
            touch(root / "合集" / "Group2" / "A" / "cover.jpg")
            plan = build_plan(str(root), "single")
            targets = rel_targets(plan, root)

            self.assertEqual(
                targets,
                sorted([
                    "marker.txt",
                    str(Path("合集") / "Group1-A-cover.jpg"),
                    str(Path("合集") / "Group2-A-cover.jpg"),
                ]),
            )


class StableKeyTests(unittest.TestCase):
    """缺口 5：stable_key 的跨枚举顺序一致性 —— 决定计划是否可复现。"""

    def test_stable_key_ignores_insertion_order(self) -> None:
        names = ["b.txt", "A.txt", "C.txt", "a.txt"]

        def build(order: list[str]) -> tuple[list[str], list[str]]:
            node = VDir(name="资源A", original_path=Path("资源A"))
            for name in order:
                node.files.append(VFile(name=name, original_path=Path("资源A") / name, size=1, mtime_ns=0))
            for name in order:
                node.dirs.append(VDir(name=name, original_path=Path("资源A") / name))
            node.sort()
            return [f.name for f in node.files], [d.name for d in node.dirs]

        baseline = build(names)
        for order in (list(reversed(names)), sorted(names), names[2:] + names[:2]):
            self.assertEqual(build(order), baseline, f"枚举顺序 {order} 改变了结果")

        # 排序由 casefold 主导；大小写变体靠原文做稳定 tie-break，不依赖插入顺序
        self.assertEqual(baseline[0], ["A.txt", "a.txt", "b.txt", "C.txt"])

    def test_plan_is_reproducible_across_builds(self) -> None:
        """同一棵树重复规划，映射序列（含顺序）必须逐条一致，而不是仅集合相等。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "资源A"
            touch(root / "marker.txt")
            touch(root / "合集" / "A" / "cover.jpg")
            touch(root / "合集" / "b" / "cover.jpg")
            touch(root / "其他" / "z.txt")
            first = [str(Path(m.planned_target_path)) for m in build_plan(str(root), "single").mappings]
            for _ in range(2):
                again = [str(Path(m.planned_target_path)) for m in build_plan(str(root), "single").mappings]
                self.assertEqual(first, again, "同一输入两次规划结果不一致")

    def test_stable_key_orders_dirs_before_files_on_tie(self) -> None:
        path = Path("资源A") / "同名"
        self.assertLess(stable_key(path, "dir"), stable_key(path, "file"))


if __name__ == "__main__":
    unittest.main()
