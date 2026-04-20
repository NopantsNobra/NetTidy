"""可视化对比模块 — 原目录树 vs 新目录树差异分析"""
from dataclasses import dataclass, field
from typing import Optional

from .models import TreeNode


@dataclass
class DiffItem:
    """单个差异项"""
    path: str           # 在原树或新树中的路径
    kind: str           # removed / added / moved / renamed / unchanged
    old_path: str = ""  # 移动/重命名前的路径
    new_path: str = ""  # 移动/重命名后的路径
    is_dir: bool = False


@dataclass
class DiffReport:
    """差异报告"""
    items: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.stats:
            self.stats = {
                "removed_dirs": 0,
                "promoted_files": 0,
                "renamed_files": 0,
                "unchanged": 0,
            }


def compute_diff(
    source_tree: TreeNode,
    result_tree: TreeNode,
    mapping: dict[str, str],
) -> DiffReport:
    """
    对比源树和结果树，生成差异报告。

    Parameters
    ----------
    source_tree : TreeNode
        原始扫描树
    result_tree : TreeNode
        算法输出的目标树
    mapping : dict
        源路径 → 目标路径 映射表

    Returns
    -------
    DiffReport
    """
    report = DiffReport()

    # 收集源树中所有路径
    source_paths = set()
    _collect_all_paths(source_tree, source_paths)

    # 收集结果树中所有路径
    result_paths = set()
    _collect_all_paths(result_tree, result_paths)

    # 分析差异
    for src_rel, tgt_rel in mapping.items():
        src_name = src_rel.split("/")[-1]
        tgt_name = tgt_rel.split("/")[-1]

        if src_rel == tgt_rel:
            # 路径完全未变
            report.items.append(DiffItem(
                path=tgt_rel, kind="unchanged",
                old_path=src_rel, new_path=tgt_rel,
            ))
            report.stats["unchanged"] += 1
        elif src_name != tgt_name:
            # 文件名变化 → 重命名（即使父目录也变了）
            report.items.append(DiffItem(
                path=tgt_rel, kind="renamed",
                old_path=src_rel, new_path=tgt_rel,
            ))
            report.stats["renamed_files"] += 1
        elif _parent_changed(src_rel, tgt_rel):
            # 仅父目录变化 → 提升/移动
            report.items.append(DiffItem(
                path=tgt_rel, kind="moved",
                old_path=src_rel, new_path=tgt_rel,
            ))
            report.stats["promoted_files"] += 1
        else:
            # 其他变化
            report.items.append(DiffItem(
                path=tgt_rel, kind="unchanged",
                old_path=src_rel, new_path=tgt_rel,
            ))
            report.stats["unchanged"] += 1

    # 源树中有但结果树中没有的目录（被消除的空壳）
    for src_path in source_paths:
        if src_path not in result_paths and src_path not in mapping:
            report.items.append(DiffItem(
                path=src_path, kind="removed", is_dir=True,
            ))
            report.stats["removed_dirs"] += 1

    # 排序：先按类型，再按路径
    kind_order = {"removed": 0, "moved": 1, "renamed": 2, "unchanged": 3, "added": 4}
    report.items.sort(key=lambda x: (kind_order.get(x.kind, 99), x.path))

    return report


def render_tree_with_diff(
    tree: TreeNode,
    diff_report: DiffReport,
    *,
    side: str = "source",  # "source" or "result"
) -> str:
    """
    将树渲染为带差异标记的文本。

    标记方式：
    - 🔴 removed（壳删除）
    - 🟢 moved/promoted（文件提升）
    - 🟡 renamed（重命名）
    - ⚪ unchanged（无变化）
    """
    lines = []
    _render_node(tree, lines, diff_report, side, indent=0)
    return "\n".join(lines)


def _render_node(
    node: TreeNode,
    lines: list,
    diff_report: DiffReport,
    side: str,
    indent: int,
) -> None:
    """递归渲染单个节点"""
    prefix = "  " * indent
    path = node.path

    # 查找对应的差异项
    diff_item = _find_diff(diff_report, path, side)

    if indent == 0:
        lines.append(f"{prefix}📁 {node.name or '(root)'}")
    elif diff_item:
        icon = _diff_icon(diff_item.kind)
        marker = _diff_marker(diff_item.kind)
        if node.is_dir:
            lines.append(f"{prefix}📁 {node.name} {icon}{marker}")
        else:
            lines.append(f"{prefix}📄 {node.name} {icon}{marker}")
    else:
        if node.is_dir:
            lines.append(f"{prefix}📁 {node.name}")
        else:
            lines.append(f"{prefix}📄 {node.name}")

    if node.is_dir:
        for child in sorted(node.children, key=lambda c: (not c.is_dir, c.name.lower())):
            _render_node(child, lines, diff_report, side, indent + 1)


def _diff_icon(kind: str) -> str:
    """差异类型对应的表情符号"""
    return {
        "removed": "🔴",
        "moved": "🟢",
        "promoted": "🟢",
        "renamed": "🟡",
        "unchanged": "",
        "added": "🔵",
    }.get(kind, "")


def _diff_marker(kind: str) -> str:
    """差异类型对应的文字标记"""
    return {
        "removed": " [删除]",
        "moved": " [↑提升]",
        "promoted": " [↑提升]",
        "renamed": " [重命名]",
        "unchanged": "",
        "added": " [新增]",
    }.get(kind, "")


def _find_diff(diff_report: DiffReport, path: str, side: str) -> Optional[DiffItem]:
    """在差异报告中查找指定路径的差异项"""
    for item in diff_report.items:
        if side == "source" and item.old_path == path:
            return item
        elif side == "result" and item.new_path == path:
            return item
    return None


def _collect_all_paths(node: TreeNode, paths: set) -> None:
    """递归收集所有节点路径"""
    if node.path:
        paths.add(node.path)
    if node.is_dir:
        for child in node.children:
            _collect_all_paths(child, paths)


def _parent_changed(src: str, tgt: str) -> bool:
    """判断文件的父目录是否发生变化"""
    src_parent = "/".join(src.split("/")[:-1])
    tgt_parent = "/".join(tgt.split("/")[:-1])
    return src_parent != tgt_parent


def render_summary(diff_report: DiffReport) -> str:
    """渲染差异统计摘要"""
    s = diff_report.stats
    parts = []
    if s["removed_dirs"] > 0:
        parts.append(f"消除 {s['removed_dirs']} 个空壳目录")
    if s["promoted_files"] > 0:
        parts.append(f"提升 {s['promoted_files']} 个文件")
    if s["renamed_files"] > 0:
        parts.append(f"重命名 {s['renamed_files']} 个文件")
    if s["unchanged"] > 0:
        parts.append(f"{s['unchanged']} 个无变化")
    return "📊 " + "，".join(parts) if parts else "📊 无差异"
