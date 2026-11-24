"""Opt-in plan benchmark. Real paths are read-only; generated fixtures are isolated."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack
import cProfile
import ctypes
import hashlib
import json
import os
from pathlib import Path
import platform
import pstats
import statistics
import sys
import tempfile
import time
import tracemalloc
from unittest.mock import patch

from nesttidy import planner, executor
from nesttidy.recovery import undo_transaction
from nesttidy.reports import readable_group_rows
from nesttidy.web import PlanRegistry, _plan_payload
from nesttidy import web


def working_set_bytes():
    if os.name != "nt":
        return None
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                "PagefileUsage", "PeakPagefileUsage")]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return None
    return {"current": counters.WorkingSetSize, "process_peak": counters.PeakWorkingSetSize}


def make_fixture(root: Path, count: int, shape: str) -> None:
    for i in range(count):
        package = root / ("pkg-%03d" % (i % 100) if shape == "multi" else "pkg")
        if shape == "deep":
            parent = package / ("branch-%03d" % (i % 100)) / "a" / "b" / "c"
        elif shape == "conflict":
            parent = package / ("branch-%03d" % (i % 100))
        elif shape == "move":
            parent = package / "wrapper" / "payload"
        else:
            parent = package
        parent.mkdir(parents=True, exist_ok=True)
        filename = "file-%08d.dat" % (i // 100 if shape == "conflict" else i)
        (parent / filename).write_bytes(b"benchmark")


def canonical_digest(plan):
    """Hash complete operation/mapping semantics, ignoring random IDs/timestamps."""
    digest = hashlib.sha256()
    for label, records, omitted in (
        ("operations", plan.operations, {"op_id"}),
        ("mappings", plan.mappings, {"entry_id"}),
        ("issues", plan.issues, {"issue_id", "created_at"}),
    ):
        digest.update(label.encode())
        for record in records:
            value = {k: v for k, v in record.to_dict().items() if k not in omitted}
            digest.update(json.dumps(value, ensure_ascii=True, sort_keys=True).encode())
            digest.update(b"\n")
    return digest.hexdigest()


def run_once(root: Path, mode: str, pages: int, instrument: bool = False):
    phases = Counter()
    calls = Counter()
    progress_calls = Counter()
    def progress(done, total, label="", step=None):
        progress_calls[step or ""] += 1

    with ExitStack() as stack:
        if instrument:
            def timed(name, fn):
                def wrapped(*args, **kwargs):
                    calls[name] += 1
                    start = time.perf_counter()
                    try:
                        return fn(*args, **kwargs)
                    finally:
                        phases[name] += time.perf_counter() - start
                return wrapped
            for name in ("build_tree", "resource_roots", "collect_dir_paths", "prune_empty_dirs",
                         "apply_root_chain_penetration", "process_node", "final_files",
                         "scan_residual_dirs", "snapshot_digest"):
                stack.enter_context(patch.object(planner, name, timed(name, getattr(planner, name))))
            original_resolver = planner._path_resolver
            def resolver_factory():
                resolve = original_resolver()
                seen = set()
                def resolve_counted(path):
                    key = str(path)
                    calls["resolve.hit" if key in seen else "resolve.miss"] += 1
                    seen.add(key)
                    return resolve(path)
                return timed("resolve", resolve_counted)
            stack.enter_context(patch.object(planner, "_path_resolver", resolver_factory))
        start = time.perf_counter()
        plan = planner.build_plan(str(root), mode=mode, progress=progress)
        plan_seconds = time.perf_counter() - start
    start = time.perf_counter()
    payload = _plan_payload(plan)
    payload_bytes = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    first_screen_seconds = time.perf_counter() - start
    samples = []
    indexed_samples = []
    registry = PlanRegistry()
    registry.put(plan)
    for i in range(pages):
        if not plan.resource_roots:
            break
        resource = plan.resource_roots[i % len(plan.resource_roots)]
        start = time.perf_counter()
        offset = (i // len(plan.resource_roots)) * 200
        legacy = readable_group_rows(plan, resource, offset=offset, limit=200)
        samples.append(time.perf_counter() - start)
        start = time.perf_counter()
        indexed = registry.page(plan.plan_id, resource, offset, 200)
        indexed_samples.append(time.perf_counter() - start)
        if indexed != legacy:
            raise RuntimeError("indexed page differs from legacy")
    sorted_samples = sorted(samples)
    p95 = sorted_samples[max(0, (95 * len(samples) + 99) // 100 - 1)] if samples else None
    return {
        "version": plan.app_version, "rule_version": plan.rule_version,
        "mapping_count": len(plan.mappings), "resource_count": len(plan.resource_roots),
        "operation_counts": dict(Counter(op.op_type for op in plan.operations)),
        "issue_counts": dict(Counter(issue.code for issue in plan.issues)),
        "plan_seconds": plan_seconds, "first_screen_seconds": first_screen_seconds,
        "payload_bytes": payload_bytes, "page_p50_seconds": statistics.median(samples) if samples else None,
        "page_p95_seconds": p95, "page_samples": len(samples),
        "indexed_first_page_seconds": indexed_samples[0] if indexed_samples else None,
        "indexed_warm_page_p50_seconds": statistics.median(indexed_samples[1:]) if len(indexed_samples) > 1 else None,
        "indexed_warm_page_p95_seconds": sorted(indexed_samples[1:])[max(0, (95 * (len(indexed_samples)-1) + 99) // 100 - 1)] if len(indexed_samples) > 1 else None,
        "indexed_total_seconds": sum(indexed_samples), "legacy_total_seconds": sum(samples),
        "phase_seconds_inclusive": dict(phases), "phase_calls": dict(calls),
        "progress_calls": dict(progress_calls), "working_set_bytes": working_set_bytes(),
        "snapshot_digest": plan.source_snapshot_digest, "semantic_digest": canonical_digest(plan),
    }


def progress_overhead(samples: int = 200000):
    from datetime import UTC, datetime
    import threading
    lock = threading.Lock()
    legacy = {}
    def old(done, total, label):
        with lock:
            legacy.update({"phase": "扫描", "status": "running", "done": done, "total": total,
                           "label": label, "note": "", "updated_at": datetime.now(UTC).isoformat()})
    start = time.perf_counter()
    for n in range(samples):
        old(n, 0, "file")
    old_seconds = time.perf_counter() - start
    key, report = web._track({"progress_key": "benchmark-progress"}, "扫描")
    try:
        start = time.perf_counter()
        for n in range(samples):
            report(n, 0, "file")
        web._finish_progress(key, True)
        new_seconds = time.perf_counter() - start
        if web._progress_slot(key)["done"] != samples - 1:
            raise RuntimeError("throttled progress lost final count")
    finally:
        with web._PROGRESS_LOCK:
            web.PROGRESS.pop(key, None)
    return {"callbacks": samples, "legacy_seconds": old_seconds, "throttled_seconds": new_seconds}


def run_execution_fixture(count: int, parent: Path | None, repeat: int):
    """Only generated files are ever mutated; every round has a fresh fixture."""
    results = []
    for _ in range(repeat):
        with tempfile.TemporaryDirectory(prefix="nesttidy-exec-bench-", dir=parent) as tmp:
            base = Path(tmp).resolve()
            root = base / "input"
            make_fixture(root, count, "move")
            before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            plan = planner.build_plan(str(root), mode="batch")
            journal = base / "move.jsonl"
            start = time.perf_counter()
            outcome = executor.execute_plan(plan, "move", confirm_move=True, transaction_log=str(journal))
            elapsed = time.perf_counter() - start
            start = time.perf_counter()
            restored = undo_transaction(str(journal)) if journal.exists() else []
            undo_seconds = time.perf_counter() - start
            after = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            if outcome.failed or any(row.status == "failed" for row in restored) or before != after:
                raise RuntimeError("隔离执行基准未能完整恢复")
            results.append({"files": count, "move_seconds": elapsed, "undo_seconds": undo_seconds,
                            "completed": outcome.completed, "skipped": outcome.skipped,
                            "swept": outcome.swept, "files_per_second": count / max(elapsed, 1e-9),
                            "restored": True})
    return {"runs": results, "median_files_per_second": statistics.median(r["files_per_second"] for r in results)}


def resident_memory_probe(root: Path, mode: str, rounds: int):
    """Measure actual repeated registry retention; no mutation, no forced cold cache."""
    import gc
    registry = PlanRegistry()
    samples = []
    tracemalloc.start()
    try:
        for _ in range(rounds):
            start = time.perf_counter()
            plan = planner.build_plan(str(root), mode=mode)
            registry.put(plan)
            count = len(plan.mappings)
            if plan.resource_roots:
                registry.page(plan.plan_id, plan.resource_roots[0], 0, 200)
            del plan
            gc.collect()
            current, peak = tracemalloc.get_traced_memory()
            samples.append({"mappings": count, "retained_plans": len(registry._plans),
                            "entries": sum(registry._weight(p) for p in registry._plans.values()),
                            "traced_current": current, "traced_peak": peak,
                            "working_set": working_set_bytes(), "seconds": time.perf_counter() - start})
        registry.clear()
        gc.collect()
        released, _ = tracemalloc.get_traced_memory()
        return {"rounds": samples, "traced_after_clear": released}
    finally:
        tracemalloc.stop()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--root", type=Path, help="Existing directory, read-only plan")
    source.add_argument("--fixture-files", type=int, help="Generated temporary fixture, e.g. 200000")
    parser.add_argument("--fixture-parent", type=Path, help="Existing volume/directory for isolated fixture")
    parser.add_argument("--shape", choices=("flat", "deep", "multi", "conflict", "keep", "move"), default="multi")
    parser.add_argument("--mode", choices=("batch", "single"), default="batch")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--pages", type=int, default=10)
    parser.add_argument("--disk", default="unspecified", help="Operator-supplied SSD/HDD/filesystem note")
    parser.add_argument("--profile", action="store_true", help="Extra instrumented cProfile run (not included in median)")
    parser.add_argument("--execute-fixture-files", type=int, default=0, help="Opt-in extra isolated move/undo benchmark; never mutates --root")
    parser.add_argument("--resident-rounds", type=int, default=0, help="Extra repeated read-only plan retention probe")
    parser.add_argument("--memory", action="store_true", help="Extra tracemalloc run (not included in median)")
    args = parser.parse_args(argv)
    if args.repeat < 1 or args.pages < 0 or (args.fixture_files is not None and args.fixture_files < 1):
        parser.error("repeat/fixture-files must be positive; pages nonnegative")
    if args.resident_rounds < 0:
        parser.error("--resident-rounds must be nonnegative")
    if args.execute_fixture_files < 0:
        parser.error("--execute-fixture-files must be nonnegative")
    with ExitStack() as stack:
        if args.root is not None:
            if args.fixture_parent is not None:
                parser.error("--fixture-parent only applies to generated fixtures")
            root = args.root.resolve(strict=True)
            if not root.is_dir():
                parser.error("--root must be a directory")
            source_kind = "real-read-only"
        else:
            root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="nesttidy-bench-", dir=args.fixture_parent)))
            make_fixture(root, args.fixture_files, args.shape)
            source_kind = "synthetic"
        results = [run_once(root, args.mode, args.pages) for _ in range(args.repeat)]
        result = {"source": source_kind, "shape": args.shape if source_kind == "synthetic" else "operator directory",
                  "disk_note": args.disk, "platform": platform.platform(), "python": platform.python_version(),
                  "cache_note": "first run not guaranteed cold; subsequent runs potentially warm",
                  "runs": results, "median_plan_seconds": statistics.median(r["plan_seconds"] for r in results),
                  "median_first_screen_seconds": statistics.median(r["first_screen_seconds"] for r in results),
                  "semantic_stable": len({r["semantic_digest"] for r in results}) == 1,
                  "snapshot_stable": len({r["snapshot_digest"] for r in results}) == 1,
                  "progress_overhead": progress_overhead()}
        if args.profile:
            profiler = cProfile.Profile()
            result["profile_run"] = profiler.runcall(run_once, root, args.mode, args.pages, True)
            pstats.Stats(profiler, stream=sys.stderr).sort_stats("cumulative").print_stats(30)
        if args.memory:
            tracemalloc.start()
            try:
                result["memory_run"] = run_once(root, args.mode, args.pages)
                current, peak = tracemalloc.get_traced_memory()
                result["tracemalloc_bytes"] = {"current": current, "peak": peak}
            finally:
                tracemalloc.stop()
        if args.resident_rounds:
            result["resident_memory"] = resident_memory_probe(root, args.mode, args.resident_rounds)
        if args.execute_fixture_files:
            result["execution_fixture"] = run_execution_fixture(args.execute_fixture_files, args.fixture_parent, args.repeat)
        print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
