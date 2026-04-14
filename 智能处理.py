# -*- coding: utf-8 -*-
"""
文件夹重组工具 V7.0 — 智能处理模块
包含：EliminationEngine、FlattenEngine、RestructureEngine
"""

import bisect
import fnmatch
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Set, Tuple

from .公共定义 import (
    _win_path, IS_WINDOWS, Win32IO,
    CACHE_DIR_PATTERNS, JUNK_FILE_PATTERNS,
    MAX_WORKERS,
)
from .数据模型 import ErrorCollector, FileNode, NodeAction, NodeStatus

# ============================================================
# 智能剔除引擎
# ============================================================

class EliminationEngine:
    def __init__(self, nodes: List[FileNode], errors: ErrorCollector):
        self.nodes = nodes
        self.errors = errors
        self._dup_groups: List[List[FileNode]] = []

    def mark_cache_dirs(self, extra_patterns: List[str] = None) -> None:
        patterns = list(CACHE_DIR_PATTERNS)
        if extra_patterns:
            patterns.extend(extra_patterns)

        # 垃圾文件标记（与离线模式一致）
        for node in self.nodes:
            if node.action != NodeAction.NONE:
                continue
            if not node.is_dir:
                for pattern in JUNK_FILE_PATTERNS:
                    if fnmatch.fnmatch(node.file_name.lower(), pattern.lower()):
                        node.action = NodeAction.DELETE
                        break

        cache_roots: List[FileNode] = []
        for node in self.nodes:
            if node.action != NodeAction.NONE:
                continue
            for pattern in patterns:
                if fnmatch.fnmatch(node.file_name.lower(), pattern.lower()):
                    cache_roots.append(node)
                    node.action = NodeAction.DELETE
                    break

        dir_roots = [n for n in cache_roots if n.is_dir]
        if dir_roots:
            sorted_nodes = sorted(self.nodes, key=lambda n: n.full_path.lower())
            for root in dir_roots:
                prefix = root.full_path.lower() + os.sep
                lo = bisect.bisect_left(sorted_nodes, prefix, key=lambda n: n.full_path.lower())
                for j in range(lo, len(sorted_nodes)):
                    if sorted_nodes[j].full_path.lower().startswith(prefix):
                        if sorted_nodes[j].action == NodeAction.NONE:
                            sorted_nodes[j].action = NodeAction.DELETE
                    else:
                        break

    def deduplicate(self, strategy: str = "shallowest") -> int:
        """
        三级哈希去重（并行优化）
        
        第一级：按文件大小分组
        第二级：前4KB SHA256 QuickHash
        第三级：完整文件 SHA256 FullHash
        
        Args:
            strategy: 保留策略 ("shallowest" 或 "newest")
        
        Returns:
            标记删除的重复文件数量
        """
        # 不再排除硬链接，而是纳入候选；在去重组中优先保留硬链接（不占额外空间）
        files = [n for n in self.nodes
                 if not n.is_dir and n.action == NodeAction.NONE]

        size_groups: Dict[int, List[FileNode]] = defaultdict(list)
        for f in files:
            size_groups[f.size_bytes].append(f)

        # 过滤出有 2+ 文件的组
        candidate_groups = [(size, group) for size, group in size_groups.items() if len(group) >= 2]

        if not candidate_groups:
            return 0

        print(f"  🔍 并行去重: {sum(len(g) for _, g in candidate_groups)} 个候选文件 "
              f"({len(candidate_groups)} 个大小组, {MAX_WORKERS} 线程)")

        # Phase 1: 并行计算所有候选文件的 QuickHash
        all_candidates = [f for _, group in candidate_groups for f in group]
        lock = threading.Lock()

        def _compute_quick_hash(node: FileNode) -> None:
            node.quick_hash()  # 结果缓存在 node._quick_hash

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(_compute_quick_hash, f) for f in all_candidates]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception:
                    pass  # 哈希计算失败会在节点内标记为空

        # Phase 2: 按 QuickHash 分组，收集需要 FullHash 的组
        need_full_hash: List[List[FileNode]] = []
        for size, group in candidate_groups:
            qhash_groups: Dict[str, List[FileNode]] = defaultdict(list)
            for f in group:
                qh = f._quick_hash or ""
                if qh:
                    qhash_groups[qh].append(f)
            for qh, qgroup in qhash_groups.items():
                if len(qgroup) >= 2:
                    need_full_hash.append(qgroup)

        if not need_full_hash:
            return 0

        # Phase 3: 并行计算需要 FullHash 的文件
        need_full_hash_flat = [f for group in need_full_hash for f in group]

        def _compute_full_hash(node: FileNode) -> None:
            node.full_hash()  # 结果缓存在 node._full_hash

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(_compute_full_hash, f) for f in need_full_hash_flat]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception:
                    pass

        # Phase 4: 按 FullHash 确定真正的重复组
        dup_count = 0
        self._dup_groups = []

        for qgroup in need_full_hash:
            fhash_groups: Dict[str, List[FileNode]] = defaultdict(list)
            for f in qgroup:
                fh = f._full_hash or ""
                if fh:
                    fhash_groups[fh].append(f)

            for fh, fgroup in fhash_groups.items():
                if len(fgroup) < 2:
                    continue

                # 优先保留硬链接（不占额外磁盘空间）
                hard_link_nodes = [f for f in fgroup if f.reparse_type == ReparseType.HARDLINK]
                if hard_link_nodes:
                    keep = hard_link_nodes[0]
                elif strategy == "shallowest":
                    keep = min(fgroup, key=lambda n: n.depth)
                else:
                    keep = max(fgroup, key=lambda n: n.last_modified)

                self._dup_groups.append(list(fgroup))

                for f in fgroup:
                    if f.full_path != keep.full_path:
                        f.action = NodeAction.DELETE
                        dup_count += 1

        print(f"  发现 {len(self._dup_groups)} 组重复文件, 标记删除 {dup_count} 个")
        return dup_count

    def get_duplicate_groups(self) -> List[Dict]:
        """获取去重组的详细信息"""
        result = []
        for i, group in enumerate(self._dup_groups, 1):
            if len(group) < 2:
                continue
            kept = None
            deleted = []
            for f in group:
                if f.action != NodeAction.DELETE:
                    kept = f
                else:
                    deleted.append(f)
            if not kept:
                kept = group[0]
                deleted = group[1:]
                kept.action = NodeAction.NONE

            result.append({
                "group_id": i,
                "size": _fmt_size(kept.size_bytes),
                "hash": kept.full_hash()[:16] if kept.full_hash() else "?",
                "keep": {"path": kept.full_path, "depth": kept.depth},
                "delete": [{"path": f.full_path, "depth": f.depth} for f in deleted],
            })
        return result


# ============================================================
# 扁平化引擎
# ============================================================

class FlattenEngine:
    """层级扁平化引擎：将深层文件提取到单层目录"""
    
    def __init__(self, nodes: List[FileNode], root_path: str,
                 errors: ErrorCollector):
        self.nodes = nodes
        self.root_path = root_path
        self.errors = errors

    def extract_files(self, target_dir: str, extensions: List[str] = None) -> int:
        """
        提取文件到目标目录
        
        Args:
            target_dir: 目标目录路径
            extensions: 要提取的文件扩展名列表（None 表示全部）
        
        Returns:
            提取的文件数量
        """
        files = [n for n in self.nodes
                 if not n.is_dir and n.action == NodeAction.NONE]

        if extensions:
            ext_set = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in extensions}
            files = [f for f in files if f.extension in ext_set]

        existing_names: Set[str] = set()
        os.makedirs(target_dir, exist_ok=True)

        for f in files:
            base, ext = os.path.splitext(f.file_name)
            source_dir = os.path.basename(os.path.dirname(f.full_path))

            target_name = f.file_name
            if target_name in existing_names:
                target_name = f"{base}_from_{source_dir}{ext}"
                counter = 2
                while target_name in existing_names:
                    target_name = f"{base}_from_{source_dir}_{counter}{ext}"
                    counter += 1

            name_no_ext = os.path.splitext(target_name)[0].upper()
            if name_no_ext in WINDOWS_RESERVED:
                target_name = f"_{target_name}"

            target_path = os.path.join(target_dir, target_name)
            path_bytes = len(target_path.encode("utf-8"))
            if path_bytes >= MAX_PATH_EXTENDED:
                safe_name = _truncate_name(target_name,
                    MAX_PATH_EXTENDED - len(target_dir.encode("utf-8")) - 2)
                target_path = os.path.join(target_dir, safe_name)

            f.target_path = target_path
            f.action = NodeAction.MOVE
            existing_names.add(target_name)

        return len(files)


# ============================================================
# 结构重组引擎
# ============================================================

class RestructureEngine:
    """结构重组引擎：解包冗余目录结构"""
    
    def __init__(self, nodes: List[FileNode], root_path: str,
                 errors: ErrorCollector):
        self.nodes = nodes
        self.root_path = root_path
        self.errors = errors

    def unpack_redundant(self) -> int:
        r"""
        智能解包：识别并合并 A\A\file.txt 冗余结构
        
        Returns:
            解包的目录数量
        """
        unpacked = 0
        dirs = [n for n in self.nodes if n.is_dir and n.action == NodeAction.NONE]

        dir_children: Dict[str, List[FileNode]] = defaultdict(list)
        for d in dirs:
            parent = os.path.dirname(d.full_path)
            dir_children[parent.lower()].append(d)

        for parent_lower, children in dir_children.items():
            if len(children) != 1:
                continue
            child = children[0]
            parent_name = os.path.basename(parent_lower) if parent_lower != self.root_path.lower() else ""
            if not parent_name or parent_name != child.file_name.lower():
                continue

            actual_parent = os.path.dirname(child.full_path)
            child_prefix = child.full_path.lower() + os.sep
            for node in self.nodes:
                if node.full_path.lower().startswith(child_prefix) and \
                   node.action == NodeAction.NONE:
                    rel = os.path.relpath(node.full_path, child.full_path)
                    node.target_path = os.path.join(actual_parent, rel)
                    node.action = NodeAction.MOVE

            child.action = NodeAction.DELETE
            unpacked += 1

        return unpacked

    def lift_children(self, target_dir: str) -> int:
        """
        单级提级：将选中目录的直属子项提升到父目录
        
        Args:
            target_dir: 要提级的目录
        
        Returns:
            提级的文件数量
        """
        lifted = 0
        parent_dir = os.path.dirname(target_dir)
        prefix = target_dir.lower() + os.sep

        for node in self.nodes:
            if node.full_path.lower().startswith(prefix) and \
               node.action == NodeAction.NONE:
                rel = os.path.relpath(node.full_path, target_dir)
                if os.sep not in rel:
                    node.target_path = os.path.join(parent_dir, rel)
                    node.action = NodeAction.MOVE
                    lifted += 1

        for node in self.nodes:
            if node.full_path.lower() == target_dir.lower():
                node.action = NodeAction.DELETE
                break

        return lifted

