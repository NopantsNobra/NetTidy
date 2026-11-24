from __future__ import annotations

import csv
import json
import os
from datetime import datetime
from pathlib import Path

from .config import REPORT_DIR_NAME
from .model import MappingEntry, OperationPlan
from .storage import record_plan


def new_report_dir(base_dir: str | None = None) -> Path:
    base = Path(base_dir).resolve() if base_dir else Path.cwd() / REPORT_DIR_NAME
    stamp = datetime.now().strftime("task_%Y%m%d_%H%M%S")
    path = base / stamp
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_plan_report(
    plan: OperationPlan,
    report_dir: str | Path | None = None,
    db_path: str | Path | None = None,
    progress=None,
) -> Path:
    out = Path(report_dir).resolve() if report_dir else new_report_dir()
    out.mkdir(parents=True, exist_ok=True)
    steps = 5

    def report(done: int, label: str) -> None:
        if progress:
            progress(done, steps, label, "报告")

    report(0, "准备报告")
    data = plan.to_dict()
    (out / "summary.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    report(1, "摘要 JSON")
    (out / "summary.md").write_text(markdown_summary(plan), encoding="utf-8")
    report(2, "摘要 Markdown")
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
    report(3, "对照明细")
    with (out / "issues.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["issue_id", "path", "stage", "code", "message", "final_status", "created_at"])
        writer.writeheader()
        for row in plan.issues:
            item = row.to_dict()
            writer.writerow({k: item.get(k, "") for k in writer.fieldnames})
    report(4, "异常明细")
    # 历史库跟随报告目录，而非进程工作目录：报告写在哪，历史就记在哪。
    record_plan(plan, str(out), db_path=db_path or out.parent / "history.sqlite")
    report(5, "写入历史")
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


def _inside(path: str, roots: list[str], lookup: dict[str, str] | None = None,
            cache: dict[str, str] | None = None) -> bool:
    """判断 path 是否落在 roots 任一目录内（含目录自身）；借资源根查表，别对每条操作扫一遍根列表。"""
    return bool(_root_for(path, roots, lookup, cache))


def plan_display_stats(plan: OperationPlan, row_limit: int = 0, folder_limit: int = 0,
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
    checked = escaped = 0
    lookup = _root_lookup(roots)
    target_cache: dict[str, str] = {}
    for op in plan.operations:
        if op.op_type not in {"move", "copy", "keep"} or not op.target_path:
            continue
        checked += 1
        escaped += not _inside(op.target_path, roots, lookup, target_cache)
    return {
        "rule_counts": counts,
        "rule_rows": [{"rule": c, "name": _RULE_NAMES[c], "count": counts[c]} for c in _RULE_CODES],
        "r5_samples": samples,
        "boundary": {"checked": checked, "out_of_bound": escaped, "label": label},
        "readable": readable_plan_view(plan, row_limit, folder_limit, total_row_limit, summary_only=summary_only),
    }


def build_mapping_index(plan: OperationPlan) -> dict[str, list[MappingEntry]]:
    """按既有最长资源根语义建立一次索引；保存映射引用而非复制行或状态。"""
    roots = list(plan.resource_roots)
    lookup = _root_lookup(roots)
    root_cache: dict[str, str] = {}
    index: dict[str, list[MappingEntry]] = {}
    for mapping in plan.mappings:
        root = _root_for(mapping.original_path, roots, lookup, root_cache)
        index.setdefault(root, []).append(mapping)
    return index


def readable_group_rows(plan: OperationPlan, root_path: str, offset: int = 0, limit: int = 200,
                        index: dict[str, list[MappingEntry]] | None = None) -> dict:
    """按资源包分页。传入随计划存活的索引时，每页仅翻译所需行。"""
    if index is None:
        roots = list(plan.resource_roots)
        lookup = _root_lookup(roots)
        root_cache: dict[str, str] = {}
        rows: list[dict] = []
        total = 0
        for mapping in plan.mappings:
            if _root_for(mapping.original_path, roots, lookup, root_cache) != root_path:
                continue
            if offset <= total < offset + max(limit, 1):
                rows.append(_readable_row(plan, mapping, root_path))
            total += 1
    else:
        bucket = index.get(root_path, [])
        total = len(bucket)
        start, stop = max(0, offset), max(0, offset + max(limit, 1))
        rows = [_readable_row(plan, mapping, root_path) for mapping in bucket[start:stop]]
    return {"root_path": root_path, "offset": offset, "total": total, "rows": rows}


_RULE_WHY = {
    "R1": "中间几层只是单线套壳（每层只有一个子目录），文件直接提到资源包根部",
    "R2": "上层目录只包着一个子目录，合并掉这一层",
    "R3": "这里有多个分支，父目录保留下来当分组，文件各自提到分叉口",
    "R4": "目录里只剩这一个文件，把它提到上一层",
    "R5": "目标位置已有同名文件，用来源目录名做前缀区分（不会生成 (1) 这种后缀）",
}
_OP_PLAIN = {"move": "移动到", "copy": "复制到", "keep": "原地保留"}
_ACTION_PLAIN = {"moved": "挪了位置", "renamed": "原地改名", "same": "原地不动"}


def human_bytes(value: int | None) -> str:
    if not value:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"


def _rel(path: str, base: str) -> str:
    """相对路径用于阅读；跨根时退回绝对路径，绝不谎报层级。

    这里用前缀切片而不是 os.path.relpath：28 万条映射时 relpath 的 normcase 能把一次试运行
    拖慢几十秒，而 before/after 本来就落在资源包根之内，切片结果与 relpath 完全一致。
    """
    if not base:
        return path
    head = base.rstrip(os.sep) + os.sep
    low_path, low_head = path.lower(), head.lower()
    if low_path.startswith(low_head):
        return path[len(head):]
    if low_path.rstrip(os.sep) == head.rstrip(os.sep):
        return ""
    return path


def _depth(path: str, base: str) -> int:
    rel = _rel(path, base)
    return 0 if rel == path else len([part for part in rel.split(os.sep) if part]) - 1


def _root_lookup(roots: list[str]) -> dict[str, str]:
    """资源根 → 归一化路径表：28 万条映射逐条扫根列表会把一次试运行拖到几分钟。"""
    table: dict[str, str] = {}
    for root in roots:
        norm = _norm_path(root)
        table[norm] = root
        table[norm.rstrip(os.sep).rstrip("/")] = root      # 驱动器根 E:\ 归一化后是 "e:"，两种写法都要认
    return table


def _root_for(path: str, roots: list[str], lookup: dict[str, str] | None = None,
              cache: dict[str, str] | None = None) -> str:
    """取包含该路径的最长资源根；都不包含时返回空串。

    `cache` 记住「目录 → 资源根」：28 万条映射其实只落在几百个目录里，重复向上找没有意义。
    """
    table = lookup if lookup is not None else _root_lookup(roots)
    node = _norm_path(path)
    leaf = node
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
        # 缓存祖先目录，不缓存每条文件路径，避免 O(文件数) 的临时字典。
        for item in walked:
            if item != leaf:
                cache[item] = found
    return found


def _readable_row(plan: OperationPlan, mapping: MappingEntry, root: str) -> dict:
    """单条映射 → 一行「整理前 → 整理后」；整表与分页共用，纯函数不读盘。"""
    after_base = plan.output_root or ""
    before = _rel(mapping.original_path, root) if root else mapping.original_path
    # 阅读口径要「同构」：before 相对资源包根，after 也必须相对「输出目录里的同名资源包」，
    # 否则输出前缀会被算成位置变化，所有文件都显示成「换位置」。
    base_after = root
    if plan.output_root and root:
        base_after = os.path.join(after_base, os.path.basename(root))
    after = _rel(mapping.planned_target_path, base_after) if base_after else mapping.planned_target_path
    # 用户关心的是「结构变没变」：copy 模式下目标整体落在输出目录里，绝对路径必然不同，
    # 但相对资源包的层级可能一模一样。按相对路径比对，才不会把「原样带过去」说成「搬动」。
    rel_changed = _norm_path(before) != _norm_path(after)
    is_renamed = os.path.basename(mapping.original_path) != os.path.basename(mapping.planned_target_path)
    hits = [code for code in _RULE_CODES for trace in mapping.rule_trace if trace.startswith(code)]
    why = "；".join(dict.fromkeys(_RULE_WHY[code] for code in hits if code in _RULE_WHY))
    if mapping.name_cleaning_trace:
        why = (why + "；" if why else "") + "文件名里的非法字符被替换成安全字符"
    if not why:
        why = "位置和名字都不变，只是跟着复制过去" if not rel_changed and not is_renamed else "按规则提到资源包根部"
    if not rel_changed and not is_renamed:
        action, action_code = _ACTION_PLAIN["same"], "same"
    elif not rel_changed:
        action, action_code = _ACTION_PLAIN["renamed"], "renamed"
    else:
        action, action_code = _OP_PLAIN.get(mapping.operation_type, mapping.operation_type), mapping.operation_type
    return {
        "before": before,
        "after": after,
        "depth": _depth(mapping.original_path, root) if root else 0,
        "action": action,
        "action_code": action_code,
        "rules": [_RULE_NAMES[code] for code in hits if code in _RULE_NAMES],
        "why": why,
        "renamed": is_renamed,
        "moved": rel_changed,
        "changed": rel_changed or is_renamed,
        "size": human_bytes(mapping.source_size),
        "size_bytes": mapping.source_size or 0,
        "status": mapping.status,
    }


def _row_flags(plan: OperationPlan, mapping: MappingEntry, root: str) -> tuple[bool, bool, int]:
    """只算「换位置 / 改名 / 体积」这三个标记，不构造整行——几十万条里绝大多数不显示。"""
    if not plan.output_root and mapping.original_path == mapping.planned_target_path:
        return False, False, mapping.source_size or 0
    before = _rel(mapping.original_path, root) if root else mapping.original_path
    base_after = root
    if plan.output_root and root:
        base_after = os.path.join(plan.output_root, os.path.basename(root))
    after = _rel(mapping.planned_target_path, base_after) if base_after else mapping.planned_target_path
    moved = _norm_path(before) != _norm_path(after)
    is_renamed = os.path.basename(mapping.original_path) != os.path.basename(mapping.planned_target_path)
    return moved, is_renamed, mapping.source_size or 0


def readable_plan_view(plan: OperationPlan, row_limit: int = 0, folder_limit: int = 0,
                       total_row_limit: int = 0, *, summary_only: bool = False) -> dict:
    """把冻结计划翻译成人能读的「整理前 → 整理后」清单：纯派生视图，不读盘、不改计划字段。

    `row_limit > 0` 时每个资源包只带前 row_limit 行——28 万个文件一次塞进浏览器会把页面卡死，
    余下行数用 row_total / rows_shown 标注，前端按 `/api/plan/rows` 分页补齐。
    """
    roots = list(plan.resource_roots)
    lookup = _root_lookup(roots)
    root_cache: dict[str, str] = {}
    rows_emitted = 0
    groups: dict[str, dict] = {}
    renamed = in_place = changed = 0
    total_bytes = 0
    for mapping in plan.mappings:
        root = _root_for(mapping.original_path, roots, lookup, root_cache)
        bucket = groups.get(root)
        if bucket is None:
            bucket = groups[root] = {"root_path": root, "root_name": Path(root).name if root else "（资源根之外）",
                                     "rows": [], "moved": 0, "renamed": 0, "changed": 0, "row_total": 0, "rows_shown": 0}
        bucket["row_total"] += 1
        # 28 万条里绝大多数不会显示（每包只发前几十行），先算轻量标记，确实要显示才造整行 dict。
        emit = not summary_only and (row_limit <= 0 or bucket["rows_shown"] < row_limit) and (
            total_row_limit <= 0 or rows_emitted < total_row_limit
        )
        if emit:
            row = _readable_row(plan, mapping, root)
            bucket["rows"].append(row)
            bucket["rows_shown"] += 1
            rows_emitted += 1
            moved, is_renamed, size_bytes = row["moved"], row["renamed"], row["size_bytes"]
        else:
            moved, is_renamed, size_bytes = _row_flags(plan, mapping, root)
        if moved or is_renamed:
            changed += 1
            bucket["changed"] += 1
        if moved:
            bucket["moved"] += 1
            total_bytes += size_bytes
        else:
            in_place += 1
        if is_renamed:
            renamed += 1
            bucket["renamed"] += 1
    folders = []
    folder_total = 0
    for op in plan.operations:
        if op.op_type != "rmdir" or not op.source_path:
            continue
        folder_total += 1
        if folder_limit > 0 and len(folders) >= folder_limit:
            continue
        root = _root_for(op.source_path, roots, lookup, root_cache)
        folders.append(
            {
                "root_name": Path(root).name if root else "（资源根之外）",
                "root_path": root,
                "path": _rel(op.source_path, root) if root else op.source_path,
                "full_path": op.source_path,
                "depth": _depth(op.source_path, root) if root else 0,
            }
        )
    moved = sum(bucket["moved"] for bucket in groups.values())
    folders_hint = "只在文件被提走后变空才清"
    if plan.output_root:
        folders_hint = "当前是复制到输出目录，源目录里的空壳原样保留"
    cards = [
        {"label": "资源包", "value": len(roots), "hint": "每个子文件夹算一个独立资源包"},
        {"label": "会换位置的文件", "value": moved, "hint": "从深层子文件夹提到资源包根部"},
        {"label": "会改名的文件", "value": renamed, "hint": "撞名时加来源目录前缀，绝不覆盖"},
        {"label": "会清掉的空文件夹", "value": folder_total, "hint": folders_hint},
        {"label": "换位置的体积", "value": human_bytes(total_bytes), "hint": "这些文件加起来有多大"},
        {"label": "原地不动", "value": in_place, "hint": "位置和名字都不用改"},
        {"label": "异常项", "value": len(plan.issues), "hint": "下载未完成、符号链接、无权限等"},
    ]
    if plan.output_root:
        headline = (
            f"把 {len(roots)} 个资源包复制到输出目录：其中 {moved} 个文件会换位置（{human_bytes(total_bytes)}），"
            f"{renamed} 个文件原地改名，{in_place} 个原样带过去；源目录保持不动，{len(plan.issues)} 个异常。"
        )
    else:
        headline = (
            f"整理 {len(roots)} 个资源包（原地移动）：{moved} 个文件换位置（{human_bytes(total_bytes)}），"
            f"{renamed} 个文件改名，{in_place} 个原地不动，清掉 {folder_total} 个空文件夹，{len(plan.issues)} 个异常。"
        )
    return {
        "headline": headline,
        "cards": cards,
        "mode": plan.mode,
        "output_root": plan.output_root,
        "groups": [groups[key] for key in sorted(groups, key=lambda k: (k.casefold(), k))],
        "folders": folders,
        "folder_total": folder_total,
        "counts": {"moved": moved, "renamed": renamed, "changed": changed, "folders": folder_total,
                   "in_place": in_place, "total": len(plan.mappings)},
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
    view = readable_plan_view(plan)
    lines.extend(["", "## 会怎么变（整理前 → 整理后）", "", f"- {view['headline']}", ""])
    for group in view["groups"]:
        lines.append(f"### {group['root_name']}")
        lines.append("")
        for row in group["rows"][:50]:
            lines.append(f"- `{row['before']}` → `{row['after']}`（{row['action']}，{row['size']}）；原因：{row['why']}")
        if len(group["rows"]) > 50:
            lines.append(f"- …… 本资源包其余 {len(group['rows']) - 50} 条见 mappings.csv")
        lines.append("")
    if view["folders"]:
        lines.extend(["## 会被清掉的空文件夹", ""])
        for item in view["folders"][:50]:
            lines.append(f"- [{item['root_name']}] {item['path']}")
        lines.append("")
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
