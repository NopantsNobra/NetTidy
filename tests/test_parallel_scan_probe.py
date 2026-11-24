import tempfile
import unittest
from pathlib import Path
from benchmarks.parallel_scan_probe import ordered_scans, digest_scans, measure


class ParallelScanProbeTests(unittest.TestCase):
    def test_ordered_scan_with_exclusions(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots = [Path(tmp).resolve() / name for name in ("z", "a", "middle")]
            for root in roots:
                root.mkdir()
                (root / "regular.txt").write_bytes(b"x")
                (root / "unfinished.!qb").write_bytes(b"x")
            serial = list(ordered_scans(roots, 1))
            self.assertEqual(serial, list(ordered_scans(roots, 2)))
            self.assertEqual(serial, list(ordered_scans(roots, 4)))
            self.assertEqual([tree.original_path for tree, _ in serial], roots)

    def test_small_probe(self):
        self.assertTrue(measure(8, 2, [1, 2])["equivalent"])
