import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from nesttidy import planner
from nesttidy.rules import build_tree


class DirEntryMetadataTests(unittest.TestCase):
    def test_regular_and_excluded_entries_match_path_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("a.txt", "unfinished.!qb", "z", planner.REPORT_DIR_NAME):
                p = root / name
                if name in ("z", planner.REPORT_DIR_NAME):
                    p.mkdir()
                else:
                    p.write_bytes(b"x")
            with os.scandir(root) as entries:
                for entry in entries:
                    p = Path(entry.path)
                    self.assertEqual(planner.should_skip_entry(entry, p), planner.should_skip_path(p))
            old, old_issues = build_tree(root, planner.should_skip_path)
            new, new_issues = build_tree(root, planner.should_skip_path, should_skip_entry=planner.should_skip_entry)
            self.assertEqual(old, new)
            self.assertEqual(old_issues, new_issues)

    @unittest.skipUnless(os.name == "nt", "Windows metadata flags")
    def test_special_metadata_and_error_fall_back_to_original(self):
        entry = Mock()
        entry.is_symlink.return_value = False
        entry.stat.return_value.st_file_attributes = 0x400
        p = Path("junction")
        with patch.object(planner, "should_skip_path", return_value="SKIPPED_REPARSE_POINT") as old:
            self.assertEqual(planner.should_skip_entry(entry, p), "SKIPPED_REPARSE_POINT")
            old.assert_called_once_with(p)
        entry.stat.side_effect = OSError("read failed")
        with patch.object(planner, "should_skip_path", return_value="ACCESS_ERROR:read failed") as old:
            self.assertEqual(planner.should_skip_entry(entry, p), "ACCESS_ERROR:read failed")
            old.assert_called_once_with(p)

    @unittest.skipUnless(os.name == "nt", "Windows metadata flags")
    def test_system_and_download_priority(self):
        entry = Mock()
        entry.is_symlink.return_value = False
        entry.stat.return_value.st_file_attributes = 0x4
        self.assertEqual(planner.should_skip_entry(entry, Path("system.txt")), "SKIPPED_SYSTEM_FILE")
        self.assertEqual(planner.should_skip_entry(entry, Path("partial.!qb")), "DOWNLOAD_IN_PROGRESS")
        entry.is_symlink.return_value = True
        self.assertEqual(planner.should_skip_entry(entry, Path("partial.!qb")), "SKIPPED_SYMLINK")
