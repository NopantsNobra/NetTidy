from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nesttidy.planner import build_plan
from nesttidy.rules import VDir, VFile, clean_name, resolve_names


def touch(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def rel_targets(plan, root: Path) -> list[str]:
    return sorted(str(Path(m.planned_target_path).resolve().relative_to(root.resolve())) for m in plan.mappings)


class RuleTests(unittest.TestCase):
    def test_batch_boundary_keeps_resource_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            touch(root / "说明.txt")
            touch(root / "电影A" / "正片" / "movie.mkv")
            plan = build_plan(str(root), "batch")
            self.assertEqual(rel_targets(plan, root), [str(Path("电影A") / "movie.mkv")])
            self.assertTrue(any(i.code == "IGNORED_ROOT_FILE" for i in plan.issues))

    def test_single_resource_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "电影A"
            touch(root / "正片" / "movie.mkv")
            plan = build_plan(str(root), "single")
            self.assertEqual(rel_targets(plan, root), ["movie.mkv"])

    def test_r1_root_chain_penetration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "课程A"
            touch(root / "百度网盘下载" / "解压后" / "正文" / "01.mp4")
            touch(root / "百度网盘下载" / "解压后" / "正文" / "02.mp4")
            plan = build_plan(str(root), "single")
            self.assertEqual(rel_targets(plan, root), ["01.mp4", "02.mp4"])
            self.assertTrue(all("R1" in " ".join(m.rule_trace) for m in plan.mappings))

    def test_r2_shell_absorb_keeps_outer_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "资源A"
            touch(root / "外层" / "内层" / "file.txt")
            touch(root / "side.txt")
            plan = build_plan(str(root), "single")
            self.assertIn("file.txt", rel_targets(plan, root))
            self.assertTrue(any("R2" in " ".join(m.rule_trace) for m in plan.mappings))

    def test_r3_lift_all_single_file_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "资源A"
            touch(root / "合集" / "A" / "a.txt")
            touch(root / "合集" / "B" / "b.txt")
            touch(root / "marker.txt")
            plan = build_plan(str(root), "single")
            self.assertIn(str(Path("合集") / "a.txt"), rel_targets(plan, root))
            self.assertIn(str(Path("合集") / "b.txt"), rel_targets(plan, root))

    def test_r3_parent_protection_allows_child_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "资源A"
            touch(root / "合集" / "A" / "外壳" / "正文" / "file.txt")
            touch(root / "合集" / "B" / "b1.txt")
            touch(root / "合集" / "B" / "b2.txt")
            touch(root / "marker.txt")
            plan = build_plan(str(root), "single")
            targets = rel_targets(plan, root)
            self.assertIn(str(Path("合集") / "A" / "file.txt"), targets)
            self.assertIn(str(Path("合集") / "B" / "b1.txt"), targets)

    def test_r5_conflict_prefixes_from_original_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "资源A"
            touch(root / "合集" / "A" / "cover.jpg")
            touch(root / "合集" / "B" / "cover.jpg")
            touch(root / "marker.txt")
            plan = build_plan(str(root), "single")
            targets = rel_targets(plan, root)
            self.assertIn(str(Path("合集") / "A-cover.jpg"), targets)
            self.assertIn(str(Path("合集") / "B-cover.jpg"), targets)

    def test_name_cleaning_then_conflict(self) -> None:
        """清洗后撞名：用虚拟树验证，不落盘。

        Windows 上无法创建 CON.txt（保留设备名），落盘夹具会静默失效。
        这里把 clean_name 与 resolve_names 拆开，各自可验证。
        """
        cleaned, traces = clean_name("CON.txt")
        self.assertEqual(cleaned, "_CON.txt")
        self.assertIn("Windows 保留名添加前缀", traces)

        node = VDir(name="资源A", original_path=Path("资源A"))
        node.files = [
            VFile(name=cleaned, original_path=Path("资源A") / "A" / "CON.txt", size=1, mtime_ns=0, name_cleaning_trace=traces),
            VFile(name="_CON.txt", original_path=Path("资源A") / "B" / "_CON.txt", size=1, mtime_ns=0),
        ]
        resolve_names(node)
        names = [f.name for f in node.files]
        self.assertEqual(len(names), len({n.casefold() for n in names}))
        self.assertEqual(sorted(names), ["A-_CON.txt", "B-_CON.txt"])
        self.assertTrue(all(f.conflict_trace for f in node.files))

    def test_r0_removes_nested_empty_dirs_and_keeps_resource_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "资源A"
            touch(root / "正片" / "movie.mkv")
            (root / "空壳" / "更空").mkdir(parents=True)
            plan = build_plan(str(root), "single")
            self.assertTrue(any(op.op_type == "rmdir" for op in plan.operations))
            self.assertTrue(root.exists())
            removed = {Path(op.source_path).name for op in plan.operations if op.op_type == "rmdir"}
            self.assertEqual(removed, {"空壳", "更空"})

    def test_r4_lifts_single_file_dir_within_resource_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "资源A"
            touch(root / "marker.txt")
            touch(root / "正片" / "movie.mkv")
            plan = build_plan(str(root), "single")
            self.assertIn("movie.mkv", rel_targets(plan, root))
            self.assertTrue(any("R4" in " ".join(m.rule_trace) for m in plan.mappings))

    def test_case_insensitive_names_collide_on_windows(self) -> None:
        """不同来源的大小写变体提级后必须被视为撞名。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "资源A"
            touch(root / "合集" / "A" / "Cover.JPG")
            touch(root / "合集" / "B" / "cover.jpg")
            touch(root / "marker.txt")
            plan = build_plan(str(root), "single")
            names = [Path(m.planned_target_path).name for m in plan.mappings]
            self.assertEqual(len(names), len({n.casefold() for n in names}), f"大小写撞名未处理: {names}")
            self.assertIn("A-Cover.JPG", names)
            self.assertIn("B-cover.jpg", names)


if __name__ == "__main__":
    unittest.main()
