import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from benchmarks.keep_precheck_bench import legacy_precheck, measure
from nesttidy.executor import precheck
from nesttidy.model import Operation


class KeepPrecheckTests(unittest.TestCase):
    def test_exact_keep_preserves_source_validation_without_resolve(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp).resolve() / "keep.txt"
            source.write_bytes(b"keep")
            info = source.stat()
            op = Operation("op", "root", "keep", str(source), str(source), [],
                           precheck={"size": info.st_size, "mtime_ns": info.st_mtime_ns})
            legacy_precheck(op)
            with patch.object(Path, "resolve", side_effect=AssertionError("redundant resolve")):
                precheck(op)
            source.write_bytes(b"changed size")
            for fn in (legacy_precheck, precheck):
                with self.assertRaisesRegex(RuntimeError, "大小"):
                    fn(op)
            source.write_bytes(b"keep")
            os.utime(source, ns=(info.st_atime_ns, info.st_mtime_ns + 2_000_000_000))
            for fn in (legacy_precheck, precheck):
                with self.assertRaisesRegex(RuntimeError, "修改时间"):
                    fn(op)
            source.unlink()
            for fn in (legacy_precheck, precheck):
                with self.assertRaises(FileNotFoundError):
                    fn(op)

    def test_different_path_keep_and_write_operations_retain_conflict_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            source, target = base / "a", base / "b"
            source.write_bytes(b"a")
            target.write_bytes(b"b")
            for mode in ("keep", "move", "copy"):
                op = Operation("op", "root", mode, str(source), str(target), [])
                with self.subTest(mode=mode):
                    with self.assertRaises(FileExistsError):
                        precheck(op)

    def test_small_benchmark_smoke(self):
        self.assertTrue(measure(5, 1)["all_checks_passed"])
