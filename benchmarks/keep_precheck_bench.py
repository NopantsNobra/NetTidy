"""Opt-in isolated comparison of no-write keep prechecks."""
import argparse
import json
from pathlib import Path
import statistics
import tempfile
import time

from nesttidy.executor import precheck
from nesttidy.model import Operation


def legacy_precheck(op: Operation) -> None:
    if op.op_type == "rmdir":
        return
    if not op.source_path:
        raise ValueError("缺少源路径")
    source = Path(op.source_path)
    if not source.exists():
        raise FileNotFoundError(f"源路径不存在: {source}")
    stat = source.stat()
    expected_size = op.precheck.get("size")
    expected_mtime = op.precheck.get("mtime_ns")
    if expected_size is not None and stat.st_size != expected_size:
        raise RuntimeError(f"源文件大小已变化: {source}")
    if expected_mtime is not None and stat.st_mtime_ns != expected_mtime:
        raise RuntimeError(f"源文件修改时间已变化: {source}")
    if not op.target_path:
        raise ValueError("缺少目标路径")
    target = Path(op.target_path)
    if target.exists() and target.resolve() != source.resolve():
        raise FileExistsError(f"目标已存在，禁止覆盖: {target}")



def measure(count: int, repeat: int):
    with tempfile.TemporaryDirectory(prefix="nesttidy-keep-bench-") as tmp:
        base = Path(tmp).resolve()
        operations = []
        for i in range(count):
            p = base / f"group-{i % 100:03}" / f"file-{i:08}.txt"
            p.parent.mkdir(exist_ok=True)
            p.write_bytes(b"keep")
            info = p.stat()
            operations.append(Operation(str(i), "root", "keep", str(p), str(p), [],
                precheck={"size": info.st_size, "mtime_ns": info.st_mtime_ns}))
        timings = {"legacy": [], "current": []}
        for i in range(repeat):
            for label, fn in ((("legacy", legacy_precheck), ("current", precheck)) if i % 2 == 0
                              else (("current", precheck), ("legacy", legacy_precheck))):
                start = time.perf_counter()
                for op in operations:
                    fn(op)
                timings[label].append(time.perf_counter() - start)
        return {"files": count, "repeat": repeat, "legacy_seconds": timings["legacy"],
                "current_seconds": timings["current"],
                "legacy_median": statistics.median(timings["legacy"]),
                "current_median": statistics.median(timings["current"]),
                "all_checks_passed": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=int, default=20000)
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()
    if min(args.files, args.repeat) < 1:
        parser.error("files/repeat must be positive")
    print(json.dumps(measure(args.files, args.repeat), indent=2))
