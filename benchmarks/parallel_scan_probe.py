"""Bounded resource-root scan POC; no changes to production planner or write paths."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import statistics
import tempfile
import time

from benchmarks.plan_bench import make_fixture, working_set_bytes
from nesttidy.planner import resource_roots, should_skip_path, should_skip_entry
from nesttidy.rules import build_tree


def scan(root):
    return build_tree(root, should_skip_path, should_skip_entry=should_skip_entry)


def ordered_scans(roots, workers):
    if workers == 1:
        for root in roots:
            yield scan(root)
        return
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for start in range(0, len(roots), workers):
            # At most workers trees in flight. Consume results in original root order.
            yield from pool.map(scan, roots[start:start + workers])


def digest_scans(results):
    digest = hashlib.sha256()
    count = 0
    for tree, issues in results:
        # Dataclass repr includes all fields, path spellings, ordering, and issues.
        digest.update(repr((tree, issues)).encode("utf-8", "surrogatepass"))
        count += 1
    return count, digest.hexdigest()


def measure(count, repeat, workers, shape="multi"):
    with tempfile.TemporaryDirectory(prefix="nesttidy-parallel-") as tmp:
        root = Path(tmp).resolve()
        make_fixture(root, count, shape)
        roots, issues = resource_roots(root, "batch")
        timings = {str(n): [] for n in workers}
        memory = {str(n): [] for n in workers}
        baseline = None
        for i in range(repeat):
            for n in (workers if i % 2 == 0 else list(reversed(workers))):
                start = time.perf_counter()
                result = digest_scans(ordered_scans(roots, n))
                timings[str(n)].append(time.perf_counter() - start)
                memory[str(n)].append(working_set_bytes())
                if baseline is None:
                    baseline = result
                elif result != baseline:
                    raise RuntimeError("scan tree/issue/order differs")
        return {"files": count, "shape": shape, "roots": len(roots), "repeat": repeat,
                "seconds": timings, "medians": {k: statistics.median(v) for k,v in timings.items()},
                "working_set": memory, "equivalent": True, "production_change": False,
                "note": "includes deterministic tree digest; working set sampled after scan, cumulative peak"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=int, default=20000)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--workers", type=int, nargs="+", default=[1, 2, 4, 8])
    parser.add_argument("--shape", choices=("multi", "deep", "move"), default="multi")
    args = parser.parse_args()
    if min(args.files, args.repeat, *args.workers) < 1 or 1 not in args.workers:
        parser.error("positive values and serial baseline required")
    print(json.dumps(measure(args.files, args.repeat, args.workers, args.shape), indent=2))
