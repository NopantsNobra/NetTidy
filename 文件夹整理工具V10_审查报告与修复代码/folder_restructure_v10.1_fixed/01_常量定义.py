"""
常量定义模块
=============
职责：定义所有枚举、枚举值、常量配置

对外接口：
    - VERSION            版本号
    - IS_WINDOWS         平台检测
    - NodeAction         文件节点操作类型枚举
    - NodeStatus         文件节点状态枚举
    - ReparseType        重解析点类型枚举
    - SafetyLevel        安全级别枚举
    - PermissionAction   权限操作类型枚举
    - CACHE_DIR_PATTERNS 缓存目录模式列表
    - JUNK_FILE_PATTERNS 垃圾文件模式列表
    - EXTENSION_GROUPS   扩展名分组映射
    - DEFAULT_CONFIG     默认配置字典

使用示例：
    from 常量定义 import VERSION, NodeAction
    
依赖关系：
    - 无外部依赖

版本历史：
    10.0: 最终审计版本，修正逻辑错误与文档一致性
    9.0: 模块化重构、枚举值修正、文档完善
    8.0: 初始模块化版本
"""

from __future__ import annotations

import platform
import re
from enum import Enum, auto
from typing import Dict, List, Set, FrozenSet


# ═══════════════════════════════════════════════════════════════════
# 版本与平台检测
# ═══════════════════════════════════════════════════════════════════

VERSION: str = "10.0.0"
"""版本号"""

IS_WINDOWS: bool = platform.system() == "Windows"
"""平台检测标志"""

IS_LINUX: bool = platform.system() == "Linux"
"""Linux 平台检测标志"""

IS_MACOS: bool = platform.system() == "Darwin"
"""macOS 平台检测标志"""


# ═══════════════════════════════════════════════════════════════════
# 路径常量
# ═══════════════════════════════════════════════════════════════════

MAX_PATH_LEGACY: int = 260
"""传统路径最大长度"""

MAX_PATH_EXTENDED: int = 32767
"""扩展路径（\\\\?\\ 前缀）最大长度"""

LOG_DIR: str = ".restructure_logs"
"""日志目录名"""

TRASH_DIR: str = ".restructure_trash"
"""回收站目录名"""

LOCK_FILE: str = ".restructure.lock"
"""锁文件名"""

MAX_LOG_FILES: int = 100
"""最大保留日志文件数"""

QUICK_HASH_SIZE: int = 65536
"""快速哈希采样大小（字节）"""

THROTTLE_BATCH: int = 200
"""节流批次大小"""


# ═══════════════════════════════════════════════════════════════════
# 文件名规则
# ═══════════════════════════════════════════════════════════════════

ILLEGAL_CHARS: FrozenSet[str] = frozenset('<>:"|?*\x00')
"""Windows 文件名非法字符集合"""

WINDOWS_RESERVED: FrozenSet[str] = frozenset({
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
})
"""Windows 保留设备名"""


# ═══════════════════════════════════════════════════════════════════
# 缓存目录模式
# ═══════════════════════════════════════════════════════════════════

CACHE_DIR_PATTERNS: List[str] = [
    # 版本控制
    "node_modules", "__pycache__", ".git", ".svn", ".hg", "CVS",
    # 构建输出
    "target", "build", "dist", "out", "bin", "obj", ".gradle", ".mvn",
    # IDE
    ".idea", ".vscode", ".vs", ".eclipse",
    # 虚拟环境
    "venv", ".venv", "env", ".env", ".virtualenv",
    # 包管理器缓存
    ".cache", ".npm", ".yarn", ".pnpm-store", "Pods", ".deriveddata",
    # 临时文件
    "tmp", "temp", ".tmp",
    # 其他
    ".tox", ".pytest_cache", ".mypy_cache", ".ruff_cache",
]
"""
缓存目录模式列表
用于匹配需要清理的缓存/临时目录

使用 fnmatch 匹配，忽略大小写
"""


# ═══════════════════════════════════════════════════════════════════
# 垃圾文件模式
# ═══════════════════════════════════════════════════════════════════

JUNK_FILE_PATTERNS: List[str] = [
    # 临时文件
    "*.tmp", "*.temp", "*.log", "*.bak", "*.old",
    "*.swp", "*.swo", "*~", "*.stackdump", "*.pid",
    # 系统文件
    "Thumbs.db", "desktop.ini", "*.DS_Store",
    "ehthumbs.db", "Icon\\r",
    # 下载残留
    "*.bc!", "*.td", "*.xltd", "*.!ut", "*.part", "*.crdownload",
]
"""
垃圾文件模式列表
用于匹配需要清理的临时/垃圾文件

使用 fnmatch 匹配，忽略大小写
"""


# ═══════════════════════════════════════════════════════════════════
# 扩展名分组
# ═══════════════════════════════════════════════════════════════════

EXTENSION_GROUPS: Dict[str, Set[str]] = {
    "images": {
        ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".ico", ".svg", ".webp",
        ".tiff", ".tif", ".raw", ".psd", ".ai", ".eps",
    },
    "videos": {
        ".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm",
        ".m4v", ".mpeg", ".mpg", ".3gp",
    },
    "audio": {
        ".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a", ".ape",
        ".opus", ".mid", ".midi",
    },
    "documents": {
        ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
        ".txt", ".rtf", ".odt", ".ods", ".odp", ".pages",
    },
    "code": {
        ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".cpp",
        ".h", ".hpp", ".cs", ".go", ".rs", ".rb", ".php", ".swift",
        ".kt", ".scala", ".lua", ".r", ".m", ".sh", ".bat", ".ps1",
    },
    "data": {
        ".json", ".xml", ".csv", ".yaml", ".yml", ".toml", ".ini",
        ".cfg", ".conf", ".sql", ".db", ".sqlite", ".parquet",
    },
    "archives": {
        ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso",
        ".tar.gz", ".tar.bz2",
    },
    "executables": {
        ".exe", ".msi", ".dll", ".bat", ".cmd", ".ps1", ".sh",
        ".so", ".dylib", ".app", ".jar",
    },
}
"""
扩展名到分组的映射
用于按类型分组整理文件

键：分组名称（小写）
值：该分组包含的扩展名集合
"""


# ═══════════════════════════════════════════════════════════════════
# Windows 文件属性常量
# ═══════════════════════════════════════════════════════════════════

FILE_ATTRIBUTE_HIDDEN: int = 0x0002
"""隐藏属性"""

FILE_ATTRIBUTE_READONLY: int = 0x0001
"""只读属性"""

FILE_ATTRIBUTE_SYSTEM: int = 0x0004
"""系统属性"""

FILE_ATTRIBUTE_REPARSE: int = 0x0400
"""重解析点属性"""

FILE_ATTRIBUTE_DIRECTORY: int = 0x0010
"""目录属性"""

FILE_ATTRIBUTE_NORMAL: int = 0x0080
"""常规文件属性"""

# 重解析点标签
IO_REPARSE_TAG_SYMLINK: int = 0xA000000C
"""符号链接标签"""

IO_REPARSE_TAG_MOUNT_POINT: int = 0xA0000003
"""挂载点标签"""

IO_REPARSE_TAG_HARDLINK: int = 0xA0000001
"""硬链接标签"""


# ═══════════════════════════════════════════════════════════════════
# Win32 API 常量
# ═══════════════════════════════════════════════════════════════════

# MoveFileEx 标志
MOVEFILE_REPLACE_EXISTING: int = 0x0001
"""替换已存在文件"""

MOVEFILE_COPY_ALLOWED: int = 0x0002
"""允许跨卷移动"""

MOVEFILE_WRITE_THROUGH: int = 0x0008
"""直写模式"""

MOVEFILE_DELAY_UNTIL_REBOOT: int = 0x0004
"""等待重启（已废弃）"""

# SHFileOperation 标志
FO_DELETE: int = 0x0003
"""删除操作"""

FO_RENAME: int = 0x0004
"""重命名操作"""

FO_MOVE: int = 0x0001
"""移动操作"""

FO_COPY: int = 0x0002
"""复制操作"""

FOF_ALLOWUNDO: int = 0x0040
"""允许撤销（放入回收站）"""

FOF_NOCONFIRMATION: int = 0x0010
"""不显示确认对话框"""

FOF_SILENT: int = 0x0004
"""静默模式"""

FOF_NOERRORUI: int = 0x0400
"""不显示错误UI"""

FOF_RENAMEONCOLLISION: int = 0x0008
"""冲突时重命名"""

FOF_MULTIDESTFILES: int = 0x0001
"""多目标文件"""


# ═══════════════════════════════════════════════════════════════════
# 默认配置
# ═══════════════════════════════════════════════════════════════════

DEFAULT_MAX_WORKERS: int = 8
"""默认最大工作线程数"""

THROTTLE_SLEEP_DEFAULT: float = 0.05
"""默认节流休眠时间（秒）"""

SSD_MAX_WORKERS: int = 16
"""SSD 环境最大线程数"""

HDD_MAX_WORKERS: int = 2
"""HDD 环境最大线程数"""

DEFAULT_CONFIG: Dict = {
    # 去重策略: shallowest | newest | largest | hardlink
    "dedup_strategy": "shallowest",
    
    # 扁平化模式: single | ext | date | prefix | size
    "flatten_mode": "single",
    
    # 是否使用系统回收站
    "use_recycle_bin": False,
    
    # 排除路径列表
    "exclude_paths": [],
    
    # 缓存目录模式（null 使用内置）
    "cache_patterns": None,
    
    # 垃圾文件模式（null 使用内置）
    "junk_patterns": None,
    
    # 扩展名分组映射
    "ext_group_map": {k: list(v) for k, v in EXTENSION_GROUPS.items()},
    
    # 手动指定线程数（null=自动检测）
    "threads": None,
    
    # 节流间隔（毫秒）
    "throttle_ms": 50,
}
"""
默认配置字典
用于初始化和配置合并

可通过配置文件（JSON）覆盖
"""


# ═══════════════════════════════════════════════════════════════════
# 正则表达式
# ═══════════════════════════════════════════════════════════════════

TRUNCATED_LINE_RE = re.compile(r'\.\.\.["\']?\s*$')
"""截断行检测正则"""

PATH_TRAVERSE_RE = re.compile(r'\.\..*[/\\]')
"""路径穿越检测正则"""

UUID_PATTERN = re.compile(
    r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
    re.IGNORECASE
)
"""UUID 模式正则"""


# ═══════════════════════════════════════════════════════════════════
# 枚举定义
# ═══════════════════════════════════════════════════════════════════


class NodeAction(Enum):
    """
    文件节点操作类型枚举
    
    用于标记文件节点被分配的操作类型
    """
    NONE = "None"
    """无操作"""
    
    DELETE = "DELETE"
    """删除（移入回收站）"""
    
    MOVE = "MOVE"
    """移动到新位置"""
    
    RENAME = "RENAME"
    """重命名"""
    
    HARDLINK = "HARDLINK"
    """创建硬链接"""
    
    COPY = "COPY"
    """复制到新位置"""
    
    def __str__(self) -> str:
        """友好字符串表示"""
        return self.value


class NodeStatus(Enum):
    """
    文件节点状态枚举
    
    用于记录文件节点的处理状态
    """
    PENDING = "Pending"
    """待处理"""
    
    PROCESSING = "Processing"
    """处理中"""
    
    SUCCESS = "Success"
    """成功"""
    
    SKIPPED = "Skipped"
    """跳过"""
    
    FAILED = "Failed"
    """失败"""
    
    VANISHED = "Vanished"
    """文件已消失"""
    
    CHANGED = "Changed"
    """文件已变更"""
    
    LOCKED = "Locked"
    """文件被锁定"""
    
    CONFLICT = "Conflict"
    """冲突"""
    
    TRASHED = "Trashed"
    """已移入回收站"""
    
    RECYCLED = "Recycled"
    """已移入系统回收站"""
    
    HARDLINKED = "HardLinked"
    """已创建硬链接"""
    
    PERM_FIXED = "PermFixed"
    """权限已修复"""
    
    ATTR_FIXED = "AttrFixed"
    """属性已修复"""
    
    ZONE_CLEARED = "ZoneCleared"
    """Zone 标识已清除"""
    
    def __str__(self) -> str:
        """友好字符串表示"""
        return self.value


class ReparseType(Enum):
    """
    重解析点类型枚举
    
    用于标识特殊文件系统对象
    """
    NONE = "None"
    """常规文件/目录"""
    
    SYMLINK = "Symlink"
    """符号链接"""
    
    JUNCTION = "Junction"
    """NTFS 目录联接点"""
    
    MOUNTPOINT = "MountPoint"
    """挂载点"""
    
    HARDLINK = "HardLink"
    """硬链接"""
    
    SHORTCUT = "Shortcut"
    """快捷方式"""
    
    def is_special(self) -> bool:
        """是否为特殊文件对象"""
        return self != ReparseType.NONE


class SafetyLevel(Enum):
    """
    安全级别枚举
    
    用于标识操作的安全等级
    """
    FULL = 1
    """完全安全，可执行"""
    
    RESTRICTED = 2
    """受限模式，仅预览"""
    
    BLOCKED = 3
    """禁止操作"""
    
    def can_execute(self) -> bool:
        """是否可以执行实际操作"""
        return self == SafetyLevel.FULL
    
    def can_preview(self) -> bool:
        """是否可以预览"""
        return self in (SafetyLevel.FULL, SafetyLevel.RESTRICTED)


class PermissionAction(Enum):
    """
    权限操作类型枚举
    
    用于标识权限修复操作
    """
    GRANT_FULL_CONTROL = "grant_full_control"
    """授予完全控制权限"""
    
    REMOVE_READONLY = "remove_readonly"
    """移除只读属性"""
    
    REMOVE_HIDDEN = "remove_hidden"
    """移除隐藏属性"""
    
    REMOVE_SYSTEM = "remove_system"
    """移除系统属性"""
    
    CLEAR_ZONE_IDENTIFIER = "clear_zone_identifier"
    """清除 Zone.Identifier"""
    
    ALL = "all"
    """所有操作"""
    
    @classmethod
    def from_string(cls, action_str: str) -> Set["PermissionAction"]:
        """
        从字符串解析权限操作集合
        
        Args:
            action_str: 逗号分隔的操作字符串
            
        Returns:
            权限操作集合
            
        Example:
            >>> PermissionAction.from_string("readonly,hidden")
            {<PermissionAction.REMOVE_READONLY>, <PermissionAction.REMOVE_HIDDEN>}
        """
        result: Set[PermissionAction] = set()
        
        for part in action_str.lower().split(","):
            part = part.strip()
            
            if part in ("all", "full"):
                result.add(cls.ALL)
            elif part in ("readonly", "ro"):
                result.add(cls.REMOVE_READONLY)
            elif part in ("hidden", "h"):
                result.add(cls.REMOVE_HIDDEN)
            elif part in ("system", "s"):
                result.add(cls.REMOVE_SYSTEM)
            elif part in ("zone", "z"):
                result.add(cls.CLEAR_ZONE_IDENTIFIER)
            elif part in ("perm", "permission", "p"):
                result.add(cls.GRANT_FULL_CONTROL)
        
        return result


class DedupStrategy(Enum):
    """
    去重策略枚举
    
    用于确定重复文件保留策略
    """
    SHALLOWEST = "shallowest"
    """保留层级最浅的文件（默认）"""
    
    NEWEST = "newest"
    """保留最新修改的文件"""
    
    LARGEST = "largest"
    """保留最大的文件"""
    
    HARDLINK = "hardlink"
    """将重复文件转为硬链接"""
    
    @classmethod
    def from_string(cls, s: str) -> "DedupStrategy":
        """从字符串获取去重策略"""
        s = s.lower().strip()
        
        for strategy in cls:
            if strategy.value == s:
                return strategy
        
        # 默认返回最安全的策略
        return cls.SHALLOWEST
    
    def is_safe(self) -> bool:
        """是否为安全策略（不删除文件）"""
        return self == DedupStrategy.HARDLINK


class FlattenMode(Enum):
    """
    扁平化模式枚举
    
    用于确定文件分组方式
    """
    SINGLE = "single"
    """所有文件到同一目录"""
    
    EXTENSION = "ext"
    """按扩展名分组"""
    
    DATE = "date"
    """按日期分组"""
    
    PREFIX = "prefix"
    """按名称前缀分组"""
    
    SIZE = "size"
    """按大小分组"""
    
    @classmethod
    def from_string(cls, s: str) -> "FlattenMode":
        """从字符串获取扁平化模式"""
        s = s.lower().strip()
        
        for mode in cls:
            if mode.value == s:
                return mode
        
        return cls.SINGLE


# ═══════════════════════════════════════════════════════════════════
# 类型别名
# ═══════════════════════════════════════════════════════════════════

# 为常用类型定义别名
PathLike = str
"""路径类型别名"""

Timestamp = float
"""时间戳类型别名"""

FileSize = int
"""文件大小类型别名"""

ErrorCode = int
"""错误码类型别名"""

# 导出所有公共名称
__all__ = [
    # 版本与平台
    "VERSION", "IS_WINDOWS", "IS_LINUX", "IS_MACOS",
    
    # 路径常量
    "MAX_PATH_LEGACY", "MAX_PATH_EXTENDED",
    "LOG_DIR", "TRASH_DIR", "LOCK_FILE", "MAX_LOG_FILES",
    "QUICK_HASH_SIZE", "THROTTLE_BATCH",
    
    # 文件名规则
    "ILLEGAL_CHARS", "WINDOWS_RESERVED",
    
    # 模式列表
    "CACHE_DIR_PATTERNS", "JUNK_FILE_PATTERNS", "EXTENSION_GROUPS",
    
    # Windows 属性常量
    "FILE_ATTRIBUTE_HIDDEN", "FILE_ATTRIBUTE_READONLY",
    "FILE_ATTRIBUTE_SYSTEM", "FILE_ATTRIBUTE_REPARSE",
    "FILE_ATTRIBUTE_DIRECTORY", "FILE_ATTRIBUTE_NORMAL",
    "IO_REPARSE_TAG_SYMLINK", "IO_REPARSE_TAG_MOUNT_POINT",
    "IO_REPARSE_TAG_HARDLINK",
    
    # Win32 API 常量
    "MOVEFILE_REPLACE_EXISTING", "MOVEFILE_COPY_ALLOWED",
    "MOVEFILE_WRITE_THROUGH", "FO_DELETE", "FOF_ALLOWUNDO",
    
    # 默认配置
    "DEFAULT_MAX_WORKERS", "THROTTLE_SLEEP_DEFAULT",
    "SSD_MAX_WORKERS", "HDD_MAX_WORKERS", "DEFAULT_CONFIG",
    
    # 正则
    "TRUNCATED_LINE_RE", "PATH_TRAVERSE_RE", "UUID_PATTERN",
    
    # 枚举
    "NodeAction", "NodeStatus", "ReparseType", "SafetyLevel",
    "PermissionAction", "DedupStrategy", "FlattenMode",
]
