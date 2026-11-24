"""Small smoke only; large fixtures are opt-in."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from benchmarks.plan_bench import main


class BenchmarkSmokeTests(unittest.TestCase):
    def test_real_directory_remains_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = root / "pkg" / "wrapper" / "a.txt"
            p.parent.mkdir(parents=True)
            p.write_bytes(b"unchanged")
            before = [(str(x.relative_to(root)), x.stat().st_mtime_ns) for x in sorted(root.rglob("*"))]
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                main(["--root", tmp, "--repeat", "2", "--pages", "2", "--profile", "--memory"])
            data = json.loads(output.getvalue())
            self.assertEqual(data["source"], "real-read-only")
            self.assertTrue(data["semantic_stable"])
            self.assertTrue(data["snapshot_stable"])
            self.assertEqual(data["runs"][0]["mapping_count"], 1)
            self.assertIn("profile_run", data)
            self.assertIn("tracemalloc_bytes", data)
            self.assertEqual(before, [(str(x.relative_to(root)), x.stat().st_mtime_ns) for x in sorted(root.rglob("*"))])
            self.assertEqual(p.read_bytes(), b"unchanged")

    def test_synthetic_fixture_is_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                main(["--fixture-files", "12", "--fixture-parent", tmp, "--shape", "conflict", "--repeat", "1"])
            self.assertEqual(json.loads(output.getvalue())["runs"][0]["mapping_count"], 12)
            self.assertEqual(list(Path(tmp).iterdir()), [])
