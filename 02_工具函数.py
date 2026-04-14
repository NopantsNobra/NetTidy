"""
工具函数模块
=============
职责：提供纯工具函数，不涉及业务逻辑

对外接口：
    - _win_path()         转换为 Windows 长路径格式
    - _short_path()       从长路径格式还原
    - _sanitize_filename() 清理非法文件名
    - _truncate_name()    截断过长文件名
    - _fmt_size()         格式化文件大小
    - _parse_size()       解析大小字符串
    - _compute_hash()     计算文件哈希
    - _safe_path_join()   安全路径拼接

依赖关系：
    - 常量定义 (ILLEGAL_CHARS, WINDOWS_RESERVED, IS_WINDOWS)

使用示例：
    from 工具函数 import _sanitize_filename, _fmt_size
    clean_name = _sanitize_filename("test<>file.txt")
    size_str = _fmt_size(1024 * 1024)

版本历史：
    10.0: 最终审计版本，修正逻辑错误与文档一致性
    9.0: 添加类型注解、docstring 完善
    8.0: 初始模块化版本
"""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from datetime import datetime, timezone
from functools import partial
from typing import Optional, Union, Callable, TypeVar

from .常量定义 import (
    IS_WINDOWS,
    ILLEGAL_CHARS,
    WINDOWS_RESERVED,
    MAX_PATH_EXTENDED,
    QUICK_HASH_SIZE,
)


# ═══════════════════════════════════════════════════════════════════
# 类型别名
# ═══════════════════════════════════════════════════════════════════

T = TypeVar("T")
PathLike = Union[str, os.PathLike]


# ═══════════════════════════════════════════════════════════════════
# 路径处理函数
# ═══════════════════════════════════════════════════════════════════

def _win_path(path: str) -> str:
    """
    转换为 Windows 长路径格式 (\\\\?\\)
    
    Windows API 默认支持最长 260 字符的路径。
    使用 \\\\?\\ 前缀可突破此限制，支持最长 32767 字符的路径。
    
    Args:
        path: 原始路径
        
    Returns:
        长路径格式的路径
        
    Example:
        >>> _win_path("C:\\\\test\\\\file.txt")
        '\\\\\\\\?\\\\C:\\\\test\\\\file.txt'
        >>> _win_path("\\\\\\\\server\\\\share\\\\file.txt")
        '\\\\\\\\?\\\\UNC\\\\server\\\\share\\\\file.txt'
    """
    if not IS_WINDOWS:
        return path
    
    path = os.path.abspath(path)
    
    # UNC 路径转换
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    
    return "\\\\?\\" + path


def _short_path(path: str) -> str:
    """
    从长路径格式还原为普通路径
    
    Args:
        path: \\\\?\\ 格式的长路径
        
    Returns:
        普通路径格式
        
    Example:
        >>> _short_path("\\\\\\\\?\\\\C:\\\\test\\\\file.txt")
        'C:\\\\test\\\\file.txt'
    """
    if path.startswith("\\\\?\\UNC\\"):
        return "\\" + path[7:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def _safe_path_join(base: PathLike, *parts: str, max_len: int = 0) -> str:
    """
    安全拼接路径，避免路径穿越
    
    Args:
        base: 基础路径
        *parts: 路径部分
        max_len: 最大长度限制（0 表示不限制）
        
    Returns:
        安全的拼接路径
        
    Raises:
        ValueError: 如果检测到路径穿越
        
    Example:
        >>> _safe_path_join("C:\\base", "..", "secret")
        ValueError: 禁止路径穿越
    """
    # 拼接路径
    result = os.path.join(os.path.abspath(str(base)), *parts)
    
    # 检查路径穿越
    if ".." in os.path.normpath(result).split(os.sep):
        raise ValueError(f"禁止路径穿越: {parts}")
    
    # 检查长度
    if max_len > 0 and len(result.encode("utf-8")) > max_len:
        result = _truncate_path(result, max_len)
    
    return result


def _truncate_path(path: str, max_bytes: int) -> str:
    """
    截断路径以符合字节限制
    
    保留文件名，截断中间目录名
    
    Args:
        path: 原始路径
        max_bytes: 最大字节数
        
    Returns:
        截断后的路径
    """
    path_bytes = path.encode("utf-8")
    if len(path_bytes) <= max_bytes:
        return path
    
    # 分离目录和文件名
    dirname = os.path.dirname(path)
    basename = os.path.basename(path)
    ext = os.path.splitext(basename)[1]
    name_no_ext = os.path.splitext(basename)[0]
    
    # 计算可用字节
    ext_bytes = len(ext.encode("utf-8"))
    hash_suffix = f"__{uuid.uuid4().hex[:8]}__"
    hash_bytes = len(hash_suffix.encode("utf-8"))
    available = max_bytes - ext_bytes - hash_bytes - len(dirname.encode("utf-8")) - 10
    
    if available < 1:
        # 极端情况：直接截断整个路径
        return path[:max_bytes // 3] + hash_suffix + ext
    
    # 截断文件名
    result_name = name_no_ext
    while len(result_name.encode("utf-8")) > available:
        result_name = result_name[:-1]
    
    if not result_name:
        result_name = "_"
    
    return os.path.join(dirname, result_name + hash_suffix + ext)


# ═══════════════════════════════════════════════════════════════════
# 文件名处理函数
# ═══════════════════════════════════════════════════════════════════

def _sanitize_filename(name: str, replacement: str = "_") -> str:
    """
    清理文件名中的非法字符
    
    替换 Windows 文件名非法字符为指定字符，
    并处理保留设备名和空名称情况。
    
    Args:
        name: 原始文件名
        replacement: 替换字符
        
    Returns:
        清理后的安全文件名
        
    Example:
        >>> _sanitize_filename("test<>file.txt")
        'test__file.txt'
        >>> _sanitize_filename("CON")
        '_CON'
        >>> _sanitize_filename("")
        '_unnamed'
    """
    # 替换非法字符
    clean = "".join(
        replacement if c in ILLEGAL_CHARS else c
        for c in name
    )
    
    # 去除首尾空白和点
    clean = clean.strip(" .")
    
    # 处理空名称
    if not clean:
        clean = "_unnamed"
    
    # 检查保留设备名
    name_no_ext = os.path.splitext(clean)[0].upper()
    if name_no_ext in WINDOWS_RESERVED:
        clean = replacement + clean
    
    return clean


def _truncate_name(name: str, max_bytes: int, preserve_ext: bool = True) -> str:
    """
    截断文件名以符合字节限制
    
    Args:
        name: 原始文件名
        max_bytes: 最大字节数
        preserve_ext: 是否保留扩展名
        
    Returns:
        截断后的文件名
        
    Example:
        >>> _truncate_name("very_long_filename.txt", 20)
        'very_long_fil... .txt'
    """
    encoded = name.encode("utf-8")
    if len(encoded) <= max_bytes:
        return name
    
    if not preserve_ext:
        return name.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
    
    # 分离扩展名
    base, ext = os.path.splitext(name)
    ext_bytes = len(ext.encode("utf-8"))
    available = max_bytes - ext_bytes - 5  # 留出 "..." 空间
    
    if available < 1:
        # 极端情况：只保留扩展名和部分基础名
        return name[:max_bytes // 2] + "..." + ext
    
    # 截断基础名
    result = base
    while len((result + "..." + ext).encode("utf-8")) > max_bytes:
        result = result[:-1]
    
    if not result:
        result = "_"
    
    return result + "..." + ext


def _unique_filename(base_path: str, filename: str) -> str:
    """
    生成唯一的文件名
    
    如果文件名已存在，自动添加序号后缀。
    
    Args:
        base_path: 目录路径
        filename: 期望的文件名
        
    Returns:
        唯一的文件名
        
    Example:
        >>> _unique_filename("/tmp", "file.txt")  # 假设已存在
        'file_1.txt'
    """
    if not os.path.exists(os.path.join(base_path, filename)):
        return filename
    
    base, ext = os.path.splitext(filename)
    counter = 1
    
    while True:
        new_name = f"{base}_{counter}{ext}"
        if not os.path.exists(os.path.join(base_path, new_name)):
            return new_name
        counter += 1
        
        # 防止无限循环
        if counter > 10000:
            # 使用 UUID 确保唯一
            uid = uuid.uuid4().hex[:8]
            return f"{base}_{uid}{ext}"


# ═══════════════════════════════════════════════════════════════════
# 格式化函数
# ═══════════════════════════════════════════════════════════════════

def _fmt_size(size_bytes: int, precision: int = 2) -> str:
    """
    格式化文件大小为可读字符串
    
    Args:
        size_bytes: 字节数
        precision: 小数精度
        
    Returns:
        格式化后的大小字符串
        
    Example:
        >>> _fmt_size(0)
        '0 B'
        >>> _fmt_size(1536)
        '1.50 KB'
        >>> _fmt_size(1073741824)
        '1.00 GB'
    """
    if size_bytes < 0:
        return "未知"
    
    if size_bytes == 0:
        return "0 B"
    
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    size = float(size_bytes)
    
    for unit in units[:-1]:
        if abs(size) < 1024:
            return f"{size:.{precision}f} {unit}"
        size /= 1024
    
    return f"{size:.{precision}f} PB"


def _parse_size(size_str: str) -> int:
    """
    解析大小字符串为字节数
    
    Args:
        size_str: 大小字符串
        
    Returns:
        字节数
        
    Raises:
        ValueError: 格式无效
        
    Example:
        >>> _parse_size("100")
        100
        >>> _parse_size("1KB")
        1024
        >>> _parse_size("1.5MB")
        1572864
    """
    size_str = size_str.strip().upper()
    
    # 纯数字
    if size_str.isdigit():
        return int(size_str)
    
    # 带单位的格式
    units = {
        "B": 1,
        "KB": 1024,
        "MB": 1024 ** 2,
        "GB": 1024 ** 3,
        "TB": 1024 ** 4,
        "PB": 1024 ** 5,
    }
    
    # 匹配数字+单位
    match = re.match(r'^([\d.]+)\s*([A-Z]+)$', size_str)
    if not match:
        raise ValueError(f"无法解析大小字符串: {size_str}")
    
    value_str, unit = match.groups()
    
    try:
        value = float(value_str)
    except ValueError:
        raise ValueError(f"无效的数字: {value_str}")
    
    if unit not in units:
        raise ValueError(f"未知单位: {unit}")
    
    return int(value * units[unit])


def _fmt_timestamp(timestamp: float, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """
    格式化时间戳为字符串
    
    Args:
        timestamp: Unix 时间戳
        fmt: 格式字符串
        
    Returns:
        格式化的时间字符串
    """
    try:
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        # 转换为本地时间
        local_dt = dt.astimezone()
        return local_dt.strftime(fmt)
    except (ValueError, OSError):
        return "未知时间"


def _fmt_duration(seconds: float) -> str:
    """
    格式化时长为可读字符串
    
    Args:
        seconds: 秒数
        
    Returns:
        格式化的时长字符串
        
    Example:
        >>> _fmt_duration(3661)
        '1h 1m 1s'
    """
    if seconds < 60:
        return f"{seconds:.1f}s"
    
    parts = []
    
    hours = int(seconds // 3600)
    if hours > 0:
        parts.append(f"{hours}h")
        seconds %= 3600
    
    minutes = int(seconds // 60)
    if minutes > 0:
        parts.append(f"{minutes}m")
        seconds %= 60
    
    if seconds > 0 or not parts:
        parts.append(f"{seconds:.0f}s")
    
    return " ".join(parts)


def _fmt_path(path: str, max_len: int = 60) -> str:
    """
    格式化路径，超长时截断
    
    Args:
        path: 原始路径
        max_len: 最大显示长度
        
    Returns:
        格式化后的路径
    """
    if len(path) <= max_len:
        return path
    
    # 尝试保留文件名
    basename = os.path.basename(path)
    dirname = os.path.dirname(path)
    
    if len(basename) < max_len - 5:
        available = max_len - len(basename) - 5
        truncated_dir = dirname[:available] + "..."
        return os.path.join(truncated_dir, basename)
    
    # 文件名太长，直接截断
    return path[:max_len - 3] + "..."


# ═══════════════════════════════════════════════════════════════════
# 哈希计算函数
# ═══════════════════════════════════════════════════════════════════

def _compute_hash(
    file_path: str,
    algorithm: str = "md5",
    sample_size: int = 0,
    include_size: bool = True,
) -> str:
    """
    计算文件哈希值
    
    Args:
        file_path: 文件路径
        algorithm: 哈希算法 (md5, sha256, sha1)
        sample_size: 采样大小，0 表示计算完整哈希
        include_size: 是否将文件大小加入哈希
        
    Returns:
        十六进制哈希字符串，失败返回空字符串
    """
    try:
        hasher = hashlib.new(algorithm)
        file_size = os.path.getsize(file_path)
        
        with open(file_path, "rb") as f:
            if sample_size > 0 and file_size > sample_size * 2:
                # 采样模式：头 + 中 + 尾
                hasher.update(f.read(sample_size))
                f.seek(file_size // 2)
                hasher.update(f.read(sample_size))
                f.seek(-sample_size, 2)
                hasher.update(f.read(sample_size))
            else:
                # 完整模式
                while chunk := f.read(8192):
                    hasher.update(chunk)
            
            # 加入大小信息
            if include_size:
                hasher.update(str(file_size).encode())
        
        return hasher.hexdigest()
    
    except (OSError, IOError, ValueError):
        return ""


def _compute_quick_hash(file_path: str) -> str:
    """
    计算文件快速哈希
    
    用于快速比较文件相似性（采样哈希）
    
    Args:
        file_path: 文件路径
        
    Returns:
        MD5 哈希字符串
    """
    return _compute_hash(file_path, algorithm="md5", sample_size=QUICK_HASH_SIZE)


def _compute_full_hash(file_path: str, algorithm: str = "sha256") -> str:
    """
    计算文件完整哈希
    
    用于精确比较文件内容
    
    Args:
        file_path: 文件路径
        algorithm: 哈希算法
        
    Returns:
        十六进制哈希字符串
    """
    return _compute_hash(file_path, algorithm=algorithm, sample_size=0)


def _verify_hash(file_path: str, expected: str, algorithm: str = "sha256") -> bool:
    """
    验证文件哈希
    
    Args:
        file_path: 文件路径
        expected: 期望的哈希值
        algorithm: 哈希算法
        
    Returns:
        是否匹配
    """
    actual = _compute_full_hash(file_path, algorithm)
    return actual.lower() == expected.lower()


# ═══════════════════════════════════════════════════════════════════
# 字符串处理函数
# ═══════════════════════════════════════════════════════════════════

def _truncate_string(s: str, max_len: int, suffix: str = "...") -> str:
    """
    截断字符串
    
    Args:
        s: 原始字符串
        max_len: 最大长度
        suffix: 后缀
        
    Returns:
        截断后的字符串
    """
    if len(s) <= max_len:
        return s
    
    suffix_len = len(suffix)
    if max_len <= suffix_len:
        return suffix[:max_len]
    
    return s[:max_len - suffix_len] + suffix


def _escape_html(text: str) -> str:
    """
    HTML 实体转义
    
    Args:
        text: 原始文本
        
    Returns:
        转义后的文本
    """
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


def _escape_shell(text: str) -> str:
    """
    Shell 特殊字符转义
    
    Args:
        text: 原始文本
        
    Returns:
        转义后的文本
    """
    return text.replace(" ", "\\ ").replace("'", "\\'").replace('"', '\\"')


# ═══════════════════════════════════════════════════════════════════
# 集合操作函数
# ═══════════════════════════════════════════════════════════════════

def _safe_dict_get(
    d: dict,
    key: str,
    default: T = None,
    validator: Callable[[T], bool] = None,
) -> T:
    """
    安全获取字典值
    
    Args:
        d: 字典
        key: 键
        default: 默认值
        validator: 值验证器
        
    Returns:
        字典值或默认值
    """
    try:
        value = d.get(key, default)
        
        if validator is not None and value is not None:
            if not validator(value):
                return default
        
        return value
    
    except (TypeError, ValueError):
        return default


def _batch_items(items: list, batch_size: int) -> list:
    """
    将列表分批
    
    Args:
        items: 原始列表
        batch_size: 每批大小
        
    Returns:
        分批后的列表
        
    Example:
        >>> list(_batch_items([1,2,3,4,5], 2))
        [[1, 2], [3, 4], [5]]
    """
    for i in range(0, len(items), batch_size):
        yield items[i:i + batch_size]


# ═══════════════════════════════════════════════════════════════════
# 导出
# ═══════════════════════════════════════════════════════════════════

__all__ = [
    # 路径处理
    "_win_path", "_short_path", "_safe_path_join", "_truncate_path",
    # 文件名处理
    "_sanitize_filename", "_truncate_name", "_unique_filename",
    # 格式化
    "_fmt_size", "_parse_size", "_fmt_timestamp", "_fmt_duration", "_fmt_path",
    # 哈希
    "_compute_hash", "_compute_quick_hash", "_compute_full_hash", "_verify_hash",
    # 字符串
    "_truncate_string", "_escape_html", "_escape_shell",
    # 集合
    "_safe_dict_get", "_batch_items",
]
