from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nesttidy.executor import execute_plan
from nesttidy.planner import build_plan


def touch(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class ExecutorTests(unittest.TestCase):
    def test_copy_does_not_modify_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "资源包"
            output = base / "理巢输出_test"
            original = source / "电影A" / "正片" / "movie.mkv"
            touch(original, "movie")
            plan = build_plan(str(source), "batch", str(output))
            result = execute_plan(plan, "copy")
            self.assertEqual(result.failed, 0)
            self.assertTrue(original.exists())
            self.assertEqual(original.read_text(encoding="utf-8"), "movie")
            self.assertTrue((output / "电影A" / "movie.mkv").exists())


if __name__ == "__main__":
    unittest.main()

