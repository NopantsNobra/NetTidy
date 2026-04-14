"""
工具函数模块
纯工具函数（路径、文件名、格式化）
仅依赖常量定义模块
"""

import hashlib
import os
from typing import Set, Optional
from .常量定义 import (
    ILLEGAL_CHARS, WINDOWS_RESERVED, MAX_PATH_EXTENDED, IS_WINDOWS,
    ReparseType, PermissionAction,
)


# ═══════════════════════════════════════════════════════════
# 路径处理函数
# ═══════════════════════════════════════════════════════════

def _win_path(path: str) -> str:
    """
    转换为 \\?\ 长路径格式
    用于支持 Windows 长路径（超过 260 字符）
    
    Args:
        path: 原始路径
        
    Returns:
        转换后的长路径格式
    """
    if not IS_WINDOWS:
        return path
    path = os.path.abspath(path)
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


def _short_path(path: str) -> str:
    """
    从 \\?\ 格式还原为普通路径
    
    Args:
        path: 长路径格式路径
        
    Returns:
        普通路径格式
    """
    if path.startswith("\\\\?\\UNC\\"):
        return "\\" + path[7:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


# ═══════════════════════════════════════════════════════════
# 文件名处理函数
# ═══════════════════════════════════════════════════════════

def _sanitize_filename(name: str) -> str:
    """
    清理文件名中的非法字符
    
    Args:
        name: 原始文件名
        
    Returns:
        清理后的安全文件名
    """
    clean = "".join(c if c not in ILLEGAL_CHARS else "_" for c in name)
    clean = clean.strip(" .")
    if not clean:
        clean = "_unnamed"
    if os.path.splitext(clean)[0].upper() in WINDOWS_RESERVED:
        clean = f"_{clean}"
    return clean


def _truncate_name(name: str, max_bytes: int) -> str:
    """
    截断文件名以符合字节限制
    保留扩展名
    
    Args:
        name: 原始文件名
        max_bytes: 最大字节数
        
    Returns:
        截断后的文件名
    """
    encoded = name.encode("utf-8")
    if len(encoded) <= max_bytes:
        return name
    # 保留扩展名
    base, ext = os.path.splitext(name)
    ext_bytes = len(ext.encode("utf-8"))
    available = max_bytes - ext_bytes - 3  # 留出省略号空间
    if available < 1:
        return name[:max_bytes // 3] + "..."
    # 截断基础名
    truncated = base
    while len(truncated.encode("utf-8")) > available:
        truncated = truncated[:-1]
    return truncated + "..." + ext


# ═══════════════════════════════════════════════════════════
# 格式化函数
# ═══════════════════════════════════════════════════════════

def _fmt_size(size_bytes: int) -> str:
    """
    格式化文件大小为可读字符串
    
    Args:
        size_bytes: 字节数
        
    Returns:
        格式化后的大小字符串
    """
    if size_bytes < 0:
        return "未知"
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def _node_attrs_str(node) -> str:
    """
    生成节点属性标记字符串
    
    Args:
        node: FileNode 对象
        
    Returns:
        属性标记字符串
    """
    attrs = []
    if node.is_hidden:
        attrs.append("H")
    if node.is_readonly:
        attrs.append("R")
    if node.is_system:
        attrs.append("S")
    if node.reparse_type != ReparseType.NONE:
        attrs.append(node.reparse_type.value[0])
    if node.has_ads:
        attrs.append("A")
    if node.has_zone_identifier:
        attrs.append("Z")
    return "".join(attrs)


def _parse_actions(action_str: Optional[str]) -> Set[PermissionAction]:
    """
    解析权限操作字符串
    
    Args:
        action_str: 逗号分隔的操作字符串
        
    Returns:
        PermissionAction 集合
    """
    if not action_str:
        return set()
    result = set()
    for part in action_str.split(","):
        part = part.strip().lower()
        if part == "all":
            result.add(PermissionAction.ALL)
        elif part == "readonly":
            result.add(PermissionAction.REMOVE_READONLY)
        elif part == "hidden":
            result.add(PermissionAction.REMOVE_HIDDEN)
        elif part == "zone":
            result.add(PermissionAction.CLEAR_ZONE_IDENTIFIER)
        elif part == "full":
            result.add(PermissionAction.GRANT_FULL_CONTROL)
    return result


# ═══════════════════════════════════════════════════════════
# 哈希计算函数
# ═══════════════════════════════════════════════════════════

def _compute_quick_hash(file_path: str, sample_size: int = 65536) -> str:
    """
    计算文件快速哈希（采样哈希）
    用于快速比较文件相似性
    
    Args:
        file_path: 文件路径
        sample_size: 采样大小（字节）
        
    Returns:
        快速哈希值
    """
    try:
        file_size = os.path.getsize(file_path)
        hasher = hashlib.md5()
        
        with open(file_path, "rb") as f:
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
        
        return hasher.hexdigest()
    except (OSError, IOError):
        return ""


def _compute_full_hash(file_path: str) -> str:
    """
    计算文件完整哈希
    用于精确比较文件内容
    
    Args:
        file_path: 文件路径
        
    Returns:
        完整哈希值
    """
    try:
        hasher = hashlib.md5()
        with open(file_path, "rb") as f:
            while chunk := f.read(8192):
                hasher.update(chunk)
        return hasher.hexdigest()
    except (OSError, IOError):
        return ""
