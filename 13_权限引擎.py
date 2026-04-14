"""
权限引擎模块
=============
职责：文件权限修复、属性修改

对外接口：
    - PermissionEngine  权限引擎类

依赖关系：
    - 数据模型 (FileNode)
    - 常量定义 (枚举)
    - 安全检查 (ErrorCollector)
    - Win32底层IO (Win32IO)
    - 区域标识处理 (ZoneIdentifierHandler)
    - 配置管理 (make_progress)

使用示例：
    from 权限引擎 import PermissionEngine
    engine = PermissionEngine(nodes)
    engine.apply_all({"readonly", "hidden"}, dry_run=False)
    print(engine.stats)

版本历史：
    9.0: 简化实现、类型注解完善
    8.0: 初始模块化版本
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Set, Optional, Any

from .常量定义 import (
    IS_WINDOWS,
    PermissionAction,
    NodeStatus,
)
from .数据模型 import FileNode
from .安全检查 import ErrorCollector
from .Win32底层IO import Win32IO
from .区域标识处理 import ZoneIdentifierHandler
from .配置管理 import runtime, make_progress


# ═══════════════════════════════════════════════════════════════════
# PermissionEngine - 权限引擎
# ═══════════════════════════════════════════════════════════════════

class PermissionEngine:
    """
    权限引擎
    
    负责修复文件权限和属性：
    - 授予完全控制权限
    - 移除只读属性
    - 移除隐藏属性
    - 清除 Zone.Identifier
    
    使用示例：
        >>> engine = PermissionEngine(nodes)
        >>> engine.grant_full_control(dry_run=False)
        >>> engine.remove_readonly(dry_run=False)
        >>> print(f"修复: {engine.stats['readonly_removed']}")
    """
    
    def __init__(self, nodes: List[FileNode]):
        """
        初始化权限引擎
        
        Args:
            nodes: 文件节点列表
        """
        self.nodes = nodes
        self.errors = ErrorCollector()
        
        # 统计
        self.stats = {
            "full_control": 0,
            "readonly_removed": 0,
            "hidden_removed": 0,
            "zone_cleared": 0,
            "failed": 0,
        }
    
    def grant_full_control(
        self,
        dry_run: bool = True,
    ) -> Dict[str, int]:
        """
        授予完全控制权限
        
        移除只读和系统属性，使文件可被修改。
        
        Args:
            dry_run: 是否模拟运行
            
        Returns:
            统计字典
        """
        # 找出需要处理的文件
        items = [
            n for n in self.nodes
            if not n.is_dir and (n.is_readonly or n.is_system)
        ]
        
        if not items:
            print("\n🔓 无需授予完全控制权限")
            return self.stats
        
        print(f"\n🔓 授予完全控制权限: {len(items)} 个文件")
        
        if dry_run:
            for node in items[:10]:
                print(f"  [PERM] grant:F {node.file_name}")
            if len(items) > 10:
                print(f"  ... 还有 {len(items) - 10} 项")
            return self.stats
        
        # 执行修复
        workers = min(runtime.max_workers, 8)
        
        def fix_permission(node: FileNode) -> None:
            path = node.full_path
            
            success = True
            
            if node.is_readonly:
                if not Win32IO.remove_readonly(path):
                    success = False
            
            if node.is_system:
                if not Win32IO.remove_system(path):
                    success = False
            
            if success:
                node.is_readonly = False
                node.is_system = False
                node.status = NodeStatus.PERM_FIXED
                self.stats["full_control"] += 1
            else:
                self.errors.add(path, "PermFixFailed", "权限修复失败")
                self.stats["failed"] += 1
        
        with ThreadPoolExecutor(max_workers=workers) as executor:
            list(executor.map(
                fix_permission,
                make_progress(items, desc="权限修复", unit="项", disable=not runtime.verbose)
            ))
        
        return self.stats
    
    def remove_readonly(
        self,
        dry_run: bool = True,
    ) -> Dict[str, int]:
        """
        移除只读属性
        
        Args:
            dry_run: 是否模拟运行
            
        Returns:
            统计字典
        """
        items = [n for n in self.nodes if n.is_readonly and not n.is_dir]
        
        if not items:
            print("\n📝 无需移除只读属性")
            return self.stats
        
        print(f"\n📝 移除只读属性: {len(items)} 个文件")
        
        if dry_run:
            return self.stats
        
        workers = min(runtime.max_workers, 8)
        
        def fix_readonly(node: FileNode) -> None:
            if Win32IO.remove_readonly(node.full_path):
                node.is_readonly = False
                node.status = NodeStatus.ATTR_FIXED
                self.stats["readonly_removed"] += 1
            else:
                self.errors.add(node.full_path, "ReadonlyRemoveFailed", "移除只读失败")
                self.stats["failed"] += 1
        
        with ThreadPoolExecutor(max_workers=workers) as executor:
            list(executor.map(
                fix_readonly,
                make_progress(items, desc="移除只读", unit="项", disable=not runtime.verbose)
            ))
        
        return self.stats
    
    def remove_hidden(
        self,
        dry_run: bool = True,
    ) -> Dict[str, int]:
        """
        移除隐藏属性
        
        Args:
            dry_run: 是否模拟运行
            
        Returns:
            统计字典
        """
        items = [n for n in self.nodes if n.is_hidden and not n.is_dir]
        
        # 排除以 "." 开头的常规隐藏文件（如 .gitignore），
        # 但保留 .restructure* 前缀的内部目录相关文件
        items = [
            n for n in items
            if n.file_name.startswith(".restructure")
            or not n.file_name.startswith(".")
        ]
        
        if not items:
            print("\n👁️ 无需移除隐藏属性")
            return self.stats
        
        print(f"\n👁️ 移除隐藏属性: {len(items)} 个文件")
        
        if dry_run:
            return self.stats
        
        workers = min(runtime.max_workers, 8)
        
        def fix_hidden(node: FileNode) -> None:
            if Win32IO.remove_hidden(node.full_path):
                node.is_hidden = False
                node.status = NodeStatus.ATTR_FIXED
                self.stats["hidden_removed"] += 1
            else:
                self.errors.add(node.full_path, "HiddenRemoveFailed", "移除隐藏失败")
                self.stats["failed"] += 1
        
        with ThreadPoolExecutor(max_workers=workers) as executor:
            list(executor.map(
                fix_hidden,
                make_progress(items, desc="移除隐藏", unit="项", disable=not runtime.verbose)
            ))
        
        return self.stats
    
    def clear_zone_identifiers(
        self,
        dry_run: bool = True,
    ) -> Dict[str, int]:
        """
        清除 Zone.Identifier
        
        Args:
            dry_run: 是否模拟运行
            
        Returns:
            统计字典
        """
        items = [n for n in self.nodes if n.has_zone_identifier and not n.is_dir]
        
        if not items:
            print("\n🌐 无需清除 Zone.Identifier")
            return self.stats
        
        print(f"\n🌐 清除 Zone.Identifier: {len(items)} 个文件")
        
        if dry_run:
            return self.stats
        
        workers = min(runtime.max_workers, 8)
        
        def clear_zone(node: FileNode) -> None:
            path = _get_win_path(node.full_path)
            
            if ZoneIdentifierHandler.clear_zone_identifier(path):
                node.has_zone_identifier = False
                node.status = NodeStatus.ZONE_CLEARED
                self.stats["zone_cleared"] += 1
            else:
                self.errors.add(node.full_path, "ZoneClearFailed", "清除 Zone.Identifier 失败")
                self.stats["failed"] += 1
        
        def _get_win_path(path: str) -> str:
            from .工具函数 import _win_path
            return _win_path(path) if os.path.exists(path) else path
        
        with ThreadPoolExecutor(max_workers=workers) as executor:
            list(executor.map(
                clear_zone,
                make_progress(items, desc="Zone清理", unit="项", disable=not runtime.verbose)
            ))
        
        return self.stats
    
    def apply_all(
        self,
        actions: Set[PermissionAction],
        dry_run: bool = True,
    ) -> Dict[str, int]:
        """
        应用所有指定的权限操作
        
        Args:
            actions: 权限操作集合
            dry_run: 是否模拟运行
            
        Returns:
            统计字典
        """
        if PermissionAction.ALL in actions:
            # 全选
            self.grant_full_control(dry_run)
            self.remove_readonly(dry_run)
            self.remove_hidden(dry_run)
            self.clear_zone_identifiers(dry_run)
        
        else:
            if PermissionAction.GRANT_FULL_CONTROL in actions:
                self.grant_full_control(dry_run)
            
            if PermissionAction.REMOVE_READONLY in actions:
                self.remove_readonly(dry_run)
            
            if PermissionAction.REMOVE_HIDDEN in actions:
                self.remove_hidden(dry_run)
            
            if PermissionAction.CLEAR_ZONE_IDENTIFIER in actions:
                self.clear_zone_identifiers(dry_run)
        
        return self.stats
    
    def get_stats(self) -> Dict[str, int]:
        """获取统计"""
        return self.stats.copy()
    
    def get_errors(self) -> List[Dict]:
        """获取错误列表"""
        return self.errors.to_dict()
