"""Same-fixture read-only Windows scan comparison; no user directories."""
import argparse
import json
import statistics
import tempfile
import time
from pathlib import Path

from benchmarks.plan_bench import make_fixture
from nesttidy.planner import should_skip_path, should_skip_entry
from nesttidy.rules import build_tree


def measure(count, repeat):
    with tempfile.TemporaryDirectory(prefix="nesttidy-direntry-") as tmp:
        root = Path(tmp)
        make_fixture(root, count, "multi")
        times = {"path": [], "entry": []}
        for i in range(repeat):
            baseline = None
            for label in (("path", "entry") if i % 2 == 0 else ("entry", "path")):
                start = time.perf_counter()
                result = build_tree(root, should_skip_path, should_skip_entry=should_skip_entry if label == "entry" else None)
                times[label].append(time.perf_counter() - start)
                if baseline is None:
                    baseline = result
                elif baseline != result:
                    raise RuntimeError("scan result differs")
        return {"files": count, "repeat": repeat, "path_seconds": times["path"], "entry_seconds": times["entry"],
                "path_median": statistics.median(times["path"]), "entry_median": statistics.median(times["entry"]),
                "equivalent": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=int, default=20000)
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()
    if min(args.files, args.repeat) < 1:
        parser.error("positive count/repeat required")
    print(json.dumps(measure(args.files, args.repeat), indent=2))
