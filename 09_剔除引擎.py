"""
剔除引擎模块
=============
职责：缓存清理、垃圾文件识别、重复文件检测与去重

对外接口：
    - EliminationEngine  剔除引擎类

依赖关系：
    - 数据模型 (FileNode, DuplicateGroup)
    - 常量定义 (枚举、模式列表)
    - 工具函数 (_fmt_size)
    - 配置管理 (runtime, make_progress)

使用示例：
    from 剔除引擎 import EliminationEngine
    engine = EliminationEngine(nodes)
    engine.mark_cache_dirs()
    engine.mark_junk_files()
    engine.deduplicate("hardlink")
    groups = engine.get_duplicate_groups()

版本历史：
    9.0: 多线程哈希计算、类型注解完善
    8.0: 初始模块化版本
"""

from __future__ import annotations

import bisect
import fnmatch
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import List, Dict, Set, Optional, Any, Tuple

from .常量定义 import (
    CACHE_DIR_PATTERNS,
    JUNK_FILE_PATTERNS,
    EXTENSION_GROUPS,
    NodeAction,
    DedupStrategy,
)
from .数据模型 import FileNode, DuplicateGroup
from .工具函数 import _fmt_size
from .配置管理 import runtime, make_progress


# ═══════════════════════════════════════════════════════════════════
# EliminationEngine - 剔除引擎
# ═══════════════════════════════════════════════════════════════════

class EliminationEngine:
    """
    剔除引擎
    
    负责识别和标记需要删除的文件：
    - 缓存目录识别与清理
    - 垃圾文件识别
    - 重复文件检测（多级哈希）
    
    使用示例：
        >>> engine = EliminationEngine(nodes)
        >>> cache_marked = engine.mark_cache_dirs()
        >>> junk_marked = engine.mark_junk_files()
        >>> dup_marked = engine.deduplicate("shallowest")
        >>> print(f"共标记 {cache_marked + junk_marked + dup_marked} 项")
    """
    
    def __init__(
        self,
        nodes: List[FileNode],
        config: Optional[Dict] = None,
    ):
        """
        初始化剔除引擎
        
        Args:
            nodes: 文件节点列表
            config: 配置字典
        """
        self.nodes = nodes
        self.config = config or {}
        
        # 缓存模式
        self.cache_patterns = (
            self.config.get("cache_patterns") or CACHE_DIR_PATTERNS
        )
        self.junk_patterns = (
            self.config.get("junk_patterns") or JUNK_FILE_PATTERNS
        )
        
        # 去重结果
        self._duplicate_groups: List[DuplicateGroup] = []
        
        # 统计
        self._stats = {
            "cache_marked": 0,
            "junk_marked": 0,
            "dup_marked": 0,
        }
    
    # ═══════════════════════════════════════════════════════════════
    # 缓存目录清理
    # ═══════════════════════════════════════════════════════════════
    
    def mark_cache_dirs(
        self,
        patterns: Optional[List[str]] = None,
    ) -> int:
        """
        标记缓存目录
        
        标记匹配模式的目录及其所有子项为删除。
        
        Args:
            patterns: 缓存模式列表
            
        Returns:
            标记的文件数量
        """
        patterns = patterns or self.cache_patterns
        marked = 0
        
        # 找出缓存目录根节点
        cache_roots: List[FileNode] = []
        
        for node in self.nodes:
            if node.action != NodeAction.NONE:
                continue
            
            if not node.is_dir:
                continue
            
            for pattern in patterns:
                if fnmatch.fnmatch(node.file_name.lower(), pattern.lower()):
                    cache_roots.append(node)
                    node.action = NodeAction.DELETE
                    node.reason = f"缓存目录: {pattern}"
                    marked += 1
                    break
        
        # 标记缓存目录下的所有子项
        for root in cache_roots:
            prefix = root.full_path.lower() + os.sep
            sorted_nodes = sorted(self.nodes, key=lambda n: n.full_path.lower())
            
            # 二分查找定位
            lo = bisect.bisect_left(
                sorted_nodes, prefix,
                key=lambda n: n.full_path.lower()
            )
            
            for j in range(lo, len(sorted_nodes)):
                node = sorted_nodes[j]
                if not node.full_path.lower().startswith(prefix):
                    break
                
                if node.action == NodeAction.NONE:
                    node.action = NodeAction.DELETE
                    node.reason = f"缓存子树: {root.file_name}"
                    marked += 1
        
        self._stats["cache_marked"] = marked
        return marked
    
    def mark_junk_files(
        self,
        patterns: Optional[List[str]] = None,
    ) -> int:
        """
        标记垃圾文件
        
        标记匹配模式的文件为删除。
        
        Args:
            patterns: 垃圾文件模式列表
            
        Returns:
            标记的文件数量
        """
        patterns = patterns or self.junk_patterns
        marked = 0
        
        for node in self.nodes:
            if node.action != NodeAction.NONE:
                continue
            
            if node.is_dir:
                continue
            
            for pattern in patterns:
                if fnmatch.fnmatch(node.file_name.lower(), pattern.lower()):
                    node.action = NodeAction.DELETE
                    node.reason = f"垃圾文件: {pattern}"
                    marked += 1
                    break
        
        self._stats["junk_marked"] = marked
        return marked
    
    # ═══════════════════════════════════════════════════════════════
    # 重复文件检测
    # ═══════════════════════════════════════════════════════════════
    
    def deduplicate(
        self,
        strategy: str = "shallowest",
    ) -> int:
        """
        重复文件检测与标记
        
        使用多级哈希策略：
        1. 按大小分组
        2. 快速哈希（前 64KB）
        3. 完整哈希（SHA-256）
        
        Args:
            strategy: 去重策略 (shallowest/newest/largest/hardlink)
            
        Returns:
            标记的文件数量
        """
        strategy_enum = DedupStrategy.from_string(strategy)
        files = [n for n in self.nodes if not n.is_dir and n.action == NodeAction.NONE]
        
        # 第一级：按大小分组
        size_groups: Dict[int, List[FileNode]] = defaultdict(list)
        for f in files:
            size_groups[f.size_bytes].append(f)
        
        # 筛选有重复的大小组
        candidates = [
            (size, group)
            for size, group in size_groups.items()
            if len(group) >= 2
        ]
        
        if not candidates:
            return 0
        
        total_candidates = sum(len(g) for _, g in candidates)
        print(f"  🔍 并行去重: {total_candidates} 个候选 "
              f"({len(candidates)} 大小区, 策略: {strategy})")
        
        # 收集所有候选文件
        all_candidates = [f for _, g in candidates for f in g]
        
        # 第二级：快速哈希（多线程）
        self._compute_quick_hashes(all_candidates)
        
        # 按快速哈希分组
        need_full_hash: List[List[FileNode]] = []
        
        for _, group in candidates:
            qh_groups: Dict[str, List[FileNode]] = defaultdict(list)
            
            for f in group:
                qh = f._quick_hash or ""
                if qh:
                    qh_groups[qh].append(f)
            
            for qh, qgroup in qh_groups.items():
                if len(qgroup) >= 2:
                    need_full_hash.append(qgroup)
        
        if not need_full_hash:
            return 0
        
        # 第三级：完整哈希（多线程）
        flat_list = [f for g in need_full_hash for f in g]
        self._compute_full_hashes(flat_list)
        
        # 第四级：精确匹配并标记
        marked = self._process_duplicate_groups(need_full_hash, strategy_enum)
        
        self._stats["dup_marked"] = marked
        return marked
    
    def _compute_quick_hashes(self, nodes: List[FileNode]) -> None:
        """
        并行计算快速哈希
        
        Args:
            nodes: 文件节点列表
        """
        def compute_hash(node: FileNode) -> None:
            _ = node.quick_hash  # 触发计算
        
        workers = runtime.max_workers
        
        with ThreadPoolExecutor(max_workers=workers) as executor:
            list(executor.map(
                compute_hash,
                make_progress(nodes, desc="QuickHash", unit="项", disable=not runtime.verbose)
            ))
    
    def _compute_full_hashes(self, nodes: List[FileNode]) -> None:
        """
        并行计算完整哈希
        
        Args:
            nodes: 文件节点列表
        """
        def compute_hash(node: FileNode) -> None:
            _ = node.full_hash  # 触发计算
        
        workers = runtime.max_workers
        
        with ThreadPoolExecutor(max_workers=workers) as executor:
            list(executor.map(
                compute_hash,
                make_progress(nodes, desc="FullHash", unit="项", disable=not runtime.verbose)
            ))
    
    def _process_duplicate_groups(
        self,
        quick_groups: List[List[FileNode]],
        strategy: DedupStrategy,
    ) -> int:
        """
        处理重复文件组
        
        Args:
            quick_groups: 快速哈希分组
            strategy: 去重策略
            
        Returns:
            标记的文件数量
        """
        marked = 0
        self._duplicate_groups = []
        
        for qgroup in make_progress(
            quick_groups, desc="去重分组", unit="组", disable=not runtime.verbose
        ):
            # 按完整哈希分组
            fh_groups: Dict[str, List[FileNode]] = defaultdict(list)
            
            for f in qgroup:
                fh = f._full_hash or ""
                if fh:
                    fh_groups[fh].append(f)
            
            for fh, fgroup in fh_groups.items():
                if len(fgroup) < 2:
                    continue
                
                # 选择保留文件
                keep = self._select_keep(fgroup, strategy)
                
                # 创建重复文件组
                group = DuplicateGroup(
                    group_id=len(self._duplicate_groups) + 1,
                    size_bytes=fgroup[0].size_bytes,
                    file_hash=fh,
                    keep_path=keep.full_path,
                    action="HARDLINK" if strategy == DedupStrategy.HARDLINK else "DELETE",
                )
                
                # 标记其他文件
                for f in fgroup:
                    if f.full_path == keep.full_path:
                        continue
                    
                    if strategy == DedupStrategy.HARDLINK:
                        f.action = NodeAction.HARDLINK
                        f.target_path = keep.full_path
                        f.reason = f"硬链接到: {keep.file_name}"
                    else:
                        f.action = NodeAction.DELETE
                        f.reason = f"重复文件: {keep.file_name}"
                    
                    group.delete_paths.append(f.full_path)
                    marked += 1
                
                self._duplicate_groups.append(group)
        
        print(f"  📦 发现 {len(self._duplicate_groups)} 组重复文件, "
              f"标记 {marked} 项")
        
        return marked
    
    def _select_keep(
        self,
        files: List[FileNode],
        strategy: DedupStrategy,
    ) -> FileNode:
        """
        选择保留的文件
        
        Args:
            files: 文件列表
            strategy: 选择策略
            
        Returns:
            保留的文件节点
        """
        if strategy == DedupStrategy.SHALLOWEST:
            # 保留层级最浅的
            return min(files, key=lambda f: f.depth)
        
        elif strategy == DedupStrategy.NEWEST:
            # 保留最新修改的
            return max(files, key=lambda f: f.last_modified)
        
        elif strategy == DedupStrategy.LARGEST:
            # 保留最大的
            return max(files, key=lambda f: f.size_bytes)
        
        elif strategy == DedupStrategy.HARDLINK:
            # 硬链接策略：保留层级最浅的
            return min(files, key=lambda f: f.depth)
        
        else:
            return files[0]
    
    # ═══════════════════════════════════════════════════════════════
    # 结果查询
    # ═══════════════════════════════════════════════════════════════
    
    def get_duplicate_groups(self) -> List[DuplicateGroup]:
        """获取重复文件组"""
        return self._duplicate_groups
    
    def get_stats(self) -> Dict[str, Any]:
        """
        获取统计信息
        
        Returns:
            统计字典
        """
        delete_nodes = [n for n in self.nodes if n.action == NodeAction.DELETE]
        hardlink_nodes = [n for n in self.nodes if n.action == NodeAction.HARDLINK]
        
        delete_size = sum(n.size_bytes for n in delete_nodes)
        hardlink_size = sum(n.size_bytes for n in hardlink_nodes)
        
        return {
            "cache_marked": self._stats["cache_marked"],
            "junk_marked": self._stats["junk_marked"],
            "dup_marked": self._stats["dup_marked"],
            "total_marked": self._stats["cache_marked"] + 
                           self._stats["junk_marked"] + 
                           self._stats["dup_marked"],
            "delete_count": len(delete_nodes),
            "delete_size": delete_size,
            "delete_size_str": _fmt_size(delete_size),
            "hardlink_count": len(hardlink_nodes),
            "hardlink_size": hardlink_size,
            "hardlink_size_str": _fmt_size(hardlink_size),
            "total_freed": delete_size + hardlink_size,
            "total_freed_str": _fmt_size(delete_size + hardlink_size),
            "duplicate_groups": len(self._duplicate_groups),
        }
    
    def run(
        self,
        cache_patterns: Optional[List[str]] = None,
        junk_patterns: Optional[List[str]] = None,
        dedup_strategy: str = "shallowest",
    ) -> Dict[str, Any]:
        """
        运行完整的剔除流程
        
        Args:
            cache_patterns: 缓存模式
            junk_patterns: 垃圾模式
            dedup_strategy: 去重策略
            
        Returns:
            统计结果
        """
        print("\n🗑️ 剔除处理:")
        
        cache_marked = self.mark_cache_dirs(cache_patterns)
        junk_marked = self.mark_junk_files(junk_patterns)
        dup_marked = self.deduplicate(dedup_strategy)
        
        stats = self.get_stats()
        
        print(f"\n📊 剔除统计:")
        print(f"  缓存目录: {cache_marked} 项")
        print(f"  垃圾文件: {junk_marked} 项")
        print(f"  重复文件: {dup_marked} 项")
        
        if stats["delete_size_str"]:
            print(f"  💾 删除: {stats['delete_size_str']}")
        
        if stats["hardlink_size_str"]:
            print(f"  🔗 硬链接: {stats['hardlink_size_str']}")
        
        return stats
