"""
数据模型模块
=============
职责：定义所有数据结构（Data Classes）

对外接口：
    - FileNode           文件节点数据类
    - VirtualFileNode    虚拟文件节点（离线分析用）
    - OperationLog       操作日志数据类
    - ErrorEntry         错误条目数据类
    - DuplicateGroup     重复文件组数据类

依赖关系：
    - 常量定义 (枚举)
    - 工具函数 (路径处理)

使用示例：
    from 数据模型 import FileNode, OperationLog
    node = FileNode(full_path="C:/test.txt", ...)
    log = OperationLog(seq=1, action="DELETE", ...)

版本历史：
    10.0: 最终审计版本，修正逻辑错误与文档一致性
    9.0: 完整类型注解、属性验证、数据类优化
    8.0: 初始模块化版本
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Set, Tuple

from .常量定义 import (
    NodeAction,
    NodeStatus,
    ReparseType,
    VERSION,
)
from .工具函数 import _win_path, _fmt_timestamp


# ═══════════════════════════════════════════════════════════════════
# FileNode - 文件节点
# ═══════════════════════════════════════════════════════════════════

@dataclass
class FileNode:
    """
    文件节点数据类
    
    表示扫描到的文件或目录的完整信息。
    包含文件属性、操作状态、哈希缓存等。
    
    属性:
        full_path: 完整路径
        file_name: 文件名
        extension: 扩展名（小写，带点）
        size_bytes: 文件大小（字节）
        depth: 路径深度（根目录为 0）
        last_modified: 最后修改时间戳
        reparse_type: 重解析点类型
        is_hidden: 是否隐藏
        is_readonly: 是否只读
        is_system: 是否系统文件
        hard_link_count: 硬链接数
        is_dir: 是否目录
        has_zone_identifier: 是否有 Zone.Identifier
        action: 分配的操作
        status: 处理状态
        target_path: 目标路径（移动/重命名时）
        reason: 操作原因
        error_msg: 错误信息
        
    使用示例:
        >>> node = FileNode(full_path="/test/file.txt", file_name="file.txt", extension=".txt")
        >>> node.action = NodeAction.DELETE
        >>> node.reason = "重复文件"
    """
    
    # 必需属性
    full_path: str
    file_name: str
    extension: str
    
    # 可选属性（带默认值）
    size_bytes: int = 0
    depth: int = 0
    last_modified: float = 0.0
    reparse_type: ReparseType = ReparseType.NONE
    is_hidden: bool = False
    is_readonly: bool = False
    is_system: bool = False
    hard_link_count: int = 1
    is_dir: bool = False
    has_zone_identifier: bool = False
    action: NodeAction = NodeAction.NONE
    status: NodeStatus = NodeStatus.PENDING
    target_path: str = ""
    reason: str = ""
    error_msg: str = ""
    
    # 内部缓存
    _quick_hash: Optional[str] = field(default=None, repr=False)
    _full_hash: Optional[str] = field(default=None, repr=False)
    
    @property
    def quick_hash(self) -> str:
        """
        计算或返回快速哈希值
        
        快速哈希采样文件头、中间、尾部各 64KB，
        用于快速比较文件相似性。
        
        Returns:
            MD5 哈希字符串，失败返回空字符串
        """
        if self._quick_hash is not None:
            return self._quick_hash
        
        if self.is_dir:
            return ""
        
        try:
            path = _win_path(self.full_path) if os.path.exists(self.full_path) else self.full_path
            hasher = hashlib.md5()
            file_size = self.size_bytes
            sample_size = min(65536, file_size)
            
            with open(path, "rb") as f:
                hasher.update(f.read(sample_size))
                
                if file_size > sample_size * 2:
                    f.seek(file_size // 2)
                    hasher.update(f.read(sample_size))
                    f.seek(-sample_size, 2)
                    hasher.update(f.read(sample_size))
                
                hasher.update(str(file_size).encode())
            
            self._quick_hash = hasher.hexdigest()
            return self._quick_hash
        
        except (OSError, IOError):
            self._quick_hash = ""
            return ""
    
    @property
    def full_hash(self) -> str:
        """
        计算或返回完整哈希值
        
        计算整个文件的 SHA-256 哈希，
        用于精确比较文件内容。
        
        Returns:
            SHA-256 哈希字符串，失败返回空字符串
        """
        if self._full_hash is not None:
            return self._full_hash
        
        if self.is_dir:
            return ""
        
        try:
            path = _win_path(self.full_path) if os.path.exists(self.full_path) else self.full_path
            hasher = hashlib.sha256()
            
            with open(path, "rb") as f:
                while chunk := f.read(8192):
                    hasher.update(chunk)
            
            self._full_hash = hasher.hexdigest()
            return self._full_hash
        
        except (OSError, IOError):
            self._full_hash = ""
            return ""
    
    def invalidate_hashes(self) -> None:
        """
        使哈希缓存失效
        
        当文件内容可能发生变化时调用
        """
        self._quick_hash = None
        self._full_hash = None
    
    @property
    def attrs_str(self) -> str:
        """
        获取属性标记字符串
        
        Returns:
            属性标记，如 "HRSZ" 表示隐藏、只读、系统、Zone
            
        Example:
            >>> node = FileNode(full_path="test", is_hidden=True, is_readonly=True)
            >>> node.attrs_str
            'HR'
        """
        parts = []
        
        if self.is_hidden:
            parts.append("H")
        if self.is_readonly:
            parts.append("R")
        if self.is_system:
            parts.append("S")
        if self.has_zone_identifier:
            parts.append("Z")
        if self.reparse_type != ReparseType.NONE:
            parts.append(self.reparse_type.value[0])
        
        return "".join(parts) if parts else "-"
    
    @property
    def modified_time_str(self) -> str:
        """
        获取最后修改时间的格式化字符串
        
        Returns:
            格式化的修改时间
        """
        return _fmt_timestamp(self.last_modified)
    
    @property
    def size_str(self) -> str:
        """获取格式化的文件大小字符串"""
        from .工具函数 import _fmt_size
        return _fmt_size(self.size_bytes)
    
    @property
    def is_marked(self) -> bool:
        """
        是否被标记了操作
        
        Returns:
            是否已分配非 NONE 操作
        """
        return self.action != NodeAction.NONE
    
    @property
    def is_duplicate_target(self) -> bool:
        """
        是否为重复文件的待删除目标
        
        Returns:
            是否标记为 DELETE 且原因包含"重复"
        """
        return self.action == NodeAction.DELETE and "重复" in self.reason
    
    def to_dict(self, include_hashes: bool = False) -> Dict[str, Any]:
        """
        转换为字典
        
        Args:
            include_hashes: 是否包含哈希值
            
        Returns:
            字典表示
        """
        result = {
            "full_path": self.full_path,
            "file_name": self.file_name,
            "extension": self.extension,
            "size_bytes": self.size_bytes,
            "size_str": self.size_str,
            "depth": self.depth,
            "last_modified": self.modified_time_str,
            "reparse_type": self.reparse_type.value,
            "is_hidden": self.is_hidden,
            "is_readonly": self.is_readonly,
            "is_system": self.is_system,
            "is_dir": self.is_dir,
            "has_zone_identifier": self.has_zone_identifier,
            "action": self.action.value,
            "status": self.status.value,
            "target_path": self.target_path,
            "reason": self.reason,
            "attrs": self.attrs_str,
        }
        
        if include_hashes:
            result["quick_hash"] = self.quick_hash
            result["full_hash"] = self.full_hash
        
        return result
    
    def __str__(self) -> str:
        """友好字符串表示"""
        return f"FileNode({self.file_name}, {self.action.value}, {self.size_str})"
    
    def __repr__(self) -> str:
        """调试字符串表示"""
        return (
            f"FileNode(path={self.full_path!r}, "
            f"action={self.action.value}, "
            f"status={self.status.value})"
        )


# ═══════════════════════════════════════════════════════════════════
# VirtualFileNode - 虚拟文件节点
# ═══════════════════════════════════════════════════════════════════

@dataclass
class VirtualFileNode:
    """
    虚拟文件节点
    
    用于离线分析模式，从文件列表解析得到。
    不包含实际文件操作功能，仅用于分析。
    
    属性:
        full_path: 完整路径
        file_name: 文件名
        extension: 扩展名
        size_bytes: 文件大小（可能为 0）
        depth: 路径深度
        is_dir: 是否目录
        action: 分配的操作
        reason: 操作原因
    """
    
    full_path: str
    file_name: str
    extension: str
    size_bytes: int = 0
    depth: int = 0
    is_dir: bool = False
    action: NodeAction = NodeAction.NONE
    reason: str = ""
    
    @property
    def is_marked(self) -> bool:
        """是否被标记"""
        return self.action != NodeAction.NONE
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "full_path": self.full_path,
            "file_name": self.file_name,
            "extension": self.extension,
            "size_bytes": self.size_bytes,
            "depth": self.depth,
            "is_dir": self.is_dir,
            "action": self.action.value,
            "reason": self.reason,
        }
    
    def to_filenode(self) -> FileNode:
        """
        转换为 FileNode
        
        用于离线分析与在线执行的对接
        
        Returns:
            FileNode 实例
        """
        return FileNode(
            full_path=self.full_path,
            file_name=self.file_name,
            extension=self.extension,
            size_bytes=self.size_bytes,
            depth=self.depth,
            is_dir=self.is_dir,
            action=self.action,
            reason=self.reason,
        )
    
    def __str__(self) -> str:
        return f"VirtualNode({self.file_name}, depth={self.depth})"


# ═══════════════════════════════════════════════════════════════════
# OperationLog - 操作日志
# ═══════════════════════════════════════════════════════════════════

@dataclass
class OperationLog:
    """
    操作日志记录
    
    记录每次文件操作的详细信息，用于回滚和审计。
    
    属性:
        seq: 序号
        action: 操作类型
        source: 源路径
        target: 目标路径
        timestamp: 操作时间
        status: 操作状态
        size_bytes: 文件大小
        error: 错误信息
    """
    
    seq: int
    action: str
    source: str
    target: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status: str = "SUCCESS"
    size_bytes: int = 0
    error: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "OperationLog":
        """从字典创建"""
        return cls(**d)
    
    def __str__(self) -> str:
        return f"Log[{self.seq}] {self.action} {self.source} -> {self.target} ({self.status})"


# ═══════════════════════════════════════════════════════════════════
# ErrorEntry - 错误条目
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ErrorEntry:
    """
    错误条目
    
    记录操作过程中发生的错误。
    
    属性:
        seq: 序号
        path: 相关路径
        error_type: 错误类型
        message: 错误消息
        timestamp: 错误时间
    """
    
    seq: int
    path: str
    error_type: str
    message: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ErrorEntry":
        """从字典创建"""
        return cls(**d)
    
    def __str__(self) -> str:
        return f"Error[{self.error_type}] {self.path}: {self.message}"


# ═══════════════════════════════════════════════════════════════════
# DuplicateGroup - 重复文件组
# ═══════════════════════════════════════════════════════════════════

@dataclass
class DuplicateGroup:
    """
    重复文件组
    
    记录一组重复文件的信息。
    
    属性:
        group_id: 组 ID
        size_bytes: 文件大小
        file_hash: 文件哈希
        keep_path: 保留的文件路径
        delete_paths: 待删除的文件路径列表
        action: 操作类型 (DELETE 或 HARDLINK)
    """
    
    group_id: int
    size_bytes: int
    file_hash: str
    keep_path: str
    delete_paths: List[str] = field(default_factory=list)
    action: str = "DELETE"
    
    @property
    def delete_count(self) -> int:
        """待删除文件数量"""
        return len(self.delete_paths)
    
    @property
    def save_bytes(self) -> int:
        """可节省的空间"""
        return self.size_bytes * self.delete_count
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "group_id": self.group_id,
            "size": self.size_bytes,
            "hash": self.file_hash[:16],  # 只保留前16位
            "keep": {"path": self.keep_path},
            "delete": [{"path": p} for p in self.delete_paths],
            "action": self.action,
            "save_bytes": self.save_bytes,
        }
    
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DuplicateGroup":
        """从字典创建"""
        delete_paths = [item["path"] for item in d.get("delete", [])]
        return cls(
            group_id=d["group_id"],
            size_bytes=d.get("size", 0),
            file_hash=d.get("hash", ""),
            keep_path=d.get("keep", {}).get("path", ""),
            delete_paths=delete_paths,
            action=d.get("action", "DELETE"),
        )


# ═══════════════════════════════════════════════════════════════════
# ScanResult - 扫描结果
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ScanResult:
    """
    扫描结果
    
    封装扫描的统计信息和节点列表。
    
    属性:
        root_path: 根目录路径
        total_files: 文件总数
        total_dirs: 目录总数
        total_size: 总大小（字节）
        max_depth: 最大深度
        nodes: 文件节点列表
        timestamp: 扫描时间
    """
    
    root_path: str
    total_files: int = 0
    total_dirs: int = 0
    total_size: int = 0
    max_depth: int = 0
    nodes: List[FileNode] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    engine_version: str = VERSION
    
    @property
    def total_items(self) -> int:
        """总项目数"""
        return self.total_files + self.total_dirs
    
    @property
    def stats(self) -> Dict[str, Any]:
        """获取统计字典"""
        from collections import Counter
        
        # 扩展名统计
        extensions = Counter(n.extension for n in self.nodes if not n.is_dir)
        
        # 深度统计
        depths = Counter(n.depth for n in self.nodes)
        
        # 属性统计
        hidden_count = sum(1 for n in self.nodes if n.is_hidden)
        readonly_count = sum(1 for n in self.nodes if n.is_readonly)
        
        return {
            "total_files": self.total_files,
            "total_dirs": self.total_dirs,
            "total_size": self.total_size,
            "total_size_str": self.total_size_str,
            "max_depth": self.max_depth,
            "extension_stats": dict(extensions.most_common(20)),
            "depth_stats": dict(sorted(depths.items())),
            "hidden_count": hidden_count,
            "readonly_count": readonly_count,
        }
    
    @property
    def total_size_str(self) -> str:
        """总大小格式化字符串"""
        from .工具函数 import _fmt_size
        return _fmt_size(self.total_size)
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "version": self.engine_version,
            "root_path": self.root_path,
            "timestamp": self.timestamp,
            "stats": self.stats,
            "node_count": len(self.nodes),
        }
    
    def export_nodes(self, output_path: str, format: str = "json") -> bool:
        """
        导出节点列表
        
        Args:
            output_path: 输出文件路径
            format: 格式 (json 或 csv)
            
        Returns:
            是否成功
        """
        import json
        
        try:
            if format == "json":
                data = {
                    "version": self.engine_version,
                    "root_path": self.root_path,
                    "nodes": [n.to_dict(include_hashes=False) for n in self.nodes],
                }
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            else:
                import csv
                with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        "路径", "文件名", "扩展名", "大小", "深度",
                        "是否目录", "操作", "原因"
                    ])
                    for n in self.nodes:
                        writer.writerow([
                            n.full_path, n.file_name, n.extension,
                            n.size_bytes, n.depth, n.is_dir,
                            n.action.value, n.reason
                        ])
            return True
        except (OSError, IOError):
            return False


# ═══════════════════════════════════════════════════════════════════
# 导出
# ═══════════════════════════════════════════════════════════════════

__all__ = [
    "FileNode",
    "VirtualFileNode",
    "OperationLog",
    "ErrorEntry",
    "DuplicateGroup",
    "ScanResult",
]
