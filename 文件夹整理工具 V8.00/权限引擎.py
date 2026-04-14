"""
权限引擎模块
PermissionEngine - 处理文件权限修复操作
"""

import os
from typing import List, Dict, Set

from .常量定义 import IS_WINDOWS, PermissionAction, NodeStatus
from .数据模型 import FileNode
from .安全检查 import ErrorCollector
from .Win32底层IO import Win32IO
from .区域标识处理 import ZoneIdentifierHandler
from .工具函数 import _win_path
from .配置管理 import make_progress


# ═══════════════════════════════════════════════════════════
# PermissionEngine 类
# ═══════════════════════════════════════════════════════════

class PermissionEngine:
    """
    权限引擎
    处理文件权限修复操作，包括：
    - 授予完全控制权限
    - 移除只读属性
    - 移除隐藏属性
    - 清除 Zone.Identifier
    """
    
    def __init__(self, nodes: List[FileNode]):
        """
        初始化权限引擎
        
        Args:
            nodes: 文件节点列表
        """
        self.nodes = nodes
        self._errors = ErrorCollector()
        self.stats = {
            "full_control": 0,
            "readonly_removed": 0,
            "hidden_removed": 0,
            "zone_cleared": 0,
            "failed": 0,
        }
    
    def grant_full_control(self, dry_run: bool = True) -> Dict[str, int]:
        """
        授予完全控制权限
        
        Args:
            dry_run: 是否为模拟运行
            
        Returns:
            统计结果
        """
        items = [n for n in self.nodes if n.is_readonly or n.is_system]
        
        if not items:
            print("\n🔓 无需授予完全控制权限")
            return self.stats
        
        print(f"\n🔓 授予完全控制权限: {len(items)} 个文件")
        
        for node in make_progress(items, desc="权限修复", unit="项"):
            if dry_run:
                continue
            
            path = node.full_path
            
            # Windows 平台使用 Win32 API
            if IS_WINDOWS:
                success = True
                
                # 移除只读属性
                if node.is_readonly:
                    if not Win32IO.remove_readonly(path):
                        success = False
                
                # 移除系统属性
                if node.is_system:
                    if not Win32IO.remove_system(path):
                        success = False
                
                if success:
                    node.is_readonly = False
                    node.is_system = False
                    node.status = NodeStatus.PERM_FIXED
                    self.stats["full_control"] += 1
                else:
                    self._errors.add(path, "PermFail", "")
                    self.stats["failed"] += 1
            else:
                # 非 Windows 平台使用 os.chmod
                try:
                    os.chmod(path, 0o777)
                    node.is_readonly = False
                    node.status = NodeStatus.PERM_FIXED
                    self.stats["full_control"] += 1
                except OSError:
                    self._errors.add(path, "PermFail", "")
                    self.stats["failed"] += 1
        
        return self.stats
    
    def remove_readonly(self, dry_run: bool = True) -> Dict[str, int]:
        """
        移除只读属性
        
        Args:
            dry_run: 是否为模拟运行
            
        Returns:
            统计结果
        """
        items = [n for n in self.nodes if n.is_readonly]
        
        if not items:
            print("\n📝 无需移除只读属性")
            return self.stats
        
        print(f"\n📝 移除只读属性: {len(items)} 个文件")
        
        for node in make_progress(items, desc="移除只读", unit="项"):
            if dry_run:
                continue
            
            path = node.full_path
            
            if IS_WINDOWS:
                if Win32IO.remove_readonly(path):
                    node.is_readonly = False
                    node.status = NodeStatus.ATTR_FIXED
                    self.stats["readonly_removed"] += 1
                else:
                    self._errors.add(path, "ReadonlyFail", "")
                    self.stats["failed"] += 1
            else:
                try:
                    current_mode = os.stat(path).st_mode
                    os.chmod(path, current_mode | 0o222)
                    node.is_readonly = False
                    node.status = NodeStatus.ATTR_FIXED
                    self.stats["readonly_removed"] += 1
                except OSError:
                    self._errors.add(path, "ReadonlyFail", "")
                    self.stats["failed"] += 1
        
        return self.stats
    
    def remove_hidden(self, dry_run: bool = True) -> Dict[str, int]:
        """
        移除隐藏属性
        
        Args:
            dry_run: 是否为模拟运行
            
        Returns:
            统计结果
        """
        items = [n for n in self.nodes if n.is_hidden]
        
        if not items:
            print("\n👁️ 无需移除隐藏属性")
            return self.stats
        
        print(f"\n👁️ 移除隐藏属性: {len(items)} 个文件")
        
        for node in make_progress(items, desc="移除隐藏", unit="项"):
            if dry_run:
                continue
            
            path = node.full_path
            
            if IS_WINDOWS:
                if Win32IO.remove_hidden(path):
                    node.is_hidden = False
                    node.status = NodeStatus.ATTR_FIXED
                    self.stats["hidden_removed"] += 1
                else:
                    self._errors.add(path, "HiddenFail", "")
                    self.stats["failed"] += 1
            else:
                # 非 Windows 平台通常没有隐藏属性概念
                node.is_hidden = False
                self.stats["hidden_removed"] += 1
        
        return self.stats
    
    def clear_zone_identifiers(self, dry_run: bool = True) -> Dict[str, int]:
        """
        清除 Zone.Identifier
        
        Args:
            dry_run: 是否为模拟运行
            
        Returns:
            统计结果
        """
        items = []
        for n in self.nodes:
            if n.has_zone_identifier:
                items.append(n)
        
        if not items:
            print("\n🌐 无需清除 Zone.Identifier")
            return self.stats
        
        print(f"\n🌐 清除 Zone.Identifier: {len(items)} 个文件")
        
        for node in make_progress(items, desc="Zone清理", unit="项"):
            if dry_run:
                continue
            
            # 使用长路径格式
            if IS_WINDOWS and Win32IO.exists(node.full_path):
                t = _win_path(node.full_path)
            else:
                t = node.full_path
            
            if ZoneIdentifierHandler.clear_zone_identifier(t):
                node.has_zone_identifier = False
                node.status = NodeStatus.ZONE_CLEARED
                self.stats["zone_cleared"] += 1
            else:
                self._errors.add(node.full_path, "ZoneFail", "")
                self.stats["failed"] += 1
        
        return self.stats
    
    def apply_all(self, actions: Set[PermissionAction], dry_run: bool = True) -> Dict[str, int]:
        """
        应用所有指定的权限操作
        
        Args:
            actions: 要执行的操作集合
            dry_run: 是否为模拟运行
            
        Returns:
            统计结果
        """
        if PermissionAction.GRANT_FULL_CONTROL in actions or PermissionAction.ALL in actions:
            self.grant_full_control(dry_run=dry_run)
        
        if PermissionAction.REMOVE_READONLY in actions or PermissionAction.ALL in actions:
            self.remove_readonly(dry_run=dry_run)
        
        if PermissionAction.REMOVE_HIDDEN in actions or PermissionAction.ALL in actions:
            self.remove_hidden(dry_run=dry_run)
        
        if PermissionAction.CLEAR_ZONE_IDENTIFIER in actions or PermissionAction.ALL in actions:
            self.clear_zone_identifiers(dry_run=dry_run)
        
        return self.stats
    
    def get_errors(self) -> List[dict]:
        """
        获取错误列表
        
        Returns:
            错误列表
        """
        return self._errors.to_list()
