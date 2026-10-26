from __future__ import annotations

import csv
import json
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


def markdown_summary(plan: OperationPlan) -> str:
    op_counts: dict[str, int] = {}
    for op in plan.operations:
        op_counts[op.op_type] = op_counts.get(op.op_type, 0) + 1
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
    lines.extend(["", "## 安全边界", "", "- 计划已冻结。", "- 文件最高只提升到资源根目录内。", "- copy 模式不修改源目录。", "- move 模式需要显式确认并写入事务日志。", ""])
    return "\n".join(lines)
