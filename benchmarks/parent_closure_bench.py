"""Same-input CPU benchmark for residual-directory ancestor closure."""
import argparse
import json
from pathlib import Path
import statistics
import time

from nesttidy.planner import collect_parent_paths


def legacy(targets):
    result = set()
    for target in targets:
        result.update(target.parents)
    return result


def measure(count, repeat):
    root = Path.cwd() / "fixture"
    targets = [root / f"pkg-{i % 100}" / "a" / "b" / "c" / f"{i}.dat" for i in range(count)]
    times = {"legacy": [], "current": []}
    for i in range(repeat):
        results = {}
        for name, fn in ((("legacy", legacy), ("current", collect_parent_paths)) if i % 2 == 0
                         else (("current", collect_parent_paths), ("legacy", legacy))):
            start = time.perf_counter()
            results[name] = fn(targets)
            times[name].append(time.perf_counter() - start)
        if results["legacy"] != results["current"]:
            raise RuntimeError("ancestor closure differs")
    return {"files": count, "repeat": repeat, "legacy_seconds": times["legacy"],
            "current_seconds": times["current"], "legacy_median": statistics.median(times["legacy"]),
            "current_median": statistics.median(times["current"]), "equivalent": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=int, default=200000)
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()
    if min(args.files, args.repeat) < 1:
        parser.error("positive count/repeat required")
    print(json.dumps(measure(args.files, args.repeat), indent=2))
