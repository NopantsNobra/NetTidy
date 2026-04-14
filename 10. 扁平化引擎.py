"""
扁平化引擎模块
=============
职责：目录结构扁平化重组

对外接口：
    - FlattenEngine  扁平化引擎类

依赖关系：
    - 数据模型 (FileNode)
    - 常量定义 (枚举、扩展名分组)
    - 工具函数 (_sanitize_filename, _truncate_name, _fmt_size)
    - 配置管理 (runtime, make_progress)

使用示例：
    from 扁平化引擎 import FlattenEngine
    engine = FlattenEngine(nodes, "/target")
    engine.extract_files("/output", mode="ext")
    stats = engine.get_stats()

版本历史：
    9.0: 多种分组模式、冲突处理、类型注解完善
    8.0: 初始模块化版本
"""

from __future__ import annotations

import os
import bisect
from collections import defaultdict
from datetime import datetime
from typing import List, Dict, Optional, Set, Tuple, Any

from .常量定义 import (
    NodeAction,
    FlattenMode,
    EXTENSION_GROUPS,
    WINDOWS_RESERVED,
)
from .数据模型 import FileNode
from .工具函数 import _sanitize_filename, _truncate_name, _fmt_size


# ═══════════════════════════════════════════════════════════════════
# FlattenEngine - 扁平化引擎
# ═══════════════════════════════════════════════════════════════════

class FlattenEngine:
    """
    扁平化引擎
    
    负责将深层嵌套的目录结构重组为扁平结构：
    - 单子目录解包
    - 空目录合并
    - 文件提取与分组
    
    支持多种分组模式：
    - single: 所有文件到同一目录
    - ext: 按扩展名分组
    - date: 按修改日期分组
    - prefix: 按文件名首字母分组
    - size: 按文件大小分组
    
    使用示例：
        >>> engine = FlattenEngine(nodes, "/project")
        >>> engine.extract_files("/output", mode="ext")
        >>> print(f"提取了 {engine.get_stats()['extracted']} 个文件")
    """
    
    def __init__(
        self,
        nodes: List[FileNode],
        root_path: str,
        config: Optional[Dict] = None,
    ):
        """
        初始化扁平化引擎
        
        Args:
            nodes: 文件节点列表
            root_path: 根目录路径
            config: 配置字典
        """
        self.nodes = nodes
        self.root_path = os.path.abspath(root_path)
        self.config = config or {}
        
        # 扩展名分组映射
        self.ext_group_map = self._build_ext_map()
        
        # 统计
        self._stats = {
            "unpacked": 0,
            "merged": 0,
            "extracted": 0,
        }
    
    def _build_ext_map(self) -> Dict[str, str]:
        """构建扩展名到分组的映射"""
        ext_map: Dict[str, str] = {}
        
        # 优先使用配置
        config_map = self.config.get("ext_group_map")
        
        if config_map:
            for group_name, exts in config_map.items():
                for ext in exts:
                    ext_map[ext.lower()] = group_name.lower()
        else:
            for group_name, exts in EXTENSION_GROUPS.items():
                for ext in exts:
                    ext_map[ext.lower()] = group_name.lower()
        
        return ext_map
    
    # ═══════════════════════════════════════════════════════════════
    # 目录结构优化
    # ═══════════════════════════════════════════════════════════════
    
    def unpack_single_child_dirs(self) -> int:
        """
        解包单子目录结构
        
        识别并解包形如 A/B/C/file.txt 的冗余嵌套结构。
        
        Returns:
            解包的目录数量
        """
        unpacked = 0
        
        # 构建目录树
        dir_children: Dict[str, List[FileNode]] = defaultdict(list)
        for node in self.nodes:
            if node.is_dir:
                parent = os.path.dirname(node.full_path)
                dir_children[parent].append(node)
        
        # 迭代解包
        changed = True
        while changed:
            changed = False
            
            for parent, children in list(dir_children.items()):
                # 过滤活跃子目录
                active_children = [
                    c for c in children
                    if c.action == NodeAction.NONE and c.is_dir
                ]
                
                if len(active_children) != 1:
                    continue
                
                child = active_children[0]
                
                # 检查是否为单子目录
                grandchildren = [
                    gc for gc in dir_children.get(child.full_path, [])
                    if gc.action == NodeAction.NONE
                ]
                
                if len(grandchildren) == 1 and grandchildren[0].is_dir:
                    # 标记中间目录删除
                    child.action = NodeAction.DELETE
                    child.reason = "冗余中间目录"
                    unpacked += 1
                    changed = True
        
        self._stats["unpacked"] = unpacked
        return unpacked
    
    def merge_empty_dirs(self) -> int:
        """
        合并空目录
        
        标记没有子文件的目录为删除。
        
        Returns:
            合并的目录数量
        """
        merged = 0
        
        # 计算每个目录的子文件数
        dir_file_count: Dict[str, int] = defaultdict(int)
        for node in self.nodes:
            if not node.is_dir:
                parent = os.path.dirname(node.full_path)
                dir_file_count[parent] += 1
        
        # 标记空目录
        for node in self.nodes:
            if node.is_dir and node.action == NodeAction.NONE:
                if dir_file_count[node.full_path] == 0:
                    node.action = NodeAction.DELETE
                    node.reason = "空目录"
                    merged += 1
        
        self._stats["merged"] = merged
        return merged
    
    # ═══════════════════════════════════════════════════════════════
    # 文件提取与分组
    # ═══════════════════════════════════════════════════════════════
    
    def extract_files(
        self,
        target_dir: str,
        mode: str = "single",
        extensions: Optional[Set[str]] = None,
    ) -> int:
        """
        提取并分组文件
        
        Args:
            target_dir: 目标目录
            mode: 分组模式 (single/ext/date/prefix/size)
            extensions: 要处理的扩展名集合
            
        Returns:
            提取的文件数量
        """
        mode_enum = FlattenMode.from_string(mode)
        os.makedirs(target_dir, exist_ok=True)
        
        # 收集未处理的文件
        files = [
            n for n in self.nodes
            if not n.is_dir and n.action == NodeAction.NONE
        ]
        
        # 按扩展名过滤
        if extensions:
            ext_lower = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in extensions}
            files = [f for f in files if f.extension in ext_lower]
        
        # 按分组模式处理
        if mode_enum == FlattenMode.SINGLE:
            return self._extract_single(files, target_dir)
        elif mode_enum == FlattenMode.EXTENSION:
            return self._extract_by_ext(files, target_dir)
        elif mode_enum == FlattenMode.DATE:
            return self._extract_by_date(files, target_dir)
        elif mode_enum == FlattenMode.PREFIX:
            return self._extract_by_prefix(files, target_dir)
        elif mode_enum == FlattenMode.SIZE:
            return self._extract_by_size(files, target_dir)
        
        return 0
    
    def _extract_single(
        self,
        files: List[FileNode],
        target_dir: str,
    ) -> int:
        """提取到单个目录"""
        existing_names: Set[str] = set()
        extracted = 0
        
        for file_node in files:
            new_name = self._get_unique_name(
                target_dir, file_node.file_name, existing_names
            )
            
            file_node.target_path = os.path.join(target_dir, new_name)
            file_node.action = NodeAction.MOVE
            file_node.reason = "扁平化: 单层"
            existing_names.add(new_name)
            extracted += 1
        
        self._stats["extracted"] = extracted
        return extracted
    
    def _extract_by_ext(
        self,
        files: List[FileNode],
        target_dir: str,
    ) -> int:
        """按扩展名分组"""
        existing_names: Dict[str, Set[str]] = defaultdict(set)
        extracted = 0
        
        for file_node in files:
            # 确定分组
            ext = file_node.extension.lower()
            group = self.ext_group_map.get(ext, "other")
            
            # 创建分组目录
            group_dir = os.path.join(target_dir, group)
            os.makedirs(group_dir, exist_ok=True)
            
            # 生成唯一文件名
            new_name = self._get_unique_name(
                group_dir, file_node.file_name, existing_names[group]
            )
            
            file_node.target_path = os.path.join(group_dir, new_name)
            file_node.action = NodeAction.MOVE
            file_node.reason = f"扁平化: 按扩展名 ({group})"
            existing_names[group].add(new_name)
            extracted += 1
        
        self._stats["extracted"] = extracted
        return extracted
    
    def _extract_by_date(
        self,
        files: List[FileNode],
        target_dir: str,
    ) -> int:
        """按修改日期分组"""
        existing_names: Dict[str, Set[str]] = defaultdict(set)
        extracted = 0
        
        for file_node in files:
            # 确定分组
            try:
                dt = datetime.fromtimestamp(file_node.last_modified)
                date_str = dt.strftime("%Y-%m")
            except (OSError, ValueError):
                date_str = "unknown"
            
            # 创建分组目录
            group_dir = os.path.join(target_dir, date_str)
            os.makedirs(group_dir, exist_ok=True)
            
            # 生成唯一文件名
            new_name = self._get_unique_name(
                group_dir, file_node.file_name, existing_names[date_str]
            )
            
            file_node.target_path = os.path.join(group_dir, new_name)
            file_node.action = NodeAction.MOVE
            file_node.reason = f"扁平化: 按日期 ({date_str})"
            existing_names[date_str].add(new_name)
            extracted += 1
        
        self._stats["extracted"] = extracted
        return extracted
    
    def _extract_by_prefix(
        self,
        files: List[FileNode],
        target_dir: str,
    ) -> int:
        """按文件名首字母分组"""
        existing_names: Dict[str, Set[str]] = defaultdict(set)
        extracted = 0
        
        for file_node in files:
            # 确定分组
            first_char = file_node.file_name[0].upper() if file_node.file_name else "#"
            if not first_char.isalpha():
                first_char = "#"
            
            # 创建分组目录
            group_dir = os.path.join(target_dir, first_char)
            os.makedirs(group_dir, exist_ok=True)
            
            # 生成唯一文件名
            new_name = self._get_unique_name(
                group_dir, file_node.file_name, existing_names[first_char]
            )
            
            file_node.target_path = os.path.join(group_dir, new_name)
            file_node.action = NodeAction.MOVE
            file_node.reason = f"扁平化: 按前缀 ({first_char})"
            existing_names[first_char].add(new_name)
            extracted += 1
        
        self._stats["extracted"] = extracted
        return extracted
    
    def _extract_by_size(
        self,
        files: List[FileNode],
        target_dir: str,
    ) -> int:
        """按文件大小分组"""
        existing_names: Dict[str, Set[str]] = defaultdict(set)
        extracted = 0
        
        for file_node in files:
            # 确定分组
            size_category = self._get_size_category(file_node.size_bytes)
            
            # 创建分组目录
            group_dir = os.path.join(target_dir, size_category)
            os.makedirs(group_dir, exist_ok=True)
            
            # 生成唯一文件名
            new_name = self._get_unique_name(
                group_dir, file_node.file_name, existing_names[size_category]
            )
            
            file_node.target_path = os.path.join(group_dir, new_name)
            file_node.action = NodeAction.MOVE
            file_node.reason = f"扁平化: 按大小 ({size_category})"
            existing_names[size_category].add(new_name)
            extracted += 1
        
        self._stats["extracted"] = extracted
        return extracted
    
    def _get_size_category(self, size_bytes: int) -> str:
        """获取大小分类"""
        if size_bytes < 1024:
            return "小于1KB"
        elif size_bytes < 1024 * 1024:
            return "1KB-1MB"
        elif size_bytes < 100 * 1024 * 1024:
            return "1MB-100MB"
        elif size_bytes < 1024 * 1024 * 1024:
            return "100MB-1GB"
        else:
            return "大于1GB"
    
    def _get_unique_name(
        self,
        directory: str,
        filename: str,
        existing: Set[str],
    ) -> str:
        """
        生成唯一文件名
        
        处理文件名冲突，添加来源目录后缀。
        
        Args:
            directory: 目标目录
            filename: 原始文件名
            existing: 已存在的文件名集合
            
        Returns:
            唯一文件名
        """
        if filename not in existing:
            return filename
        
        # 分离文件名和扩展名
        base, ext = os.path.splitext(filename)
        
        # 尝试添加序号
        counter = 2
        while True:
            new_name = f"{base}_{counter}{ext}"
            if new_name not in existing:
                return new_name
            counter += 1
            
            if counter > 1000:
                # 使用 UUID
                import uuid
                uid = uuid.uuid4().hex[:8]
                return f"{base}_{uid}{ext}"
    
    # ═══════════════════════════════════════════════════════════════
    # 结果
    # ═══════════════════════════════════════════════════════════════
    
    def get_stats(self) -> Dict[str, Any]:
        """
        获取统计信息
        
        Returns:
            统计字典
        """
        return {
            "unpacked": self._stats["unpacked"],
            "merged": self._stats["merged"],
            "extracted": self._stats["extracted"],
            "total": self._stats["unpacked"] + 
                    self._stats["merged"] + 
                    self._stats["extracted"],
        }
    
    def run(
        self,
        target_dir: Optional[str] = None,
        mode: str = "single",
        unpack: bool = True,
        merge_empty: bool = True,
        extensions: Optional[Set[str]] = None,
    ) -> Dict[str, Any]:
        """
        运行完整的扁平化流程
        
        Args:
            target_dir: 目标目录
            mode: 分组模式
            unpack: 是否解包单子目录
            merge_empty: 是否合并空目录
            extensions: 处理的扩展名
            
        Returns:
            统计结果
        """
        if target_dir is None:
            target_dir = os.path.join(self.root_path, "_flattened")
        
        print(f"\n📦 扁平化处理 ({mode}):")
        
        if unpack:
            unpacked = self.unpack_single_child_dirs()
            print(f"  解包: {unpacked} 个")
        
        if merge_empty:
            merged = self.merge_empty_dirs()
            print(f"  空目录: {merged} 个")
        
        extracted = self.extract_files(target_dir, mode, extensions)
        print(f"  提取: {extracted} 个文件到 {target_dir}")
        
        return self.get_stats()
