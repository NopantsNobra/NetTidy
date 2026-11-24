"""Experimental parent-only projection; never used in the production planner."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import statistics
import subprocess
import tempfile
import time

from benchmarks.plan_bench import make_fixture


def projected(path, cache):
    key = str(path.parent)
    if key not in cache:
        cache[key] = path.parent.resolve()
    return cache[key] / path.name


def compare(paths):
    cache = {}
    result = []
    for label, path in paths:
        expected, actual = path.resolve(), projected(path, cache)
        result.append({"case": label, "exact": str(expected) == str(actual),
                       "path_equal": expected == actual})
    return result


def measure(count, repeat):
    with tempfile.TemporaryDirectory(prefix="nesttidy-resolve-") as tmp:
        base = Path(tmp).resolve()
        root = base / "fixture"
        make_fixture(root, count, "multi")
        paths = sorted(p for p in root.rglob("*") if p.is_file())
        times = {"resolved": [], "projected": []}
        for i in range(repeat):
            results = {}
            for kind in (("resolved", "projected") if i % 2 == 0 else ("projected", "resolved")):
                cache = {}
                start = time.perf_counter()
                results[kind] = [str(p.resolve() if kind == "resolved" else projected(p, cache)) for p in paths]
                times[kind].append(time.perf_counter() - start)
            if results["resolved"] != results["projected"]:
                raise RuntimeError("ordinary path results differ")
        real = base / "Long File Name Example.txt"
        real.write_bytes(b"x")
        directory = base / "real-directory"
        directory.mkdir()
        cases = [("case_variant", real.with_name(real.name.upper())),
                 ("missing", base / "missing.txt"),
                 ("dotdot", directory / ".." / real.name)]
        unavailable = []
        if os.name == "nt":
            fn = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
            fn.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
            fn.restype = ctypes.c_uint32
            buffer = ctypes.create_unicode_buffer(32768)
            n = fn(str(real), buffer, len(buffer))
            if 0 < n < len(buffer) and Path(buffer.value).name.casefold() != real.name.casefold():
                cases.append(("short_alias", Path(buffer.value)))
            else:
                unavailable.append("8.3 final component unavailable")
        link = base / "file-link"
        try:
            link.symlink_to(real)
            cases.append(("final_symlink", link))
        except (OSError, NotImplementedError):
            unavailable.append("symlink unavailable")
        junction = base / "junction"
        if os.name == "nt":
            proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(directory)],
                                  capture_output=True, text=True, errors="replace")
            if proc.returncode == 0:
                cases.append(("final_junction", junction))
            else:
                unavailable.append("junction unavailable")
        try:
            special = compare(cases)
        finally:
            if junction.exists():
                junction.rmdir()
        return {"files": count, "repeat": repeat, "ordinary_equal": True,
                "resolved_median": statistics.median(times["resolved"]),
                "projected_median": statistics.median(times["projected"]),
                "special": special, "unavailable": unavailable, "production_change": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=int, default=20000)
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()
    if min(args.files, args.repeat) < 1:
        parser.error("positive count/repeat required")
    print(json.dumps(measure(args.files, args.repeat), indent=2))
