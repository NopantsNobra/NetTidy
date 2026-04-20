"""核心算法引擎 — R0-R5 目录整理规则"""
import copy
from typing import Optional

from .models import TreeNode


def run_engine(
    source_tree: TreeNode,
    *,
    case_sensitive: bool = True,
) -> tuple[TreeNode, dict[str, str]]:
    """
    对源目录树运行 R0-R5 算法，返回 (目标树, 映射表)。

    Parameters
    ----------
    source_tree : TreeNode
        扫描模块产出的源目录树（不会被修改）
    case_sensitive : bool
        文件名比较是否区分大小写（Windows 上为 False）

    Returns
    -------
    result_tree : TreeNode
        整理后的目标目录树
    mapping : dict[str, str]
        源路径 → 目标路径 映射表
    """
    # 深拷贝，不修改源树；source_path 在拷贝中保留
    tree = copy.deepcopy(source_tree)
    # 为所有节点设置 source_path（等于初始 path）
    _set_source_paths(tree)
    mapping: dict[str, str] = {}

    # R0：前置清理（先于 R1，因为清理后可能使 R1 条件满足）
    _r0_clean_empty(tree)

    # R1：直链穿透（仅限根层级，终止则不再执行后续规则）
    r1_triggered = _try_r1_root(tree, mapping, case_sensitive)
    if r1_triggered:
        # R1 已结束处理，只需执行 R5
        _resolve_collisions(tree, tree, case_sensitive)
        mapping.clear()
        _collect_mapping(tree, mapping)
        return tree, mapping

    # R0-R4 递归处理
    _process_node(tree, mapping, case_sensitive, is_root=True, frozen=False)

    # R5：撞名重命名
    _resolve_collisions(tree, tree, case_sensitive)

    # 收集最终映射
    mapping.clear()
    _collect_mapping(tree, mapping)

    return tree, mapping


def _set_source_paths(node: TreeNode) -> None:
    """递归设置所有节点的 source_path = path"""
    node.source_path = node.path
    if node.is_dir:
        for child in node.children:
            _set_source_paths(child)


# ── R1：直链穿透 ────────────────────────────────────────────

def _try_r1_root(
    root: TreeNode,
    mapping: dict,
    case_sensitive: bool,
) -> bool:
    """
    R1：如果根目录恰好只有 1 个子文件夹，且它是壳，
    沿壳链一路向下直到终止点，消除所有壳，终止点内容提升到根。
    返回 True 表示 R1 触发（后续规则不再执行）。
    """
    if root.file_count != 0 or root.child_count != 1:
        return False

    shell = root.dir_children[0]
    if not shell.is_shell:
        return False

    # 沿壳链向下找到终止点
    current = shell
    while current.is_shell:
        current = current.dir_children[0]

    # 如果终止点有子目录，检查是否全是单文件叶子目录
    # 全是单文件叶子 → R1 不触发，交给 R3 的全提+撞名处理
    # 不全是（有子目录的嵌套结构）→ R1 触发，简化结构
    if current.child_count > 0:
        all_simple_leaves = all(
            d.is_single_file_dir for d in current.dir_children
        )
        if all_simple_leaves:
            return False

    # 终止点只有文件（或为空），R1 正式执行
    # 重新沿壳链向下，消除所有壳
    current = shell
    while current.is_shell:
        current = current.dir_children[0]

    # current 现在是终止点（有文件，或有 0/2+ 个子目录）
    # 终止点的所有内容提升到根
    root.children = current.children[:]
    root.is_empty = len(root.children) == 0
    # 更新提升节点的路径（相对于根）
    for child in root.children:
        _update_paths(child, root.path)
    return True


# ── R0-R4 递归处理 ──────────────────────────────────────────

def _r0_clean_empty(node: TreeNode) -> bool:
    """
    R0：前置清理 — 递归删除空子目录。
    删除空目录后，父目录可能也变空，需要反复检查直到稳定。
    返回 True 表示当前节点本身变空。
    """
    if not node.is_dir:
        return False

    changed = True
    while changed:
        changed = False
        new_children = []
        for child in node.children:
            if child.is_dir:
                # 先递归清理子目录内部
                child_empty = _r0_clean_empty(child)
                if child_empty or child.is_empty:
                    # 该子目录为空，删除它
                    changed = True
                    continue
            new_children.append(child)
        node.children = new_children

    node.is_empty = len(node.children) == 0
    return node.is_empty


def _process_node(
    node: TreeNode,
    mapping: dict,
    case_sensitive: bool,
    is_root: bool,
    frozen: bool,
) -> None:
    """递归处理单个节点，应用 R0 → R2 → R3 → R4"""
    if not node.is_dir:
        return

    # R0：前置清理 — 递归删除空子目录
    _r0_clean_empty(node)

    # R2：壳吸收（当前节点的子目录如果是壳，吸收其唯一子目录）
    _absorb_shells(node, mapping, case_sensitive)

    # R3：分叉口判定
    if node.is_branching:
        if is_root:
            # 根层级：强制全留，冻结所有直接子项，递归处理
            for child in node.dir_children:
                child.is_frozen = True
                _process_node(child, mapping, case_sensitive, is_root=False, frozen=True)
        else:
            # 非根层级：判断全提还是全留
            if _all_single_file(node.dir_children):
                # 全提：提取所有文件到当前层，子文件夹删除
                # 注意：不更新文件 path，保留 source_path 供 R5 祖先追溯
                for child in node.dir_children:
                    node.children.extend(child.children)
                    child.children.clear()
                    child.is_empty = True
                # 清除已提空的子目录
                node.children = [c for c in node.children if not (c.is_dir and c.is_empty)]
            else:
                # 全留：冻结所有子目录，递归处理
                for child in node.dir_children:
                    child.is_frozen = True
                    _process_node(child, mapping, case_sensitive, is_root=False, frozen=True)
    else:
        # 非分叉口：对所有子目录递归处理（继承 frozen 状态）
        for child in node.dir_children:
            if frozen:
                child.is_frozen = True
            _process_node(child, mapping, case_sensitive, is_root=False, frozen=frozen)

    # R4：单文件提级（仅对未冻结的子目录；根层级有文件时跳过）
    if not frozen and not (is_root and node.file_count > 0):
        promoted = []
        remaining = []
        for child in node.children:
            if child.is_dir and not child.is_frozen and child.is_single_file_dir:
                # 提取唯一文件（保留 source_path 供 R5 祖先追溯）
                file_node = child.file_children[0]
                promoted.append(file_node)
            else:
                remaining.append(child)
        node.children = remaining + promoted

    # 更新空标记
    node.is_empty = len(node.children) == 0


# ── R2：壳吸收 ─────────────────────────────────────────────

def _absorb_shells(
    node: TreeNode,
    mapping: dict,
    case_sensitive: bool,
) -> None:
    """对当前节点的每个子目录：如果是壳，反复吸收直到非壳"""
    for i, child in enumerate(node.children):
        if not child.is_dir:
            continue
        # 反复吸收：壳 = 0文件 + 1子目录
        while child.file_count == 0 and child.child_count == 1:
            inner = child.dir_children[0]
            # 将 inner 的内容移入 child
            child.children = inner.children[:]
            # 注意：不更新子节点的 path，保留 source_path 供 R5 祖先追溯
            child.is_empty = len(child.children) == 0
            # 如果吸收后不再是壳，停止
            if not (child.file_count == 0 and child.child_count == 1):
                break


# ── R5：撞名重命名 ─────────────────────────────────────────

def _resolve_collisions(node: TreeNode, root: TreeNode, case_sensitive: bool) -> None:
    """
    R5：撞名重命名。
    同一层级存在同名项时，沿来源路径向上追溯第一个不同名祖先，
    重命名为「祖先名-原名」。所有冲突方都改名。
    使用 source_path（移动前的原始路径）进行祖先追溯。
    """
    if not node.is_dir:
        return

    # 先递归处理子节点
    for child in node.children:
        if child.is_dir:
            _resolve_collisions(child, root, case_sensitive)

    # 更新当前层所有节点的 path（此时结构已稳定）
    for child in node.children:
        _update_paths(child, node.path)

    # 检查当前层同名冲突
    if case_sensitive:
        name_groups: dict[str, list[TreeNode]] = {}
        for child in node.children:
            key = child.name
            name_groups.setdefault(key, []).append(child)
    else:
        name_groups: dict[str, list[TreeNode]] = {}
        for child in node.children:
            key = child.name.lower()
            name_groups.setdefault(key, []).append(child)

    for key, group in name_groups.items():
        if len(group) <= 1:
            continue
        # 有冲突：每个冲突方都改名
        for child in group:
            # 使用 source_path 进行祖先追溯（保留了移动前的目录结构信息）
            ancestor_name = _find_ancestor_name(child, node)
            old_name = child.name
            if child.is_dir:
                new_name = f"{ancestor_name}-{old_name}"
            else:
                # 文件保留扩展名
                stem = old_name
                ext = ""
                dot_idx = old_name.rfind('.')
                if dot_idx > 0:
                    stem = old_name[:dot_idx]
                    ext = old_name[dot_idx:]
                new_name = f"{ancestor_name}-{stem}{ext}"
            child.name = new_name
            # 更新 path 以反映新名字
            _update_paths(child, node.path)


def _find_ancestor_name(node: TreeNode, parent: TreeNode) -> str:
    """
    沿节点的 source_path 向上追溯第一个与当前节点不同名的祖先。
    如果追溯到根都没有不同名的，用根目录名。
    使用 source_path（移动前的原始路径），确保能正确追溯到原始目录。
    """
    source = node.source_path or node.path
    parts = source.split('/')
    # 去掉自身名字
    ancestor_parts = parts[:-1]
    # 从最近的祖先开始找不同名的
    for part in reversed(ancestor_parts):
        if part.lower() != node.name.lower():
            return part
    # 全都同名，用根名
    return ancestor_parts[0] if ancestor_parts else "root"


def _update_paths(node: TreeNode, new_parent_path: str) -> None:
    """递归更新节点及其后代的 path（不影响 source_path）"""
    node.path = f"{new_parent_path}/{node.name}" if new_parent_path else node.name
    if node.is_dir:
        for child in node.children:
            _update_paths(child, node.path)


# ── 辅助函数 ────────────────────────────────────────────────

def _all_single_file(dirs: list[TreeNode]) -> bool:
    """判断是否所有子目录都是单文件目录（恰好1个文件、无子目录）"""
    return all(d.is_single_file_dir for d in dirs)


def _collect_mapping(node: TreeNode, mapping: dict) -> None:
    """遍历目标树，收集最终的 source_path → target_path 映射"""
    if not node.is_dir:
        # 文件节点：source_path 是原始路径，path 是目标路径
        mapping[node.source_path or node.path] = node.path
        return
    for child in node.children:
        _collect_mapping(child, mapping)


def format_tree(node: TreeNode, indent: int = 0) -> str:
    """将 TreeNode 树格式化为可读的缩进文本"""
    lines = []
    prefix = "  " * indent
    if indent == 0:
        lines.append(f"{prefix}📁 {node.name or '(root)'}")

    for child in sorted(node.children, key=lambda c: (not c.is_dir, c.name.lower())):
        if child.is_dir:
            lines.append(f"{prefix}  📁 {child.name}")
            lines.append(format_tree(child, indent + 1))
        else:
            lines.append(f"{prefix}  📄 {child.name}")

    return "\n".join(lines)
