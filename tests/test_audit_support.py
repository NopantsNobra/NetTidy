from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nesttidy.app import verified_saved_plan
from nesttidy.executor import execute_plan
from nesttidy.healthcheck import run_healthcheck
from nesttidy.manual_pdf import write_manual_pdf
from nesttidy.planner import build_plan


class AuditSupportTests(unittest.TestCase):
    def test_saved_plan_rechecks_current_intent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            source = root / "pack" / "sub" / "a.txt"
            source.parent.mkdir(parents=True)
            source.write_text("original", encoding="utf-8")
            saved = Path(tmp) / "summary.json"
            data = build_plan(str(root)).to_dict()
            saved.write_text(json.dumps(data), encoding="utf-8")
            fresh = verified_saved_plan(str(saved), str(root), "batch")
            self.assertTrue(fresh.frozen)
            data["operations"][0]["target_path"] = str(Path(tmp) / "escape.txt")
            saved.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ValueError):
                verified_saved_plan(str(saved), str(root), "batch")
            saved.write_text(json.dumps(fresh.to_dict()), encoding="utf-8")
            source.write_text("changed-size", encoding="utf-8")
            with self.assertRaises(ValueError):
                verified_saved_plan(str(saved), str(root), "batch")
            self.assertTrue(source.exists())

    def test_unexpected_exception_has_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            source = root / "pack" / "a.txt"
            source.parent.mkdir(parents=True)
            source.write_text("a", encoding="utf-8")
            plan = build_plan(str(root), output_root=str(Path(tmp) / "out"))
            log = Path(tmp) / "report" / "errors.log"
            with patch("nesttidy.executor.do_copy", side_effect=AttributeError("audit sentinel")):
                result = execute_plan(plan, "copy", error_log=str(log))
            self.assertEqual(result.failed, 1)
            self.assertIn("Traceback (most recent call last)", log.read_text(encoding="utf-8"))
            self.assertIn("AttributeError: audit sentinel", log.read_text(encoding="utf-8"))
            self.assertTrue(source.exists())

    def test_healthcheck_socket_failure_is_reported_and_closed(self):
        with patch("nesttidy.healthcheck.socket.socket") as factory:
            sock = factory.return_value.__enter__.return_value
            sock.bind.side_effect = OSError("bind failed")
            items = run_healthcheck()
            factory.return_value.__exit__.assert_called_once()
        self.assertTrue(any(not item.ok and "bind failed" in item.detail for item in items))
        self.assertTrue(any(item.name == "SQLite" and item.ok for item in items))

    def test_manual_pdf_has_valid_cross_reference_offsets(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = write_manual_pdf(Path(tmp) / "manual.pdf").read_bytes()
            self.assertTrue(data.startswith(b"%PDF-"))
            xref = int(data.split(b"startxref\n")[-1].splitlines()[0])
            rows = data[xref:].splitlines()
            self.assertEqual(rows[0], b"xref")
            count = int(rows[1].split()[1])
            for i, row in enumerate(rows[3:count+2], 1):
                self.assertTrue(data[int(row[:10]):].startswith(f"{i} 0 obj".encode()))


@unittest.skipUnless(os.name == "nt", "Windows-specific filesystem tests")
class WindowsFilesystemAuditTests(unittest.TestCase):
    def test_long_path_move_and_undo(self):
        from nesttidy.recovery import undo_transaction
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            source = root / "pack" / ("a" * 110) / ("b" * 110) / "file.txt"
            source.parent.mkdir(parents=True)
            source.write_text("long", encoding="utf-8")
            self.assertGreater(len(str(source)), 260)
            plan = build_plan(str(root))
            log = Path(tmp) / "transaction_log.jsonl"
            result = execute_plan(plan, "move", confirm_move=True, transaction_log=str(log))
            self.assertEqual(result.failed, 0)
            undo_transaction(str(log))
            self.assertEqual(source.read_text(encoding="utf-8"), "long")

    def test_ads_zone_identifier_repair(self):
        from nesttidy.repair import repair_permissions
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "file.txt"
            source.write_text("body", encoding="utf-8")
            ads = str(source) + ":Zone.Identifier"
            with open(ads, "w", encoding="utf-8") as f:
                f.write("[ZoneTransfer]\nZoneId=3\n")
            repair_permissions(tmp, clear_zone_identifier=True)
            with self.assertRaises(FileNotFoundError):
                open(ads, "r", encoding="utf-8")
            self.assertEqual(source.read_text(encoding="utf-8"), "body")
