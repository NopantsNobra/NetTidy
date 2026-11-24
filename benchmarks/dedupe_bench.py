"""Opt-in same-fixture duplicate hashing benchmark, isolated small files only."""
import argparse
import json
from pathlib import Path
import statistics
import tempfile
import time
from unittest.mock import patch

from nesttidy import duplicate


def measure(groups: int, repeat: int, workers: int, parent=None):
    with tempfile.TemporaryDirectory(prefix="nesttidy-dedupe-bench-", dir=parent) as tmp:
        root = Path(tmp)
        for n in range(1, groups + 1):
            for name in ("a", "b"):
                (root / f"{n:06}-{name}.bin").write_bytes(b"x" * n)
        actual_hash_map = duplicate._hash_map
        def separate(paths, level, workers, pool=None):
            return actual_hash_map(paths, level, workers)
        times = {"separate": [], "shared": []}
        for i in range(repeat):
            results = {}
            for variant in (("separate", "shared") if i % 2 == 0 else ("shared", "separate")):
                start = time.perf_counter()
                if variant == "separate":
                    with patch.object(duplicate, "_hash_map", separate):
                        rows = duplicate.detect_duplicates(tmp, "standard", workers=workers)
                else:
                    rows = duplicate.detect_duplicates(tmp, "standard", workers=workers)
                times[variant].append(time.perf_counter() - start)
                results[variant] = [row.to_dict() for row in rows]
            if results["separate"] != results["shared"]:
                raise RuntimeError("去重结果有差异")
            if len(results["shared"]) != 2 * groups:
                raise RuntimeError("重复组计数错误")
        return {"groups": groups, "files": groups * 2, "workers": workers, "repeat": repeat,
                "separate_pool_seconds": times["separate"], "shared_pool_seconds": times["shared"],
                "separate_median": statistics.median(times["separate"]),
                "shared_median": statistics.median(times["shared"]),
                "equivalent": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--groups", type=int, default=500)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if min(args.groups, args.repeat, args.workers) < 1:
        parser.error("groups, repeat and workers must be positive")
    print(json.dumps(measure(args.groups, args.repeat, args.workers), indent=2))
