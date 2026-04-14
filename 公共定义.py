# -*- coding: utf-8 -*-
"""
文件夹重组工具 V7.0 — 公共定义模块
包含：平台检测、常量、辅助函数、名称工具、Win32 IO 封装
"""

import fnmatch
import hashlib
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import ctypes
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

if sys.platform == "win32":
    import ctypes.wintypes
    try:
        import winreg
    except ImportError:
        winreg = None

# ============================================================
# 平台检测
# ============================================================

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

# ============================================================
# Win32 API 常量 (仅 Windows)
# ============================================================

if IS_WINDOWS:
    MOVEFILE_COPY_ALLOWED      = 0x0002
    MOVEFILE_REPLACE_EXISTING  = 0x0001
    MOVEFILE_WRITE_THROUGH     = 0x0008

    FILE_ATTRIBUTE_HIDDEN      = 0x0002
    FILE_ATTRIBUTE_READONLY    = 0x0001
    FILE_ATTRIBUTE_SYSTEM      = 0x0004
    FILE_ATTRIBUTE_REPARSE     = 0x0400
    FILE_ATTRIBUTE_DIRECTORY   = 0x0010

    IO_REPARSE_TAG_SYMLINK     = 0xA000000C
    IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003
else:
    # 非 Windows 平台：定义常量值以保证导入不报错（实际不会被使用）
    MOVEFILE_COPY_ALLOWED      = 0x0002
    MOVEFILE_REPLACE_EXISTING  = 0x0001
    MOVEFILE_WRITE_THROUGH     = 0x0008

    FILE_ATTRIBUTE_HIDDEN      = 0x0002
    FILE_ATTRIBUTE_READONLY    = 0x0001
    FILE_ATTRIBUTE_SYSTEM      = 0x0004
    FILE_ATTRIBUTE_REPARSE     = 0x0400
    FILE_ATTRIBUTE_DIRECTORY   = 0x0010

    IO_REPARSE_TAG_SYMLINK     = 0xA000000C
    IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003

# ============================================================
# 版本与常量
# ============================================================

VERSION = "7.0"
LOCK_FILENAME = ".restructure.lock"
LOG_DIR = ".restructure_log"
TRASH_DIR = ".restructure_trash"
MAX_LOG_FILES = 10

# 多线程配置
MAX_WORKERS = min(8, (os.cpu_count() or 4))  # 自动根据 CPU 核心数调整
HASH_BATCH_SIZE = 50  # 每批提交的哈希计算任务数
IO_BATCH_SIZE = 200   # 每批提交的 IO 操作任务数

# Windows 保留设备名
WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

# 常见缓存/临时目录名 (可由用户扩展)
CACHE_DIR_PATTERNS = [
    "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".tox", ".eggs", "*.egg-info", ".gradle", ".mvn",
    "target", "dist", ".next", ".nuxt",
    "vendor", ".terraform", ".serverless",
    ".cache", ".parcel-cache", ".webpack",
    "Thumbs.db", "desktop.ini", ".DS_Store",
    "ehthumbs.db", "System Volume Information",
    "$RECYCLE.BIN", "RECYCLER",
]

# 垃圾文件模式
JUNK_FILE_PATTERNS = [
    "*.bc!", "*.td", "*.xltd", "*.!ut", "*.part", "*.crdownload",
]

# 文件名非法字符 (NTFS)
ILLEGAL_CHARS = set('<>:"|?*\x00')

# 哈希相关
QUICK_HASH_SIZE = 4096

# Windows 长路径
MAX_PATH_LEGACY = 260
MAX_PATH_EXTENDED = 32767

# 节流间隔
THROTTLE_BATCH = 200
THROTTLE_SLEEP = 0.05

# Zone.Identifier ADS 名称
ZONE_IDENTIFIER = "Zone.Identifier"

# PowerShell 截断行正则
TRUNCATED_LINE_RE = re.compile(r'\.\.\.\s*$')


# ============================================================
# Win32 API 封装 — 底层IO层 (仅 Windows)
# ============================================================

def _win_path(path: str) -> str:
    r"""将路径转换为 Windows 长路径格式 (\\?\ )"""
    if not IS_WINDOWS:
        return path
    path = os.path.abspath(path)
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


def _short_path(path: str) -> str:
    r"""从 \\?\ 格式还原为普通路径"""
    if path.startswith("\\\\?\\UNC\\"):
        return "\\" + path[7:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


class Win32IO:
    """Win32 API 底层IO操作，完整支持长路径 (仅 Windows)"""

    _kernel32 = ctypes.windll.kernel32 if IS_WINDOWS else None

    @classmethod
    def get_file_attributes(cls, path: str) -> Optional[int]:
        if not IS_WINDOWS:
            return None
        wp = _win_path(path)
        attrs = cls._kernel32.GetFileAttributesW(wp)
        if attrs == -1:
            attrs = cls._kernel32.GetFileAttributesW(path)
        return attrs if attrs != -1 else None

    @classmethod
    def exists(cls, path: str) -> bool:
        if not IS_WINDOWS:
            return os.path.exists(path)
        return cls.get_file_attributes(path) is not None

    @classmethod
    def is_dir(cls, path: str) -> bool:
        if not IS_WINDOWS:
            return os.path.isdir(path)
        attrs = cls.get_file_attributes(path)
        if attrs is None:
            return False
        return bool(attrs & FILE_ATTRIBUTE_DIRECTORY)

    @classmethod
    def safe_stat(cls, path: str):
        try:
            wp = _win_path(path)
            if os.path.exists(wp):
                return os.lstat(wp)
        except (OSError, ValueError):
            pass
        try:
            return os.lstat(path)
        except (OSError, ValueError):
            return None

    @classmethod
    def safe_listdir(cls, path: str) -> List[str]:
        try:
            wp = _win_path(path)
            if cls.is_dir(path):
                return os.listdir(wp)
        except (OSError, ValueError):
            pass
        try:
            return os.listdir(path)
        except (OSError, ValueError):
            return []

    @classmethod
    def move(cls, src: str, dst: str) -> bool:
        if not IS_WINDOWS:
            try:
                shutil.move(src, dst)
                return True
            except OSError:
                return False
        src_wp = _win_path(src)
        dst_wp = _win_path(dst)
        result = cls._kernel32.MoveFileExW(
            src_wp, dst_wp,
            MOVEFILE_COPY_ALLOWED | MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH
        )
        if result:
            return True
        result = cls._kernel32.MoveFileExW(
            src, dst,
            MOVEFILE_COPY_ALLOWED | MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH
        )
        return bool(result)

    @classmethod
    def delete_file(cls, path: str) -> bool:
        if not IS_WINDOWS:
            try:
                os.remove(path)
                return True
            except OSError:
                return False
        wp = _win_path(path)
        result = cls._kernel32.DeleteFileW(wp)
        if not result:
            result = cls._kernel32.DeleteFileW(path)
        return bool(result)

    @classmethod
    def remove_directory(cls, path: str) -> bool:
        if not IS_WINDOWS:
            try:
                os.rmdir(path)
                return True
            except OSError:
                return False
        wp = _win_path(path)
        result = cls._kernel32.RemoveDirectoryW(wp)
        if not result:
            result = cls._kernel32.RemoveDirectoryW(path)
        return bool(result)

    @classmethod
    def get_reparse_tag(cls, path: str) -> Optional[int]:
        if not IS_WINDOWS:
            return None
        try:
            import struct
            GENERIC_READ = 0x80000000
            FILE_SHARE_READ = 0x00000001
            FILE_SHARE_WRITE = 0x00000002
            FILE_SHARE_DELETE = 0x00000004
            OPEN_EXISTING = 3
            FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
            FILE_FLAG_BACKUP_SEMANTICS = 0x02000000

            wp = _win_path(path)
            handle = cls._kernel32.CreateFileW(
                wp, GENERIC_READ,
                FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                None, OPEN_EXISTING,
                FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS,
                None
            )
            if handle == -1 or handle is None:
                return None

            try:
                buf = ctypes.create_string_buffer(1024)
                bytes_returned = wintypes.DWORD()
                FSCTL_GET_REPARSE_POINT = 0x000900A8
                result = cls._kernel32.DeviceIoControl(
                    handle, FSCTL_GET_REPARSE_POINT,
                    None, 0, buf, 1024,
                    ctypes.byref(bytes_returned), None
                )
                if result:
                    tag = struct.unpack_from("<I", buf.raw, 0)[0]
                    return tag
            finally:
                cls._kernel32.CloseHandle(handle)
        except Exception:
            pass
        return None


# ============================================================
# 名称工具
# ============================================================

def _sanitize_filename(name: str) -> str:
    """清理非法文件名"""
    clean = "".join(c if c not in ILLEGAL_CHARS else "_" for c in name)
    clean = clean.strip(" .")
    if not clean:
        clean = "_unnamed"
    name_no_ext = os.path.splitext(clean)[0].upper()
    if name_no_ext in WINDOWS_RESERVED:
        clean = f"_{clean}"
    return clean


def _truncate_name(name: str, max_bytes: int) -> str:
    """
    截断文件名以符合最大字节限制
    """
    encoded = name.encode("utf-8")
    if len(encoded) <= max_bytes:
        return name

    base, ext = os.path.splitext(name)
    ext_bytes = len(ext.encode("utf-8"))
    name_hash = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
    hash_suffix = f"_{name_hash}"
    hash_bytes = len(hash_suffix.encode("utf-8"))

    available = max_bytes - ext_bytes - hash_bytes
    if available < 1:
        available = 1

    truncated_base = ""
    current_bytes = 0
    for ch in base:
        ch_bytes = len(ch.encode("utf-8"))
        if current_bytes + ch_bytes > available:
            break
        truncated_base += ch
        current_bytes += ch_bytes

    if not truncated_base:
        truncated_base = "x"

    return f"{truncated_base}{hash_suffix}{ext}"


def _fmt_size(size_bytes: int) -> str:
    """格式化文件大小"""
    size = size_bytes  # 使用局部变量，不修改输入
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def _node_attrs_str(n) -> str:
    """获取节点属性字符串"""
    parts = []
    if getattr(n, 'is_hidden', False):
        parts.append("H")
    if getattr(n, 'is_readonly', False):
        parts.append("R")
    if getattr(n, 'is_system', False):
        parts.append("S")
    if getattr(n, 'has_zone_identifier', False):
        parts.append("Z")
    reparse = getattr(n, 'reparse_type', None)
    if reparse and reparse != ReparseType.NONE:
        # Symlink=S, Junction=J, HardLink=H, MountPoint=M
        prefix_map = {"Symlink": "S", "Junction": "J", "HardLink": "H", "MountPoint": "M"}
        parts.append(prefix_map.get(reparse.value, "?"))
    return "".join(parts) if parts else "-"

