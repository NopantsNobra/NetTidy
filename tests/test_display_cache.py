import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmarks.summary_bench import legacy_root_for, legacy_display_stats
from nesttidy import reports
from nesttidy.planner import build_plan


class DisplayCacheTests(unittest.TestCase):
    def test_longest_root_and_empty_root_equivalence(self):
        base = Path.cwd() / "fixture"
        roots = [str(base / "pkg"), str(base / "pkg" / "deep")]
        lookup = reports._root_lookup(roots)
        old, new = {}, {}
        paths = [base / "pkg" / "deep" / str(i) for i in range(200)]
        paths += [base / "pkg", base / "pkg" / "deep", base / "pkg" / "other" / "x",
                  base / "outside" / "x", base / "pkg" / "deep" / ".." / "a"]
        for p in paths + list(reversed(paths)):
            self.assertEqual(reports._root_for(str(p), roots, lookup, new),
                             legacy_root_for(str(p), roots, lookup, old))
        self.assertLess(len(new), 20)
        self.assertGreater(len(old), 200)

    def test_full_and_summary_display_match_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            root = base / "input"
            for name in ("a/keep.txt", "a/shell/x.txt", "b/deep/other.txt"):
                p = root / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"x")
            (root / "b" / "empty").mkdir()
            for output in (None, str(base / "output")):
                plan = build_plan(str(root), "batch", output_root=output)
                for summary in (False, True):
                    with patch.object(reports, "_root_for", legacy_root_for):
                        expected = legacy_display_stats(plan, 80, 300, 1500, summary_only=summary)
                    self.assertEqual(reports.plan_display_stats(plan, 80, 300, 1500, summary_only=summary), expected)
