from __future__ import annotations

import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path

from nesttidy.planner import build_plan
from nesttidy.storage import list_history, record_execution, record_plan


class HistoryMigrationTests(unittest.TestCase):
    def test_legacy_db_migrates_and_preserves_old_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            db = base / "history.sqlite"
            conn = sqlite3.connect(db)
            conn.execute("create table task_history(plan_id text primary key, created_at text not null, mode text not null, selected_root text not null, output_root text, operation_count integer not null, issue_count integer not null, estimated_space integer not null, report_dir text, plan_json text not null)")
            conn.execute("insert into task_history values(?,?,?,?,?,?,?,?,?,?)", ("old","2025","single","oldroot",None,1,0,1,"oldreport",'{"legacy":true}'))
            conn.commit()
            conn.close()
            root = base / "pkg"
            root.mkdir()
            (root / "a.txt").write_text("a")
            plan = build_plan(str(root), "single")
            log = base / "transaction_log.jsonl"
            record_execution(plan, "move", "started", str(log), db)
            log.write_text("{}")
            record_plan(plan, "report", db)
            entries = {r["plan_id"]:r for r in list_history(db_path=db)}
            self.assertTrue(entries[plan.plan_id]["undo_available"])
            self.assertEqual(entries[plan.plan_id]["execution_status"], "started")
            self.assertIsNone(entries["old"]["execution_status"])
            with closing(sqlite3.connect(db)) as conn:
                data = dict(conn.execute("select plan_id, plan_json from task_history"))
            self.assertEqual(data[plan.plan_id], "{}")
            self.assertIn("legacy", data["old"])
