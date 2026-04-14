"""
常量定义模块
包含所有常量、枚举、默认配置原始数据
无外部依赖
"""

import os
import platform
import re
from enum import Enum

# ═══════════════════════════════════════════════════════════
# 版本与平台检测
# ═══════════════════════════════════════════════════════════
VERSION = "8.0.0"
IS_WINDOWS = platform.system() == "Windows"

# ═══════════════════════════════════════════════════════════
# 路径常量
# ═══════════════════════════════════════════════════════════
MAX_PATH_LEGACY = 260
MAX_PATH_EXTENDED = 32767
LOG_DIR = ".restructure_logs"
TRASH_DIR = ".restructure_trash"
MAX_LOG_FILES = 100

# ═══════════════════════════════════════════════════════════
# 文件名规则
# ═══════════════════════════════════════════════════════════
ILLEGAL_CHARS = set('<>:"/\\|?*\x00')
WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}

# ═══════════════════════════════════════════════════════════
# 缓存目录模式
# ═══════════════════════════════════════════════════════════
CACHE_DIR_PATTERNS = [
    "node_modules", "__pycache__", ".git", ".svn", ".hg",
    "target", "build", "dist", "out", "bin", "obj",
    ".gradle", ".mvn", ".idea", ".vscode", ".vs",
    "venv", ".venv", "env", ".env",
    ".cache", ".npm", ".yarn", ".pnpm-store",
    "Pods", ".deriveddata",
]

# ═══════════════════════════════════════════════════════════
# 垃圾文件模式
# ═══════════════════════════════════════════════════════════
JUNK_FILE_PATTERNS = [
    "*.tmp", "*.temp", "*.log", "*.bak", "*.old",
    "*.swp", "*.swo", "*~", "*.DS_Store", "Thumbs.db",
    "desktop.ini", "*.stackdump", "*.pid",
]

# ═══════════════════════════════════════════════════════════
# 扩展名分组
# ═══════════════════════════════════════════════════════════
EXTENSION_GROUPS = {
    "images": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".ico", ".svg", ".webp", ".tiff", ".tif"},
    "videos": {".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".mpeg", ".mpg"},
    "audio": {".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a", ".ape"},
    "documents": {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".rtf", ".odt"},
    "code": {".py", ".js", ".ts", ".java", ".c", ".cpp", ".h", ".hpp", ".cs", ".go", ".rs", ".rb", ".php"},
    "archives": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"},
    "executables": {".exe", ".msi", ".bat", ".cmd", ".ps1", ".sh"},
}

# ═══════════════════════════════════════════════════════════
# Windows 文件属性常量
# ═══════════════════════════════════════════════════════════
FILE_ATTRIBUTE_HIDDEN = 0x0002
FILE_ATTRIBUTE_READONLY = 0x0001
FILE_ATTRIBUTE_SYSTEM = 0x0004
FILE_ATTRIBUTE_REPARSE = 0x0400
FILE_ATTRIBUTE_DIRECTORY = 0x0010
IO_REPARSE_TAG_SYMLINK = 0xA000000C
IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003

# ═══════════════════════════════════════════════════════════
# Win32 API 常量
# ═══════════════════════════════════════════════════════════
MOVEFILE_REPLACE_EXISTING = 0x0001
MOVEFILE_COPY_ALLOWED = 0x0002
MOVEFILE_WRITE_THROUGH = 0x0008

# ═══════════════════════════════════════════════════════════
# 回收站 API 常量 (shell32.SHFileOperationW)
# ═══════════════════════════════════════════════════════════
FO_DELETE = 0x0003
FOF_ALLOWUNDO = 0x0040
FOF_NOCONFIRMATION = 0x0010
FOF_SILENT = 0x0004
FOF_NOERRORUI = 0x0400

# ═══════════════════════════════════════════════════════════
# 默认配置
# ═══════════════════════════════════════════════════════════
DEFAULT_MAX_WORKERS = 8
THROTTLE_SLEEP_DEFAULT = 0.05  # 50ms
EXT_GROUP_MAP = EXTENSION_GROUPS  # 别名，保持兼容性

DEFAULT_CONFIG = {
    "dedup_strategy": "shallowest",
    "flatten_mode": "single",
    "use_recycle_bin": False,
    "exclude_paths": [],
    "cache_patterns": CACHE_DIR_PATTERNS,
    "junk_patterns": JUNK_FILE_PATTERNS,
    "ext_group_map": {k: list(v) for k, v in EXTENSION_GROUPS.items()},
    "threads": None,
    "throttle_ms": 50,
}

# ═══════════════════════════════════════════════════════════
# 正则表达式
# ═══════════════════════════════════════════════════════════
TRUNCATED_LINE_RE = re.compile(r'\.\.\.["\']?\s*$')

# ═══════════════════════════════════════════════════════════
# 枚举定义
# ═══════════════════════════════════════════════════════════


class NodeAction(Enum):
    """节点操作类型"""
    NONE = "None"
    DELETE = "DELETE"
    MOVE = "MOVE"
    RENAME = "RENAME"
    HARDLINK = "HARDLINK"


class NodeStatus(Enum):
    """节点状态"""
    PENDING = "Pending"
    SUCCESS = "Success"
    VANISHED = "Vanished"
    CHANGED = "Changed"
    LOCKED = "Locked"
    SKIPPED = "Skipped"
    CONFLICT = "Conflict"
    TRASHED = "Trashed"
    RECYCLED = "Recycled"        # V8.0+: 真回收站
    PERM_FIXED = "PermFixed"
    ATTR_FIXED = "AttrFixed"
    ZONE_CLEARED = "ZoneCleared"
    FAILED = "Failed"
    HARDLINKED = "HardLinked"


class ReparseType(Enum):
    """重解析点类型"""
    NONE = "None"
    SYMLINK = "Symlink"
    JUNCTION = "Junction"
    MOUNTPOINT = "MountPoint"
    HARDLINK = "HardLink"


class SafetyLevel(Enum):
    """安全级别"""
    FULL = 1
    RESTRICTED = 2
    BLOCKED = 3


class PermissionAction(Enum):
    """权限操作类型"""
    GRANT_FULL_CONTROL = "grant_full_control"
    REMOVE_READONLY = "remove_readonly"
    REMOVE_HIDDEN = "remove_hidden"
    CLEAR_ZONE_IDENTIFIER = "clear_zone_identifier"
    ALL = "all"
