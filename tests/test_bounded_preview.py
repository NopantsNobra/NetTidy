import tempfile
import unittest
from pathlib import Path
from nesttidy.planner import build_plan
from nesttidy.web import PlanRegistry, _plan_payload
from nesttidy.reports import readable_plan_view


class BoundedPreviewTests(unittest.TestCase):
    def test_summary_counts_and_server_filtered_pages_match_full_view(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "pkg"
            for name in ["keep.txt", "wrapper/a.txt", "wrapper/b.txt", "wrapper/c.txt"]:
                p = root / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"x")
            for output in [None, str(Path(tmp) / "out")]:
                plan = build_plan(str(root), mode="single", output_root=output)
                full = readable_plan_view(plan)
                summary = _plan_payload(plan, summary_only=True)["display"]["readable"]
                self.assertEqual(summary["counts"], full["counts"])
                self.assertTrue(all(not g["rows"] for g in summary["groups"]))
                registry = PlanRegistry()
                registry.put(plan)
                for group in full["groups"]:
                    for kind in ["all", "changed", "renamed", "same"]:
                        expected = [r for r in group["rows"] if kind == "all" or
                                    (kind == "changed" and r["changed"]) or
                                    (kind == "renamed" and r["renamed"]) or
                                    (kind == "same" and not r["changed"])]
                        page = registry.page(plan.plan_id, group["root_path"], 0, 2, kind)
                        self.assertEqual(page["total"], len(expected))
                        self.assertEqual(page["rows"], expected[:2])
                        self.assertEqual(registry.page(plan.plan_id, group["root_path"], 2, 2, kind)["rows"], expected[2:4])
