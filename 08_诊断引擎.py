"""
诊断引擎模块
=============
职责：目录扫描、信息收集、统计分析

对外接口：
    - DiagnosticEngine  诊断引擎类

依赖关系：
    - 数据模型 (FileNode)
    - 常量定义 (枚举、文件属性常量)
    - 工具函数 (_win_path, _fmt_size)
    - 安全检查 (ErrorCollector)
    - Win32底层IO (Win32IO)
    - 区域标识处理 (ZoneIdentifierHandler)
    - 配置管理 (runtime, make_progress)

使用示例：
    from 诊断引擎 import DiagnosticEngine
    engine = DiagnosticEngine("/path/to/scan")
    nodes = engine.scan()
    stats = engine.get_stats()
    engine.export_json("report.json")

版本历史：
    9.0: 模块化重构、类型注解完善
    8.0: 初始模块化版本
"""

from __future__ import annotations

import bisect
import csv
import fnmatch
import json
import os
import stat
import time
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Dict, Optional, Set, Any, Tuple

from .常量定义 import (
    IS_WINDOWS,
    VERSION,
    LOG_DIR,
    TRASH_DIR,
    FILE_ATTRIBUTE_HIDDEN,
    FILE_ATTRIBUTE_READONLY,
    FILE_ATTRIBUTE_SYSTEM,
    FILE_ATTRIBUTE_REPARSE,
    IO_REPARSE_TAG_SYMLINK,
    IO_REPARSE_TAG_MOUNT_POINT,
    MAX_PATH_LEGACY,
    ReparseType,
    NodeAction,
)
from .数据模型 import FileNode, ScanResult
from .工具函数 import _win_path, _fmt_size
from .安全检查 import ErrorCollector
from .配置管理 import runtime, make_progress


# ═══════════════════════════════════════════════════════════════════
# DiagnosticEngine - 诊断引擎
# ═══════════════════════════════════════════════════════════════════

class DiagnosticEngine:
    """
    诊断引擎
    
    负责扫描目录结构、收集文件信息、生成统计报告。
    
    主要功能：
    - 递归目录扫描
    - 文件属性检测（隐藏、只读、系统等）
    - Reparse Point 识别（符号链接、Junction、硬链接）
    - Zone.Identifier 检测
    - 统计分析与导出
    
    使用示例：
        >>> engine = DiagnosticEngine("D:/Projects")
        >>> engine.scan(detect_zone=True)
        >>> stats = engine.get_stats()
        >>> print(f"发现 {stats['total_files']} 个文件")
    """
    
    def __init__(
        self,
        root_path: str,
        exclude_patterns: Optional[List[str]] = None,
        exclude_paths: Optional[Set[str]] = None,
    ):
        """
        初始化诊断引擎
        
        Args:
            root_path: 根目录路径
            exclude_patterns: 排除的文件名模式列表
            exclude_paths: 排除的路径集合
        """
        self.root_path = os.path.abspath(root_path)
        self.exclude_patterns: List[str] = exclude_patterns or []
        self.exclude_paths: Set[str] = exclude_paths or set()
        
        # 扫描结果
        self.nodes: List[FileNode] = []
        self._scan_timestamp: float = 0
        self._errors = ErrorCollector()
        
        # 统计缓存
        self._stats_cache: Optional[Dict] = None
    
    @property
    def files(self) -> List[FileNode]:
        """获取所有文件节点"""
        return [n for n in self.nodes if not n.is_dir]
    
    @property
    def dirs(self) -> List[FileNode]:
        """获取所有目录节点"""
        return [n for n in self.nodes if n.is_dir]
    
    def scan(
        self,
        detect_zone: bool = False,
        verbose: bool = False,
        config: Optional[Dict] = None,
    ) -> List[FileNode]:
        """
        扫描目录
        
        Args:
            detect_zone: 是否检测 Zone.Identifier
            verbose: 是否详细输出
            config: 配置字典
            
        Returns:
            文件节点列表
        """
        self._scan_timestamp = time.time()
        self.nodes = []
        self._errors.clear()
        
        # 获取根目录深度
        root_depth = self.root_path.count(os.sep)
        
        if verbose:
            print(f"\n📂 扫描目录: {self.root_path}")
        
        # 内部目录始终排除
        internal_dirs = {LOG_DIR.lower(), TRASH_DIR.lower()}
        
        # 遍历目录
        for dirpath, dirnames, filenames in os.walk(self.root_path, topdown=True):
            # 过滤排除的目录（包括内部目录）
            dirnames[:] = [
                d for d in dirnames
                if d.lower() not in internal_dirs
                and os.path.normpath(os.path.join(dirpath, d)) not in self.exclude_paths
            ]
            
            # 处理目录
            for dirname in dirnames:
                path = os.path.join(dirpath, dirname)
                depth = path.count(os.sep) - root_depth
                
                if self._should_exclude(path, dirname):
                    continue
                
                node = self._create_node(path, depth, is_dir=True)
                self.nodes.append(node)
            
            # 处理文件
            for filename in filenames:
                path = os.path.join(dirpath, filename)
                depth = path.count(os.sep) - root_depth
                
                if self._should_exclude(path, filename):
                    continue
                
                node = self._create_node(path, depth, is_dir=False)
                self.nodes.append(node)
        
        # Zone.Identifier 检测
        if detect_zone and IS_WINDOWS:
            self._detect_zone_identifiers()
        
        # 计算统计
        self._stats_cache = None
        
        if verbose:
            self._print_scan_summary()
        
        return self.nodes
    
    def _should_exclude(self, path: str, name: str) -> bool:
        """
        检查是否应排除
        
        Args:
            path: 完整路径
            name: 文件/目录名
            
        Returns:
            是否应排除
        """
        # 检查排除模式
        for pattern in self.exclude_patterns:
            if fnmatch.fnmatch(name.lower(), pattern.lower()):
                return True
        
        # 检查排除路径
        if path in self.exclude_paths:
            return True
        
        return False
    
    def _create_node(self, path: str, depth: int, is_dir: bool) -> FileNode:
        """
        创建文件节点
        
        Args:
            path: 文件路径
            depth: 深度
            is_dir: 是否目录
            
        Returns:
            FileNode 实例
        """
        # 获取文件状态
        if IS_WINDOWS:
            st = self._safe_stat(path)
        else:
            try:
                st = os.lstat(path)
            except OSError:
                st = None
        
        # 基本信息
        name = os.path.basename(path) if path != self.root_path else os.path.basename(self.root_path)
        ext = "" if is_dir else os.path.splitext(name)[1].lower()
        
        # Reparse 类型
        reparse_type = ReparseType.NONE
        hard_link_count = 1
        
        if st:
            # 检测符号链接
            if stat.S_ISLNK(st.st_mode):
                reparse_type = ReparseType.SYMLINK
            # 检测硬链接
            elif not is_dir and st.st_nlink > 1:
                reparse_type = ReparseType.HARDLINK
                hard_link_count = st.st_nlink
        
        # Windows 文件属性
        is_hidden = False
        is_readonly = False
        is_system = False
        
        if IS_WINDOWS:
            from .Win32底层IO import Win32IO
            
            attrs = Win32IO.get_file_attributes(path)
            if attrs is not None:
                is_hidden = bool(attrs & FILE_ATTRIBUTE_HIDDEN)
                is_readonly = bool(attrs & FILE_ATTRIBUTE_READONLY)
                is_system = bool(attrs & FILE_ATTRIBUTE_SYSTEM)
                
                # 检测 Reparse Point
                if attrs & FILE_ATTRIBUTE_REPARSE and reparse_type == ReparseType.NONE:
                    tag = Win32IO.get_reparse_tag(path)
                    if tag == IO_REPARSE_TAG_SYMLINK:
                        reparse_type = ReparseType.SYMLINK
                    elif tag == IO_REPARSE_TAG_MOUNT_POINT:
                        reparse_type = ReparseType.JUNCTION
                    elif tag == 0xA0000001:
                        reparse_type = ReparseType.HARDLINK
            
            # 从权限位推断只读
            if st and not is_readonly:
                is_readonly = not bool(st.st_mode & stat.S_IWRITE)
        
        return FileNode(
            full_path=path,
            file_name=name,
            extension=ext,
            size_bytes=st.st_size if st and not is_dir else 0,
            depth=depth,
            last_modified=st.st_mtime if st else 0.0,
            reparse_type=reparse_type,
            is_hidden=is_hidden,
            is_readonly=is_readonly,
            is_system=is_system,
            hard_link_count=hard_link_count,
            is_dir=is_dir,
        )
    
    def _safe_stat(self, path: str):
        """
        安全获取文件状态
        
        Args:
            path: 文件路径
            
        Returns:
            stat_result 或 None
        """
        from .Win32底层IO import Win32IO
        return Win32IO.safe_stat(path)
    
    def _detect_zone_identifiers(self) -> None:
        """检测 Zone.Identifier"""
        file_nodes = self.files
        if not file_nodes:
            return
        
        zone_count = [0]
        lock = threading.Lock()
        
        def check_zone(node: FileNode) -> None:
            try:
                check_path = _win_path(node.full_path) if os.path.exists(node.full_path) else node.full_path
                
                from .区域标识处理 import ZoneIdentifierHandler
                if ZoneIdentifierHandler.has_zone_identifier(check_path):
                    with lock:
                        node.has_zone_identifier = True
                        zone_count[0] += 1
            except (OSError, IOError):
                pass
        
        workers = min(runtime.max_workers, 16)
        
        with ThreadPoolExecutor(max_workers=workers) as executor:
            list(executor.map(
                check_zone,
                make_progress(file_nodes, desc="Zone检测", unit="项", disable=not runtime.verbose)
            ))
        
        if zone_count[0] > 0:
            print(f"  🌐 发现 {zone_count[0]} 个 Zone.Identifier 文件")
    
    def _print_scan_summary(self) -> None:
        """打印扫描摘要"""
        stats = self.get_stats()
        
        print(f"  📄 文件: {stats['total_files']:,}")
        print(f"  📁 目录: {stats['total_dirs']:,}")
        print(f"  💾 大小: {stats['total_size_str']}")
        
        if stats['hidden_count'] > 0:
            print(f"  👁️ 隐藏: {stats['hidden_count']}")
        if stats['readonly_count'] > 0:
            print(f"  🔒 只读: {stats['readonly_count']}")
        if stats['symlink_count'] > 0:
            print(f"  🔗 符号链接: {stats['symlink_count']}")
        if stats['junction_count'] > 0:
            print(f"  🔀 Junction: {stats['junction_count']}")
        if stats['hardlink_count'] > 0:
            print(f"  🔗 硬链接: {stats['hardlink_count']}")
        if stats['zone_count'] > 0:
            print(f"  🌐 Zone.Identifier: {stats['zone_count']}")
        if stats['long_path_count'] > 0:
            print(f"  ⚠️ 超长路径: {stats['long_path_count']}")
    
    def get_stats(self, force: bool = False) -> Dict[str, Any]:
        """
        获取统计信息
        
        Args:
            force: 是否强制重新计算
            
        Returns:
            统计字典
        """
        if self._stats_cache is not None and not force:
            return self._stats_cache
        
        files = self.files
        dirs = self.dirs
        
        # 基本统计
        total_size = sum(n.size_bytes for n in files)
        max_depth = max((n.depth for n in self.nodes), default=0)
        
        # Reparse 统计
        symlink_count = sum(1 for n in self.nodes if n.reparse_type == ReparseType.SYMLINK)
        junction_count = sum(1 for n in self.nodes if n.reparse_type == ReparseType.JUNCTION)
        hardlink_count = sum(1 for n in files if n.hard_link_count > 1)
        
        # 属性统计
        hidden_count = sum(1 for n in self.nodes if n.is_hidden)
        readonly_count = sum(1 for n in files if n.is_readonly)
        system_count = sum(1 for n in self.nodes if n.is_system)
        zone_count = sum(1 for n in files if n.has_zone_identifier)
        
        # 扩展名统计
        ext_counter = Counter(n.extension for n in files)
        ext_stats = dict(ext_counter.most_common(20))
        
        # 深度统计
        depth_counter = Counter(n.depth for n in self.nodes)
        depth_stats = dict(sorted(depth_counter.items()))
        
        # 大小分布
        size_ranges = self._compute_size_distribution(files)
        
        # 超长路径
        long_paths = [
            (n.full_path, len(n.full_path.encode("utf-8")))
            for n in self.nodes
            if len(n.full_path.encode("utf-8")) > MAX_PATH_LEGACY
        ]
        
        # 最深文件
        deepest = sorted(files, key=lambda n: n.depth, reverse=True)[:10]
        
        # 最大文件
        largest = sorted(files, key=lambda n: n.size_bytes, reverse=True)[:10]
        
        self._stats_cache = {
            "root_path": self.root_path,
            "total_files": len(files),
            "total_dirs": len(dirs),
            "total_items": len(self.nodes),
            "total_size": total_size,
            "total_size_str": _fmt_size(total_size),
            "max_depth": max_depth,
            # Reparse
            "symlink_count": symlink_count,
            "junction_count": junction_count,
            "hardlink_count": hardlink_count,
            # 属性
            "hidden_count": hidden_count,
            "readonly_count": readonly_count,
            "system_count": system_count,
            "zone_count": zone_count,
            # 分布
            "extension_stats": ext_stats,
            "depth_stats": depth_stats,
            "size_distribution": size_ranges,
            # 特殊
            "long_path_count": len(long_paths),
            "long_paths": long_paths[:5],
            "deepest_files": [(n.full_path, n.depth) for n in deepest],
            "largest_files": [(n.full_path, _fmt_size(n.size_bytes)) for n in largest],
        }
        
        return self._stats_cache
    
    def _compute_size_distribution(self, files: List[FileNode]) -> Dict[str, int]:
        """计算大小分布"""
        ranges = {
            "< 1KB": 0,
            "1KB - 1MB": 0,
            "1MB - 100MB": 0,
            "100MB - 1GB": 0,
            "> 1GB": 0,
        }
        
        for n in files:
            size = n.size_bytes
            if size < 1024:
                ranges["< 1KB"] += 1
            elif size < 1024 * 1024:
                ranges["1KB - 1MB"] += 1
            elif size < 100 * 1024 * 1024:
                ranges["1MB - 100MB"] += 1
            elif size < 1024 * 1024 * 1024:
                ranges["100MB - 1GB"] += 1
            else:
                ranges["> 1GB"] += 1
        
        return ranges
    
    def to_scan_result(self) -> ScanResult:
        """
        转换为 ScanResult
        
        Returns:
            ScanResult 实例
        """
        stats = self.get_stats()
        
        return ScanResult(
            root_path=self.root_path,
            total_files=stats["total_files"],
            total_dirs=stats["total_dirs"],
            total_size=stats["total_size"],
            max_depth=stats["max_depth"],
            nodes=self.nodes,
            timestamp=datetime.fromtimestamp(self._scan_timestamp, tz=timezone.utc).isoformat(),
        )
    
    def export_csv(self, output_path: str) -> bool:
        """
        导出为 CSV
        
        Args:
            output_path: 输出文件路径
            
        Returns:
            是否成功
        """
        try:
            with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "路径", "文件名", "扩展名", "大小(字节)", "大小",
                    "深度", "是否目录", "隐藏", "只读", "系统",
                    "Zone", "操作", "原因"
                ])
                
                for n in self.nodes:
                    writer.writerow([
                        n.full_path,
                        n.file_name,
                        n.extension,
                        n.size_bytes,
                        _fmt_size(n.size_bytes),
                        n.depth,
                        "是" if n.is_dir else "否",
                        "是" if n.is_hidden else "否",
                        "是" if n.is_readonly else "否",
                        "是" if n.is_system else "否",
                        "是" if n.has_zone_identifier else "否",
                        n.action.value,
                        n.reason,
                    ])
            
            return True
        
        except OSError as e:
            print(f"❌ 导出 CSV 失败: {e}")
            return False
    
    def export_json(self, output_path: str) -> bool:
        """
        导出为 JSON
        
        Args:
            output_path: 输出文件路径
            
        Returns:
            是否成功
        """
        try:
            data = {
                "version": VERSION,
                "root_path": self.root_path,
                "scan_time": datetime.fromtimestamp(
                    self._scan_timestamp, tz=timezone.utc
                ).isoformat(),
                "stats": self.get_stats(),
                "nodes": [n.to_dict(include_hashes=False) for n in self.nodes],
            }
            
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            return True
        
        except OSError as e:
            print(f"❌ 导出 JSON 失败: {e}")
            return False
    
    def find_duplicates_by_name(self) -> Dict[str, List[FileNode]]:
        """
        按文件名查找重复文件
        
        Returns:
            文件名 -> 文件节点列表 的映射
        """
        name_groups: Dict[str, List[FileNode]] = defaultdict(list)
        
        for n in self.files:
            name_lower = n.file_name.lower()
            name_groups[name_lower].append(n)
        
        return {k: v for k, v in name_groups.items() if len(v) > 1}
    
    def find_large_dirs(self, top_n: int = 10) -> List[Tuple[str, int]]:
        """
        查找最大目录
        
        Args:
            top_n: 返回数量
            
        Returns:
            (目录路径, 大小) 列表
        """
        dir_sizes: Dict[str, int] = defaultdict(int)
        
        for n in self.files:
            parent = os.path.dirname(n.full_path)
            dir_sizes[parent] += n.size_bytes
        
        return sorted(dir_sizes.items(), key=lambda x: x[1], reverse=True)[:top_n]
