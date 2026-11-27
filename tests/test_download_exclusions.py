from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from nesttidy.duplicate import collect_files
from nesttidy.executor import execute_plan
from nesttidy.planner import build_plan, should_skip_entry, should_skip_path


class DownloadExclusionTests(unittest.TestCase):
    def test_download_suffixes_are_excluded_everywhere(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            root = base / "pkg"
            root.mkdir()
            names = ["video.xltd", "video.TD", "video.td.CFG", "video.bc!", "video.!UT",
                     "video.part", "video.!qb", ".part"]
            for name in names + ["video.td.cfg.txt", "ready.mp4"]:
                (root / name).write_bytes(b"x")
            with os.scandir(root) as entries:
                for entry in entries:
                    expected = "DOWNLOAD_IN_PROGRESS" if entry.name in names else None
                    self.assertEqual(should_skip_path(Path(entry.path)), expected, entry.name)
                    self.assertEqual(should_skip_entry(entry, Path(entry.path)), expected, entry.name)
            for output in (None, str(base / "out")):
                plan = build_plan(str(root), "single", output)
                self.assertEqual({Path(m.original_path).name for m in plan.mappings},
                                 {"video.td.cfg.txt", "ready.mp4"})
                self.assertEqual({Path(i.path).name for i in plan.issues
                                  if i.code == "DOWNLOAD_IN_PROGRESS"}, set(names))
            self.assertEqual({p.name for p in collect_files(root)}, {"video.td.cfg.txt", "ready.mp4"})

    def test_move_leaves_download_files_in_place(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            root = base / "pkg"
            shell = root / "shell"
            shell.mkdir(parents=True)
            (shell / "pending.mp4.xltd").write_bytes(b"partial")
            (shell / "ready.mp4").write_bytes(b"ready")
            plan = build_plan(str(root), "single")
            execute_plan(plan, "move", confirm_move=True, transaction_log=str(base / "tx.jsonl"))
            self.assertEqual((shell / "pending.mp4.xltd").read_bytes(), b"partial")
            self.assertEqual((root / "ready.mp4").read_bytes(), b"ready")
