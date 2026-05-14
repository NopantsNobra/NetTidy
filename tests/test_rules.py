from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nesttidy.planner import build_plan


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
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "资源A"
            touch(root / "A" / "CON.txt")
            touch(root / "B" / "_CON.txt")
            touch(root / "marker.txt")
            plan = build_plan(str(root), "single")
            names = [Path(m.planned_target_path).name for m in plan.mappings]
            self.assertEqual(len(names), len(set(n.casefold() for n in names)))
            self.assertTrue(any(m.name_cleaning_trace for m in plan.mappings))


if __name__ == "__main__":
    unittest.main()
