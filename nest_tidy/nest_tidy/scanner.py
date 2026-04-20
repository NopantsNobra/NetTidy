"""扫描模块 — 读取目录，构建 TreeNode 树"""
import os
from pathlib import Path
from typing import Optional

from .models import TreeNode

# 系统文件，自动过滤
_SYSTEM_FILES = {'.ds_store', 'desktop.ini', 'thumbs.db'}


def scan_directory(
    source: str,
    *,
    max_depth: int = 0,
    follow_symlinks: bool = True,
    exclude_hidden: bool = False,
    _depth: int = 0,
    _rel: str = "",
) -> TreeNode:
    """
    扫描源目录，返回根 TreeNode。

    Parameters
    ----------
    source : str
        源目录绝对路径
    max_depth : int
        最大扫描深度，0 = 无限制
    follow_symlinks : bool
        是否跟随符号链接
    exclude_hidden : bool
        是否排除隐藏文件/目录
    """
    root = Path(source)
    node = _build_node(root, root, max_depth, follow_symlinks, exclude_hidden, 0, "")
    return node


def _build_node(
    abs_root: Path,
    current: Path,
    max_depth: int,
    follow_symlinks: bool,
    exclude_hidden: bool,
    depth: int,
    rel: str,
) -> TreeNode:
    """递归构建单个 TreeNode"""
    name = current.name if rel else ""
    is_dir = True

    try:
        entries = sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        # 权限不足：记录为叶节点，不中断流程
        return TreeNode(name=name, path=rel, is_dir=True, is_empty=True)
    except OSError:
        return TreeNode(name=name, path=rel, is_dir=True, is_empty=True)

    children = []
    for entry in entries:
        entry_name = entry.name

        # 过滤系统文件
        if entry_name.lower() in _SYSTEM_FILES:
            continue

        # 过滤隐藏文件
        if exclude_hidden and entry_name.startswith('.'):
            continue

        entry_rel = f"{rel}/{entry_name}" if rel else entry_name

        if entry.is_dir():
            # 深度限制
            if max_depth > 0 and depth >= max_depth:
                # 达到深度限制，将子目录作为叶节点
                children.append(TreeNode(
                    name=entry_name, path=entry_rel, is_dir=True,
                ))
                continue

            child = _build_node(
                abs_root, entry, max_depth, follow_symlinks,
                exclude_hidden, depth + 1, entry_rel,
            )
            children.append(child)
        else:
            # 文件节点
            try:
                size = entry.stat().st_size
            except OSError:
                size = 0
            children.append(TreeNode(
                name=entry_name, path=entry_rel, is_dir=False, size=size,
            ))

    node = TreeNode(
        name=name,
        path=rel,
        is_dir=True,
        children=children,
        is_empty=(len(children) == 0),
    )
    return node


def tree_to_dict(node: TreeNode, indent: int = 0) -> str:
    """
    将 TreeNode 树格式化为可读的缩进文本。
    用于调试和 UI 展示。
    """
    lines = []
    prefix = "  " * indent
    if indent == 0:
        lines.append(f"{prefix}📁 {node.name or '(root)'}")

    for child in sorted(node.children, key=lambda c: (not c.is_dir, c.name.lower())):
        if child.is_dir:
            lines.append(f"{prefix}  📁 {child.name}")
            lines.append(tree_to_dict(child, indent + 1))
        else:
            lines.append(f"{prefix}  📄 {child.name}")

    return "\n".join(lines)
