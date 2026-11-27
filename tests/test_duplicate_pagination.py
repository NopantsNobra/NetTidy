from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nesttidy import web
from nesttidy.executor import DeleteEntry


class DuplicatePaginationTests(unittest.TestCase):
    def test_all_rows_accessible_beyond_legacy_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            for i in range(405):
                (root / f"{i:03}.txt").write_bytes(b"same")
            data = web.Handler.detect_duplicates_payload(None, {"selected_root": str(root), "level": "deep", "view": "paged"})
            key = data["diagnosis_id"]
            try:
                self.assertEqual(data["row_total"], 405)
                self.assertNotIn("rows", data)
                view = web.DIAGNOSES.get(key)
                self.assertEqual(view.group_page(0, 100)["total"], 1)
                first = view.row_page(0, 0, 80)
                last = view.row_page(0, 400, 80)
                self.assertEqual((len(first["rows"]), len(last["rows"])), (80, 5))
                row = last["rows"][-1]
                self.assertTrue(row["deletable"])
                g = last["group"]
                entry = DeleteEntry(row["path"], g["keep_path"], g["verified_hash"])
                view.validate_entries(str(root), [entry])
                with self.assertRaises(ValueError):
                    view.validate_entries(str(root), [DeleteEntry(g["keep_path"], g["keep_path"], g["verified_hash"])])
                with self.assertRaises(ValueError):
                    view.validate_entries(str(root), [DeleteEntry(row["path"], g["keep_path"], "changed")])
            finally:
                web.DIAGNOSES.discard(key)
            with self.assertRaises(ValueError):
                web.DIAGNOSES.get(key)

    def test_budget_evicts_older_views(self):
        registry = web.DuplicateRegistry(max_rows=1, max_views=1)
        view = web.DuplicateView("root", [], [])
        first = registry.put(view)
        second = registry.put(view)
        with self.assertRaises(ValueError):
            registry.get(first)
        self.assertIs(registry.get(second), view)
