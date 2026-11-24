import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmarks.dedupe_bench import measure
from nesttidy import duplicate


class DuplicatePoolTests(unittest.TestCase):
    def test_shared_pool_matches_separate_pools(self):
        self.assertTrue(measure(6, 2, 2)["equivalent"])

    def test_diagnosis_constructs_one_pool_for_many_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for n in range(1, 7):
                for tag in ("a", "b"):
                    (root / f"{n}-{tag}").write_bytes(b"x" * n)
            with patch.object(duplicate, "ThreadPoolExecutor", wraps=duplicate.ThreadPoolExecutor) as factory:
                rows = duplicate.detect_duplicates(tmp, "standard", workers=2)
                self.assertEqual(factory.call_count, 1)
            self.assertEqual(len(rows), 12)
            self.assertEqual(len(duplicate.group_rows(rows)), 6)

    def test_hash_batches_keep_order_and_error_markers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = [root / str(i) for i in range(80)]
            for p in paths[:-1]:
                p.write_bytes(b"x")
            rows = duplicate._hash_map(paths, "deep", 2)
            self.assertEqual([p for p, _ in rows], paths)
            self.assertTrue(rows[-1][1].startswith("__error__"))
            self.assertTrue(all(not digest.startswith("__error__") for _, digest in rows[:-1]))
