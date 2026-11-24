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

    def test_execution_fixture_round_trips_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                main(["--fixture-files", "6", "--fixture-parent", tmp, "--repeat", "1",
                      "--execute-fixture-files", "4"])
            data = json.loads(output.getvalue())
            self.assertTrue(data["execution_fixture"]["runs"][0]["restored"])
            self.assertGreater(data["execution_fixture"]["median_files_per_second"], 0)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_compact_records_preserve_serialization_and_reuse_path_strings(self):
        from nesttidy.planner import build_plan
        from nesttidy.reports import readable_plan_view
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            p = root / "pkg" / "a.txt"
            p.parent.mkdir()
            p.write_text("x", encoding="utf-8")
            plan = build_plan(str(root), mode="batch")
            mapping = plan.mappings[0]
            op = next(op for op in plan.operations if op.op_id == mapping.entry_id)
            self.assertIs(mapping.original_path, mapping.current_path)
            self.assertIs(mapping.original_path, op.source_path)
            self.assertIs(mapping.planned_target_path, op.target_path)
            self.assertFalse(hasattr(mapping, "__dict__"))
            self.assertFalse(hasattr(op, "__dict__"))
            mapping.status = "completed"
            self.assertEqual(mapping.to_dict()["status"], "completed")
            self.assertEqual(plan.to_dict()["mappings"][0], mapping.to_dict())
            self.assertEqual(readable_plan_view(plan)["counts"]["total"], 1)

    def test_resident_memory_probe_reports_cache_and_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                main(["--fixture-files", "6", "--fixture-parent", tmp, "--repeat", "1",
                      "--resident-rounds", "2"])
            data = json.loads(output.getvalue())["resident_memory"]
            self.assertEqual([r["retained_plans"] for r in data["rounds"]], [1, 2])
            self.assertGreater(data["rounds"][1]["traced_current"], data["traced_after_clear"])
            self.assertEqual(list(Path(tmp).iterdir()), [])
