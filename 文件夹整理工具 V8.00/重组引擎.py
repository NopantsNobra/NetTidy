"""
重组引擎模块
RestructureEngine - 目录结构重组
"""

import os
from collections import defaultdict
from typing import List, Dict, Optional

from .常量定义 import NodeAction, EXTENSION_GROUPS
from .数据模型 import FileNode
from .工具函数 import _sanitize_filename


# ═══════════════════════════════════════════════════════════
# RestructureEngine 类
# ═══════════════════════════════════════════════════════════

class RestructureEngine:
    """
    重组引擎
    负责重新组织目录结构，包括：
    - 按扩展名分组
    - 按类型分组
    - 按日期分组
    """
    
    def __init__(self, nodes: List[FileNode], root_path: str, config: dict = None):
        """
        初始化重组引擎
        
        Args:
            nodes: 文件节点列表
            root_path: 根目录路径
            config: 配置字典
        """
        self.nodes = nodes
        self.root_path = os.path.abspath(root_path)
        self.config = config or {}
        self._stats = {
            "grouped": 0,
            "moved": 0,
        }
    
    def group_by_extension(self, target_dir: str = None) -> int:
        """
        按扩展名分组文件
        
        Args:
            target_dir: 目标目录（默认为根目录）
            
        Returns:
            分组的文件数量
        """
        target_dir = target_dir or self.root_path
        grouped = 0
        
        for n in self.nodes:
            if n.is_dir or n.action != NodeAction.NONE:
                continue
            
            ext = n.extension or "no_extension"
            ext = ext.lstrip(".").upper() or "NO_EXT"
            
            # 创建目标路径
            ext_dir = os.path.join(target_dir, ext)
            n.target_path = os.path.join(ext_dir, n.file_name)
            n.action = NodeAction.MOVE
            n.reason = f"按扩展名分组: {ext}"
            grouped += 1
        
        self._stats["grouped"] = grouped
        return grouped
    
    def group_by_type(self, type_map: Dict[str, List[str]] = None, target_dir: str = None) -> int:
        """
        按文件类型分组
        
        Args:
            type_map: 类型映射 {类型名: [扩展名列表]}
            target_dir: 目标目录
            
        Returns:
            分组的文件数量
        """
        type_map = type_map or self.config.get("ext_group_map", EXTENSION_GROUPS)
        target_dir = target_dir or self.root_path
        grouped = 0
        
        # 构建扩展名到类型的映射
        ext_to_type = {}
        for type_name, exts in type_map.items():
            for ext in exts:
                ext_to_type[ext.lower()] = type_name
        
        for n in self.nodes:
            if n.is_dir or n.action != NodeAction.NONE:
                continue
            
            ext = n.extension.lower()
            file_type = ext_to_type.get(ext, "其他")
            
            # 创建目标路径
            type_dir = os.path.join(target_dir, file_type)
            n.target_path = os.path.join(type_dir, n.file_name)
            n.action = NodeAction.MOVE
            n.reason = f"按类型分组: {file_type}"
            grouped += 1
        
        self._stats["grouped"] = grouped
        return grouped
    
    def group_by_date(self, target_dir: str = None, format_str: str = "%Y-%m") -> int:
        """
        按日期分组文件
        
        Args:
            target_dir: 目标目录
            format_str: 日期格式字符串
            
        Returns:
            分组的文件数量
        """
        target_dir = target_dir or self.root_path
        grouped = 0
        
        from datetime import datetime
        
        for n in self.nodes:
            if n.is_dir or n.action != NodeAction.NONE:
                continue
            
            # 获取修改日期
            try:
                dt = datetime.fromtimestamp(n.last_modified)
                date_str = dt.strftime(format_str)
            except (OSError, ValueError):
                date_str = "未知日期"
            
            # 创建目标路径
            date_dir = os.path.join(target_dir, date_str)
            n.target_path = os.path.join(date_dir, n.file_name)
            n.action = NodeAction.MOVE
            n.reason = f"按日期分组: {date_str}"
            grouped += 1
        
        self._stats["grouped"] = grouped
        return grouped
    
    def group_by_size(self, target_dir: str = None) -> int:
        """
        按文件大小分组
        
        Args:
            target_dir: 目标目录
            
        Returns:
            分组的文件数量
        """
        target_dir = target_dir or self.root_path
        grouped = 0
        
        def get_size_category(size: int) -> str:
            if size < 1024:
                return "小于1KB"
            elif size < 1024 * 1024:
                return "1KB-1MB"
            elif size < 100 * 1024 * 1024:
                return "1MB-100MB"
            elif size < 1024 * 1024 * 1024:
                return "100MB-1GB"
            else:
                return "大于1GB"
        
        for n in self.nodes:
            if n.is_dir or n.action != NodeAction.NONE:
                continue
            
            size_category = get_size_category(n.size_bytes)
            
            # 创建目标路径
            size_dir = os.path.join(target_dir, size_category)
            n.target_path = os.path.join(size_dir, n.file_name)
            n.action = NodeAction.MOVE
            n.reason = f"按大小分组: {size_category}"
            grouped += 1
        
        self._stats["grouped"] = grouped
        return grouped
    
    def resolve_conflicts(self) -> int:
        """
        解决文件名冲突
        
        Returns:
            解决的冲突数量
        """
        resolved = 0
        target_paths: Dict[str, List[FileNode]] = defaultdict(list)
        
        # 收集目标路径
        for n in self.nodes:
            if n.action == NodeAction.MOVE and n.target_path:
                target_paths[n.target_path.lower()].append(n)
        
        # 解决冲突
        for path_lower, nodes in target_paths.items():
            if len(nodes) <= 1:
                continue
            
            for i, n in enumerate(nodes):
                if i == 0:
                    continue
                
                # 添加序号
                base, ext = os.path.splitext(n.target_path)
                n.target_path = f"{base}_{i+1}{ext}"
                resolved += 1
        
        return resolved
    
    def get_stats(self) -> Dict:
        """
        获取统计信息
        
        Returns:
            统计字典
        """
        return self._stats.copy()
    
    def run(self, mode: str = "extension", **kwargs) -> Dict:
        """
        运行重组流程
        
        Args:
            mode: 重组模式 (extension/type/date/size)
            **kwargs: 额外参数
            
        Returns:
            统计结果
        """
        if mode == "extension":
            grouped = self.group_by_extension(kwargs.get("target_dir"))
        elif mode == "type":
            grouped = self.group_by_type(
                kwargs.get("type_map"),
                kwargs.get("target_dir")
            )
        elif mode == "date":
            grouped = self.group_by_date(
                kwargs.get("target_dir"),
                kwargs.get("format_str", "%Y-%m")
            )
        elif mode == "size":
            grouped = self.group_by_size(kwargs.get("target_dir"))
        else:
            grouped = 0
        
        # 解决冲突
        resolved = self.resolve_conflicts()
        
        print(f"\n📦 重组统计:")
        print(f"  分组文件: {grouped} 个")
        if resolved:
            print(f"  解决冲突: {resolved} 个")
        
        return self.get_stats()
