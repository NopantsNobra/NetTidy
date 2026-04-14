#!/usr/bin/env python3
"""
文件夹结构智能重组工具 V8.0 (Windows 专用 + 离线分析模式)
Folder Structure Intelligent Restructuring Engine V8.0

核心原则：绝对防丢、极度容错、用户可控
默认为预览模式，必须显式确认才执行物理操作。

V8.0 相比 V7.0 核心变更：
- 🟢 新功能：tqdm 进度条（全流程覆盖，tqdm 为可选依赖）
- 🟢 新功能：配置文件支持（restructure_config.json）
- 🟢 新功能：交互式预览选择模式（--preview-interactive）
- 🟢 新功能：去重策略增强（新增 largest / hardlink）
- 🟢 新功能：智能分组扁平化（--flatten-by ext/date/prefix）
- 🟢 新功能：HTML 可视化报告（--html-report）
- 🟢 新功能：--only-* 精细控制（只跑指定模块）
- 🟢 新功能：空间预估（预览时显示预计释放空间）
- 🟢 新功能：SSD/HDD 自动检测 + 动态线程/节流
- 🟢 改进：模块化代码结构（单文件保持）
- 🟢 安全：hardlink 策略含完整回滚保护

环境要求: Windows 7+ / Python 3.9+ / 管理员权限(推荐)
离线模式: 任意平台 / Python 3.9+ / 仅需文件列表
"""

import os
import sys
import json
import time
import hashlib
import shutil
import stat
import subprocess
import argparse
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import List, Dict, Optional, Tuple, Set
from collections import defaultdict
import threading
import uuid
import fnmatch
import re
import tempfile
import bisect
from concurrent.futures import ThreadPoolExecutor, as_completed
import html as html_mod

# V8.0: 可选进度条依赖
try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

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
    MOVEFILE_COPY_ALLOWED = 0x0002
    MOVEFILE_REPLACE_EXISTING = 0x0001
    MOVEFILE_WRITE_THROUGH = 0x0008
    FILE_ATTRIBUTE_HIDDEN = 0x0002
    FILE_ATTRIBUTE_READONLY = 0x0001
    FILE_ATTRIBUTE_SYSTEM = 0x0004
    FILE_ATTRIBUTE_REPARSE = 0x0400
    FILE_ATTRIBUTE_DIRECTORY = 0x0010
    IO_REPARSE_TAG_SYMLINK = 0xA000000C
    IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003

# ============================================================
# 版本与常量
# ============================================================
VERSION = "8.0"
LOCK_FILENAME = ".restructure.lock"
LOG_DIR = ".restructure_log"
TRASH_DIR = ".restructure_trash"
MAX_LOG_FILES = 10

DEFAULT_MAX_WORKERS = min(8, (os.cpu_count() or 4))
MAX_WORKERS = DEFAULT_MAX_WORKERS
HASH_BATCH_SIZE = 50
IO_BATCH_SIZE = 200

WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

CACHE_DIR_PATTERNS = [
    "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".tox", ".eggs", "*.egg-info", ".gradle", ".mvn", "target",
    "dist", ".next", ".nuxt", "vendor", ".terraform", ".serverless",
    ".cache", ".parcel-cache", ".webpack", "Thumbs.db", "desktop.ini",
    ".DS_Store", "ehthumbs.db", "System Volume Information", "$RECYCLE.BIN",
    "RECYCLER",
]

JUNK_FILE_PATTERNS = [
    "*.bc!", "*.td", "*.xltd", "*.!ut", "*.part", "*.crdownload",
]

ILLEGAL_CHARS = set('<>:"|?*\x00')
QUICK_HASH_SIZE = 4096
MAX_PATH_LEGACY = 260
MAX_PATH_EXTENDED = 32767
THROTTLE_BATCH = 200
THROTTLE_SLEEP = 0.05
ZONE_IDENTIFIER = "Zone.Identifier"
TRUNCATED_LINE_RE = re.compile(r'\.\.\.\s*$')

# V8.0: 扩展名分组映射 (用于 --flatten-by ext)
EXT_GROUP_MAP = {
    "docs": [".txt", ".md", ".pdf", ".doc", ".docx", ".rtf", ".odt", ".tex", ".epub", ".xls", ".xlsx", ".ppt", ".pptx"],
    "images": [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".webp", ".ico", ".tiff", ".tif", ".raw"],
    "videos": [".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".3gp"],
    "audio": [".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a", ".opus"],
    "code": [".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".cpp", ".h", ".hpp", ".cs", ".go",
             ".rs", ".rb", ".php", ".swift", ".kt", ".scala", ".lua", ".r", ".m", ".sh", ".bat", ".ps1"],
    "data": [".json", ".xml", ".csv", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".sql", ".db", ".sqlite"],
    "archives": [".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso"],
    "executables": [".exe", ".msi", ".dll", ".so", ".dylib", ".app"],
}

# ============================================================
# V8.0: 配置管理
# ============================================================
DEFAULT_CONFIG = {
    "cache_patterns": CACHE_DIR_PATTERNS,
    "junk_patterns": JUNK_FILE_PATTERNS,
    "dedup_strategy": "shallowest",
    "flatten_mode": "single",
    "exclude_patterns": [],
    "exclude_paths": [],
    "threads": None,
    "throttle_ms": 50,
    "ext_group_map": {k: v for k, v in EXT_GROUP_MAP.items()},
}

def load_config(config_path: str) -> Dict:
    """加载配置文件并与默认值合并"""
    config = dict(DEFAULT_CONFIG)
    if config_path and os.path.isfile(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                user_config = json.load(f)
            # 安全检查：排除路径不可包含路径穿越
            for ep in user_config.get("exclude_paths", []):
                norm = os.path.normpath(ep)
                if ".." in norm.split(os.sep):
                    print(f"⚠️ 配置警告: 排除路径 '{ep}' 包含路径穿越，已忽略")
                    continue
            config.update(user_config)
        except (json.JSONDecodeError, OSError) as e:
            print(f"⚠️ 配置文件加载失败: {e}，使用默认配置")
    return config

def save_default_config(config_path: str) -> None:
    """保存默认配置文件作为模板"""
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)
        print(f"📄 默认配置已保存到: {config_path}")
    except OSError as e:
        print(f"❌ 无法保存配置文件: {e}")

def validate_config(config: Dict) -> Tuple[bool, List[str]]:
    """验证配置文件"""
    errors = []
    valid_strategies = {"shallowest", "newest", "largest", "hardlink"}
    if config.get("dedup_strategy") not in valid_strategies:
        errors.append(f"无效的去重策略: {config.get('dedup_strategy')}, 可选: {valid_strategies}")
    valid_modes = {"single", "ext", "date", "prefix"}
    if config.get("flatten_mode") not in valid_modes:
        errors.append(f"无效的扁平化模式: {config.get('flatten_mode')}, 可选: {valid_modes}")
    if config.get("threads") is not None and (not isinstance(config["threads"], int) or config["threads"] < 1):
        errors.append("threads 必须为正整数")
    return len(errors) == 0, errors

# ============================================================
# V8.0: SSD/HDD 自动检测
# ============================================================
_disk_type_cache: Dict[str, str] = {}

def detect_disk_type(drive_letter: str) -> str:
    """检测指定驱动器是 SSD 还是 HDD (仅 Windows)"""
    global _disk_type_cache
    if not IS_WINDOWS:
        return "UNKNOWN"
    dl = drive_letter.upper().rstrip(":\\")
    if dl in _disk_type_cache:
        return _disk_type_cache[dl]
    try:
        cmd = [
            "powershell", "-NoProfile", "-Command",
            f"Get-PhysicalDisk | ForEach-Object {{ $d=$_; Get-Partition -DiskNumber $d.DiskNumber "
            f"-ErrorAction SilentlyContinue | Where-Object {{$_.DriveLetter -eq '{dl}'}} | "
            f"Select-Object -First 1 }} | ForEach-Object {{ (Get-PhysicalDisk -DeviceId "
            f"$_.DiskNumber -ErrorAction SilentlyContinue).MediaType }}"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        media = result.stdout.strip()
        if "SSD" in media:
            _disk_type_cache[dl] = "SSD"
        elif "HDD" in media:
            _disk_type_cache[dl] = "HDD"
        else:
            _disk_type_cache[dl] = "UNKNOWN"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        _disk_type_cache[dl] = "UNKNOWN"
    return _disk_type_cache[dl]

def apply_disk_tuning(target_path: str, config: Dict) -> None:
    """根据磁盘类型自动调整线程数和节流间隔"""
    global MAX_WORKERS, THROTTLE_SLEEP
    if not IS_WINDOWS:
        return
    drive, _ = os.path.splitdrive(os.path.abspath(target_path))
    if not drive:
        return
    disk_type = detect_disk_type(drive)
    manual_threads = config.get("threads")
    if disk_type == "SSD":
        MAX_WORKERS = manual_threads or min(16, (os.cpu_count() or 4) * 2)
        THROTTLE_SLEEP = (config.get("throttle_ms", 20)) / 1000.0
        print(f"💾 检测到 SSD ({drive}), 线程: {MAX_WORKERS}, 节流: {THROTTLE_SLEEP*1000:.0f}ms")
    elif disk_type == "HDD":
        MAX_WORKERS = manual_threads or 2
        THROTTLE_SLEEP = (config.get("throttle_ms", 100)) / 1000.0
        print(f"💿 检测到 HDD ({drive}), 线程: {MAX_WORKERS}, 节流: {THROTTLE_SLEEP*1000:.0f}ms")
    else:
        MAX_WORKERS = manual_threads or DEFAULT_MAX_WORKERS
        THROTTLE_SLEEP = (config.get("throttle_ms", 50)) / 1000.0
        print(f"💿 磁盘类型未知 ({drive}), 线程: {MAX_WORKERS}, 节流: {THROTTLE_SLEEP*1000:.0f}ms")

# ============================================================
# V8.0: tqdm 进度条包装器
# ============================================================
def make_progress(iterable, total=None, desc="", unit="", disable=False):
    """统一的进度条接口，无 tqdm 时退化为普通迭代"""
    if HAS_TQDM and not disable:
        return tqdm(iterable, total=total, desc=desc, unit=unit,
                    bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]")
    return iterable

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
            MOVEFILE_COPY_ALLOWED | MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)
        if result:
            return True
        result = cls._kernel32.MoveFileExW(
            src, dst,
            MOVEFILE_COPY_ALLOWED | MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)
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
                None)
            if handle == -1 or handle is None:
                return None
            try:
                buf = ctypes.create_string_buffer(1024)
                bytes_returned = wintypes.DWORD()
                FSCTL_GET_REPARSE_POINT = 0x000900A8
                result = cls._kernel32.DeviceIoControl(
                    handle, FSCTL_GET_REPARSE_POINT, None, 0,
                    buf, 1024, ctypes.byref(bytes_returned), None)
                if result:
                    tag = struct.unpack_from("<I", buf.raw, 0)[0]
                    return tag
            finally:
                cls._kernel32.CloseHandle(handle)
        except Exception:
            pass
        return None

# ============================================================
# 枚举
# ============================================================
class NodeAction(Enum):
    NONE = "NONE"
    DELETE = "DELETE"
    MOVE = "MOVE"
    RENAME = "RENAME"
    HARDLINK = "HARDLINK"

class NodeStatus(Enum):
    PENDING = "Pending"
    SUCCESS = "Success"
    VANISHED = "Vanished"
    CHANGED = "Changed"
    LOCKED = "Locked"
    SKIPPED = "Skipped"
    CONFLICT = "Conflict"
    TRASHED = "Trashed"
    PERM_FIXED = "PermFixed"
    ATTR_FIXED = "AttrFixed"
    ZONE_CLEARED = "ZoneCleared"
    FAILED = "Failed"
    HARDLINKED = "HardLinked"

class ReparseType(Enum):
    NONE = "None"
    SYMLINK = "Symlink"
    JUNCTION = "Junction"
    MOUNTPOINT = "MountPoint"
    HARDLINK = "HardLink"

class SafetyLevel(Enum):
    FULL = 1
    RESTRICTED = 2
    BLOCKED = 3

class PermissionAction(Enum):
    GRANT_FULL_CONTROL = "grant_full_control"
    REMOVE_READONLY = "remove_readonly"
    REMOVE_HIDDEN = "remove_hidden"
    CLEAR_ZONE_IDENTIFIER = "clear_zone_identifier"
    ALL = "all"

# ============================================================
# 数据结构
# ============================================================
@dataclass
class FileNode:
    full_path: str
    file_name: str
    extension: str
    size_bytes: int = 0
    depth: int = 0
    last_modified: float = 0.0
    reparse_type: ReparseType = ReparseType.NONE
    is_hidden: bool = False
    is_readonly: bool = False
    is_system: bool = False
    hard_link_count: int = 1
    is_dir: bool = False
    has_ads: bool = False
    has_zone_identifier: bool = False
    target_path: str = ""
    action: NodeAction = NodeAction.NONE
    status: NodeStatus = NodeStatus.PENDING
    error_msg: str = ""
    reason: str = ""
    _quick_hash: Optional[str] = field(default=None, repr=False)
    _full_hash: Optional[str] = field(default=None, repr=False)

    def quick_hash(self) -> str:
        if self._quick_hash is not None:
            return self._quick_hash
        if self.is_dir:
            self._quick_hash = ""
            return ""
        try:
            wp = _win_path(self.full_path)
            fp = wp if os.path.exists(wp) else self.full_path
            with open(fp, "rb") as f:
                data = f.read(QUICK_HASH_SIZE)
            self._quick_hash = hashlib.sha256(data).hexdigest()
        except (OSError, IOError):
            self._quick_hash = ""
        return self._quick_hash

    def full_hash(self) -> str:
        if self._full_hash is not None:
            return self._full_hash
        if self.is_dir:
            self._full_hash = ""
            return ""
        try:
            wp = _win_path(self.full_path)
            fp = wp if os.path.exists(wp) else self.full_path
            h = hashlib.sha256()
            with open(fp, "rb") as f:
                while chunk := f.read(65536):
                    h.update(chunk)
            self._full_hash = h.hexdigest()
        except (OSError, IOError):
            self._full_hash = ""
        return self._full_hash

@dataclass
class VirtualFileNode:
    full_path: str
    file_name: str
    extension: str
    size_bytes: int = 0
    depth: int = 0
    is_dir: bool = False
    is_truncated: bool = False
    action: NodeAction = NodeAction.NONE
    reason: str = ""
    target_path: str = ""

@dataclass
class OperationLog:
    seq: int
    action: str
    source: str
    target: str
    timestamp: str
    status: str = "SUCCESS"
    error: str = ""

@dataclass
class ErrorEntry:
    seq: int
    path: str
    error_type: str
    message: str
    timestamp: str

# ============================================================
# 错误收集器
# ============================================================
class ErrorCollector:
    def __init__(self):
        self._errors: List[ErrorEntry] = []
        self._lock = threading.Lock()
        self._seq = 0

    def add(self, path: str, error_type: str, message: str) -> None:
        with self._lock:
            self._seq += 1
            self._errors.append(ErrorEntry(
                seq=self._seq, path=path, error_type=error_type,
                message=message, timestamp=datetime.now(timezone.utc).isoformat()))

    @property
    def errors(self) -> List[ErrorEntry]:
        return list(self._errors)

    @property
    def count(self) -> int:
        return len(self._errors)

    def has_errors(self) -> bool:
        return len(self._errors) > 0

# ============================================================
# 锁文件
# ============================================================
class LockManager:
    def __init__(self, target_dir: str):
        self.target_dir = target_dir
        self.lock_path = os.path.join(target_dir, LOCK_FILENAME)
        self.session_id = str(uuid.uuid4())
        self._acquired = False

    def acquire(self, force: bool = False) -> bool:
        if os.path.exists(self.lock_path):
            try:
                with open(self.lock_path, "r", encoding="utf-8") as f:
                    lock_data = json.load(f)
                expires = lock_data.get("expires_at", "")
                if expires and datetime.fromisoformat(expires) > datetime.now(timezone.utc):
                    if not force:
                        print(f"⚠️ 目录正在被处理:")
                        print(f"  主机: {lock_data.get('hostname', 'unknown')}")
                        print(f"  进程: {lock_data.get('pid', 'unknown')}")
                        print(f"  开始: {lock_data.get('started_at', 'unknown')}")
                        print(f"  过期: {expires}")
                        return False
                os.remove(self.lock_path)
            except (json.JSONDecodeError, OSError):
                if force:
                    os.remove(self.lock_path)
                else:
                    print("⚠️ 发现损坏的锁文件，使用 --force-lock 清除")
                    return False
        lock_data = {
            "pid": os.getpid(),
            "hostname": os.environ.get("COMPUTERNAME", "unknown"),
            "user": os.environ.get("USERNAME", "unknown"),
            "session_id": self.session_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": datetime.fromtimestamp(
                time.time() + 12 * 3600, tz=timezone.utc).isoformat(),
        }
        try:
            with open(self.lock_path, "w", encoding="utf-8") as f:
                json.dump(lock_data, f, indent=2)
            self._acquired = True
        except OSError as e:
            print(f"❌ 无法创建锁文件: {e}")
            return False
        return True

    def release(self) -> None:
        if not self._acquired:
            return
        try:
            if os.path.exists(self.lock_path):
                os.remove(self.lock_path)
            self._acquired = False
        except OSError:
            pass

# ============================================================
# 安全检查 (仅 Windows)
# ============================================================
class SafetyChecker:
    @classmethod
    def _get_system_dirs(cls) -> Set[str]:
        dirs = set()
        for env_var in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
            val = os.environ.get(env_var, "")
            if val:
                dirs.add(val)
        for drive_letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            root = f"{drive_letter}:\\"
            if os.path.exists(root):
                dirs.add(os.path.join(root, "$Recycle.Bin"))
                dirs.add(os.path.join(root, "System Volume Information"))
                dirs.add(os.path.join(root, "Windows"))
                dirs.add(os.path.join(root, "Program Files"))
                dirs.add(os.path.join(root, "Program Files (x86)"))
                dirs.add(os.path.join(root, "ProgramData"))
        return dirs

    @classmethod
    def _get_user_profile_dirs(cls) -> Set[str]:
        dirs = set()
        userprofile = os.environ.get("USERPROFILE", "")
        if userprofile:
            dirs.add(os.path.join(userprofile, "AppData"))
            dirs.add(os.path.join(userprofile, "AppData", "Local"))
            dirs.add(os.path.join(userprofile, "AppData", "Roaming"))
            dirs.add(os.path.join(userprofile, "AppData", "LocalLow"))
        return dirs

    @classmethod
    def check(cls, target_path: str) -> Tuple[SafetyLevel, str]:
        target_path = os.path.abspath(target_path)
        if not os.path.exists(target_path):
            return SafetyLevel.BLOCKED, f"路径不存在: {target_path}"
        drive, _ = os.path.splitdrive(target_path)
        if target_path == drive + "\\" or target_path == drive + "/":
            return SafetyLevel.BLOCKED, "禁止操作驱动器根目录"
        all_protected = cls._get_system_dirs() | cls._get_user_profile_dirs()
        target_lower = target_path.lower()
        for sd in all_protected:
            sd_lower = sd.lower()
            if target_lower == sd_lower or target_lower.startswith(sd_lower + "\\"):
                return SafetyLevel.BLOCKED, f"命中系统保护目录: {sd}"
        if not os.access(target_path, os.W_OK):
            return SafetyLevel.RESTRICTED, "目标目录无写入权限"
        try:
            usage = shutil.disk_usage(target_path)
            if usage.free < 200 * 1024 * 1024:
                return SafetyLevel.RESTRICTED, "磁盘剩余空间不足 200MB"
        except OSError:
            pass
        test_file = os.path.join(target_path, f".restr_test_{os.getpid()}")
        try:
            with open(test_file, "w") as f:
                f.write("test")
            os.remove(test_file)
        except OSError:
            return SafetyLevel.RESTRICTED, "目标目录所在文件系统为只读"
        return SafetyLevel.FULL, "安全"

# ============================================================
# Zone.Identifier (网络文件标识) 处理
# ============================================================
class ZoneIdentifierHandler:
    @staticmethod
    def has_zone_identifier(file_path: str) -> bool:
        ads_path = file_path + ":" + ZONE_IDENTIFIER
        try:
            wp = _win_path(ads_path)
            return os.path.exists(wp)
        except (OSError, ValueError):
            pass
        try:
            return os.path.exists(ads_path)
        except (OSError, ValueError):
            return False

    @staticmethod
    def clear_zone_identifier(file_path: str) -> bool:
        ads_path = file_path + ":" + ZONE_IDENTIFIER
        for attempt_path in [_win_path(ads_path), ads_path]:
            try:
                if os.path.exists(attempt_path):
                    os.remove(attempt_path)
                    return True
            except (OSError, ValueError):
                continue
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Remove-Item", "-LiteralPath", file_path,
                 "-Stream", ZONE_IDENTIFIER, "-ErrorAction", "SilentlyContinue"],
                capture_output=True, text=True, timeout=10)
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    @staticmethod
    def list_ads(file_path: str) -> List[str]:
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-Item", "-LiteralPath", file_path, "-Stream", "*",
                 "|", "Select-Object", "-ExpandProperty", "Stream"],
                capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                return [s.strip() for s in result.stdout.strip().split("\n") if s.strip()]
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return []

# ============================================================
# 权限与属性修改引擎 (V8.0: 添加 tqdm)
# ============================================================
class PermissionEngine:
    def __init__(self, nodes: List[FileNode]):
        self.nodes = nodes
        self._errors = ErrorCollector()
        self.stats: Dict[str, int] = {
            "perm_granted": 0, "readonly_removed": 0,
            "hidden_removed": 0, "zone_cleared": 0,
            "failed": 0, "skipped": 0,
        }

    def grant_full_control(self, targets=None, dry_run=True):
        items = targets or self.nodes
        username = os.environ.get("USERNAME", "")
        if not username:
            print("⚠️ 无法获取当前用户名，跳过权限修改")
            return self.stats
        to_process = [n for n in items if not n.is_dir]
        total = len(to_process)
        print(f"\n🔑 授予完全控制权限: {total} 个文件 (用户: {username})")
        for i, node in enumerate(make_progress(to_process, desc="权限修复", unit="项"), 1):
            wp = _win_path(node.full_path)
            target = wp if (IS_WINDOWS and Win32IO.exists(node.full_path)) else node.full_path
            if dry_run:
                if i <= 20:
                    print(f"  [PERM] grant:F {node.file_name}")
                continue
            try:
                result = subprocess.run(
                    ["icacls", target, "/grant", f"{username}:(F)", "/Q"],
                    capture_output=True, text=True, timeout=30)
                if result.returncode == 0:
                    node.status = NodeStatus.PERM_FIXED
                    self.stats["perm_granted"] += 1
                else:
                    self._errors.add(node.full_path, "PermGrantFailed", result.stderr.strip()[:200])
                    self.stats["failed"] += 1
            except subprocess.TimeoutExpired:
                self._errors.add(node.full_path, "PermTimeout", "icacls 超时")
                self.stats["failed"] += 1
            except FileNotFoundError:
                print("❌ icacls 命令不可用")
                return self.stats
        return self.stats

    def remove_readonly(self, targets=None, dry_run=True):
        items = targets or [n for n in self.nodes if n.is_readonly]
        print(f"\n🔓 去除只读属性: {len(items)} 个文件")
        for node in make_progress(items, desc="去只读", unit="项"):
            wp = _win_path(node.full_path)
            target = wp if (IS_WINDOWS and Win32IO.exists(node.full_path)) else node.full_path
            if dry_run:
                continue
            try:
                st = Win32IO.safe_stat(node.full_path) if IS_WINDOWS else os.lstat(node.full_path)
                if st:
                    os.chmod(target, st.st_mode | stat.S_IWRITE)
                subprocess.run(["attrib", "-R", target], capture_output=True, timeout=10)
                node.is_readonly = False
                node.status = NodeStatus.ATTR_FIXED
                self.stats["readonly_removed"] += 1
            except (OSError, subprocess.TimeoutExpired) as e:
                self._errors.add(node.full_path, "ReadonlyRemoveFailed", str(e))
                self.stats["failed"] += 1
        return self.stats

    def remove_hidden(self, targets=None, dry_run=True):
        items = targets or [n for n in self.nodes if n.is_hidden]
        items = [n for n in items if not n.file_name.startswith(".restructure")]
        print(f"\n👁️ 去除隐藏属性: {len(items)} 个文件")
        for node in make_progress(items, desc="去隐藏", unit="项"):
            wp = _win_path(node.full_path)
            target = wp if (IS_WINDOWS and Win32IO.exists(node.full_path)) else node.full_path
            if dry_run:
                continue
            try:
                subprocess.run(["attrib", "-H", target], capture_output=True, timeout=10)
                node.is_hidden = False
                node.status = NodeStatus.ATTR_FIXED
                self.stats["hidden_removed"] += 1
            except (OSError, subprocess.TimeoutExpired) as e:
                self._errors.add(node.full_path, "HiddenRemoveFailed", str(e))
                self.stats["failed"] += 1
        return self.stats

    def clear_zone_identifiers(self, targets=None, dry_run=True):
        items = targets or []
        if not targets:
            for n in self.nodes:
                if n.is_dir or n.action == NodeAction.DELETE:
                    continue
                if ZoneIdentifierHandler.has_zone_identifier(n.full_path):
                    n.has_zone_identifier = True
                    items.append(n)
        print(f"\n🌐 清除网络文件标识 (Zone.Identifier): {len(items)} 个文件")
        for node in make_progress(items, desc="Zone清理", unit="项"):
            if dry_run:
                continue
            wp = _win_path(node.full_path)
            target = wp if (IS_WINDOWS and Win32IO.exists(node.full_path)) else node.full_path
            if ZoneIdentifierHandler.clear_zone_identifier(target):
                node.has_zone_identifier = False
                node.status = NodeStatus.ZONE_CLEARED
                self.stats["zone_cleared"] += 1
            else:
                self._errors.add(node.full_path, "ZoneClearFailed", "无法清除 Zone.Identifier")
                self.stats["failed"] += 1
        return self.stats

    def apply_all(self, actions: Set[PermissionAction], dry_run=True):
        if PermissionAction.GRANT_FULL_CONTROL in actions or PermissionAction.ALL in actions:
            self.grant_full_control(dry_run=dry_run)
        if PermissionAction.REMOVE_READONLY in actions or PermissionAction.ALL in actions:
            self.remove_readonly(dry_run=dry_run)
        if PermissionAction.REMOVE_HIDDEN in actions or PermissionAction.ALL in actions:
            self.remove_hidden(dry_run=dry_run)
        if PermissionAction.CLEAR_ZONE_IDENTIFIER in actions or PermissionAction.ALL in actions:
            self.clear_zone_identifiers(dry_run=dry_run)
        return self.stats

# ============================================================
# 诊断引擎
# ============================================================
class DiagnosticEngine:
    def __init__(self, root_path: str):
        self.root_path = os.path.abspath(root_path)
        self.nodes: List[FileNode] = []
        self._scan_timestamp: float = 0
        self._errors = ErrorCollector()

    def scan(self, detect_zone=False, verbose=False, config=None):
        self._scan_timestamp = time.time()
        self.nodes = []
        root_depth = self.root_path.count(os.sep)
        skipped_dirs = 0
        exclude_paths = set()
        if config:
            for ep in config.get("exclude_paths", []):
                norm = os.path.normpath(ep)
                if ".." not in norm.split(os.sep):
                    exclude_paths.add(norm)

        print(f"🔍 扫描目录: {self.root_path}")
        for dirpath, dirnames, filenames in os.walk(self.root_path, topdown=True):
            if exclude_paths:
                dirnames[:] = [d for d in dirnames
                               if os.path.normpath(os.path.join(dirpath, d)) not in exclude_paths]
            try:
                wp = _win_path(dirpath)
                test_dir = wp if os.path.exists(wp) else dirpath
                os.listdir(test_dir)
            except PermissionError:
                skipped_dirs += 1
                self._errors.add(dirpath, "PermissionDenied", "无权限访问，已跳过子项")
                dirnames.clear()
                continue
            rel_depth = dirpath.count(os.sep) - root_depth
            if dirpath != self.root_path:
                self.nodes.append(self._make_node(dirpath, rel_depth, is_dir=True))
            for fname in filenames:
                fpath = os.path.join(dirpath, fname)
                try:
                    self.nodes.append(self._make_node(fpath, rel_depth))
                except (OSError, IOError):
                    continue
        print(f"  扫描完成: {len(self.nodes)} 项")

        if detect_zone and IS_WINDOWS:
            file_nodes = [n for n in self.nodes if not n.is_dir]
            if file_nodes:
                print(f"  🔍 并行检测 Zone.Identifier ({len(file_nodes)} 文件, {MAX_WORKERS} 线程)...")
                lock = threading.Lock()
                zone_count = [0]
                def _check_zone(node):
                    try:
                        wp = _win_path(node.full_path)
                        cp = wp if os.path.exists(wp) else node.full_path
                        if ZoneIdentifierHandler.has_zone_identifier(cp):
                            with lock:
                                node.has_zone_identifier = True
                                zone_count[0] += 1
                    except (OSError, IOError):
                        pass
                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                    list(executor.map(_check_zone, make_progress(
                        file_nodes, desc="Zone检测", unit="项")))
                if zone_count[0]:
                    print(f"  发现 {zone_count[0]} 个含 Zone.Identifier 的文件")
        if skipped_dirs:
            print(f"⚠️ 跳过 {skipped_dirs} 个无权限目录")
        return self.nodes

    def _make_node(self, path, depth, is_dir=False):
        st = Win32IO.safe_stat(path) if IS_WINDOWS else os.lstat(path)
        name = os.path.basename(path) if path != self.root_path else os.path.basename(self.root_path)
        ext = "" if is_dir else os.path.splitext(name)[1].lower()
        reparse = ReparseType.NONE
        hard_links = 1
        is_hidden = is_readonly = is_system = False
        if st:
            if stat.S_ISLNK(st.st_mode):
                reparse = ReparseType.SYMLINK
            elif not stat.S_ISDIR(st.st_mode) and st.st_nlink > 1:
                reparse = ReparseType.HARDLINK
                hard_links = st.st_nlink
            if IS_WINDOWS:
                try:
                    attrs = Win32IO.get_file_attributes(path)
                    if attrs is not None:
                        is_hidden = bool(attrs & FILE_ATTRIBUTE_HIDDEN)
                        is_readonly = bool(attrs & FILE_ATTRIBUTE_READONLY)
                        is_system = bool(attrs & FILE_ATTRIBUTE_SYSTEM)
                        if attrs & FILE_ATTRIBUTE_REPARSE and reparse == ReparseType.NONE:
                            tag = Win32IO.get_reparse_tag(path)
                            if tag == IO_REPARSE_TAG_SYMLINK:
                                reparse = ReparseType.SYMLINK
                            elif tag == IO_REPARSE_TAG_MOUNT_POINT:
                                reparse = ReparseType.JUNCTION
                            else:
                                reparse = ReparseType.SYMLINK
                except (AttributeError, OSError):
                    if st:
                        is_readonly = not bool(st.st_mode & stat.S_IWRITE)
        return FileNode(
            full_path=path, file_name=name, extension=ext,
            size_bytes=st.st_size if st and not is_dir else 0,
            depth=depth, last_modified=st.st_mtime if st else 0,
            reparse_type=reparse, is_hidden=is_hidden, is_readonly=is_readonly,
            is_system=is_system, hard_link_count=hard_links, is_dir=is_dir)

    def get_stats(self, top_n=10):
        files = [n for n in self.nodes if not n.is_dir]
        dirs = [n for n in self.nodes if n.is_dir]
        deepest = sorted(files, key=lambda n: n.depth, reverse=True)[:top_n]
        largest_files = sorted(files, key=lambda n: n.size_bytes, reverse=True)[:top_n]
        hardlinked = [n for n in files if n.hard_link_count > 1]
        dir_sizes: Dict[str, int] = defaultdict(int)
        for n in files:
            dir_sizes[os.path.dirname(n.full_path)] += n.size_bytes
        largest_dirs = sorted(dir_sizes.items(), key=lambda x: x[1], reverse=True)[:top_n]
        ext_count: Dict[str, int] = defaultdict(int)
        for n in files:
            ext_count[n.extension or "(无扩展名)"] += 1
        long_names = []
        for n in self.nodes:
            try:
                fb = len(n.full_path.encode("utf-8"))
                if fb > MAX_PATH_LEGACY:
                    long_names.append((n.full_path, fb))
            except (UnicodeEncodeError, UnicodeDecodeError):
                long_names.append((n.full_path, -1))
        total_size = sum(n.size_bytes for n in files)
        max_depth = max((n.depth for n in self.nodes), default=0)
        return {
            "root": self.root_path, "total_items": len(self.nodes),
            "total_files": len(files), "total_dirs": len(dirs),
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "max_depth": max_depth,
            "symlinks": sum(1 for n in self.nodes if n.reparse_type == ReparseType.SYMLINK),
            "junctions": sum(1 for n in self.nodes if n.reparse_type in (ReparseType.JUNCTION, ReparseType.MOUNTPOINT)),
            "hard_links": len(hardlinked),
            "readonly_files": len([n for n in files if n.is_readonly]),
            "hidden_files": len([n for n in self.nodes if n.is_hidden]),
            "system_files": sum(1 for n in self.nodes if n.is_system),
            "zone_identifier_files": len([n for n in files if n.has_zone_identifier]),
            "long_path_files": len(long_names), "long_path_examples": long_names[:5],
            "deepest_files": [(n.full_path, n.depth) for n in deepest],
            "largest_files": [(n.full_path, _fmt_size(n.size_bytes)) for n in largest_files],
            "largest_dirs": [(d, _fmt_size(s)) for d, s in largest_dirs],
            "extension_stats": dict(sorted(ext_count.items(), key=lambda x: x[1], reverse=True)[:20]),
        }

    def export_csv(self, output_path):
        import csv
        with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["full_path", "file_name", "extension", "size_bytes", "size_human",
                             "depth", "last_modified", "reparse_type", "is_hidden", "is_readonly",
                             "is_system", "hard_link_count", "has_zone_identifier", "is_dir"])
            for n in make_progress(self.nodes, desc="导出CSV", unit="行"):
                writer.writerow([
                    n.full_path, n.file_name, n.extension, n.size_bytes,
                    _fmt_size(n.size_bytes), n.depth,
                    datetime.fromtimestamp(n.last_modified).isoformat() if n.last_modified > 0 else "",
                    n.reparse_type.value, n.is_hidden, n.is_readonly, n.is_system,
                    n.hard_link_count, n.has_zone_identifier, n.is_dir])

    def export_json(self, output_path):
        data = {
            "engine_version": VERSION,
            "scan_time": datetime.fromtimestamp(self._scan_timestamp).isoformat(),
            "stats": self.get_stats(),
            "nodes": [{"path": n.full_path, "name": n.file_name, "ext": n.extension,
                        "size": n.size_bytes, "depth": n.depth,
                        "reparse": n.reparse_type.value, "is_hidden": n.is_hidden,
                        "is_readonly": n.is_readonly, "has_zone": n.has_zone_identifier,
                        "is_dir": n.is_dir} for n in self.nodes]
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

# ============================================================
# 智能剔除引擎 (V8.0 增强: largest / hardlink 策略)
# ============================================================
class EliminationEngine:
    def __init__(self, nodes: List[FileNode], errors: ErrorCollector):
        self.nodes = nodes
        self.errors = errors
        self._dup_groups: List[List[FileNode]] = []

    def mark_cache_dirs(self, extra_patterns=None, config=None):
        patterns = list(CACHE_DIR_PATTERNS)
        junk_patterns = list(JUNK_FILE_PATTERNS)
        if config:
            patterns = list(config.get("cache_patterns", patterns))
            junk_patterns = list(config.get("junk_patterns", junk_patterns))
        if extra_patterns:
            patterns.extend(extra_patterns)

        for node in self.nodes:
            if node.action != NodeAction.NONE:
                continue
            if not node.is_dir:
                for pattern in junk_patterns:
                    if fnmatch.fnmatch(node.file_name.lower(), pattern.lower()):
                        node.action = NodeAction.DELETE
                        node.reason = f"临时文件: {pattern}"
                        break
        cache_roots = []
        for node in self.nodes:
            if node.action != NodeAction.NONE:
                continue
            for pattern in patterns:
                if fnmatch.fnmatch(node.file_name.lower(), pattern.lower()):
                    cache_roots.append(node)
                    node.action = NodeAction.DELETE
                    node.reason = f"缓存目录: {pattern}"
                    break
        dir_roots = [n for n in cache_roots if n.is_dir]
        if dir_roots:
            sorted_nodes = sorted(self.nodes, key=lambda n: n.full_path.lower())
            for root in dir_roots:
                prefix = root.full_path.lower() + os.sep
                lo = bisect.bisect_left(sorted_nodes, prefix, key=lambda n: n.full_path.lower())
                for j in range(lo, len(sorted_nodes)):
                    if sorted_nodes[j].full_path.lower().startswith(prefix):
                        if sorted_nodes[j].action == NodeAction.NONE:
                            sorted_nodes[j].action = NodeAction.DELETE
                            sorted_nodes[j].reason = f"缓存子树: {root.file_name}"
                    else:
                        break

    def deduplicate(self, strategy="shallowest"):
        """V8.0: 支持 shallowest / newest / largest / hardlink 四种策略"""
        files = [n for n in self.nodes if not n.is_dir and n.action == NodeAction.NONE]
        size_groups: Dict[int, List[FileNode]] = defaultdict(list)
        for f in files:
            size_groups[f.size_bytes].append(f)
        candidate_groups = [(size, group) for size, group in size_groups.items() if len(group) >= 2]
        if not candidate_groups:
            return 0

        total_candidates = sum(len(g) for _, g in candidate_groups)
        print(f"  🔍 并行去重: {total_candidates} 个候选文件 "
              f"({len(candidate_groups)} 个大小区, {MAX_WORKERS} 线程, 策略: {strategy})")

        all_candidates = [f for _, group in candidate_groups for f in group]
        for f in make_progress(all_candidates, desc="QuickHash", unit="项"):
            f.quick_hash()

        need_full_hash = []
        for size, group in candidate_groups:
            qh_groups: Dict[str, List[FileNode]] = defaultdict(list)
            for f in group:
                qh = f._quick_hash or ""
                if qh:
                    qh_groups[qh].append(f)
            for qh, qgroup in qh_groups.items():
                if len(qgroup) >= 2:
                    need_full_hash.append(qgroup)
        if not need_full_hash:
            return 0

        need_flat = [f for group in need_full_hash for f in group]
        for f in make_progress(need_flat, desc="FullHash", unit="项"):
            f.full_hash()

        dup_count = 0
        self._dup_groups = []
        for qgroup in make_progress(need_full_hash, desc="去重分组", unit="组"):
            fh_groups: Dict[str, List[FileNode]] = defaultdict(list)
            for f in qgroup:
                fh = f._full_hash or ""
                if fh:
                    fh_groups[fh].append(f)
            for fh, fgroup in fh_groups.items():
                if len(fgroup) < 2:
                    continue
                hard_link_nodes = [f for f in fgroup if f.reparse_type == ReparseType.HARDLINK]
                if strategy == "shallowest":
                    keep = hard_link_nodes[0] if hard_link_nodes else min(fgroup, key=lambda n: n.depth)
                elif strategy == "newest":
                    keep = hard_link_nodes[0] if hard_link_nodes else max(fgroup, key=lambda n: n.last_modified)
                elif strategy == "largest":
                    keep = hard_link_nodes[0] if hard_link_nodes else max(fgroup, key=lambda n: n.size_bytes)
                elif strategy == "hardlink":
                    keep = min(fgroup, key=lambda n: n.depth)
                else:
                    keep = min(fgroup, key=lambda n: n.depth)

                self._dup_groups.append(list(fgroup))
                for f in fgroup:
                    if f.full_path != keep.full_path:
                        if strategy == "hardlink":
                            f.action = NodeAction.HARDLINK
                            f.target_path = keep.full_path
                            f.reason = f"转为硬链接 → {keep.full_path}"
                        else:
                            f.action = NodeAction.DELETE
                            f.reason = f"重复文件 (保留: {keep.full_path})"
                        dup_count += 1

        print(f"  发现 {len(self._dup_groups)} 组重复文件, 标记 {dup_count} 项")
        return dup_count

    def get_duplicate_groups(self):
        result = []
        for i, group in enumerate(self._dup_groups, 1):
            if len(group) < 2:
                continue
            kept = deleted = None
            for f in group:
                if f.action == NodeAction.NONE:
                    kept = f
                else:
                    if deleted is None:
                        deleted = []
                    deleted.append(f)
            if not kept:
                kept = group[0]
                deleted = group[1:]
            kept.action = NodeAction.NONE
            result.append({
                "group_id": i,
                "size": _fmt_size(kept.size_bytes),
                "hash": kept.full_hash()[:16] if kept.full_hash() else "?",
                "action": deleted[0].action.value if deleted else "NONE",
                "keep": {"path": kept.full_path, "depth": kept.depth},
                "delete": [{"path": f.full_path, "depth": f.depth, "action": f.action.value} for f in (deleted or [])],
            })
        return result

# ============================================================
# V8.0: 智能分组扁平化引擎
# ============================================================
class FlattenEngine:
    """V8.0: 支持单层 / 按扩展名分组 / 按日期分组 / 按前缀分组"""

    def __init__(self, nodes: List[FileNode], root_path: str, errors: ErrorCollector,
                 ext_group_map=None):
        self.nodes = nodes
        self.root_path = root_path
        self.errors = errors
        self.ext_group_map = ext_group_map or EXT_GROUP_MAP

    def _classify_ext(self, ext: str) -> str:
        """将扩展名分类到分组名称"""
        ext_lower = ext.lower()
        for group_name, exts in self.ext_group_map.items():
            if ext_lower in exts:
                return group_name
        return "other"

    def _classify_date(self, mtime: float) -> str:
        """将修改时间分类为 YYYY-MM"""
        try:
            dt = datetime.fromtimestamp(mtime)
            return dt.strftime("%Y-%m")
        except (OSError, ValueError):
            return "unknown"

    def _classify_prefix(self, name: str) -> str:
        """按文件名首字母分类"""
        first = name[0].upper() if name else "#"
        if first.isalpha():
            return first
        return "#"

    def extract_files(self, target_dir: str, mode: str = "single",
                      extensions=None):
        """
        V8.0: 智能分组扁平化
        mode: single | ext | date | prefix
        """
        files = [n for n in self.nodes if not n.is_dir and n.action == NodeAction.NONE]
        if extensions:
            ext_set = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in extensions}
            files = [f for f in files if f.extension in ext_set]

        existing_names: Dict[str, Set[str]] = {}  # group -> set of names
        os.makedirs(target_dir, exist_ok=True)
        extracted = 0

        for f in make_progress(files, desc="扁平化", unit="项"):
            if mode == "ext":
                group = self._classify_ext(f.extension)
            elif mode == "date":
                group = self._classify_date(f.last_modified)
            elif mode == "prefix":
                group = self._classify_prefix(f.file_name)
            else:
                group = ""

            group_dir = os.path.join(target_dir, group) if group else target_dir
            os.makedirs(group_dir, exist_ok=True)

            if group not in existing_names:
                existing_names[group] = set()

            base, ext = os.path.splitext(f.file_name)
            target_name = f.file_name
            if target_name in existing_names[group]:
                source_dir = os.path.basename(os.path.dirname(f.full_path))
                target_name = f"{base}_from_{source_dir}{ext}"
                counter = 2
                while target_name in existing_names[group]:
                    target_name = f"{base}_from_{source_dir}_{counter}{ext}"
                    counter += 1

            name_no_ext = os.path.splitext(target_name)[0].upper()
            if name_no_ext in WINDOWS_RESERVED:
                target_name = f"_{target_name}"

            target_path = os.path.join(group_dir, target_name)
            path_bytes = len(target_path.encode("utf-8"))
            if path_bytes >= MAX_PATH_EXTENDED:
                safe_name = _truncate_name(target_name,
                    MAX_PATH_EXTENDED - len(group_dir.encode("utf-8")) - 2)
                target_path = os.path.join(group_dir, safe_name)

            f.target_path = target_path
            f.action = NodeAction.MOVE
            f.reason = f"扁平化 [{mode}]"
            existing_names[group].add(target_name)
            extracted += 1

        return extracted

# ============================================================
# 结构重组引擎
# ============================================================
class RestructureEngine:
    def __init__(self, nodes: List[FileNode], root_path: str, errors: ErrorCollector):
        self.nodes = nodes
        self.root_path = root_path
        self.errors = errors

    def unpack_redundant(self):
        unpacked = 0
        dirs = [n for n in self.nodes if n.is_dir and n.action == NodeAction.NONE]
        dir_children: Dict[str, List[FileNode]] = defaultdict(list)
        for d in dirs:
            parent = os.path.dirname(d.full_path)
            dir_children[parent.lower()].append(d)
        for parent_lower, children in dir_children.items():
            if len(children) != 1:
                continue
            child = children[0]
            parent_name = os.path.basename(parent_lower)
            # 如果是根目录则跳过
            if parent_lower == self.root_path.lower():
                continue
            if not parent_name or parent_name != child.file_name.lower():
                continue
            actual_parent = os.path.dirname(child.full_path)
            child_prefix = child.full_path.lower() + os.sep
            for node in self.nodes:
                if node.full_path.lower().startswith(child_prefix) and node.action == NodeAction.NONE:
                    rel = os.path.relpath(node.full_path, child.full_path)
                    node.target_path = os.path.join(actual_parent, rel)
                    node.action = NodeAction.MOVE
                    node.reason = f"解包冗余: {child.file_name}"
            child.action = NodeAction.DELETE
            child.reason = "冗余目录（子项已提级）"
            unpacked += 1
        return unpacked


    def lift_children(self, target_dir):
        lifted = 0
        parent_dir = os.path.dirname(target_dir)
        prefix = target_dir.lower() + os.sep
        for node in self.nodes:
            if node.full_path.lower().startswith(prefix) and node.action == NodeAction.NONE:
                rel = os.path.relpath(node.full_path, target_dir)
                if os.sep not in rel:
                    node.target_path = os.path.join(parent_dir, rel)
                    node.action = NodeAction.MOVE
                    lifted += 1
        for node in self.nodes:
            if node.full_path.lower() == target_dir.lower():
                node.action = NodeAction.DELETE
                break
        return lifted

# ============================================================
# 执行引擎 (V8.0: tqdm + hardlink 处理)
# ============================================================
class ExecutionEngine:
    def __init__(self, nodes, root_path, log_dir, trash_dir):
        self.nodes = nodes
        self.root_path = root_path
        self.log_dir = log_dir
        self.trash_dir = trash_dir
        self.errors = ErrorCollector()
        self.op_logs: List[OperationLog] = []
        self._seq = 0
        self._scan_timestamp: float = 0
        self._lock = threading.Lock()
        self.stats: Dict[str, int] = {
            "success": 0, "vanished": 0, "changed": 0,
            "locked": 0, "skipped": 0, "conflict": 0,
            "failed": 0, "trashed": 0, "hardlinked": 0,
        }
        self._throttle = 0

    def set_scan_timestamp(self, ts):
        self._scan_timestamp = ts

    def execute(self, dry_run=True):
        os.makedirs(self.log_dir, exist_ok=True)
        os.makedirs(self.trash_dir, exist_ok=True)
        moved = [n for n in self.nodes if n.action == NodeAction.MOVE]
        deleted = [n for n in self.nodes if n.action == NodeAction.DELETE]
        hardlinked = [n for n in self.nodes if n.action == NodeAction.HARDLINK]
        total = len(moved) + len(deleted) + len(hardlinked)
        if total == 0:
            print("✅ 无需执行任何操作")
            return self.stats

        mode_label = "🔍 预览模式" if dry_run else "⚡ 执行模式"
        print(f"\n{mode_label}: 共 {total} 项操作 "
              f"(移动 {len(moved)} / 删除 {len(deleted)} / 硬链接 {len(hardlinked)})")
        if not dry_run and total > 20:
            print(f"  使用 {MAX_WORKERS} 线程并行处理...")

        # Phase 2: 移动操作
        print("  Phase 2: 移动操作...")
        self._parallel_process(moved, self._process_move, dry_run, "移动")

        # Phase 3: 删除操作
        print("  Phase 3: 删除操作...")
        self._parallel_process(deleted, self._process_delete, dry_run, "删除")

        # Phase 3.5: 硬链接操作 (V8.0)
        if hardlinked:
            print("  Phase 3.5: 硬链接操作...")
            self._parallel_process(hardlinked, self._process_hardlink, dry_run, "硬链接")

        if not dry_run:
            print("  Phase 4: 空目录清理...")
            self._cleanup_empty_dirs()
            self._write_log()
            self._rotate_logs()
        return self.stats

    def _parallel_process(self, nodes, handler, dry_run, label):
        if not nodes:
            return
        if dry_run or len(nodes) <= 20:
            for node in make_progress(nodes, desc=f"预览{label}", unit="项", disable=dry_run and len(nodes) <= 20):
                handler(node, dry_run)
            return
        completed = [0]
        progress_lock = threading.Lock()
        def _safe_handler(node):
            try:
                handler(node, dry_run)
                self._maybe_throttle()
                with progress_lock:
                    completed[0] += 1
                    if completed[0] % 200 == 0:
                        print(f"  {label}进度: {completed[0]}/{len(nodes)}")
            except Exception as e:
                with self._lock:
                    self.errors.add(node.full_path, f"{label}Failed", str(e))
                    self.stats["failed"] += 1
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(_safe_handler, n) for n in nodes]
            for future in make_progress(as_completed(futures), total=len(futures),
                                        desc=f"{label}并行", unit="项"):
                try:
                    future.result()
                except Exception:
                    pass

    def _process_move(self, node, dry_run):
        if not Win32IO.exists(node.full_path):
            with self._lock:
                node.status = NodeStatus.VANISHED
                self.stats["vanished"] += 1
            return
        if self._scan_timestamp:
            try:
                st = Win32IO.safe_stat(node.full_path)
                if st and st.st_mtime > self._scan_timestamp:
                    with self._lock:
                        node.status = NodeStatus.CHANGED
                        self.stats["changed"] += 1
                    return
            except OSError:
                pass
        target = node.target_path
        if not target:
            return
        if dry_run:
            with self._lock:
                node.status = NodeStatus.SUCCESS
                self.stats["success"] += 1
            return
        target_dir = os.path.dirname(target)
        os.makedirs(target_dir, exist_ok=True)
        for attempt in range(3):
            try:
                if Win32IO.exists(target):
                    target = self._resolve_conflict(target)
                    node.target_path = target
                if not Win32IO.move(node.full_path, target):
                    src_wp = _win_path(node.full_path) if Win32IO.exists(node.full_path) else node.full_path
                    dst_wp = _win_path(target)
                    shutil.copy2(src_wp, dst_wp)
                    Win32IO.delete_file(node.full_path)
                with self._lock:
                    self._log_op("MOVE", node.full_path, target)
                    node.status = NodeStatus.SUCCESS
                    self.stats["success"] += 1
                return
            except OSError:
                if attempt < 2:
                    time.sleep(0.5)
                continue
        with self._lock:
            node.status = NodeStatus.LOCKED
            node.error_msg = "移动失败"
            self.stats["locked"] += 1
            self.errors.add(node.full_path, "MoveFailed", "IO错误")

    def _process_delete(self, node, dry_run):
        if not Win32IO.exists(node.full_path):
            with self._lock:
                node.status = NodeStatus.VANISHED
                self.stats["vanished"] += 1
            return
        if dry_run:
            with self._lock:
                node.status = NodeStatus.SUCCESS
                self.stats["success"] += 1
            return
        for attempt in range(3):
            try:
                if not Win32IO.exists(node.full_path):
                    with self._lock:
                        node.status = NodeStatus.VANISHED
                        self.stats["vanished"] += 1
                    return
                trash_target = self._get_trash_path(node.full_path)
                os.makedirs(os.path.dirname(trash_target), exist_ok=True)
                if not Win32IO.move(node.full_path, trash_target):
                    src_wp = _win_path(node.full_path) if Win32IO.exists(node.full_path) else node.full_path
                    dst_wp = _win_path(trash_target)
                    shutil.copy2(src_wp, dst_wp)
                    Win32IO.delete_file(node.full_path)
                with self._lock:
                    self._log_op("DELETE", node.full_path, trash_target, status="TRASHED")
                    node.status = NodeStatus.TRASHED
                    self.stats["success"] += 1
                    self.stats["trashed"] += 1
                return
            except OSError:
                if attempt < 2:
                    time.sleep(0.5)
                continue
        with self._lock:
            node.status = NodeStatus.LOCKED
            node.error_msg = "删除失败"
            self.stats["locked"] += 1
            self.errors.add(node.full_path, "DeleteFailed", "IO错误")

    def _process_hardlink(self, node, dry_run):
        """V8.0: 将重复文件转换为硬链接"""
        if not Win32IO.exists(node.full_path):
            with self._lock:
                node.status = NodeStatus.VANISHED
                self.stats["vanished"] += 1
            return
        if not node.target_path or not Win32IO.exists(node.target_path):
            with self._lock:
                node.status = NodeStatus.FAILED
                self.stats["failed"] += 1
                self.errors.add(node.full_path, "HardLinkTargetMissing",
                                f"目标文件不存在: {node.target_path}")
            return
        if dry_run:
            with self._lock:
                node.status = NodeStatus.SUCCESS
                self.stats["success"] += 1
            return
        # 安全操作: 先移入回收站，再创建硬链接，失败则恢复
        trash_target = self._get_trash_path(node.full_path)
        os.makedirs(os.path.dirname(trash_target), exist_ok=True)
        try:
            if not Win32IO.move(node.full_path, trash_target):
                raise OSError("移动到回收站失败")
            try:
                wp_src = _win_path(node.target_path)
                wp_dst = _win_path(node.full_path)
                os.makedirs(os.path.dirname(wp_dst), exist_ok=True)
                os.link(wp_src, wp_dst)
                with self._lock:
                    self._log_op("HARDLINK", node.full_path, node.target_path,
                                status="HARDLINKED")
                    node.status = NodeStatus.HARDLINKED
                    self.stats["success"] += 1
                    self.stats["hardlinked"] += 1
            except OSError as link_err:
                # 硬链接创建失败，从回收站恢复
                Win32IO.move(trash_target, node.full_path)
                with self._lock:
                    self.errors.add(node.full_path, "HardLinkFailed", str(link_err))
                    self.stats["failed"] += 1
        except OSError as move_err:
            with self._lock:
                self.errors.add(node.full_path, "HardLinkMoveFailed", str(move_err))
                self.stats["failed"] += 1

    def _cleanup_empty_dirs(self):
        dirs = sorted([n for n in self.nodes if n.is_dir],
                      key=lambda n: n.depth, reverse=True)
        for d in dirs:
            if d.action == NodeAction.DELETE:
                continue
            try:
                if Win32IO.is_dir(d.full_path) and not Win32IO.safe_listdir(d.full_path):
                    if Win32IO.remove_directory(d.full_path):
                        with self._lock:
                            self._log_op("RMDIR", d.full_path, "")
            except OSError:
                pass

    def _resolve_conflict(self, target_path):
        dir_name = os.path.dirname(target_path)
        base, ext = os.path.splitext(os.path.basename(target_path))
        counter = 1
        while True:
            new_path = os.path.join(dir_name, f"{base}_{counter}{ext}")
            if not Win32IO.exists(new_path):
                return new_path
            counter += 1

    def _get_trash_path(self, original_path):
        try:
            rel = os.path.relpath(original_path, self.root_path)
        except ValueError:
            rel = os.path.basename(original_path)
        target = os.path.join(self.trash_dir, rel)
        base, ext = os.path.splitext(target)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_suffix = uuid.uuid4().hex[:8]
        return f"{base}_{ts}_{unique_suffix}{ext}"

    def _log_op(self, action, source, target, status="SUCCESS"):
        self._seq += 1
        self.op_logs.append(OperationLog(
            seq=self._seq, action=action, source=source, target=target,
            timestamp=datetime.now(timezone.utc).isoformat(), status=status))

    def _maybe_throttle(self):
        with self._lock:
            self._throttle += 1
            if self._throttle % THROTTLE_BATCH == 0:
                time.sleep(THROTTLE_SLEEP)

    def _write_log(self):
        if not self.op_logs:
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = os.path.join(self.log_dir, f"operations_{ts}.jsonl")
        with open(log_path, "w", encoding="utf-8") as f:
            for entry in self.op_logs:
                f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
        print(f"📝 操作日志: {log_path}")

    def _rotate_logs(self):
        try:
            if not os.path.isdir(self.log_dir):
                return
            log_files = sorted([f for f in os.listdir(self.log_dir)
                                if f.startswith("operations_") and f.endswith(".jsonl")])
            if len(log_files) > MAX_LOG_FILES:
                for old in log_files[:len(log_files) - MAX_LOG_FILES]:
                    try:
                        os.remove(os.path.join(self.log_dir, old))
                    except OSError:
                        pass
        except OSError:
            pass

    def generate_rollback_script(self):
        if not self.op_logs:
            return "#!/usr/bin/env python3\n# 无操作记录，无需回滚\nimport sys\nsys.exit(0)\n"
        lines = [
            '#!/usr/bin/env python3',
            '"""回滚脚本 - 自动生成"""',
            f'# 生成时间: {datetime.now().isoformat()}',
            f'# 共 {len(self.op_logs)} 条操作记录',
            '', 'import os', 'import shutil', 'import sys', '',
            'def main():', '    restored = 0', '    errors = 0', '',
        ]
        mkdir_dirs: Set[str] = set()
        for entry in reversed(self.op_logs):
            if entry.action in ("MOVE", "DELETE", "HARDLINK"):
                src_dir = os.path.dirname(entry.source)
                if src_dir:
                    mkdir_dirs.add(src_dir)
            elif entry.action == "RMDIR":
                mkdir_dirs.add(entry.source)
        for d in sorted(mkdir_dirs):
            lines.append(f'    os.makedirs({json.dumps(d)}, exist_ok=True)')
        lines.append('')
        lines.append('    # --- 操作回滚 ---')
        for entry in reversed(self.op_logs):
            if entry.action in ("MOVE", "DELETE", "HARDLINK"):
                lines.append(f'    try:')
                lines.append(f'        shutil.move({json.dumps(entry.target)}, {json.dumps(entry.source)})')
                lines.append(f'        restored += 1')
                lines.append(f'    except Exception as e:')
                lines.append(f'        print(f"❌ 回滚失败: {entry.source} — {{e}}")')
                lines.append(f'        errors += 1')
                lines.append('')
            elif entry.action == "RMDIR":
                lines.append(f'    try:')
                lines.append(f'        os.makedirs({json.dumps(entry.source)}, exist_ok=True)')
                lines.append(f'        restored += 1')
                lines.append(f'    except Exception as e:')
                lines.append(f'        print(f"❌ 目录重建失败: {entry.source} — {{e}}")')
                lines.append(f'        errors += 1')
                lines.append('')
        lines.append(f'    print(f"✅ 回滚完成: 恢复 {{restored}} 项, 失败 {{errors}} 项")')
        lines.append('    return 0 if errors == 0 else 1')
        lines.append('', 'if __name__ == "__main__":', '    sys.exit(main())')
        return '\n'.join(lines)

    def generate_report(self):
        lines = [
            "=" * 55, "📊 操作报告", "=" * 55,
            f"  ✅ 成功处理: {self.stats['success']} 项",
            f"  🗑️ 移入回收站: {self.stats['trashed']} 项",
        ]
        if self.stats["hardlinked"]:
            lines.append(f"  🔗 转为硬链接: {self.stats['hardlinked']} 项")
        for key, label in [("vanished", "Vanished"), ("locked", "Locked"),
                           ("changed", "Changed"), ("conflict", "Conflict"),
                           ("failed", "Failed")]:
            if self.stats[key]:
                lines.append(f"  - {label}: {self.stats[key]} 项")
        lines.append("=" * 55)
        return "\n".join(lines)

# ============================================================
# V8.0: HTML 可视化报告生成器
# ============================================================
class HTMLReportGenerator:
    """生成自包含的 HTML 可视化报告"""

    def generate(self, stats: Dict, nodes: List[FileNode],
                 dup_groups: List[Dict], freed_bytes: int,
                 output_path: str) -> str:
        freed_mb = freed_bytes / (1024 * 1024)
        total_mb = stats.get("total_size_mb", 0)

        # 分类统计
        delete_nodes = [n for n in nodes if n.action == NodeAction.DELETE]
        move_nodes = [n for n in nodes if n.action == NodeAction.MOVE]
        hardlink_nodes = [n for n in nodes if n.action == NodeAction.HARDLINK]
        delete_size = sum(n.size_bytes for n in delete_nodes)
        hardlink_save = sum(n.size_bytes for n in hardlink_nodes)
        total_freed_mb = (delete_size + hardlink_save) / (1024 * 1024)

        # 按原因分组
        reason_groups: Dict[str, List] = defaultdict(list)
        for n in nodes:
            if n.action != NodeAction.NONE:
                reason_groups[n.reason or n.action.value].append(n)

        # 扩展名分布（饼图数据）
        ext_data = stats.get("extension_stats", {})
        ext_top = list(ext_data.items())[:8]

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>文件夹重组报告 V{VERSION}</title>
<style>
:root {{
    --bg: #0d1117; --card: #161b22; --border: #30363d;
    --text: #c9d1d9; --text-dim: #8b949e; --accent: #58a6ff;
    --green: #3fb950; --red: #f85149; --yellow: #d29922;
    --orange: #db6d28; --purple: #bc8cff;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: 'Segoe UI', -apple-system, sans-serif;
    background: var(--bg); color: var(--text);
    line-height: 1.6; padding: 2rem; max-width: 1200px; margin: 0 auto;
}}
h1 {{ color: var(--accent); margin-bottom: 0.5rem; font-size: 1.8rem; }}
h2 {{ color: var(--accent); margin: 2rem 0 1rem; font-size: 1.3rem;
     border-bottom: 1px solid var(--border); padding-bottom: 0.5rem; }}
.subtitle {{ color: var(--text-dim); margin-bottom: 2rem; }}
.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
          gap: 1rem; margin-bottom: 2rem; }}
.card {{
    background: var(--card); border: 1px solid var(--border);
    border-radius: 8px; padding: 1.2rem;
}}
.card .value {{ font-size: 2rem; font-weight: 700; color: var(--accent); }}
.card .label {{ color: var(--text-dim); font-size: 0.9rem; margin-top: 0.3rem; }}
.card.green .value {{ color: var(--green); }}
.card.red .value {{ color: var(--red); }}
.card.yellow .value {{ color: var(--yellow); }}
.card.purple .value {{ color: var(--purple); }}

/* 空间收益柱状图 */
.bar-chart {{ margin: 1.5rem 0; }}
.bar-row {{ display: flex; align-items: center; margin: 0.5rem 0; }}
.bar-label {{ width: 120px; font-size: 0.85rem; color: var(--text-dim); text-align: right;
              padding-right: 1rem; flex-shrink: 0; }}
.bar-track {{ flex: 1; background: var(--card); border-radius: 4px; height: 28px;
              overflow: hidden; }}
.bar-fill {{ height: 100%; border-radius: 4px; display: flex; align-items: center;
             padding-left: 0.5rem; font-size: 0.8rem; font-weight: 600;
             transition: width 0.5s ease; min-width: 2rem; }}
.bar-fill.green {{ background: linear-gradient(90deg, #238636, #3fb950); }}
.bar-fill.blue {{ background: linear-gradient(90deg, #1f6feb, #58a6ff); }}
.bar-fill.orange {{ background: linear-gradient(90deg, #9e6a03, #d29922); }}
.bar-fill.purple {{ background: linear-gradient(90deg, #8957e5, #bc8cff); }}

/* 表格 */
table {{ width: 100%; border-collapse: collapse; margin: 1rem 0; font-size: 0.85rem; }}
th {{ background: var(--card); color: var(--text-dim); text-align: left;
     padding: 0.6rem 0.8rem; border-bottom: 2px solid var(--border); position: sticky; top: 0; }}
td {{ padding: 0.5rem 0.8rem; border-bottom: 1px solid var(--border); word-break: break-all; }}
tr:hover {{ background: rgba(88, 166, 255, 0.05); }}
.action-delete {{ color: var(--red); }}
.action-move {{ color: var(--blue); }}
.action-hardlink {{ color: var(--purple); }}

.filter-bar {{ display: flex; gap: 0.5rem; margin: 1rem 0; flex-wrap: wrap; }}
.filter-btn {{
    background: var(--card); border: 1px solid var(--border);
    color: var(--text-dim); padding: 0.4rem 1rem; border-radius: 6px;
    cursor: pointer; font-size: 0.85rem; transition: all 0.2s;
}}
.filter-btn:hover, .filter-btn.active {{
    background: var(--accent); color: #fff; border-color: var(--accent);
}}

.tree {{ font-family: 'Cascadia Code', 'Consolas', monospace; font-size: 0.8rem;
        padding: 1rem; background: var(--card); border-radius: 8px;
        max-height: 400px; overflow-y: auto; white-space: nowrap; }}
.tree-item {{ padding: 0.15rem 0; }}
.tree-dir {{ color: var(--accent); }}
.tree-del {{ color: var(--red); text-decoration: line-through; opacity: 0.6; }}

.summary-box {{
    background: linear-gradient(135deg, #161b22, #1c2333);
    border: 1px solid var(--border); border-radius: 12px;
    padding: 2rem; margin: 2rem 0; text-align: center;
}}
.summary-box .big {{ font-size: 3rem; font-weight: 800; color: var(--green); }}
.summary-box .sub {{ color: var(--text-dim); font-size: 1rem; margin-top: 0.5rem; }}

@media (max-width: 768px) {{
    body {{ padding: 1rem; }}
    .cards {{ grid-template-columns: repeat(2, 1fr); }}
    h1 {{ font-size: 1.4rem; }}
}}
</style>
</head>
<body>

<h1>📊 文件夹重组报告</h1>
<p class="subtitle">引擎版本 V{VERSION} | 生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>

<div class="summary-box">
    <div class="big">{_fmt_size(delete_size + hardlink_save)}</div>
    <div class="sub">预计释放空间 (删除 {_fmt_size(delete_size)} + 硬链接 {_fmt_size(hardlink_save)})</div>
</div>

<div class="cards">
    <div class="card"><div class="value">{stats.get('total_files', 0):,}</div><div class="label">总文件数</div></div>
    <div class="card"><div class="value">{stats.get('total_dirs', 0):,}</div><div class="label">总目录数</div></div>
    <div class="card"><div class="value">{stats.get('total_size_mb', 0):,.1f} MB</div><div class="label">总大小</div></div>
    <div class="card"><div class="value">{stats.get('max_depth', 0)}</div><div class="label">最大深度</div></div>
    <div class="card red"><div class="value">{len(delete_nodes)}</div><div class="label">待删除项</div></div>
    <div class="card blue"><div class="value">{len(move_nodes)}</div><div class="label">待移动项</div></div>
    <div class="card purple"><div class="value">{len(hardlink_nodes)}</div><div class="label">硬链接项</div></div>
    <div class="card yellow"><div class="value">{len(dup_groups)}</div><div class="label">重复文件组</div></div>
</div>

<h2>📦 操作分布</h2>
<div class="bar-chart">
    <div class="bar-row">
        <div class="bar-label">缓存清理</div>
        <div class="bar-track"><div class="bar-fill green" style="width:{self._bar_pct(len([n for n in delete_nodes if '缓存' in n.reason]), len(delete_nodes) or 1)}%">{len([n for n in delete_nodes if '缓存' in n.reason])}</div></div>
    </div>
    <div class="bar-row">
        <div class="bar-label">临时文件</div>
        <div class="bar-track"><div class="bar-fill orange" style="width:{self._bar_pct(len([n for n in delete_nodes if '临时' in n.reason]), len(delete_nodes) or 1)}%">{len([n for n in delete_nodes if '临时' in n.reason])}</div></div>
    </div>
    <div class="bar-row">
        <div class="bar-label">重复文件</div>
        <div class="bar-track"><div class="bar-fill red" style="width:{self._bar_pct(len([n for n in delete_nodes if '重复' in n.reason]), len(delete_nodes) or 1)}%">{len([n for n in delete_nodes if '重复' in n.reason])}</div></div>
    </div>
    <div class="bar-row">
        <div class="bar-label">硬链接转换</div>
        <div class="bar-track"><div class="bar-fill purple" style="width:{self._bar_pct(len(hardlink_nodes), max(len(delete_nodes), 1))}%">{len(hardlink_nodes)}</div></div>
    </div>
    <div class="bar-row">
        <div class="bar-label">扁平化移动</div>
        <div class="bar-track"><div class="bar-fill blue" style="width:{self._bar_pct(len(move_nodes), max(len(move_nodes) + len(delete_nodes), 1))}%">{len(move_nodes)}</div></div>
    </div>
</div>

<h2>📋 扩展名分布 (Top {len(ext_top)})</h2>
<div class="bar-chart">"""

        max_ext = ext_top[0][1] if ext_top else 1
        colors = ["green", "blue", "orange", "purple", "green", "blue", "orange", "purple"]
        for i, (ext, cnt) in enumerate(ext_top):
            pct = cnt / max_ext * 100
            color = colors[i % len(colors)]
            html += f"""
    <div class="bar-row">
        <div class="bar-label">{html_mod.escape(ext)}</div>
        <div class="bar-track"><div class="bar-fill {color}" style="width:{pct:.0f}%">{cnt:,}</div></div>
    </div>"""

        html += f"""
</div>

<h2>📁 重复文件详情</h2>
<div class="filter-bar">
    <button class="filter-btn active" onclick="filterTable('all')">全部 ({len(dup_groups)})</button>
</div>
<div style="max-height:400px;overflow-y:auto;">
<table id="dupTable">
<tr><th>组</th><th>大小</th><th>操作</th><th>保留路径</th><th>处理路径</th></tr>"""

        for g in dup_groups[:50]:
            keep = g.get("keep", {})
            deletes = g.get("delete", [])
            action_cls = "action-hardlink" if g.get("action") == "HARDLINK" else "action-delete"
            del_paths = "<br>".join(html_mod.escape(d["path"][-80:]) for d in deletes[:3])
            if len(deletes) > 3:
                del_paths += f"<br>+{len(deletes)-3} 项"
            html += f"""<tr>
<td>{g['group_id']}</td>
<td>{html_mod.escape(g['size'])}</td>
<td class="{action_cls}">{html_mod.escape(g.get('action', 'DELETE'))}</td>
<td>{html_mod.escape(keep.get('path', '?')[-80:])}</td>
<td>{del_paths}</td></tr>"""

        html += f"""</table></div>

<h2>🗑️ 删除预览 (前 50 项)</h2>
<div style="max-height:400px;overflow-y:auto;">
<table>
<tr><th>#</th><th>文件名</th><th>大小</th><th>原因</th></tr>"""

        for i, n in enumerate(delete_nodes[:50], 1):
            html += f"""<tr class="action-delete">
<td>{i}</td>
<td>{html_mod.escape(n.file_name[:60])}</td>
<td>{_fmt_size(n.size_bytes)}</td>
<td>{html_mod.escape(n.reason[:60])}</td></tr>"""

        if len(delete_nodes) > 50:
            html += f"""<tr><td colspan="4" style="text-align:center;color:var(--text-dim)">... 共 {len(delete_nodes)} 项</td></tr>"""

        html += f"""</table></div>

<h2>📦 移动预览 (前 50 项)</h2>
<div style="max-height:400px;overflow-y:auto;">
<table>
<tr><th>#</th><th>源路径</th><th>目标路径</th></tr>"""

        for i, n in enumerate(move_nodes[:50], 1):
            html += f"""<tr class="action-move">
<td>{i}</td>
<td>{html_mod.escape(n.full_path[-70:])}</td>
<td>{html_mod.escape(n.target_path[-70:])}</td></tr>"""

        if len(move_nodes) > 50:
            html += f"""<tr><td colspan="3" style="text-align:center;color:var(--text-dim)">... 共 {len(move_nodes)} 项</td></tr>"""

        html += f"""</table></div>

<footer style="margin-top:3rem;text-align:center;color:var(--text-dim);font-size:0.8rem;">
    文件夹结构智能重组引擎 V{VERSION} | 所有"删除"操作实际为移入 .restructure_trash/ 目录
</footer>
</body>
</html>"""

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)
        return output_path

    @staticmethod
    def _bar_pct(value, total):
        return max(3, min(100, value / total * 100)) if total > 0 else 3

# ============================================================
# V8.0: 交互式预览选择
# ============================================================
class InteractivePreview:
    """交互式操作选择器"""

    def __init__(self, nodes: List[FileNode], dup_groups: List[Dict]):
        self.nodes = nodes
        self.dup_groups = dup_groups

    def categorize(self) -> Dict[str, Dict]:
        """将操作分组为可选类别"""
        categories = {}
        # 1. 缓存清理
        cache_nodes = [n for n in self.nodes
                       if n.action == NodeAction.DELETE and "缓存" in n.reason]
        if cache_nodes:
            cache_size = sum(n.size_bytes for n in cache_nodes)
            categories["cache"] = {
                "label": f"缓存目录清理 ({len(cache_nodes)} 项, {_fmt_size(cache_size)})",
                "nodes": cache_nodes, "enabled": True,
            }

        # 2. 垃圾文件
        junk_nodes = [n for n in self.nodes
                      if n.action == NodeAction.DELETE and "临时" in n.reason]
        if junk_nodes:
            junk_size = sum(n.size_bytes for n in junk_nodes)
            categories["junk"] = {
                "label": f"垃圾文件清理 ({len(junk_nodes)} 项, {_fmt_size(junk_size)})",
                "nodes": junk_nodes, "enabled": True,
            }

        # 3. 重复文件
        dedup_nodes = [n for n in self.nodes
                       if n.action in (NodeAction.DELETE, NodeAction.HARDLINK)
                       and ("重复" in n.reason or "硬链接" in n.reason)]
        if dedup_nodes:
            dedup_size = sum(n.size_bytes for n in dedup_nodes if n.action == NodeAction.DELETE)
            hl_size = sum(n.size_bytes for n in dedup_nodes if n.action == NodeAction.HARDLINK)
            categories["dedup"] = {
                "label": f"重复文件处理 ({len(dedup_nodes)} 项, "
                         f"删除 {_fmt_size(dedup_size)} / 硬链接 {_fmt_size(hl_size)})",
                "nodes": dedup_nodes, "enabled": True,
            }

        # 4. 冗余解包
        unpack_nodes = [n for n in self.nodes
                        if n.action == NodeAction.MOVE and "解包" in n.reason]
        del_unpack = [n for n in self.nodes
                      if n.action == NodeAction.DELETE and "冗余" in n.reason]
        if unpack_nodes or del_unpack:
            categories["restructure"] = {
                "label": f"冗余目录解包 ({len(unpack_nodes)} 移动, {len(del_unpack)} 删除)",
                "nodes": unpack_nodes + del_unpack, "enabled": True,
            }

        # 5. 扁平化
        flatten_nodes = [n for n in self.nodes
                         if n.action == NodeAction.MOVE and "扁平化" in n.reason]
        if flatten_nodes:
            categories["flatten"] = {
                "label": f"扁平化移动 ({len(flatten_nodes)} 项)",
                "nodes": flatten_nodes, "enabled": True,
            }

        return categories

    def run(self) -> Dict[str, bool]:
        """运行交互式选择，返回启用/禁用的类别"""
        categories = self.categorize()
        if not categories:
            print("\n✅ 无需执行任何操作")
            return {}

        print("\n" + "=" * 60)
        print("🎯 交互式操作选择")
        print("=" * 60)
        print("输入要包含的操作编号 (逗号分隔, 如 1,3,5)")
        print("  all  → 全部启用  |  none → 全部禁用  |  直接回车 → 保持默认(全选)")
        print("-" * 60)

        keys = list(categories.keys())
        for i, key in enumerate(keys, 1):
            cat = categories[key]
            status = "✅" if cat["enabled"] else "❌"
            print(f"  [{i}] {status} {cat['label']}")

        # 计算总空间
        total_delete = sum(
            n.size_bytes for n in self.nodes
            if n.action in (NodeAction.DELETE, NodeAction.HARDLINK))
        total_move = len([n for n in self.nodes if n.action == NodeAction.MOVE])
        print("-" * 60)
        print(f"  💡 预计释放: {_fmt_size(total_delete)}  |  移动: {total_move} 项")

        while True:
            choice = input("\n请选择 (编号/all/none/回车): ").strip().lower()
            if not choice:
                return {k: True for k in keys}
            if choice == "all":
                return {k: True for k in keys}
            if choice == "none":
                return {k: False for k in keys}
            try:
                selected = [int(x.strip()) for x in choice.split(",")]
                result = {}
                for k in keys:
                    result[k] = False
                for s in selected:
                    if 1 <= s <= len(keys):
                        result[keys[s - 1]] = True
                return result
            except ValueError:
                print("⚠️ 输入格式无效，请重试")

    def apply_selection(self, selection: Dict[str, bool]):
        """根据用户选择调整节点的 action"""
        categories = self.categorize()
        enabled_nodes: Set[str] = set()
        for key, enabled in selection.items():
            if enabled and key in categories:
                for n in categories[key]["nodes"]:
                    enabled_nodes.add(n.full_path)

        for n in self.nodes:
            if n.action != NodeAction.NONE and n.full_path not in enabled_nodes:
                n.action = NodeAction.NONE
                n.target_path = ""
                n.reason = ""

# ============================================================
# 名称工具
# ============================================================
def _sanitize_filename(name):
    clean = "".join(c if c not in ILLEGAL_CHARS else "_" for c in name)
    clean = clean.strip(" .")
    if not clean:
        clean = "_unnamed"
    name_no_ext = os.path.splitext(clean)[0].upper()
    if name_no_ext in WINDOWS_RESERVED:
        clean = f"_{clean}"
    return clean

def _truncate_name(name, max_bytes):
    encoded = name.encode("utf-8")
    if len(encoded) <= max_bytes:
        return name
    base, ext = os.path.splitext(name)
    name_hash = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
    hash_suffix = f"_{name_hash}"
    available = max_bytes - len(ext.encode("utf-8")) - len(hash_suffix.encode("utf-8"))
    if available < 1:
        available = 1
    truncated = ""
    current = 0
    for ch in base:
        cb = len(ch.encode("utf-8"))
        if current + cb > available:
            break
        truncated += ch
        current += cb
    if not truncated:
        truncated = "x"
    return f"{truncated}{hash_suffix}{ext}"

def _fmt_size(size_bytes):
    size = size_bytes
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"

def _node_attrs_str(n):
    parts = []
    if getattr(n, 'is_hidden', False): parts.append("H")
    if getattr(n, 'is_readonly', False): parts.append("R")
    if getattr(n, 'is_system', False): parts.append("S")
    if getattr(n, 'has_zone_identifier', False): parts.append("Z")
    reparse = getattr(n, 'reparse_type', None)
    if reparse and reparse != ReparseType.NONE:
        prefix_map = {"Symlink": "S", "Junction": "J", "HardLink": "H", "MountPoint": "M"}
        parts.append(prefix_map.get(reparse.value, "?"))
    return "".join(parts) if parts else "-"

def _parse_actions(action_str):
    mapping = {
        "perm": PermissionAction.GRANT_FULL_CONTROL,
        "permission": PermissionAction.GRANT_FULL_CONTROL,
        "readonly": PermissionAction.REMOVE_READONLY,
        "ro": PermissionAction.REMOVE_READONLY,
        "hidden": PermissionAction.REMOVE_HIDDEN,
        "h": PermissionAction.REMOVE_HIDDEN,
        "zone": PermissionAction.CLEAR_ZONE_IDENTIFIER,
        "z": PermissionAction.CLEAR_ZONE_IDENTIFIER,
        "all": PermissionAction.ALL,
    }
    actions = set()
    for part in action_str.lower().split(","):
        part = part.strip()
        if part in mapping:
            actions.add(mapping[part])
    return actions

# ============================================================
# 离线文件列表解析器
# ============================================================
class FileListParser:
    _CUSTOM_FMT_RE = re.compile(r'^\[\{?(\d+)\}?\]\s+(.+?)\s+#(DIR|FILE)\s*$')

    def __init__(self):
        self.total_truncated = 0
        self.total_skipped = 0
        self._type_map: Dict[str, bool] = {}
        self._depth_map: Dict[str, int] = {}
        self._has_custom_format = False

    def load_files(self, filepaths):
        all_lines = []
        self.total_truncated = 0
        self.total_skipped = 0
        self._type_map = {}
        self._depth_map = {}
        self._has_custom_format = False
        for fp in filepaths:
            if not os.path.isfile(fp):
                print(f"❌ 文件不存在: {fp}")
                continue
            print(f"📂 加载文件列表: {fp}")
            lines, truncated, skipped = self._load_single(fp)
            all_lines.extend(lines)
            self.total_truncated += truncated
            self.total_skipped += skipped
        before = len(all_lines)
        all_lines = list(dict.fromkeys(all_lines))
        if len(all_lines) < before:
            print(f"  跨文件去重: {before} → {len(all_lines)} 行")
        return all_lines

    def _load_single(self, filepath):
        with open(filepath, "r", encoding="utf-8-sig") as f:
            raw_lines = f.readlines()
        valid = []
        tc = sc = 0
        in_header = True
        found_fullname = False
        for raw in raw_lines:
            line = raw.rstrip("\r\n")
            if not line.strip():
                sc += 1; continue
            m = self._CUSTOM_FMT_RE.match(line)
            if m:
                self._has_custom_format = True
                self._type_map[m.group(2).strip()] = (m.group(3) == "DIR")
                self._depth_map[m.group(2).strip()] = int(m.group(1))
                valid.append(m.group(2).strip())
                continue
            if in_header:
                stripped = line.strip()
                if stripped == "FullName" or "FullName" in stripped:
                    found_fullname = True; sc += 1; continue
                if stripped.startswith("-" * 8):
                    in_header = False; sc += 1; continue
                if not found_fullname:
                    in_header = False
                else:
                    in_header = False
            line = line.rstrip()
            if TRUNCATED_LINE_RE.search(line):
                tc += 1; continue
            valid.append(line)
        return valid, tc, sc

    def parse_to_virtual_nodes(self, lines):
        SEP_WIN = "\\"
        use_custom = self._has_custom_format and bool(self._type_map)
        root_parts = None
        for line in lines:
            parts = line.split(SEP_WIN)
            if root_parts is None:
                root_parts = list(parts)
            else:
                new_root = []
                for a, b in zip(root_parts, parts):
                    if a == b: new_root.append(a)
                    else: break
                root_parts = new_root
        root = SEP_WIN.join(root_parts) if root_parts else ""
        if re.match(r'^[A-Z]:$', root, re.IGNORECASE):
            root = root + SEP_WIN
        root_depth = len(root_parts) if root_parts else 1

        all_intermediate: Set[str] = set()
        for line in lines:
            parts = line.split(SEP_WIN)
            for i in range(root_depth, len(parts) - 1):
                all_intermediate.add(SEP_WIN.join(parts[:i + 1]))

        sorted_paths = sorted(all_intermediate | set(lines), key=lambda p: p.lower())

        def has_children(path):
            prefix = path.lower() + SEP_WIN
            lo = bisect.bisect_left(sorted_paths, prefix, key=lambda p: p.lower())
            return lo < len(sorted_paths) and sorted_paths[lo].lower().startswith(prefix)

        is_dir_map = {}
        for line in lines:
            if line in all_intermediate:
                is_dir_map[line] = True
            elif use_custom and line in self._type_map:
                is_dir_map[line] = self._type_map[line]
            elif has_children(line):
                is_dir_map[line] = True
            else:
                is_dir_map[line] = False

        nodes = []
        existing = set()
        for line in lines:
            parts = line.split(SEP_WIN)
            rel_depth = self._depth_map.get(line, len(parts) - root_depth) if use_custom else len(parts) - root_depth
            fname = parts[-1] if parts else ""
            is_dir = is_dir_map.get(line, False)
            ext = "" if is_dir else ("." + fname.rsplit(".", 1)[1].lower() if "." in fname else "")
            nodes.append(VirtualFileNode(full_path=line, file_name=fname, extension=ext,
                                         depth=rel_depth, is_dir=is_dir))
            existing.add(line)

        implicit = 0
        for dp in all_intermediate:
            if dp not in existing:
                dp_parts = dp.split(SEP_WIN)
                nodes.append(VirtualFileNode(full_path=dp, file_name=dp_parts[-1],
                                             extension="", depth=len(dp_parts) - root_depth, is_dir=True))
                implicit += 1

        stats = {"root": root, "total_input_lines": len(lines),
                 "dirs_detected": sum(1 for v in is_dir_map.values() if v),
                 "files_detected": sum(1 for v in is_dir_map.values() if not v),
                 "implicit_dirs": implicit, "custom_format": use_custom}
        return nodes, root, stats

# ============================================================
# 离线分析引擎
# ============================================================
class OfflineDiagnosticEngine:
    def __init__(self, nodes, root):
        self.nodes = nodes
        self.root = root

    def get_stats(self, top_n=10):
        files = [n for n in self.nodes if not n.is_dir]
        dirs = [n for n in self.nodes if n.is_dir]
        deepest = sorted(files, key=lambda n: n.depth, reverse=True)[:top_n]
        ext_count: Dict[str, int] = defaultdict(int)
        for n in files:
            ext_count[n.extension or "(无扩展名)"] += 1
        long_names = []
        for n in self.nodes:
            try:
                if len(n.full_path.encode("utf-8")) > MAX_PATH_LEGACY:
                    long_names.append(n.full_path)
            except (UnicodeEncodeError, UnicodeDecodeError):
                long_names.append(n.full_path)
        return {
            "root": self.root, "total_items": len(self.nodes),
            "total_files": len(files), "total_dirs": len(dirs),
            "max_depth": max((n.depth for n in self.nodes), default=0),
            "long_path_files": len(long_names), "long_path_examples": long_names[:5],
            "deepest_files": [(n.full_path, n.depth) for n in deepest[:5]],
            "extension_stats": dict(sorted(ext_count.items(), key=lambda x: x[1], reverse=True)[:15]),
        }

class OfflineEliminationEngine:
    def __init__(self, nodes):
        self.nodes = nodes
        self.duplicate_groups: List[Dict] = []

    def mark_cache_dirs(self, extra_patterns=None, config=None):
        patterns = list(CACHE_DIR_PATTERNS)
        junk_patterns = list(JUNK_FILE_PATTERNS)
        if config:
            patterns = list(config.get("cache_patterns", patterns))
            junk_patterns = list(config.get("junk_patterns", junk_patterns))
        if extra_patterns:
            patterns.extend(extra_patterns)
        for node in self.nodes:
            if node.action != NodeAction.NONE:
                continue
            if not node.is_dir:
                for pattern in junk_patterns:
                    if fnmatch.fnmatch(node.file_name.lower(), pattern.lower()):
                        node.action = NodeAction.DELETE
                        node.reason = f"临时文件: {pattern}"
                        break
        cache_roots = []
        for node in self.nodes:
            if node.action != NodeAction.NONE:
                continue
            for pattern in patterns:
                if fnmatch.fnmatch(node.file_name.lower(), pattern.lower()):
                    cache_roots.append(node)
                    node.action = NodeAction.DELETE
                    node.reason = f"缓存目录: {pattern}"
                    break
        dir_roots = [n for n in cache_roots if n.is_dir]
        if dir_roots:
            sorted_nodes = sorted(self.nodes, key=lambda n: n.full_path.lower())
            for root in dir_roots:
                prefix = root.full_path.lower() + "\\"
                lo = bisect.bisect_left(sorted_nodes, prefix, key=lambda n: n.full_path.lower())
                for j in range(lo, len(sorted_nodes)):
                    if sorted_nodes[j].full_path.lower().startswith(prefix):
                        if sorted_nodes[j].action == NodeAction.NONE:
                            sorted_nodes[j].action = NodeAction.DELETE
                            sorted_nodes[j].reason = f"缓存子树: {root.file_name}"
                    else:
                        break

    def deduplicate_by_name(self):
        files = [n for n in self.nodes if not n.is_dir and n.action == NodeAction.NONE]
        name_groups: Dict[str, List[VirtualFileNode]] = defaultdict(list)
        for f in files:
            name_groups[f.file_name.lower()].append(f)
        dup_count = 0
        for name, group in name_groups.items():
            if len(group) < 2:
                continue
            has_size = any(n.size_bytes > 0 for n in group)
            if has_size:
                size_sub: Dict[int, List] = defaultdict(list)
                for n in group:
                    size_sub[n.size_bytes].append(n)
                for size, sub in size_sub.items():
                    if len(sub) < 2:
                        continue
                    keep = min(sub, key=lambda n: n.depth)
                    self.duplicate_groups.append({
                        "name": name, "size": size,
                        "keep": keep.full_path,
                        "delete": [n.full_path for n in sub if n.full_path != keep.full_path],
                    })
                    for n in sub:
                        if n.full_path != keep.full_path:
                            n.action = NodeAction.DELETE
                            n.reason = f"同名同大小重复 (保留: 深度{keep.depth})"
                            dup_count += 1
            else:
                keep = min(group, key=lambda n: n.depth)
                for n in group:
                    n.reason = "同名疑似重复，请手动确认"
                self.duplicate_groups.append({
                    "name": name, "size": None,
                    "keep": keep.full_path,
                    "delete": [m.full_path for m in group if m.full_path != keep.full_path],
                })
        return dup_count

class OfflineRestructureEngine:
    def __init__(self, nodes, root):
        self.nodes = nodes
        self.root = root

    def unpack_redundant(self):
        SEP_WIN = "\\"
        unpacked = 0
        dirs = [n for n in self.nodes if n.is_dir and n.action == NodeAction.NONE]
        dc = defaultdict(list)
        for d in dirs:
            parent = SEP_WIN.join(d.full_path.split(SEP_WIN)[:-1])
            dc[parent.lower()].append(d)
        for pl, children in dc.items():
            if len(children) != 1:
                continue
            child = children[0]
            pname = pl.split(SEP_WIN)[-1]
            if pl == self.root.lower(): continue
            if not pname or pname != child.file_name.lower():
                continue
            ap = SEP_WIN.join(child.full_path.split(SEP_WIN)[:-1])
            cp = child.full_path.lower() + SEP_WIN
            for node in self.nodes:
                if node.full_path.lower().startswith(cp) and node.action == NodeAction.NONE:
                    rel = node.full_path[len(child.full_path):].lstrip(SEP_WIN)
                    node.target_path = SEP_WIN.join([ap, rel]) if rel else ap
                    node.reason = f"解包: {child.file_name}"
                    node.action = NodeAction.MOVE
            child.action = NodeAction.DELETE
            child.reason = "冗余目录（子项已提级）"
            unpacked += 1
        return unpacked

# ============================================================
# 离线分析报告输出
# ============================================================
def print_offline_report(stats, parse_stats, cache_count, junk_count, dup_count,
                         dup_groups, unpacked, nodes, truncated_count, skipped_count,
                         flatten_count=0):
    sep = "=" * 65
    print(f"\n{sep}")
    print(f"📊 离线诊断报告 — 文件夹重组引擎 V{VERSION}")
    print(sep)
    print(f"  路径: {stats['root']}")
    print(f"  总项数: {stats['total_items']} (文件 {stats['total_files']}, 目录 {stats['total_dirs']})")
    print(f"  最大深度: {stats['max_depth']}")
    if truncated_count > 0:
        print(f"\n  ⛔ 截断警告: {truncated_count} 行被截断，已跳过")
    print(f"\n  📂 路径解析: {parse_stats['files_detected']} 文件 + {parse_stats['dirs_detected']} 目录")
    if stats["long_path_files"]:
        print(f"  ⚠️ 超长路径: {stats['long_path_files']} 个")
    print(f"\n📋 扩展名分布:")
    for ext, cnt in sorted(stats["extension_stats"].items(), key=lambda x: x[1], reverse=True)[:10]:
        bar = "█" * min(40, cnt // 500)
        print(f"  {ext:15s} {cnt:6d} {bar}")
    total_del = junk_count + cache_count + dup_count
    print(f"\n🔧 推荐操作:")
    print(f"  🗑️ 垃圾文件: {junk_count} 项")
    print(f"  🗑️ 缓存目录: {cache_count} 项")
    print(f"  🔄 冗余目录解包: {unpacked} 项")
    print(f"  🗑️ 同名重复文件: {dup_count} 项")
    print(f"\n  📊 总计可清理: {total_del} 项")
    if dup_groups:
        print(f"\n📋 重复文件详情 (前 15 组):")
        for i, g in enumerate(dup_groups[:15], 1):
            print(f"  组 {i}: {g['name'][:60]}")
            print(f"    ✅ 保留: {g['keep'][:90]}")
            for d in g["delete"][:3]:
                print(f"    🗑️ 删除: {d[:90]}")
    print(f"\n{sep}")

# ============================================================
# 主引擎 (V8.0 增强)
# ============================================================
class FolderRestructureEngine:
    def __init__(self, target_path: str):
        self.target_path = os.path.abspath(target_path)
        self.log_dir = os.path.join(self.target_path, LOG_DIR)
        self.trash_dir = os.path.join(self.target_path, TRASH_DIR)
        self.errors = ErrorCollector()
        self.diagnostic: Optional[DiagnosticEngine] = None
        self.perm_engine: Optional[PermissionEngine] = None
        self.elim_engine: Optional[EliminationEngine] = None
        self.nodes: List[FileNode] = []
        self._scan_ts: float = 0
        self.config: Dict = DEFAULT_CONFIG

    def run_safety_check(self):
        level, msg = SafetyChecker.check(self.target_path)
        icons = {SafetyLevel.FULL: "🟢", SafetyLevel.RESTRICTED: "🟡", SafetyLevel.BLOCKED: "🔴"}
        print(f"\n🔒 安全检查: {icons.get(level, '?')} Level {level.value} — {msg}")
        if level == SafetyLevel.BLOCKED:
            return False, msg
        elif level == SafetyLevel.RESTRICTED:
            print("  ⚠️ 受限模式：仅允许预览和诊断操作")
        return True, msg

    def diagnose(self, detect_zone=False, verbose=False):
        print(f"\n🔍 扫描目录: {self.target_path}")
        self.diagnostic = DiagnosticEngine(self.target_path)
        self.nodes = self.diagnostic.scan(detect_zone=detect_zone, verbose=verbose,
                                          config=self.config)
        self._scan_ts = self.diagnostic._scan_timestamp
        stats = self.diagnostic.get_stats()
        self._print_stats(stats)
        return stats

    def analyze(self, cache_patterns=None, dedup_strategy=None,
                # V8.0: --only-* 精细控制
                only_cache=False, only_dedup=False,
                only_restructure=False, only_flatten=False):
        """V8.0: 支持 --only-* 选择性执行"""
        result: Dict[str, int] = {}
        strategy = dedup_strategy or self.config.get("dedup_strategy", "shallowest")

        # V8.0: 如果有 --only-* 指定，跳过未选中的模块
        do_cache = not (only_dedup or only_restructure or only_flatten) or only_cache
        do_dedup = not (only_cache or only_restructure or only_flatten) or only_dedup
        do_restructure = not (only_cache or only_dedup or only_flatten) or only_restructure

        if do_cache:
            self.elim_engine = EliminationEngine(self.nodes, self.errors)
            self.elim_engine.mark_cache_dirs(cache_patterns, self.config)
            result["cache_marked"] = sum(1 for n in self.nodes if n.action == NodeAction.DELETE)
        else:
            self.elim_engine = EliminationEngine(self.nodes, self.errors)

        if do_dedup:
            result["duplicates_marked"] = self.elim_engine.deduplicate(strategy)
        else:
            result["duplicates_marked"] = 0

        if do_restructure:
            restruct = RestructureEngine(self.nodes, self.target_path, self.errors)
            result["redundant_unpacked"] = restruct.unpack_redundant()
        else:
            result["redundant_unpacked"] = 0

        # V8.0: 空间预估
        result["estimated_freed_bytes"] = sum(
            n.size_bytes for n in self.nodes
            if n.action in (NodeAction.DELETE, NodeAction.HARDLINK))
        result["estimated_freed_mb"] = round(
            result["estimated_freed_bytes"] / (1024 * 1024), 2)

        return result

    def fix_permissions(self, actions, dry_run=True):
        self.perm_engine = PermissionEngine(self.nodes)
        return self.perm_engine.apply_all(actions, dry_run=dry_run)

    def preview(self):
        actions = []
        for n in self.nodes:
            if n.action != NodeAction.NONE:
                actions.append({
                    "action": n.action.value, "source": n.full_path,
                    "target": getattr(n, "target_path", ""),
                    "size": _fmt_size(n.size_bytes) if not n.is_dir else "[目录]",
                    "depth": n.depth, "attrs": _node_attrs_str(n),
                    "reason": getattr(n, "reason", ""),
                })
        return actions

    def get_duplicate_preview(self):
        if self.elim_engine:
            return self.elim_engine.get_duplicate_groups()
        return []

    def get_space_estimate(self):
        """V8.0: 获取空间预估"""
        delete_size = sum(n.size_bytes for n in self.nodes if n.action == NodeAction.DELETE)
        hardlink_size = sum(n.size_bytes for n in self.nodes if n.action == NodeAction.HARDLINK)
        return {
            "delete_bytes": delete_size,
            "delete_mb": round(delete_size / (1024 * 1024), 2),
            "hardlink_bytes": hardlink_size,
            "hardlink_mb": round(hardlink_size / (1024 * 1024), 2),
            "total_bytes": delete_size + hardlink_size,
            "total_mb": round((delete_size + hardlink_size) / (1024 * 1024), 2),
        }

    def execute(self, dry_run=True):
        engine = ExecutionEngine(self.nodes, self.target_path, self.log_dir, self.trash_dir)
        engine.set_scan_timestamp(self._scan_ts)
        stats = engine.execute(dry_run=dry_run)
        if not dry_run:
            print(engine.generate_report())
            rollback = engine.generate_rollback_script()
            rollback_path = os.path.join(self.log_dir, "rollback.py")
            with open(rollback_path, "w", encoding="utf-8") as f:
                f.write(rollback)
            print(f"🔄 回滚脚本: {rollback_path}")
            if self.errors.has_errors():
                error_path = os.path.join(self.log_dir, "errors.json")
                with open(error_path, "w", encoding="utf-8") as f:
                    json.dump([asdict(e) for e in self.errors.errors], f, indent=2, ensure_ascii=False)
                print(f"⚠️ 错误报告: {error_path}")
        return stats

    def _print_stats(self, stats):
        print(f"\n{'=' * 55}")
        print(f"📊 目录诊断报告")
        print(f"{'=' * 55}")
        print(f"  路径: {stats['root']}")
        print(f"  总项数: {stats['total_items']} (文件 {stats['total_files']}, 目录 {stats['total_dirs']})")
        print(f"  总大小: {stats['total_size_mb']} MB")
        print(f"  最大深度: {stats['max_depth']}")
        print(f"  符号链接: {stats['symlinks']} | Junction: {stats['junctions']} | 硬链接: {stats['hard_links']}")
        perm_parts = []
        if stats["readonly_files"]: perm_parts.append(f"只读 {stats['readonly_files']}")
        if stats["hidden_files"]: perm_parts.append(f"隐藏 {stats['hidden_files']}")
        if stats["system_files"]: perm_parts.append(f"系统 {stats['system_files']}")
        if stats["zone_identifier_files"]: perm_parts.append(f"Zone标识 {stats['zone_identifier_files']}")
        if perm_parts:
            print(f"  🔑 属性: {' / '.join(perm_parts)}")
        if stats["long_path_files"]:
            print(f"  ⚠️ 超长路径 (>260字节): {stats['long_path_files']} 个文件")
        if stats["deepest_files"]:
            print(f"\n  📁 层级最深 (Top 5):")
            for p, d in stats["deepest_files"][:5]:
                print(f"    深度 {d}: {p}")
        if stats["largest_dirs"]:
            print(f"\n  📦 体积最大目录 (Top 5):")
            for p, s in stats["largest_dirs"][:5]:
                print(f"    {s}: {p}")
        if stats["extension_stats"]:
            print(f"\n  📋 扩展名分布 (Top 10):")
            for ext, cnt in sorted(stats["extension_stats"].items(), key=lambda x: x[1], reverse=True)[:10]:
                print(f"    {ext}: {cnt} 个文件")
        print(f"{'=' * 55}\n")

# ============================================================
# CLI (V8.0 增强)
# ============================================================
def _update_workers(n):
    global MAX_WORKERS
    MAX_WORKERS = max(1, n)

def main():
    parser = argparse.ArgumentParser(
        description=f"文件夹结构智能重组引擎 V{VERSION} (Windows + 离线分析 | 多线程加速)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python 文件夹重组工具V8.0.py D:\\Projects --diagnose
  python 文件夹重组工具V8.0.py D:\\Projects --preview-interactive
  python 文件夹重组工具V8.0.py D:\\Projects --execute
  python 文件夹重组工具V8.0.py D:\\Projects --only-cache --only-dedup --execute -y
  python 文件夹重组工具V8.0.py D:\\Projects --flatten-by ext --execute
  python 文件夹重组工具V8.0.py D:\\Projects --config restructure_config.json
  python 文件夹重组工具V8.0.py --from-list list.txt --html-report report.html
""")
    parser.add_argument("target", nargs="?", help="目标目录路径 (在线模式)")
    # V8.0 新增
    parser.add_argument("--config", metavar="FILE", help="配置文件路径 (JSON)")
    parser.add_argument("--preview-interactive", action="store_true", help="交互式操作选择模式")
    parser.add_argument("--only-cache", action="store_true", help="仅执行缓存清理")
    parser.add_argument("--only-dedup", action="store_true", help="仅执行去重")
    parser.add_argument("--only-restructure", action="store_true", help="仅执行结构重组")
    parser.add_argument("--only-flatten", action="store_true", help="仅执行扁平化")
    parser.add_argument("--only-perm", action="store_true", help="仅执行权限修复")
    parser.add_argument("--flatten-by", choices=["single", "ext", "date", "prefix"],
                        default=None, help="扁平化分组模式")
    parser.add_argument("--html-report", metavar="FILE", help="生成 HTML 可视化报告")
    parser.add_argument("--init-config", action="store_true", help="生成默认配置文件并退出")
    # 原有参数
    parser.add_argument("--from-list", nargs="+", metavar="FILE", help="离线模式：从文件列表分析")
    parser.add_argument("--diagnose", action="store_true", help="仅诊断，不修改")
    parser.add_argument("--preview", action="store_true", help="分析 + 预览操作清单")
    parser.add_argument("--execute", action="store_true", help="执行操作")
    parser.add_argument("--yes", "-y", action="store_true", help="跳过确认")
    parser.add_argument("--detect-zone", action="store_true", help="扫描时检测 Zone.Identifier")
    parser.add_argument("--export", metavar="FILE", help="导出报告 (CSV/JSON)")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")
    parser.add_argument("--extra-patterns", nargs="+", help="额外的缓存匹配模式")
    parser.add_argument("--dedup-strategy", choices=["shallowest", "newest", "largest", "hardlink"],
                        default=None)
    parser.add_argument("--fix-attr", metavar="ACTIONS", help="修复权限/属性: perm,readonly,hidden,zone,all")
    parser.add_argument("--flatten", nargs="?", const="", metavar="TARGET_DIR", help="层级扁平化 (兼容)")
    parser.add_argument("--force-lock", action="store_true", help="强制清除锁文件")
    parser.add_argument("--threads", "-j", type=int, metavar="N", help=f"并行线程数")
    parser.add_argument("--test", action="store_true", help="运行内置单元测试")

    args = parser.parse_args()

    if args.threads and args.threads > 0:
        _update_workers(args.threads)

    # V8.0: 生成默认配置
    if args.init_config:
        save_default_config("restructure_config.json")
        sys.exit(0)

    # 加载配置文件
    config = load_config(args.config) if args.config else DEFAULT_CONFIG

    # 验证配置
    valid, errors = validate_config(config)
    if not valid:
        for e in errors:
            print(f"❌ 配置错误: {e}")
        sys.exit(1)

    # 单元测试
    if args.test:
        success = run_tests()
        sys.exit(0 if success else 1)

    # 离线模式
    if args.from_list:
        parser_obj = FileListParser()
        all_lines = parser_obj.load_files(args.from_list)
        if not all_lines:
            print("❌ 没有有效的路径数据")
            sys.exit(1)
        nodes, root, parse_stats = parser_obj.parse_to_virtual_nodes(all_lines)
        diag = OfflineDiagnosticEngine(nodes, root)
        stats = diag.get_stats()
        elim = OfflineEliminationEngine(nodes)
        elim.mark_cache_dirs(extra_patterns=args.extra_patterns, config=config)
        cache_count = sum(1 for n in nodes if n.action == NodeAction.DELETE and "缓存" in n.reason)
        junk_count = sum(1 for n in nodes if n.action == NodeAction.DELETE and "临时" in n.reason)
        dup_count = elim.deduplicate_by_name()
        dup_groups = elim.duplicate_groups
        restruct = OfflineRestructureEngine(nodes, root)
        unpacked = restruct.unpack_redundant()
        flatten_count = 0
        if args.flatten is not None or args.flatten_by:
            flatten_count = sum(1 for n in nodes if not n.is_dir and n.action == NodeAction.NONE)
            print("  ⚠️ 扁平化仅在线模式可执行")
        print_offline_report(stats, parse_stats, cache_count, junk_count, dup_count,
                             dup_groups, unpacked, nodes,
                             parser_obj.total_truncated, parser_obj.total_skipped,
                             flatten_count=flatten_count)
        if args.export:
            report = {
                "engine_version": VERSION, "mode": "offline",
                "stats": stats, "parse_stats": parse_stats,
                "operations": {"cache_cleanup": cache_count, "junk_cleanup": junk_count,
                               "duplicates": dup_count, "redundant_unpack": unpacked},
                "duplicate_groups": dup_groups[:50],
            }
            with open(args.export, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, ensure_ascii=False)
            print(f"\n📄 报告已导出: {args.export}")
        # V8.0: 离线模式 HTML 报告
        if args.html_report:
            hr = HTMLReportGenerator()
            freed = sum(n.size_bytes for n in nodes if n.action == NodeAction.DELETE)
            hr.generate(stats, nodes, dup_groups, freed, args.html_report)
            print(f"📊 HTML 报告: {args.html_report}")
        return

    # 在线模式
    if not IS_WINDOWS:
        print("❌ 在线模式仅支持 Windows 系统")
        sys.exit(1)
    if not args.target:
        parser.print_help()
        sys.exit(1)
    if not os.path.isdir(args.target):
        print(f"❌ 不是有效目录: {args.target}")
        sys.exit(1)

    engine = FolderRestructureEngine(args.target)
    engine.config = config

    safe, msg = engine.run_safety_check()
    if not safe:
        print(f"❌ 操作被禁止: {msg}")
        sys.exit(1)

    # V8.0: SSD/HDD 自动调优
    if not args.diagnose:
        apply_disk_tuning(args.target, config)

    lock = LockManager(args.target)
    lock_acquired = False
    if not args.diagnose:
        if not lock.acquire(force=args.force_lock):
            print("❌ 无法获取目录锁")
            sys.exit(1)
        lock_acquired = True
        import atexit
        atexit.register(lock.release)

    try:
        engine.diagnose(detect_zone=args.detect_zone, verbose=args.verbose)
        if args.export:
            if args.export.endswith(".csv"):
                engine.diagnostic.export_csv(args.export)
            else:
                engine.diagnostic.export_json(args.export)
            print(f"📄 报告已导出: {args.export}")
        if args.diagnose:
            return

        # V8.0: --only-* 精细控制
        only_flags = (args.only_cache, args.only_dedup,
                      args.only_restructure, args.only_flatten, args.only_perm)
        analysis = engine.analyze(
            cache_patterns=args.extra_patterns,
            dedup_strategy=args.dedup_strategy,
            only_cache=args.only_cache,
            only_dedup=args.only_dedup,
            only_restructure=args.only_restructure,
            only_flatten=args.only_flatten,
        )
        print(f"  缓存目录标记: {analysis.get('cache_marked', 0)} 项")
        print(f"  重复文件标记: {analysis.get('duplicates_marked', 0)} 项")
        print(f"  冗余结构解包: {analysis.get('redundant_unpacked', 0)} 项")
        if analysis.get('estimated_freed_bytes', 0) > 0:
            print(f"  💡 预计释放空间: {analysis['estimated_freed_mb']} MB")

        # V8.0: 扁平化 (--flatten-by 优先于 --flatten)
        flatten_mode = args.flatten_by or config.get("flatten_mode", "single")
        flatten_count = 0
        if args.flatten is not None or args.only_flatten:
            flatten_target = args.flatten if args.flatten else ""
            if not flatten_target:
                flatten_target = os.path.join(engine.target_path, "_flattened")
            print(f"\n📦 层级扁平化: {flatten_target} (模式: {flatten_mode})")
            egm = config.get("ext_group_map", EXT_GROUP_MAP)
            flatten_engine = FlattenEngine(engine.nodes, engine.target_path,
                                           engine.errors, ext_group_map=egm)
            flatten_engine.extract_files(flatten_target, mode=flatten_mode)
            flatten_count = sum(1 for n in engine.nodes if n.action == NodeAction.MOVE
                                and "扁平化" in n.reason)
            print(f"  扁平化文件: {flatten_count} 项")

        dup_groups = engine.get_duplicate_preview()
        if dup_groups:
            print(f"\n📋 重复文件清单 ({len(dup_groups)} 组):")
            for g in dup_groups[:20]:
                action_label = "🔗 硬链接" if g.get("action") == "HARDLINK" else "🗑️ 删除"
                print(f"  组 {g['group_id']}: {g['size']}")
                print(f"    ✅ 保留: {g['keep']['path']} (深度 {g['keep']['depth']})")
                for d in g["delete"]:
                    print(f"    {action_label}: {d['path']} (深度 {d['depth']})")

        # V8.0: 权限修复 (--only-perm 或 --fix-attr)
        perm_stats: Dict[str, int] = {}
        if args.fix_attr or args.only_perm:
            perm_actions = _parse_actions(args.fix_attr) if args.fix_attr else {PermissionAction.ALL}
            if perm_actions:
                perm_stats = engine.fix_permissions(perm_actions, dry_run=not args.execute)

        # V8.0: 交互式预览
        if args.preview_interactive:
            preview = engine.preview()
            if not preview:
                print("\n✅ 无需执行任何操作")
                return
            space = engine.get_space_estimate()
            print(f"\n💡 空间预估: 删除 {space['delete_mb']} MB, 硬链接 {space['hardlink_mb']} MB, "
                  f"总计 {space['total_mb']} MB")
            ip = InteractivePreview(engine.nodes, dup_groups)
            selection = ip.run()
            ip.apply_selection(selection)
            # 重新计算
            preview = engine.preview()
            if not preview:
                print("✅ 已取消所有操作")
                return
            space = engine.get_space_estimate()
            print(f"\n💡 选择后空间预估: 总计 {space['total_mb']} MB")
            if not args.execute:
                print(f"\n💡 使用 --execute 参数执行实际操作")
                return

        preview = engine.preview()
        if preview and not args.preview_interactive:
            print(f"\n📋 结构变更清单 ({len(preview)} 项):")
            print("-" * 65)
            for i, item in enumerate(preview[:50], 1):
                icon = {"DELETE": "🗑️", "MOVE": "📦", "HARDLINK": "🔗",
                        "RENAME": "✏️"}.get(item["action"], "❓")
                if item["action"] in ("DELETE", "HARDLINK"):
                    print(f"  {i:4d}. {icon} {item['action']} {item['source']} ({item['size']})")
                    if item.get("reason"):
                        print(f"        原因: {item['reason'][:60]}")
                else:
                    print(f"  {i:4d}. {icon} {item['source']}")
                    print(f"        → {item['target']}")
            if len(preview) > 50:
                print(f"  ... 还有 {len(preview) - 50} 项")
            print("-" * 65)

            # V8.0: 空间预估
            space = engine.get_space_estimate()
            if space['total_mb'] > 0:
                print(f"\n💾 预计释放空间: {space['delete_mb']} MB (删除) + "
                      f"{space['hardlink_mb']} MB (硬链接) = {space['total_mb']} MB")

        if args.preview:
            print(f"\n💡 使用 --execute 参数执行实际操作")
            return

        if args.execute:
            total_ops = len(preview) + sum(v for k, v in perm_stats.items()
                                          if k not in ("failed", "skipped"))
            if not args.yes and total_ops > 0:
                space = engine.get_space_estimate()
                confirm_msg = f"\n⚠️ 即将执行 {total_ops} 项操作"
                if space['total_mb'] > 0:
                    confirm_msg += f" (预计释放 {space['total_mb']} MB)"
                confirm_msg += "。确认？(yes/no): "
                confirm = input(confirm_msg)
                if confirm.lower() not in ("yes", "y"):
                    print("❌ 已取消")
                    return
            engine.execute(dry_run=False)

            # V8.0: HTML 报告
            if args.html_report:
                hr = HTMLReportGenerator()
                stats = engine.diagnostic.get_stats() if engine.diagnostic else {}
                freed = space.get('total_bytes', 0)
                path = hr.generate(stats, engine.nodes, dup_groups, freed, args.html_report)
                print(f"\n📊 HTML 报告已生成: {path}")

    except KeyboardInterrupt:
        print("\n\n⚠️ 用户中断")
    finally:
        if lock_acquired:
            lock.release()

# ============================================================
# 单元测试
# ============================================================
def run_tests():
    import unittest
    import tempfile

    class TestSanitizeFilename(unittest.TestCase):
        def test_normal(self):
            self.assertEqual(_sanitize_filename("hello.txt"), "hello.txt")
        def test_illegal_chars(self):
            result = _sanitize_filename('file<>:"|?*.txt')
            for c in '<>:"|?*\x00':
                self.assertNotIn(c, result)
        def test_reserved_name(self):
            self.assertTrue(_sanitize_filename("CON").startswith("_"))
        def test_empty(self):
            self.assertEqual(_sanitize_filename(""), "_unnamed")

    class TestTruncateName(unittest.TestCase):
        def test_short(self):
            self.assertEqual(_truncate_name("short.txt", 100), "short.txt")
        def test_long(self):
            result = _truncate_name("a" * 300 + ".txt", 50)
            self.assertLessEqual(len(result.encode("utf-8")), 50)
            self.assertTrue(result.endswith(".txt"))

    class TestFmtSize(unittest.TestCase):
        def test_zero(self): self.assertIn("B", _fmt_size(0))
        def test_kb(self): self.assertIn("KB", _fmt_size(2048))
        def test_mb(self): self.assertIn("MB", _fmt_size(5 * 1024 * 1024))
        def test_gb(self): self.assertIn("GB", _fmt_size(3 * 1024 * 1024 * 1024))

    class TestWinPath(unittest.TestCase):
        def test_roundtrip(self):
            if IS_WINDOWS:
                self.assertEqual(_short_path(_win_path(r"C:\test\file.txt")), r"C:\test\file.txt")

    class TestConfig(unittest.TestCase):
        def test_load_missing(self):
            cfg = load_config("/nonexistent/config.json")
            self.assertEqual(cfg, DEFAULT_CONFIG)
        def test_validate(self):
            ok, errs = validate_config({"dedup_strategy": "shallowest", "flatten_mode": "ext"})
            self.assertTrue(ok)
            ok2, errs2 = validate_config({"dedup_strategy": "invalid"})
            self.assertFalse(ok2)

    class TestFlattenClassify(unittest.TestCase):
        def test_ext_classify(self):
            fe = FlattenEngine([], "/tmp", ErrorCollector())
            self.assertEqual(fe._classify_ext(".jpg"), "images")
            self.assertEqual(fe._classify_ext(".py"), "code")
            self.assertEqual(fe._classify_ext(".xyz"), "other")
        def test_date_classify(self):
            fe = FlattenEngine([], "/tmp", ErrorCollector())
            from datetime import datetime
            ts = datetime(2026, 4, 14, 10, 30).timestamp()
            self.assertEqual(fe._classify_date(ts), "2026-04")
        def test_prefix_classify(self):
            fe = FlattenEngine([], "/tmp", ErrorCollector())
            self.assertEqual(fe._classify_prefix("abc.txt"), "A")
            self.assertEqual(fe._classify_prefix("1file.txt"), "#")

    class TestOfflineDedup(unittest.TestCase):
        def test_size_aware(self):
            nodes = [
                VirtualFileNode(r"C:\a\v.mp4", "v.mp4", ".mp4", 1024*1024, 1),
                VirtualFileNode(r"C:\b\v.mp4", "v.mp4", ".mp4", 1024*1024, 2),
                VirtualFileNode(r"C:\c\v.mp4", "v.mp4", ".mp4", 100, 1),
            ]
            engine = OfflineEliminationEngine(nodes)
            count = engine.deduplicate_by_name()
            self.assertEqual(count, 1)
            self.assertEqual(nodes[0].action, NodeAction.NONE)
            self.assertEqual(nodes[1].action, NodeAction.DELETE)
            self.assertEqual(nodes[2].action, NodeAction.NONE)

    class TestFileListParser(unittest.TestCase):
        def test_pure_path(self):
            with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
                f.write("C:\\a\\b.txt\nC:\\a\\c.txt\n")
                tmppath = f.name
            p = FileListParser()
            lines = p.load_files([tmppath])
            os.unlink(tmppath)
            self.assertEqual(len(lines), 2)
        def test_truncated(self):
            with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
                f.write("C:\\ok.txt\nC:\\long_path...\nC:\\fine.txt\n")
                tmppath = f.name
            p = FileListParser()
            lines = p.load_files([tmppath])
            os.unlink(tmppath)
            self.assertEqual(len(lines), 2)
            self.assertEqual(p.total_truncated, 1)

    class TestRollbackScript(unittest.TestCase):
        def test_empty(self):
            e = ExecutionEngine([], "/tmp", "/tmp/log", "/tmp/trash")
            s = e.generate_rollback_script()
            self.assertIn("无需回滚", s)
        def test_hardlink_action(self):
            e = ExecutionEngine([], "/tmp", "/tmp/log", "/tmp/trash")
            e.op_logs.append(OperationLog(1, "HARDLINK", r"C:\a.txt", r"C:\b.txt",
                                          "2026-01-01T00:00:00"))
            s = e.generate_rollback_script()
            self.assertIn("shutil.move", s)
            self.assertIn('"HARDLINK"', s)

    class TestTrashPathConflict(unittest.TestCase):
        def test_unique(self):
            e = ExecutionEngine([], "/tmp", "/tmp/log", "/tmp/trash")
            paths = [e._get_trash_path("/tmp/sub/test.txt") for _ in range(20)]
            self.assertEqual(len(paths), len(set(paths)))

    class TestNodeAction(unittest.TestCase):
        def test_hardlink_exists(self):
            self.assertTrue(hasattr(NodeAction, 'HARDLINK'))
        def test_hardlinked_status(self):
            self.assertTrue(hasattr(NodeStatus, 'HARDLINKED'))

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [TestSanitizeFilename, TestTruncateName, TestFmtSize, TestWinPath,
                TestConfig, TestFlattenClassify, TestOfflineDedup,
                TestFileListParser, TestRollbackScript, TestTrashPathConflict,
                TestNodeAction]:
        suite.addTests(loader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return result.wasSuccessful()

if __name__ == "__main__":
    main()
