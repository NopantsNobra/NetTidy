"""
剔除引擎模块
EliminationEngine - 缓存清理与重复文件检测
"""

import bisect
import fnmatch
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Optional

from .常量定义 import (
    CACHE_DIR_PATTERNS, JUNK_FILE_PATTERNS, NodeAction,
)
from .数据模型 import FileNode
from .配置管理 import runtime, make_progress


# ═══════════════════════════════════════════════════════════
# EliminationEngine 类
# ═══════════════════════════════════════════════════════════

class EliminationEngine:
    """
    剔除引擎
    负责识别和标记需要删除的文件，包括：
    - 缓存目录识别
    - 垃圾文件识别
    - 重复文件检测
    """

    def __init__(self, nodes: List[FileNode], config: dict = None):
        """
        初始化剔除引擎

        Args:
            nodes: 文件节点列表
            config: 配置字典
        """
        self.nodes = nodes
        self.config = config or {}
        self._dup_groups: List[Dict] = []

    def mark_cache_dirs(self, patterns: List[str] = None) -> int:
        """
        标记缓存目录

        Args:
            patterns: 缓存目录模式列表

        Returns:
            标记的文件数量
        """
        patterns = patterns or self.config.get("cache_patterns", CACHE_DIR_PATTERNS)
        marked = 0

        # 找出缓存目录根节点
        cache_roots = []
        for n in self.nodes:
            if n.action != NodeAction.NONE:
                continue
            for p in patterns:
                if fnmatch.fnmatch(n.file_name.lower(), p.lower()):
                    cache_roots.append(n)
                    n.action = NodeAction.DELETE
                    n.reason = f"缓存目录: {p}"
                    marked += 1
                    break

        # 标记缓存目录下的所有子项
        for root in cache_roots:
            if not root.is_dir:
                continue

            prefix = root.full_path.lower() + os.sep
            sorted_nodes = sorted(self.nodes, key=lambda x: x.full_path.lower())

            # 使用二分查找定位子项
            lo = bisect.bisect_left(
                sorted_nodes, prefix,
                key=lambda x: x.full_path.lower()
            )

            for j in range(lo, len(sorted_nodes)):
                if sorted_nodes[j].full_path.lower().startswith(prefix):
                    if sorted_nodes[j].action == NodeAction.NONE:
                        sorted_nodes[j].action = NodeAction.DELETE
                        sorted_nodes[j].reason = f"缓存子树: {root.file_name}"
                        marked += 1
                else:
                    break

        return marked

    def mark_junk_files(self, patterns: List[str] = None) -> int:
        """
        标记垃圾文件

        Args:
            patterns: 垃圾文件模式列表

        Returns:
            标记的文件数量
        """
        patterns = patterns or self.config.get("junk_patterns", JUNK_FILE_PATTERNS)
        marked = 0

        for n in self.nodes:
            if n.action != NodeAction.NONE or n.is_dir:
                continue

            for p in patterns:
                if fnmatch.fnmatch(n.file_name.lower(), p.lower()):
                    n.action = NodeAction.DELETE
                    n.reason = f"临时文件: {p}"
                    marked += 1
                    break

        return marked

    def deduplicate(self, strategy: str = "shallowest") -> int:
        """
        重复文件检测与标记
        V8.0 支持 shallowest / newest / largest / hardlink 策略

        Args:
            strategy: 去重策略

        Returns:
            标记的文件数量
        """
        files = [n for n in self.nodes if not n.is_dir and n.action == NodeAction.NONE]

        # 按大小分组
        size_groups: Dict[int, list] = defaultdict(list)
        for f in files:
            size_groups[f.size_bytes].append(f)

        # 筛选有重复的大小组
        candidates = [(s, g) for s, g in size_groups.items() if len(g) >= 2]

        if not candidates:
            return 0

        total_candidates = sum(len(g) for _, g in candidates)
        print(f"  🔍 并行去重: {total_candidates} 个候选 ({len(candidates)} 大小区, 策略: {strategy})")

        # ========== 修复5：使用多线程并行计算快速哈希 ==========
        all_candidates = [f for _, g in candidates for f in g]

        def compute_quick_hash_wrapper(node: FileNode) -> None:
            """包装函数用于多线程执行"""
            node.quick_hash()

        with ThreadPoolExecutor(max_workers=runtime.max_workers) as executor:
            list(executor.map(
                compute_quick_hash_wrapper,
                make_progress(all_candidates, desc="QuickHash", unit="项")
            ))
        # ========== 修复5结束 ==========

        # 按快速哈希分组
        need_full_hash = []
        for _, g in candidates:
            qh_groups: Dict[str, list] = defaultdict(list)
            for f in g:
                qh = f._quick_hash or ""
                if qh:
                    qh_groups[qh].append(f)

            for qh, qg in qh_groups.items():
                if len(qg) >= 2:
                    need_full_hash.append(qg)

        if not need_full_hash:
            return 0

        # ========== 修复5：使用多线程并行计算完整哈希 ==========
        need_flat = [f for g in need_full_hash for f in g]

        def compute_full_hash_wrapper(node: FileNode) -> None:
            """包装函数用于多线程执行"""
            node.full_hash()

        with ThreadPoolExecutor(max_workers=runtime.max_workers) as executor:
            list(executor.map(
                compute_full_hash_wrapper,
                make_progress(need_flat, desc="FullHash", unit="项")
            ))
        # ========== 修复5结束 ==========

        # 按完整哈希分组并标记
        marked = 0
        self._dup_groups = []

        for qg in make_progress(need_full_hash, desc="去重分组", unit="组"):
            fh_groups: Dict[str, list] = defaultdict(list)
            for f in qg:
                fh = f._full_hash or ""
                if fh:
                    fh_groups[fh].append(f)

            for fh, fg in fh_groups.items():
                if len(fg) < 2:
                    continue

                # 选择保留文件
                keep = self._select_keep(fg, strategy)

                # 标记删除或硬链接
                group_info = {
                    "group_id": len(self._dup_groups) + 1,
                    "size": fg[0].size_bytes,
                    "hash": fh,
                    "keep": {"path": keep.full_path, "depth": keep.depth},
                    "delete": [],
                    "action": "HARDLINK" if strategy == "hardlink" else "DELETE",
                }

                for f in fg:
                    if f.full_path != keep.full_path:
                        if strategy == "hardlink":
                            f.action = NodeAction.HARDLINK
                            f.target_path = keep.full_path
                            f.reason = f"硬链接到: {keep.file_name}"
                        else:
                            f.action = NodeAction.DELETE
                            f.reason = f"重复文件: {keep.file_name}"

                        group_info["delete"].append({
                            "path": f.full_path,
                            "depth": f.depth,
                            "action": f.action.value
                        })
                        marked += 1

                self._dup_groups.append(group_info)

        print(f"  发现 {len(self._dup_groups)} 组重复文件, 标记 {marked} 项")
        return marked

    def _select_keep(self, files: List[FileNode], strategy: str) -> FileNode:
        """
        选择保留的文件

        Args:
            files: 文件列表
            strategy: 选择策略

        Returns:
            保留的文件节点
        """
        if strategy == "shallowest":
            # 保留层级最浅的文件
            return min(files, key=lambda f: f.depth)
        elif strategy == "newest":
            # 保留最新修改的文件
            return max(files, key=lambda f: f.last_modified)
        elif strategy == "largest":
            # 保留最大的文件
            return max(files, key=lambda f: f.size_bytes)
        elif strategy == "hardlink":
            # 硬链接策略：保留层级最浅的
            return min(files, key=lambda f: f.depth)
        else:
            # 默认保留层级最浅的
            return min(files, key=lambda f: f.depth)

    def get_duplicate_groups(self) -> List[Dict]:
        """
        获取重复文件组列表

        Returns:
            重复文件组列表
        """
        return self._dup_groups

    def get_duplicate_preview(self) -> List[Dict]:
        """
        获取重复文件预览（别名，保持兼容性）

        Returns:
            重复文件组列表
        """
        return self.get_duplicate_groups()

    def get_stats(self) -> Dict:
        """
        获取统计信息

        Returns:
            统计字典
        """
        delete_count = sum(1 for n in self.nodes if n.action == NodeAction.DELETE)
        hardlink_count = sum(1 for n in self.nodes if n.action == NodeAction.HARDLINK)
        delete_size = sum(n.size_bytes for n in self.nodes if n.action == NodeAction.DELETE)
        hardlink_size = sum(n.size_bytes for n in self.nodes if n.action == NodeAction.HARDLINK)

        return {
            "delete_count": delete_count,
            "hardlink_count": hardlink_count,
            "delete_size": delete_size,
            "hardlink_size": hardlink_size,
            "delete_size_mb": round(delete_size / (1024 * 1024), 2),
            "hardlink_size_mb": round(hardlink_size / (1024 * 1024), 2),
            "total_freed_mb": round((delete_size + hardlink_size) / (1024 * 1024), 2),
            "duplicate_groups": len(self._dup_groups),
        }

    def run(self, cache_patterns: List[str] = None, junk_patterns: List[str] = None,
            dedup_strategy: str = "shallowest") -> Dict:
        """
        运行完整的剔除流程

        Args:
            cache_patterns: 缓存目录模式
            junk_patterns: 垃圾文件模式
            dedup_strategy: 去重策略

        Returns:
            统计结果
        """
        cache_marked = self.mark_cache_dirs(cache_patterns)
        junk_marked = self.mark_junk_files(junk_patterns)
        dup_marked = self.deduplicate(dedup_strategy)

        stats = self.get_stats()

        print(f"\n🗑️ 剔除统计:")
        print(f"  缓存目录: {cache_marked} 项")
        print(f"  垃圾文件: {junk_marked} 项")
        print(f"  重复文件: {dup_marked} 项")

        if stats["delete_size_mb"] > 0:
            print(f"  💾 可释放空间: {stats['delete_size_mb']} MB")

        if stats["hardlink_size_mb"] > 0:
            print(f"  🔗 硬链接节省: {stats['hardlink_size_mb']} MB")

        return stats