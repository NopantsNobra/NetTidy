from __future__ import annotations

import os
import json
import stat
import tempfile
import unittest
from pathlib import Path

from nesttidy.duplicate import detect_duplicates
from nesttidy.manual_pdf import write_manual_pdf
from nesttidy.planner import build_plan
from nesttidy.repair import repair_permissions
from nesttidy.recovery import undo_transaction
from nesttidy.reports import write_plan_report
from nesttidy.storage import list_history


def touch(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class MVP4Tests(unittest.TestCase):
    def test_duplicate_detection_reports_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            touch(root / "A" / "same.txt", "same")
            touch(root / "B" / "same.txt", "same")
            rows = detect_duplicates(str(root), "deep", workers=2)
            self.assertEqual(len(rows), 2)
            self.assertEqual({row.duplicate_group_id for row in rows}, {"dup-1"})
            self.assertTrue((root / "A" / "same.txt").exists())
            self.assertTrue((root / "B" / "same.txt").exists())

    def test_repair_clear_readonly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "readonly.txt"
            touch(path)
            path.chmod(stat.S_IREAD)
            rows = repair_permissions(str(path), clear_readonly=True)
            self.assertTrue(any(row.action == "clear_readonly" and row.status == "ok" for row in rows))
            path.write_text("updated", encoding="utf-8")
            self.assertEqual(path.read_text(encoding="utf-8"), "updated")

    def test_history_records_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "资源"
            report_dir = Path(tmp) / "reports"
            touch(root / "正片" / "movie.mkv")
            plan = build_plan(str(root), "single")
            write_plan_report(plan, report_dir)
            rows = list_history(db_path=Path.cwd() / "理巢报告" / "history.sqlite")
            self.assertTrue(any(row["plan_id"] == plan.plan_id for row in rows))

    def test_manual_pdf_is_created(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = write_manual_pdf(Path(tmp) / "manual.pdf")
            self.assertTrue(out.exists())
            self.assertGreater(out.stat().st_size, 500)
            self.assertTrue(out.read_bytes().startswith(b"%PDF-1.4"))

    def test_undo_transaction_moves_file_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source.txt"
            target = base / "nested" / "target.txt"
            touch(target, "moved")
            log = base / "transaction_log.jsonl"
            log.write_text(json.dumps({"action": "move", "source": str(source), "target": str(target), "status": "done"}, ensure_ascii=False) + "\n", encoding="utf-8")
            rows = undo_transaction(str(log))
            self.assertEqual(rows[0].status, "ok")
            self.assertTrue(source.exists())
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
