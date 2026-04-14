"""
数据模型模块
定义 FileNode / VirtualFileNode / OperationLog / ErrorEntry 等数据结构
"""

import hashlib
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional, List, Dict, Any

from .常量定义 import ReparseType, NodeAction, NodeStatus
from .工具函数 import _win_path


# ═══════════════════════════════════════════════════════════
# FileNode - 文件节点
# ═══════════════════════════════════════════════════════════

@dataclass
class FileNode:
    """
    文件节点数据结构
    表示扫描到的文件或目录的完整信息
    """
    # 基本属性
    full_path: str
    file_name: str
    extension: str
    size_bytes: int = 0
    depth: int = 0
    last_modified: float = 0.0
    
    # 文件类型属性
    reparse_type: ReparseType = ReparseType.NONE
    is_hidden: bool = False
    is_readonly: bool = False
    is_system: bool = False
    hard_link_count: int = 1
    is_dir: bool = False
    
    # 附加属性
    has_ads: bool = False
    has_zone_identifier: bool = False
    
    # 操作属性
    target_path: str = ""
    action: NodeAction = NodeAction.NONE
    status: NodeStatus = NodeStatus.PENDING
    error_msg: str = ""
    reason: str = ""
    
    # 缓存哈希
    _quick_hash: Optional[str] = field(default=None, repr=False)
    _full_hash: Optional[str] = field(default=None, repr=False)
    
    def quick_hash(self) -> str:
        """
        计算或返回快速哈希值
        用于快速比较文件相似性
        
        Returns:
            快速哈希值
        """
        if self._quick_hash is not None:
            return self._quick_hash
        
        if self.is_dir:
            self._quick_hash = ""
            return ""
        
        try:
            file_size = self.size_bytes
            sample_size = 65536
            hasher = hashlib.md5()
            
            path = _win_path(self.full_path) if hasattr(os, 'name') and os.name == 'nt' else self.full_path
            with open(path, "rb") as f:
                # 读取文件头
                hasher.update(f.read(min(sample_size, file_size)))
                
                # 如果文件足够大，读取中间和尾部
                if file_size > sample_size * 2:
                    f.seek(file_size // 2)
                    hasher.update(f.read(sample_size))
                    f.seek(-sample_size, 2)
                    hasher.update(f.read(sample_size))
                
                # 加入文件大小信息
                hasher.update(str(file_size).encode())
            
            self._quick_hash = hasher.hexdigest()
            return self._quick_hash
        except (OSError, IOError):
            self._quick_hash = ""
            return ""
    
    def full_hash(self) -> str:
        """
        计算或返回完整哈希值
        用于精确比较文件内容
        
        Returns:
            完整哈希值
        """
        if self._full_hash is not None:
            return self._full_hash
        
        if self.is_dir:
            self._full_hash = ""
            return ""
        
        try:
            hasher = hashlib.md5()
            path = _win_path(self.full_path) if hasattr(os, 'name') and os.name == 'nt' else self.full_path
            with open(path, "rb") as f:
                while chunk := f.read(8192):
                    hasher.update(chunk)
            self._full_hash = hasher.hexdigest()
            return self._full_hash
        except (OSError, IOError):
            self._full_hash = ""
            return ""
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        result = asdict(self)
        result['reparse_type'] = self.reparse_type.value
        result['action'] = self.action.value
        result['status'] = self.status.value
        return result


# ═══════════════════════════════════════════════════════════
# VirtualFileNode - 虚拟文件节点
# ═══════════════════════════════════════════════════════════

@dataclass
class VirtualFileNode:
    """
    虚拟文件节点
    用于离线分析模式，从文件列表解析得到
    """
    full_path: str
    file_name: str
    extension: str
    size_bytes: int = 0
    depth: int = 0
    is_dir: bool = False
    
    # 操作属性
    target_path: str = ""
    action: NodeAction = NodeAction.NONE
    reason: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'full_path': self.full_path,
            'file_name': self.file_name,
            'extension': self.extension,
            'size_bytes': self.size_bytes,
            'depth': self.depth,
            'is_dir': self.is_dir,
            'target_path': self.target_path,
            'action': self.action.value,
            'reason': self.reason,
        }


# ═══════════════════════════════════════════════════════════
# OperationLog - 操作日志
# ═══════════════════════════════════════════════════════════

@dataclass
class OperationLog:
    """
    操作日志记录
    记录每次文件操作的详细信息
    """
    seq: int
    action: str
    source: str
    target: str
    timestamp: str
    status: str = "SUCCESS"
    size_bytes: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return asdict(self)


# ═══════════════════════════════════════════════════════════
# ErrorEntry - 错误条目
# ═══════════════════════════════════════════════════════════

@dataclass
class ErrorEntry:
    """
    错误条目
    记录操作过程中发生的错误
    """
    path: str
    error_type: str
    message: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return asdict(self)


# ═══════════════════════════════════════════════════════════
# DuplicateGroup - 重复文件组
# ═══════════════════════════════════════════════════════════

@dataclass
class DuplicateGroup:
    """
    重复文件组
    记录一组重复文件的信息
    """
    group_id: int
    size_bytes: int
    file_hash: str
    keep_path: str
    delete_paths: List[str]
    action: str = "DELETE"  # DELETE 或 HARDLINK
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'group_id': self.group_id,
            'size': self.size_bytes,
            'hash': self.file_hash,
            'keep': {'path': self.keep_path},
            'delete': [{'path': p} for p in self.delete_paths],
            'action': self.action,
        }
