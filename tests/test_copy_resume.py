from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nesttidy import executor
from nesttidy.planner import build_plan


class CopyResumeTests(unittest.TestCase):
    def test_resume_copies_missing_and_skips_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            root, out = base / "pkg", base / "out"
            root.mkdir()
            for name in ("a.txt", "b.txt"):
                (root / name).write_bytes(name.encode())
            (out / "pkg").mkdir(parents=True)
            shutil.copy2(root / "a.txt", out / "pkg" / "a.txt")
            result = executor.execute_plan(build_plan(str(root), "single", str(out)), "copy")
            self.assertEqual((result.completed, result.skipped, result.failed), (1, 1, 0))
            self.assertEqual((out / "pkg" / "b.txt").read_bytes(), b"b.txt")

    def test_same_size_mtime_but_different_content_is_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            root, out = base / "pkg", base / "out"
            root.mkdir()
            source = root / "a.txt"
            source.write_bytes(b"good")
            (out / "pkg").mkdir(parents=True)
            target = out / "pkg" / "a.txt"
            shutil.copy2(source, target)
            info = target.stat()
            target.write_bytes(b"evil")
            os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns))
            result = executor.execute_plan(build_plan(str(root), "single", str(out)), "copy")
            self.assertEqual(result.failed, 1)
            self.assertEqual(result.issues[-1].code, "TARGET_CONFLICT")
            self.assertEqual(target.read_bytes(), b"evil")

    def test_late_conflict_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            root, out = base / "pkg", base / "out"
            root.mkdir()
            (root / "a.txt").write_bytes(b"source")
            target = out / "pkg" / "a.txt"
            original = shutil.copy2
            def race(source, dest):
                result = original(source, dest)
                if Path(dest) != target:
                    target.write_bytes(b"late")
                return result
            with patch.object(executor.shutil, "copy2", side_effect=race):
                result = executor.execute_plan(build_plan(str(root), "single", str(out)), "copy")
            self.assertEqual(result.failed, 1)
            self.assertEqual(target.read_bytes(), b"late")
            self.assertEqual(list(target.parent.iterdir()), [target])

    def test_failed_copy_does_not_publish_partial_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            root, out = base / "pkg", base / "out"
            root.mkdir()
            (root / "a.txt").write_bytes(b"source")
            target = out / "pkg" / "a.txt"
            def interrupted(source, dest):
                Path(dest).write_bytes(b"partial")
                raise OSError("injected interruption")
            with patch.object(executor.shutil, "copy2", side_effect=interrupted):
                result = executor.execute_plan(build_plan(str(root), "single", str(out)), "copy")
            self.assertEqual(result.failed, 1)
            self.assertFalse(target.exists())
            self.assertFalse(list(target.parent.iterdir()))
            result = executor.execute_plan(build_plan(str(root), "single", str(out)), "copy")
            self.assertEqual(result.completed, 1)
            self.assertEqual(target.read_bytes(), b"source")

    def test_existing_symlink_is_not_resume_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            root, out = base / "pkg", base / "out"
            root.mkdir()
            source = root / "a.txt"
            source.write_bytes(b"source")
            (out / "pkg").mkdir(parents=True)
            target = out / "pkg" / "a.txt"
            try:
                target.symlink_to(source)
            except (OSError, NotImplementedError):
                self.skipTest("symlink privilege unavailable")
            result = executor.execute_plan(build_plan(str(root), "single", str(out)), "copy")
            self.assertEqual(result.failed, 1)
            self.assertTrue(target.is_symlink())
            self.assertEqual(source.read_bytes(), b"source")
