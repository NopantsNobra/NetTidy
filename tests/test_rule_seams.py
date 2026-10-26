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


class ShellRemovalTests(unittest.TestCase):
    """缺陷①：剥壳必须登记 rmdir，否则文件搬走了、空壳全留在磁盘上。

    R3 全提、R4 提级都登记了 rmdir，唯独 R1/R2 只改内存树。
    判定：遗漏，不是设计 —— 用户跑完发现空壳一个没少，等于核心功能失效。
    """

    def test_r1_penetration_registers_every_shell_in_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "课程A"
            touch(root / "百度网盘" / "解压后" / "正文" / "01.mp4")
            touch(root / "百度网盘" / "解压后" / "正文" / "02.mp4")
            plan = build_plan(str(root), "single")

            self.assertEqual(rel_targets(plan, root), ["01.mp4", "02.mp4"])
            self.assertEqual(
                rel_dirs(plan, root),
                sorted([str(Path("百度网盘")), str(Path("百度网盘") / "解压后"), str(Path("百度网盘") / "解压后" / "正文")]),
            )

    def test_r1_registers_descendants_of_penetrated_node(self) -> None:
        """穿透点下面还有子目录时，子目录的旧路径同样会变空，必须一并登记。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "剧集A"
            touch(root / "壳1" / "壳2" / "内层" / "S01" / "a.mkv")
            touch(root / "壳1" / "壳2" / "内层" / "S02" / "b.mkv")
            plan = build_plan(str(root), "single")

            self.assertEqual(rel_targets(plan, root), sorted([str(Path("S01") / "a.mkv"), str(Path("S02") / "b.mkv")]))
            self.assertEqual(
                rel_dirs(plan, root),
                sorted([
                    str(Path("壳1")),
                    str(Path("壳1") / "壳2"),
                    str(Path("壳1") / "壳2" / "内层"),
                    str(Path("壳1") / "壳2" / "内层" / "S01"),
                    str(Path("壳1") / "壳2" / "内层" / "S02"),
                ]),
            )

    def test_r2_absorb_registers_collapsed_shells(self) -> None:
        """R2 保留最外层壳名（合集/A），只折叠 A 以内的 B、C，且折叠掉的壳必须登记删除。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "资源A"
            touch(root / "marker.txt")
            touch(root / "合集" / "A" / "B" / "C" / "file.txt")
            touch(root / "合集" / "其他" / "x.txt")
            touch(root / "合集" / "其他" / "y.txt")
            plan = build_plan(str(root), "single")

            dirs = rel_dirs(plan, root)
            for inner in (str(Path("A") / "B"), str(Path("A") / "B" / "C")):
                self.assertIn(str(Path("合集") / inner), dirs, f"内层壳 {inner} 未被登记删除")
            self.assertNotIn(str(Path("合集") / "A"), dirs, "A 是最外层壳，保留其名字承载内容")
            self.assertNotIn(str(Path("合集")), dirs, "合集 保留内容，不得删除")
            self.assertNotIn(str(Path("合集") / "其他"), dirs, "其他 保留内容，不得删除")

    def test_rmdir_refuses_non_empty_dir_without_claiming_success(self) -> None:
        """do_rmdir 遇到非空目录必须返回未执行，不能被上层计入成功。"""
        from nesttidy.executor import do_rmdir
        from nesttidy.model import Operation

        def rmdir_op(path: Path) -> Operation:
            return Operation(
                op_id="t",
                resource_root_id="directory",
                op_type="rmdir",
                source_path=str(path),
                target_path=None,
                rule_trace=["测试"],
                risk_level="medium",
                precheck={},
            )

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "非空"
            touch(target / "still.txt")

            self.assertFalse(do_rmdir(rmdir_op(target), None), "非空目录不得报告删除成功")
            self.assertTrue(target.exists())
            self.assertTrue(do_rmdir(rmdir_op(Path(tmp) / "不存在"), None), "不存在视为已达目的")

    def test_move_execution_leaves_no_shell_dirs(self) -> None:
        """端到端：真实执行后磁盘上不得残留任何壳目录（R1 穿透链 + R2 折叠链各一）。"""
        from nesttidy.executor import execute_plan

        with tempfile.TemporaryDirectory() as tmp:
            base = (Path(tmp) / "树").resolve()
            touch(base / "电影A" / "百度网盘" / "解压后" / "正文" / "01.mp4")
            touch(base / "合集" / "A" / "B" / "C" / "file.txt")
            touch(base / "合集" / "其他" / "x.txt")
            touch(base / "合集" / "其他" / "y.txt")

            plan = build_plan(str(base), "batch")
            execute_plan(plan, "move", confirm_move=True, transaction_log=str(Path(tmp) / "tx.jsonl"))

            left_dirs = sorted(str(p.relative_to(base)) for p in base.rglob("*") if p.is_dir())
            self.assertEqual(
                left_dirs,
                sorted([str(Path("电影A")), str(Path("合集")), str(Path("合集") / "A"), str(Path("合集") / "其他")]),
                "壳目录 电影A/百度网盘/解压后/正文、合集/A/B、合集/A/B/C 均应消失",
            )
            left_files = sorted(str(p.relative_to(base)) for p in base.rglob("*") if p.is_file())
            self.assertEqual(
                left_files,
                sorted([
                    str(Path("电影A") / "01.mp4"),
                    str(Path("合集") / "A" / "file.txt"),
                    str(Path("合集") / "其他" / "x.txt"),
                    str(Path("合集") / "其他" / "y.txt"),
                ]),
            )


class MultiRoundConflictTests(unittest.TestCase):
    """缺口 4：R5 需要多轮才能解决的撞名。"""

    def test_r5_multi_round_conflict_uses_ancestor_chain(self) -> None:
        """Group1/A/cover.jpg + Group2/A/cover.jpg：两轮冲突后应为 Group1-A-cover.jpg。

        规则表述是「按 original_path 由近及远加祖先名前缀」，第二轮的前缀应**替换**
        第一轮前缀（祖先链 Group1→A），而不是在 current_name 上叠加成 Group1-A-A-cover.jpg。
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

    def test_r5_three_rounds_stay_bounded(self) -> None:
        """三轮以上冲突时前缀不得重复累积，长度必须与祖先深度线性相关。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "资源A"
            touch(root / "marker.txt")
            for group in ("G1", "G2"):
                touch(root / "合集" / group / "A" / "B" / "cover.jpg")
            plan = build_plan(str(root), "single")
            names = sorted(Path(m.planned_target_path).name for m in plan.mappings if m.planned_target_path.endswith(".jpg"))

            self.assertEqual(names, ["G1-A-B-cover.jpg", "G2-A-B-cover.jpg"])
            for name in names:
                self.assertNotIn("-A-A", name)
                self.assertNotIn("-B-B", name)

    def test_r5_cleaned_name_is_not_repeated_in_prefix(self) -> None:
        """清洗后的基础名不得参与前缀累积：前缀来自祖先链，基础名只出现一次。"""
        from nesttidy.rules import VFile, resolve_names

        node = VDir(name="合集", original_path=Path("资源A") / "合集")
        for group in ("G1", "G2"):
            node.files.append(
                VFile(
                    name="_con.txt",  # con.txt 被清洗为 _con.txt（Windows 保留设备名）
                    original_path=Path("资源A") / "合集" / group / "A" / "con.txt",
                    size=1,
                    mtime_ns=0,
                )
            )
        resolve_names(node)
        names = sorted(f.name for f in node.files)
        self.assertEqual(names, ["G1-A-_con.txt", "G2-A-_con.txt"])
        for name in names:
            self.assertEqual(name.count("_con"), 1, "基础名被重复拼进前缀")


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
