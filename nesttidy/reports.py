from __future__ import annotations

import csv
import json
import os
from datetime import datetime
from pathlib import Path

from .config import REPORT_DIR_NAME
from .model import OperationPlan
from .storage import record_plan


def new_report_dir(base_dir: str | None = None) -> Path:
    base = Path(base_dir).resolve() if base_dir else Path.cwd() / REPORT_DIR_NAME
    stamp = datetime.now().strftime("task_%Y%m%d_%H%M%S")
    path = base / stamp
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_plan_report(plan: OperationPlan, report_dir: str | Path | None = None, db_path: str | Path | None = None) -> Path:
    out = Path(report_dir).resolve() if report_dir else new_report_dir()
    out.mkdir(parents=True, exist_ok=True)
    data = plan.to_dict()
    (out / "summary.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "summary.md").write_text(markdown_summary(plan), encoding="utf-8")
    with (out / "mappings.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "entry_id",
                "resource_root_id",
                "entry_type",
                "original_path",
                "planned_target_path",
                "operation_type",
                "rule_trace",
                "risk_level",
                "status",
                "name_cleaning_trace",
                "conflict_resolution_trace",
            ],
        )
        writer.writeheader()
        for row in plan.mappings:
            item = row.to_dict()
            item["rule_trace"] = " | ".join(row.rule_trace)
            item["name_cleaning_trace"] = " | ".join(row.name_cleaning_trace)
            item["conflict_resolution_trace"] = " | ".join(row.conflict_resolution_trace)
            writer.writerow({k: item.get(k, "") for k in writer.fieldnames})
    with (out / "issues.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["issue_id", "path", "stage", "code", "message", "final_status", "created_at"])
        writer.writeheader()
        for row in plan.issues:
            item = row.to_dict()
            writer.writerow({k: item.get(k, "") for k in writer.fieldnames})
    # 历史库跟随报告目录，而非进程工作目录：报告写在哪，历史就记在哪。
    record_plan(plan, str(out), db_path=db_path or out.parent / "history.sqlite")
    return out


_RULE_NAMES = {
    "R0": "空目录清理（含 R1–R4 产生的空壳）",
    "R1": "资源根内直链穿透",
    "R2": "壳吸收",
    "R3": "分叉口/父级保护",
    "R4": "单文件提级",
    "R5": "撞名重命名",
}
_RULE_CODES = tuple(_RULE_NAMES)
_R5_SAMPLE_LIMIT = 5


def _norm_path(value: str) -> str:
    """纯词法归一化：消 .. 并统一大小写/分隔符，不读磁盘、不跟随链接。"""
    return os.path.normcase(os.path.abspath(value))


def _inside(path: str, roots: list[str]) -> bool:
    """判断 path 是否落在 roots 任一目录内（含目录自身），按路径分量比较。"""
    candidate = _norm_path(path)
    for root in roots:
        base = _norm_path(root)
        if candidate == base or candidate.startswith(base.rstrip(os.sep) + os.sep):
            return True
    return False


def plan_display_stats(plan: OperationPlan) -> dict:
    """从冻结计划派生展示统计：只读、不写盘、不改计划字段与顺序。

    口径：R0 = rmdir 操作数；R1–R5 = mappings[].rule_trace 行首命中条目（按条目去重）；
    R5 样例取 conflict_resolution_trace 含 R5 的前 5 条（name_cleaning_trace 不含改名后名字）；
    边界按 copy→output_root / 其余→resource_roots 逐条校验目标，P0 是无痕迹的不变量。
    """
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
    escaped = [op for op in file_ops if not _inside(op.target_path, roots)]
    return {
        "rule_counts": counts,
        "rule_rows": [{"rule": c, "name": _RULE_NAMES[c], "count": counts[c]} for c in _RULE_CODES],
        "r5_samples": samples,
        "boundary": {"checked": len(file_ops), "out_of_bound": len(escaped), "label": label},
    }


def markdown_summary(plan: OperationPlan) -> str:
    op_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    for op in plan.operations:
        op_counts[op.op_type] = op_counts.get(op.op_type, 0) + 1
        status_counts[op.status] = status_counts.get(op.status, 0) + 1
    lines = [
        "# 理巢任务报告",
        "",
        f"- plan_id: `{plan.plan_id}`",
        f"- created_at: `{plan.created_at}`",
        f"- mode: `{plan.mode}`",
        f"- selected_root: `{plan.selected_root}`",
        f"- output_root: `{plan.output_root or ''}`",
        f"- resource_roots: `{len(plan.resource_roots)}`",
        f"- operations: `{len(plan.operations)}`",
        f"- issues: `{len(plan.issues)}`",
        f"- estimated_space: `{plan.estimated_space}` bytes",
        "",
        "## 操作统计",
        "",
    ]
    for name in sorted(op_counts):
        lines.append(f"- {name}: {op_counts[name]}")
    stats = plan_display_stats(plan)
    lines.extend([
        "", "## 规则分类计数", "",
        "- 口径：R0 = operations 中 op_type=rmdir 的条数；R1–R5 = mappings[].rule_trace 行首命中该规则的条目数（同一条目可命中多条，各数之和可大于文件总数），按条目去重。来源：冻结计划，未新增字段。",
        "- P0 是资源根边界不变量，不产生逐条规则痕迹；真实检查结果见「边界检查」，不写固定值。",
    ])
    for row in stats["rule_rows"]:
        lines.append(f"- {row['rule']} {row['name']}: {row['count']}")
    lines.extend([
        "", "## R5 撞名前缀样例", "",
        "- 口径：取 mappings[].conflict_resolution_trace 含 R5 的前 5 条；原名/结果名分别为 original_path / planned_target_path 的末段，证据为轨迹原文。",
    ])
    if stats["r5_samples"]:
        for row in stats["r5_samples"]:
            lines.append(f"- {row['original_name']} → {row['final_name']}（证据：{row['trace']}）")
    else:
        lines.append("- 无 R5 撞名样例。")
    boundary = stats["boundary"]
    lines.extend([
        "", "## 边界检查", "",
        f"- 目标越界：{boundary['out_of_bound']} 条 / 已校验 {boundary['checked']} 条文件操作。",
        f"- 口径：边界取 {boundary['label']}，逐条比对 operations[].target_path 是否落在边界内（词法归一化、不读磁盘）。数字由当前计划实时计算，非固定值。",
        "- 盲区：本检查为计划期词法归一化，不读磁盘、不解析 junction / 符号链接；此类场景以执行层复查为准（执行层用真实路径解析，实测可拦截越界）。",
        "- 盲区：本检查只验证目标是否落在资源根并集内；跨资源根由 planner 结构保证，展示层不做逐条跨根核对（P0 不变量 2 的口径强于本检查）。",
        "- 分母：仅含 op_type ∈ {move,copy,keep} 且 target_path 非空者，rmdir 无目标不计。",
    ])
    lines.extend(["", "## 执行状态", ""])
    for name in sorted(status_counts):
        lines.append(f"- {name}: {status_counts[name]}")
    if status_counts.get("planned", 0) == len(plan.operations) and plan.operations:
        lines.append("")
        lines.append("- 说明：本报告在计划阶段生成，尚未执行；执行后需重新导出以反映实际结果。")
    lines.extend(["", "## 安全边界", "", "- 计划已冻结。", "- 文件最高只提升到资源根目录内。", "- copy 模式不修改源目录。", "- move 模式需要显式确认并写入事务日志。", ""])
    return "\n".join(lines)
