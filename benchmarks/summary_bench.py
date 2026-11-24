"""Compare display derivation with original root cache and boundary traversal."""
import argparse
import json
from pathlib import Path
import statistics
import tempfile
import time
from unittest.mock import patch

from benchmarks.plan_bench import make_fixture
from nesttidy import reports
from nesttidy.planner import build_plan
from nesttidy.model import OperationPlan
from nesttidy.reports import _root_lookup, _inside, _norm_path, _RULE_CODES, _RULE_NAMES, _R5_SAMPLE_LIMIT, readable_plan_view
import os


def legacy_root_for(path: str, roots: list[str], lookup: dict[str, str] | None = None,
              cache: dict[str, str] | None = None) -> str:
    """取包含该路径的最长资源根；都不包含时返回空串。

    `cache` 记住「目录 → 资源根」：28 万条映射其实只落在几百个目录里，重复向上找没有意义。
    """
    table = lookup if lookup is not None else _root_lookup(roots)
    node = _norm_path(path)
    walked: list[str] = []
    while True:
        if cache is not None:
            hit = cache.get(node)
            if hit is not None:
                found = hit
                break
        if node in table:
            found = table[node]
            break
        walked.append(node)
        parent = os.path.dirname(node)
        if parent == node:
            found = ""
            break
        node = parent
    if cache is not None:
        for item in walked:
            cache[item] = found
    return found



def legacy_display_stats(plan: OperationPlan, row_limit: int = 0, folder_limit: int = 0,
                       total_row_limit: int = 0, *, summary_only: bool = False) -> dict:
    """从冻结计划派生展示统计：只读、不写盘、不改计划字段与顺序。"""
    counts: dict[str, int] = {code: 0 for code in _RULE_CODES}
    counts["R0"] = sum(1 for op in plan.operations if op.op_type == "rmdir")
    for mapping in plan.mappings:
        for code in {c for c in _RULE_CODES for t in mapping.rule_trace if t.startswith(c)}:
            counts[code] += 1

    samples: list[dict[str, str]] = []
    for mapping in plan.mappings:
        traces = [t for t in mapping.conflict_resolution_trace if t.startswith("R5")]
        if not traces:
            continue
        samples.append({"original_name": Path(mapping.original_path).name,
                        "final_name": Path(mapping.planned_target_path).name, "trace": traces[-1]})
        if len(samples) == _R5_SAMPLE_LIMIT:
            break

    roots = [plan.output_root] if plan.output_root else plan.resource_roots
    label = "output_root（copy 模式）" if plan.output_root else "resource_roots（资源根内）"
    file_ops = [op for op in plan.operations if op.op_type in {"move", "copy", "keep"} and op.target_path]
    lookup = _root_lookup(roots)
    escaped = sum(not _inside(op.target_path, roots, lookup, {}) for op in file_ops)
    return {
        "rule_counts": counts,
        "rule_rows": [{"rule": c, "name": _RULE_NAMES[c], "count": counts[c]} for c in _RULE_CODES],
        "r5_samples": samples,
        "boundary": {"checked": len(file_ops), "out_of_bound": escaped, "label": label},
        "readable": readable_plan_view(plan, row_limit, folder_limit, total_row_limit, summary_only=summary_only),
    }



def measure(count, repeat):
    with tempfile.TemporaryDirectory(prefix="nesttidy-summary-") as tmp:
        root = Path(tmp)
        make_fixture(root, count, "multi")
        plan = build_plan(str(root), mode="batch")
        times = {"legacy": [], "current": []}
        for i in range(repeat):
            results = {}
            for label in (("legacy", "current") if i % 2 == 0 else ("current", "legacy")):
                start = time.perf_counter()
                if label == "legacy":
                    with patch.object(reports, "_root_for", legacy_root_for):
                        results[label] = legacy_display_stats(plan, 80, 300, 1500, summary_only=True)
                else:
                    results[label] = reports.plan_display_stats(plan, 80, 300, 1500, summary_only=True)
                times[label].append(time.perf_counter() - start)
            if results["legacy"] != results["current"]:
                raise RuntimeError("display semantics differ")
        return {"files": count, "repeat": repeat, "legacy_seconds": times["legacy"],
                "current_seconds": times["current"], "legacy_median": statistics.median(times["legacy"]),
                "current_median": statistics.median(times["current"]), "equivalent": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=int, default=20000)
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()
    if min(args.files, args.repeat) < 1:
        parser.error("positive count/repeat required")
    print(json.dumps(measure(args.files, args.repeat), indent=2))
