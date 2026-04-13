#!/usr/bin/env python3
"""
文件夹结构智能重组工具 V6.6.2 (Windows 专用 + 离线分析模式)
Folder Structure Intelligent Restructuring Engine for Windows

核心原则：绝对防丢、极度容错、用户可控
默认为预览模式，必须显式确认才执行物理操作。

V6.6.2 相比 V6.6.1 核心变更：
- 🔴 Bug修复：_log_op 内部重复获取 self._lock 导致多线程死锁
- 🟡 Bug修复：FileListParser._load_single 中 found_fullname 永远为 False，PowerShell 表头过滤失效
- 🟡 优化：execute() 补充 Phase 1 注释，与产品手册保持一致
- 🟢 清理：移除未使用的 auto 导入 (from enum import auto)

V6.6.1 相比 V6.0 核心变更：
- 🔴 安全修复：回滚脚本路径注入漏洞（使用json.dumps替代f-string）
- 🔴 安全修复：回收站TOCTOU竞态条件（使用原子操作）
- 🟡 Bug修复：离线去重变量作用域错误（deduplicate_by_name）
- 🟡 Bug修复：--diagnose重复return问题
- 🟡 改进：--flatten参数离线模式明确提示
- 🟡 优化：PermissionEngine移除未使用参数
- 🟡 优化：回滚脚本目录创建移到循环外
- 🟢 完善：补充单元测试覆盖面

V6.0 相比 V5.3 核心变更：
- 安全修复：回滚脚本改为 Python (.py)，彻底杜绝命令注入
- 安全修复：回收站路径冲突检测，防止同名文件覆盖
- 安全修复：离线去重增加文件大小比对，不再仅凭文件名判断重复
- 安全修复：哈希算法从 MD5 升级为 SHA256
- 新功能：--flatten 参数激活层级扁平化引擎
- 改进：IO 操作增加重试机制（3次重试，500ms间隔）
- 改进：硬链接纳入去重候选，保留硬链接节省空间
- 完善：补充全面的单元测试

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

# ============================================================
# 版本与常量
# ============================================================

VERSION = "6.6.2"
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
# 枚举
# ============================================================

class NodeAction(Enum):
    NONE = "NONE"
    DELETE = "DELETE"
    MOVE = "MOVE"
    RENAME = "RENAME"


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
    """轻量级虚拟节点，用于离线分析（不依赖真实文件系统）"""
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
                seq=self._seq, path=path,
                error_type=error_type, message=message,
                timestamp=datetime.now(timezone.utc).isoformat()
            ))

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
        self._acquired = False  # 追踪锁的持有状态

    def acquire(self, force: bool = False) -> bool:
        if os.path.exists(self.lock_path):
            try:
                with open(self.lock_path, "r", encoding="utf-8") as f:
                    lock_data = json.load(f)
                expires = lock_data.get("expires_at", "")
                if expires and datetime.fromisoformat(expires) > datetime.now(timezone.utc):
                    if not force:
                        print(f"⚠️  目录正在被处理:")
                        print(f"   主机: {lock_data.get('hostname', 'unknown')}")
                        print(f"   进程: {lock_data.get('pid', 'unknown')}")
                        print(f"   开始: {lock_data.get('started_at', 'unknown')}")
                        print(f"   过期: {expires}")
                        return False
                os.remove(self.lock_path)
            except (json.JSONDecodeError, OSError):
                if force:
                    os.remove(self.lock_path)
                else:
                    print("⚠️  发现损坏的锁文件，使用 --force-lock 清除")
                    return False

        lock_data = {
            "pid": os.getpid(),
            "hostname": os.environ.get("COMPUTERNAME", "unknown"),
            "user": os.environ.get("USERNAME", "unknown"),
            "session_id": self.session_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": datetime.fromtimestamp(
                time.time() + 12 * 3600, tz=timezone.utc
            ).isoformat(),
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
            return  # 未获取锁，不执行释放
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
        try:
            wp = _win_path(ads_path)
            if os.path.exists(wp):
                os.remove(wp)
                return True
        except (OSError, ValueError):
            pass
        try:
            if os.path.exists(ads_path):
                os.remove(ads_path)
                return True
        except (OSError, ValueError):
            pass
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Remove-Item", "-LiteralPath", file_path,
                 "-Stream", ZONE_IDENTIFIER, "-ErrorAction", "SilentlyContinue"],
                capture_output=True, text=True, timeout=10
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    @staticmethod
    def list_ads(file_path: str) -> List[str]:
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-Item", "-LiteralPath", file_path,
                 "-Stream", "*", "|",
                 "Select-Object", "-ExpandProperty", "Stream"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                return [s.strip() for s in result.stdout.strip().split("\n") if s.strip()]
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return []


# ============================================================
# 权限与属性修改引擎
# ============================================================

class PermissionEngine:
    """
    权限与属性修改引擎
    
    提供以下功能：
    - 授予完全控制权限 (icacls)
    - 去除只读属性 (attrib -R)
    - 去除隐藏属性 (attrib -H)
    - 清除 Zone.Identifier (网络文件标识)
    """

    def __init__(self, nodes: List[FileNode]):
        self.nodes = nodes
        self._errors = ErrorCollector()
        self.stats: Dict[str, int] = {
            "perm_granted": 0, "readonly_removed": 0,
            "hidden_removed": 0, "zone_cleared": 0,
            "failed": 0, "skipped": 0,
        }

    def grant_full_control(self, targets: List[FileNode] = None,
                           dry_run: bool = True) -> Dict[str, int]:
        items = targets or self.nodes
        username = os.environ.get("USERNAME", "")
        if not username:
            print("⚠️  无法获取当前用户名，跳过权限修改")
            return self.stats

        to_process = [n for n in items if not n.is_dir]
        total = len(to_process)
        print(f"\n🔑 授予完全控制权限: {total} 个文件 (用户: {username})")

        for i, node in enumerate(to_process, 1):
            wp = _win_path(node.full_path)
            target = wp if (IS_WINDOWS and Win32IO.exists(node.full_path)) else node.full_path

            if dry_run:
                if i <= 20:
                    print(f"  [PERM] grant:F {node.file_name}")
                elif i == 21:
                    print(f"  ... 还有 {total - 20} 项")
                continue

            try:
                result = subprocess.run(
                    ["icacls", target, "/grant", f"{username}:(F)", "/Q"],
                    capture_output=True, text=True, timeout=30
                )
                if result.returncode == 0:
                    node.status = NodeStatus.PERM_FIXED
                    self.stats["perm_granted"] += 1
                else:
                    self._errors.add(node.full_path, "PermGrantFailed",
                                    result.stderr.strip()[:200])
                    self.stats["failed"] += 1
            except subprocess.TimeoutExpired:
                self._errors.add(node.full_path, "PermTimeout", "icacls 超时")
                self.stats["failed"] += 1
            except FileNotFoundError:
                print("❌ icacls 命令不可用")
                return self.stats

            if i % 100 == 0:
                print(f"  权限进度: {i}/{total}")

        return self.stats

    def remove_readonly(self, targets: List[FileNode] = None,
                        dry_run: bool = True) -> Dict[str, int]:
        items = targets or [n for n in self.nodes if n.is_readonly]
        total = len(items)
        print(f"\n🔓 去除只读属性: {total} 个文件")

        for i, node in enumerate(items, 1):
            wp = _win_path(node.full_path)
            target = wp if (IS_WINDOWS and Win32IO.exists(node.full_path)) else node.full_path

            if dry_run:
                if i <= 20:
                    print(f"  [ATTR] -R {node.file_name}")
                elif i == 21:
                    print(f"  ... 还有 {total - 20} 项")
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

            if i % 100 == 0:
                print(f"  只读去除进度: {i}/{total}")

        return self.stats

    def remove_hidden(self, targets: List[FileNode] = None,
                      dry_run: bool = True) -> Dict[str, int]:
        items = targets or [n for n in self.nodes if n.is_hidden]
        items = [n for n in items if not n.file_name.startswith(".restructure")]
        total = len(items)
        print(f"\n👁️  去除隐藏属性: {total} 个文件")

        for i, node in enumerate(items, 1):
            wp = _win_path(node.full_path)
            target = wp if (IS_WINDOWS and Win32IO.exists(node.full_path)) else node.full_path

            if dry_run:
                if i <= 20:
                    print(f"  [ATTR] -H {node.file_name}")
                elif i == 21:
                    print(f"  ... 还有 {total - 20} 项")
                continue

            try:
                subprocess.run(["attrib", "-H", target], capture_output=True, timeout=10)
                node.is_hidden = False
                node.status = NodeStatus.ATTR_FIXED
                self.stats["hidden_removed"] += 1
            except (OSError, subprocess.TimeoutExpired) as e:
                self._errors.add(node.full_path, "HiddenRemoveFailed", str(e))
                self.stats["failed"] += 1

            if i % 100 == 0:
                print(f"  隐藏去除进度: {i}/{total}")

        return self.stats

    def clear_zone_identifiers(self, targets: List[FileNode] = None,
                                dry_run: bool = True) -> Dict[str, int]:
        items = targets or [n for n in self.nodes
                           if n.has_zone_identifier and not n.is_dir]
        if not targets:
            items = []
            for n in self.nodes:
                if n.is_dir or n.action == NodeAction.DELETE:
                    continue
                if ZoneIdentifierHandler.has_zone_identifier(n.full_path):
                    n.has_zone_identifier = True
                    items.append(n)

        total = len(items)
        print(f"\n🌐 清除网络文件标识 (Zone.Identifier): {total} 个文件")

        for i, node in enumerate(items, 1):
            if dry_run:
                if i <= 20:
                    print(f"  [ZONE] clear {node.file_name}")
                elif i == 21:
                    print(f"  ... 还有 {total - 20} 项")
                continue

            wp = _win_path(node.full_path)
            target = wp if (IS_WINDOWS and Win32IO.exists(node.full_path)) else node.full_path

            if ZoneIdentifierHandler.clear_zone_identifier(target):
                node.has_zone_identifier = False
                node.status = NodeStatus.ZONE_CLEARED
                self.stats["zone_cleared"] += 1
            else:
                self._errors.add(node.full_path, "ZoneClearFailed",
                               "无法清除 Zone.Identifier")
                self.stats["failed"] += 1

            if i % 100 == 0:
                print(f"  Zone 清除进度: {i}/{total}")

        return self.stats

    def apply_all(self, actions: Set[PermissionAction],
                  dry_run: bool = True) -> Dict[str, int]:
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

    def scan(self, detect_zone: bool = False, verbose: bool = False) -> List[FileNode]:
        self._scan_timestamp = time.time()
        self.nodes = []
        root_depth = self.root_path.count(os.sep)
        skipped_dirs = 0

        for dirpath, dirnames, filenames in os.walk(self.root_path, topdown=True):
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
                    node = self._make_node(fpath, rel_depth)
                    self.nodes.append(node)
                except (OSError, IOError):
                    continue

            if verbose and len(self.nodes) % 50000 == 0:
                print(f"  已扫描 {len(self.nodes)} 项...")

        # 并行 Zone.Identifier 检测（扫描完成后再批量检测，避免扫描阶段卡顿）
        if detect_zone and IS_WINDOWS:
            file_nodes = [n for n in self.nodes if not n.is_dir]
            if file_nodes:
                print(f"  🔍 并行检测 Zone.Identifier ({len(file_nodes)} 个文件, {MAX_WORKERS} 线程)...")
                lock = threading.Lock()
                zone_count = [0]

                def _check_zone(node: FileNode) -> None:
                    try:
                        wp = _win_path(node.full_path)
                        check_path = wp if os.path.exists(wp) else node.full_path
                        if ZoneIdentifierHandler.has_zone_identifier(check_path):
                            with lock:
                                node.has_zone_identifier = True
                                zone_count[0] += 1
                    except (OSError, IOError):
                        pass

                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                    list(executor.map(_check_zone, file_nodes))

                if zone_count[0]:
                    print(f"  发现 {zone_count[0]} 个含 Zone.Identifier 的文件")

        if skipped_dirs:
            print(f"⚠️  跳过 {skipped_dirs} 个无权限目录")

        return self.nodes

    def _make_node(self, path: str, depth: int, is_dir: bool = False) -> FileNode:
        st = Win32IO.safe_stat(path) if IS_WINDOWS else os.lstat(path)
        name = os.path.basename(path) if path != self.root_path else os.path.basename(self.root_path)
        ext = "" if is_dir else os.path.splitext(name)[1].lower()

        reparse = ReparseType.NONE
        hard_links = 1
        is_hidden = False
        is_readonly = False
        is_system = False

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

                        if attrs & FILE_ATTRIBUTE_REPARSE:
                            if reparse == ReparseType.NONE:
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
            full_path=path,
            file_name=name,
            extension=ext,
            size_bytes=st.st_size if st and not is_dir else 0,
            depth=depth,
            last_modified=st.st_mtime if st else 0,
            reparse_type=reparse,
            is_hidden=is_hidden,
            is_readonly=is_readonly,
            is_system=is_system,
            hard_link_count=hard_links,
            is_dir=is_dir,
        )

    def get_stats(self, top_n: int = 10) -> Dict:
        files = [n for n in self.nodes if not n.is_dir]
        dirs = [n for n in self.nodes if n.is_dir]

        deepest = sorted(files, key=lambda n: n.depth, reverse=True)[:top_n]
        largest_files = sorted(files, key=lambda n: n.size_bytes, reverse=True)[:top_n]
        hardlinked = [n for n in files if n.hard_link_count > 1]
        readonly_files = [n for n in files if n.is_readonly]
        hidden_files = [n for n in self.nodes if n.is_hidden]
        zone_files = [n for n in files if n.has_zone_identifier]

        dir_sizes: Dict[str, int] = defaultdict(int)
        for n in files:
            parent = os.path.dirname(n.full_path)
            dir_sizes[parent] += n.size_bytes
        largest_dirs = sorted(dir_sizes.items(), key=lambda x: x[1], reverse=True)[:top_n]

        ext_count: Dict[str, int] = defaultdict(int)
        for n in files:
            ext_count[n.extension or "(无扩展名)"] += 1

        long_names = []
        for n in self.nodes:
            try:
                full_bytes = len(n.full_path.encode("utf-8"))
                if full_bytes > MAX_PATH_LEGACY:
                    long_names.append((n.full_path, full_bytes))
            except (UnicodeEncodeError, UnicodeDecodeError):
                long_names.append((n.full_path, -1))

        total_size = sum(n.size_bytes for n in files)
        max_depth = max((n.depth for n in self.nodes), default=0)

        return {
            "root": self.root_path,
            "total_items": len(self.nodes),
            "total_files": len(files),
            "total_dirs": len(dirs),
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "max_depth": max_depth,
            "symlinks": sum(1 for n in self.nodes if n.reparse_type == ReparseType.SYMLINK),
            "junctions": sum(1 for n in self.nodes if n.reparse_type in (ReparseType.JUNCTION, ReparseType.MOUNTPOINT)),
            "hard_links": len(hardlinked),
            "readonly_files": len(readonly_files),
            "hidden_files": len(hidden_files),
            "system_files": sum(1 for n in self.nodes if n.is_system),
            "zone_identifier_files": len(zone_files),
            "long_path_files": len(long_names),
            "long_path_examples": long_names[:5],
            "deepest_files": [(n.full_path, n.depth) for n in deepest],
            "largest_files": [(n.full_path, _fmt_size(n.size_bytes)) for n in largest_files],
            "largest_dirs": [(d, _fmt_size(s)) for d, s in largest_dirs],
            "extension_stats": dict(sorted(ext_count.items(), key=lambda x: x[1], reverse=True)[:20]),
        }

    def export_csv(self, output_path: str) -> None:
        """导出诊断结果为 CSV 格式（UTF-8 BOM 编码）"""
        import csv
        with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["full_path", "file_name", "extension", "size_bytes",
                             "size_human", "depth", "last_modified", "reparse_type",
                             "is_hidden", "is_readonly", "is_system",
                             "hard_link_count", "has_zone_identifier", "is_dir"])
            for n in self.nodes:
                writer.writerow([
                    n.full_path, n.file_name, n.extension, n.size_bytes,
                    _fmt_size(n.size_bytes), n.depth,
                    datetime.fromtimestamp(n.last_modified).isoformat() if n.last_modified > 0 else "",
                    n.reparse_type.value, n.is_hidden, n.is_readonly, n.is_system,
                    n.hard_link_count, n.has_zone_identifier, n.is_dir
                ])

    def export_json(self, output_path: str) -> None:
        """导出诊断结果为 JSON 格式"""
        data = {
            "engine_version": VERSION,
            "scan_time": datetime.fromtimestamp(self._scan_timestamp).isoformat(),
            "stats": self.get_stats(),
            "nodes": [
                {
                    "path": n.full_path, "name": n.file_name, "ext": n.extension,
                    "size": n.size_bytes, "depth": n.depth,
                    "reparse": n.reparse_type.value,
                    "is_hidden": n.is_hidden, "is_readonly": n.is_readonly,
                    "has_zone": n.has_zone_identifier, "is_dir": n.is_dir,
                }
                for n in self.nodes
            ]
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)


# ============================================================
# 智能剔除引擎
# ============================================================

class EliminationEngine:
    def __init__(self, nodes: List[FileNode], errors: ErrorCollector):
        self.nodes = nodes
        self.errors = errors
        self._dup_groups: List[List[FileNode]] = []

    def mark_cache_dirs(self, extra_patterns: List[str] = None) -> None:
        patterns = list(CACHE_DIR_PATTERNS)
        if extra_patterns:
            patterns.extend(extra_patterns)

        # 垃圾文件标记（与离线模式一致）
        for node in self.nodes:
            if node.action != NodeAction.NONE:
                continue
            if not node.is_dir:
                for pattern in JUNK_FILE_PATTERNS:
                    if fnmatch.fnmatch(node.file_name.lower(), pattern.lower()):
                        node.action = NodeAction.DELETE
                        break

        cache_roots: List[FileNode] = []
        for node in self.nodes:
            if node.action != NodeAction.NONE:
                continue
            for pattern in patterns:
                if fnmatch.fnmatch(node.file_name.lower(), pattern.lower()):
                    cache_roots.append(node)
                    node.action = NodeAction.DELETE
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
                    else:
                        break

    def deduplicate(self, strategy: str = "shallowest") -> int:
        """
        三级哈希去重（并行优化）
        
        第一级：按文件大小分组
        第二级：前4KB SHA256 QuickHash
        第三级：完整文件 SHA256 FullHash
        
        Args:
            strategy: 保留策略 ("shallowest" 或 "newest")
        
        Returns:
            标记删除的重复文件数量
        """
        # 不再排除硬链接，而是纳入候选；在去重组中优先保留硬链接（不占额外空间）
        files = [n for n in self.nodes
                 if not n.is_dir and n.action == NodeAction.NONE]

        size_groups: Dict[int, List[FileNode]] = defaultdict(list)
        for f in files:
            size_groups[f.size_bytes].append(f)

        # 过滤出有 2+ 文件的组
        candidate_groups = [(size, group) for size, group in size_groups.items() if len(group) >= 2]

        if not candidate_groups:
            return 0

        print(f"  🔍 并行去重: {sum(len(g) for _, g in candidate_groups)} 个候选文件 "
              f"({len(candidate_groups)} 个大小组, {MAX_WORKERS} 线程)")

        # Phase 1: 并行计算所有候选文件的 QuickHash
        all_candidates = [f for _, group in candidate_groups for f in group]
        lock = threading.Lock()

        def _compute_quick_hash(node: FileNode) -> None:
            node.quick_hash()  # 结果缓存在 node._quick_hash

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(_compute_quick_hash, f) for f in all_candidates]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception:
                    pass  # 哈希计算失败会在节点内标记为空

        # Phase 2: 按 QuickHash 分组，收集需要 FullHash 的组
        need_full_hash: List[List[FileNode]] = []
        for size, group in candidate_groups:
            qhash_groups: Dict[str, List[FileNode]] = defaultdict(list)
            for f in group:
                qh = f._quick_hash or ""
                if qh:
                    qhash_groups[qh].append(f)
            for qh, qgroup in qhash_groups.items():
                if len(qgroup) >= 2:
                    need_full_hash.append(qgroup)

        if not need_full_hash:
            return 0

        # Phase 3: 并行计算需要 FullHash 的文件
        need_full_hash_flat = [f for group in need_full_hash for f in group]

        def _compute_full_hash(node: FileNode) -> None:
            node.full_hash()  # 结果缓存在 node._full_hash

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(_compute_full_hash, f) for f in need_full_hash_flat]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception:
                    pass

        # Phase 4: 按 FullHash 确定真正的重复组
        dup_count = 0
        self._dup_groups = []

        for qgroup in need_full_hash:
            fhash_groups: Dict[str, List[FileNode]] = defaultdict(list)
            for f in qgroup:
                fh = f._full_hash or ""
                if fh:
                    fhash_groups[fh].append(f)

            for fh, fgroup in fhash_groups.items():
                if len(fgroup) < 2:
                    continue

                # 优先保留硬链接（不占额外磁盘空间）
                hard_link_nodes = [f for f in fgroup if f.reparse_type == ReparseType.HARDLINK]
                if hard_link_nodes:
                    keep = hard_link_nodes[0]
                elif strategy == "shallowest":
                    keep = min(fgroup, key=lambda n: n.depth)
                else:
                    keep = max(fgroup, key=lambda n: n.last_modified)

                self._dup_groups.append(list(fgroup))

                for f in fgroup:
                    if f.full_path != keep.full_path:
                        f.action = NodeAction.DELETE
                        dup_count += 1

        print(f"  发现 {len(self._dup_groups)} 组重复文件, 标记删除 {dup_count} 个")
        return dup_count

    def get_duplicate_groups(self) -> List[Dict]:
        """获取去重组的详细信息"""
        result = []
        for i, group in enumerate(self._dup_groups, 1):
            if len(group) < 2:
                continue
            kept = None
            deleted = []
            for f in group:
                if f.action != NodeAction.DELETE:
                    kept = f
                else:
                    deleted.append(f)
            if not kept:
                kept = group[0]
                deleted = group[1:]
                kept.action = NodeAction.NONE

            result.append({
                "group_id": i,
                "size": _fmt_size(kept.size_bytes),
                "hash": kept.full_hash()[:16] if kept.full_hash() else "?",
                "keep": {"path": kept.full_path, "depth": kept.depth},
                "delete": [{"path": f.full_path, "depth": f.depth} for f in deleted],
            })
        return result


# ============================================================
# 扁平化引擎
# ============================================================

class FlattenEngine:
    """层级扁平化引擎：将深层文件提取到单层目录"""
    
    def __init__(self, nodes: List[FileNode], root_path: str,
                 errors: ErrorCollector):
        self.nodes = nodes
        self.root_path = root_path
        self.errors = errors

    def extract_files(self, target_dir: str, extensions: List[str] = None) -> int:
        """
        提取文件到目标目录
        
        Args:
            target_dir: 目标目录路径
            extensions: 要提取的文件扩展名列表（None 表示全部）
        
        Returns:
            提取的文件数量
        """
        files = [n for n in self.nodes
                 if not n.is_dir and n.action == NodeAction.NONE]

        if extensions:
            ext_set = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in extensions}
            files = [f for f in files if f.extension in ext_set]

        existing_names: Set[str] = set()
        os.makedirs(target_dir, exist_ok=True)

        for f in files:
            base, ext = os.path.splitext(f.file_name)
            source_dir = os.path.basename(os.path.dirname(f.full_path))

            target_name = f.file_name
            if target_name in existing_names:
                target_name = f"{base}_from_{source_dir}{ext}"
                counter = 2
                while target_name in existing_names:
                    target_name = f"{base}_from_{source_dir}_{counter}{ext}"
                    counter += 1

            name_no_ext = os.path.splitext(target_name)[0].upper()
            if name_no_ext in WINDOWS_RESERVED:
                target_name = f"_{target_name}"

            target_path = os.path.join(target_dir, target_name)
            path_bytes = len(target_path.encode("utf-8"))
            if path_bytes >= MAX_PATH_EXTENDED:
                safe_name = _truncate_name(target_name,
                    MAX_PATH_EXTENDED - len(target_dir.encode("utf-8")) - 2)
                target_path = os.path.join(target_dir, safe_name)

            f.target_path = target_path
            f.action = NodeAction.MOVE
            existing_names.add(target_name)

        return len(files)


# ============================================================
# 结构重组引擎
# ============================================================

class RestructureEngine:
    """结构重组引擎：解包冗余目录结构"""
    
    def __init__(self, nodes: List[FileNode], root_path: str,
                 errors: ErrorCollector):
        self.nodes = nodes
        self.root_path = root_path
        self.errors = errors

    def unpack_redundant(self) -> int:
        r"""
        智能解包：识别并合并 A\A\file.txt 冗余结构
        
        Returns:
            解包的目录数量
        """
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
            parent_name = os.path.basename(parent_lower) if parent_lower != self.root_path.lower() else ""
            if not parent_name or parent_name != child.file_name.lower():
                continue

            actual_parent = os.path.dirname(child.full_path)
            child_prefix = child.full_path.lower() + os.sep
            for node in self.nodes:
                if node.full_path.lower().startswith(child_prefix) and \
                   node.action == NodeAction.NONE:
                    rel = os.path.relpath(node.full_path, child.full_path)
                    node.target_path = os.path.join(actual_parent, rel)
                    node.action = NodeAction.MOVE

            child.action = NodeAction.DELETE
            unpacked += 1

        return unpacked

    def lift_children(self, target_dir: str) -> int:
        """
        单级提级：将选中目录的直属子项提升到父目录
        
        Args:
            target_dir: 要提级的目录
        
        Returns:
            提级的文件数量
        """
        lifted = 0
        parent_dir = os.path.dirname(target_dir)
        prefix = target_dir.lower() + os.sep

        for node in self.nodes:
            if node.full_path.lower().startswith(prefix) and \
               node.action == NodeAction.NONE:
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
# 执行引擎 — 统一阶段编排
# ============================================================

class ExecutionEngine:
    def __init__(self, nodes: List[FileNode], root_path: str,
                 log_dir: str, trash_dir: str):
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
            "failed": 0, "trashed": 0,
        }
        self._throttle = 0

    def set_scan_timestamp(self, ts: float) -> None:
        self._scan_timestamp = ts

    def execute(self, dry_run: bool = True) -> Dict[str, int]:
        """执行所有待处理操作"""
        os.makedirs(self.log_dir, exist_ok=True)
        os.makedirs(self.trash_dir, exist_ok=True)

        moved = [n for n in self.nodes if n.action == NodeAction.MOVE]
        deleted = [n for n in self.nodes if n.action == NodeAction.DELETE]
        total = len(moved) + len(deleted)

        if total == 0:
            print("✅ 无需执行任何操作")
            return self.stats

        mode_label = "🔍 预览模式" if dry_run else "⚡ 执行模式"
        print(f"\n{mode_label}: 共 {total} 项操作 (移动 {len(moved)} / 删除 {len(deleted)})")

        if not dry_run and total > 100:
            print(f"  使用 {MAX_WORKERS} 线程并行处理...\n")

        # Phase 1: 权限/属性修复（由外部 fix_permissions() 单独调用，此处跳过）
        # Phase 2: 移动操作（并行）
        print("  Phase 2: 移动操作...")
        self._parallel_process(moved, self._process_move, dry_run, "移动")

        # Phase 3: 删除操作（并行）
        print("  Phase 3: 删除操作...")
        self._parallel_process(deleted, self._process_delete, dry_run, "删除")

        if not dry_run:
            print("  Phase 4: 空目录清理...")
            self._cleanup_empty_dirs()
            self._write_log()
            self._rotate_logs()

        return self.stats

    def _parallel_process(self, nodes: List[FileNode], handler, dry_run: bool, label: str) -> None:
        """并行处理一组节点（移动或删除）"""
        if not nodes:
            return

        # dry-run 或小批量：直接串行处理，避免线程开销
        if dry_run or len(nodes) <= 20:
            for node in nodes:
                handler(node, dry_run)
            return

        # 并行处理
        completed = [0]
        progress_lock = threading.Lock()

        def _safe_handler(node: FileNode) -> None:
            try:
                handler(node, dry_run)
                self._maybe_throttle()
                with progress_lock:
                    completed[0] += 1
                    if completed[0] % 200 == 0:
                        print(f"    {label}进度: {completed[0]}/{len(nodes)}")
            except Exception as e:
                with self._lock:
                    self.errors.add(node.full_path, f"{label}Failed", str(e))
                    self.stats["failed"] += 1

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(_safe_handler, node) for node in nodes]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception:
                    pass  # 已在 _safe_handler 中处理

    def _process_move(self, node: FileNode, dry_run: bool) -> None:
        """处理单个移动操作"""
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
                    time.sleep(0.5)  # 重试间隔 500ms
                    continue
                with self._lock:
                    node.status = NodeStatus.LOCKED
                    node.error_msg = "移动失败"
                    self.stats["locked"] += 1
                    self.errors.add(node.full_path, "MoveFailed", "IO错误")

    def _process_delete(self, node: FileNode, dry_run: bool) -> None:
        """处理单个删除操作"""
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
                # 缩小 TOCTOU 竞态窗口：移动前再次确认文件存在
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
                    time.sleep(0.5)  # 重试间隔 500ms
                    continue
                with self._lock:
                    node.status = NodeStatus.LOCKED
                    node.error_msg = "删除失败"
                    self.stats["locked"] += 1
                    self.errors.add(node.full_path, "DeleteFailed", "IO错误")

    def _cleanup_empty_dirs(self) -> None:
        """清理空目录"""
        dirs = sorted(
            [n for n in self.nodes if n.is_dir],
            key=lambda n: n.depth, reverse=True
        )
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

    def _resolve_conflict(self, target_path: str) -> str:
        """解决目标路径冲突"""
        dir_name = os.path.dirname(target_path)
        base, ext = os.path.splitext(os.path.basename(target_path))
        counter = 1
        while True:
            new_name = f"{base}_{counter}{ext}"
            new_path = os.path.join(dir_name, new_name)
            if not Win32IO.exists(new_path):
                return new_path
            counter += 1

    def _get_trash_path(self, original_path: str) -> str:
        """
        获取回收站目标路径 (V6.6: 原子操作)
        
        使用时间戳+UUID组合直接生成唯一路径，避免TOCTOU竞态条件。
        """
        try:
            rel = os.path.relpath(original_path, self.root_path)
        except ValueError:
            rel = os.path.basename(original_path)
        target = os.path.join(self.trash_dir, rel)
        
        # V6.6 安全修复: 原子操作
        # 直接生成唯一路径，不做预先检查
        base, ext = os.path.splitext(target)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_suffix = uuid.uuid4().hex[:8]
        return f"{base}_{ts}_{unique_suffix}{ext}"

    def _log_op(self, action: str, source: str, target: str, status: str = "SUCCESS") -> None:
        """记录操作日志（调用方须已持有 self._lock，不可重入）"""
        self._seq += 1
        self.op_logs.append(OperationLog(
            seq=self._seq, action=action, source=source, target=target,
            timestamp=datetime.now(timezone.utc).isoformat(), status=status
        ))

    def _maybe_throttle(self) -> None:
        """节流控制"""
        with self._lock:
            self._throttle += 1
            do_sleep = (self._throttle % THROTTLE_BATCH == 0)
        if do_sleep:
            time.sleep(THROTTLE_SLEEP)

    def _write_log(self) -> None:
        """写入操作日志"""
        if not self.op_logs:
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = os.path.join(self.log_dir, f"operations_{ts}.jsonl")
        with open(log_path, "w", encoding="utf-8") as f:
            for entry in self.op_logs:
                f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
        print(f"📝 操作日志: {log_path}")

    def _rotate_logs(self) -> None:
        """日志轮转"""
        try:
            if not os.path.isdir(self.log_dir):
                return
            log_files = sorted([
                f for f in os.listdir(self.log_dir)
                if f.startswith("operations_") and f.endswith(".jsonl")
            ])
            if len(log_files) > MAX_LOG_FILES:
                for old_file in log_files[:len(log_files) - MAX_LOG_FILES]:
                    try:
                        os.remove(os.path.join(self.log_dir, old_file))
                    except OSError:
                        pass
        except OSError:
            pass

    def generate_rollback_script(self) -> str:
        """
        生成回滚脚本 (V6.6: 使用json.dumps防止路径注入)
        
        Returns:
            回滚脚本的完整 Python 代码
        """
        if not self.op_logs:
            return "#!/usr/bin/env python3\n# 无操作记录，无需回滚\nimport sys\nsys.exit(0)\n"

        lines = [
            '#!/usr/bin/env python3',
            '"""回滚脚本 - 自动生成"""',
            f'# 生成时间: {datetime.now().isoformat()}',
            f'# 共 {len(self.op_logs)} 条操作记录',
            '',
            'import os',
            'import shutil',
            'import sys',
            '',
            'def main():',
            '    restored = 0',
            '    errors = 0',
            '',
        ]

        # 收集所有需要创建的目录
        mkdir_dirs: Set[str] = set()
        for entry in reversed(self.op_logs):
            if entry.action in ("MOVE", "DELETE"):
                src_dir = os.path.dirname(entry.source)
                if src_dir:
                    mkdir_dirs.add(src_dir)
            elif entry.action == "RMDIR":
                mkdir_dirs.add(entry.source)

        # 在循环外一次性创建所有目录
        for d in sorted(mkdir_dirs):
            lines.append(f'    os.makedirs({json.dumps(d)}, exist_ok=True)')

        lines.append('')
        lines.append('    # --- 操作回滚 ---')

        for entry in reversed(self.op_logs):
            if entry.action in ("MOVE", "DELETE"):
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

        lines.append('    print(f"✅ 回滚完成: 恢复 {restored} 项, 失败 {errors} 项")')
        lines.append('    return 0 if errors == 0 else 1')
        lines.append('')
        lines.append('if __name__ == "__main__":')
        lines.append('    sys.exit(main())')
        return '\n'.join(lines)

    def generate_report(self) -> str:
        """生成操作报告"""
        lines = [
            "=" * 50,
            "📊 操作报告",
            "=" * 50,
            f"  ✅ 成功处理: {self.stats['success']} 项",
            f"  🗑️ 移入回收站: {self.stats['trashed']} 项",
        ]
        for key, label in [
            ("vanished", "Vanished (扫描后已消失)"),
            ("locked", "Locked (文件被占用/权限不足)"),
            ("changed", "Changed (扫描后被修改)"),
            ("conflict", "Conflict (命名冲突)"),
            ("failed", "Failed (其他失败)"),
        ]:
            if self.stats[key]:
                lines.append(f"    - {label}: {self.stats[key]} 项")
        lines.append("=" * 50)
        return "\n".join(lines)


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


def _parse_actions(action_str: str) -> Set[PermissionAction]:
    """解析权限动作字符串"""
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
    """
    解析多种格式的文件列表。

    支持格式:
      1. PowerShell Format-Table 输出 (含 FullName 表头)
      2. 纯路径列表 (每行一个路径)
      3. 带深度和类型的自定义格式: [{depth}] path #DIR / [{depth}] path #FILE

    核心能力:
      - 自动跳过 PowerShell 表头和分隔线
      - 检测被控制台宽度截断的行 (以 '...' 结尾)
      - 去除列对齐产生的尾部空白
      - 基于前缀关系推断文件/目录
      - 支持多文件合并 + 跨文件去重
    """

    _CUSTOM_FMT_RE = re.compile(r'^\[\{?(\d+)\}?\]\s+(.+?)\s+#(DIR|FILE)\s*$')

    def __init__(self):
        self.total_truncated = 0
        self.total_skipped = 0
        self._type_map: Dict[str, bool] = {}
        self._depth_map: Dict[str, int] = {}
        self._has_custom_format = False

    def load_files(self, filepaths: List[str]) -> List[str]:
        """加载文件列表"""
        all_lines = []
        self.total_truncated = 0
        self.total_skipped = 0
        self._type_map = {}
        self._depth_map = {}
        self._has_custom_format = False

        for filepath in filepaths:
            if not os.path.isfile(filepath):
                print(f"❌ 文件不存在: {filepath}")
                continue

            print(f"📂 加载文件列表: {filepath}")
            lines, truncated, skipped = self._load_single(filepath)
            all_lines.extend(lines)
            self.total_truncated += truncated
            self.total_skipped += skipped
            print(f"   有效路径: {len(lines)} 行, 截断跳过: {truncated}, 表头/空行跳过: {skipped}")

        # 跨文件去重
        before = len(all_lines)
        all_lines = list(dict.fromkeys(all_lines))
        if len(all_lines) < before:
            print(f"   跨文件去重: {before} → {len(all_lines)} 行")

        return all_lines

    def _load_single(self, filepath: str) -> Tuple[List[str], int, int]:
        """加载单个文件"""
        with open(filepath, "r", encoding="utf-8-sig") as f:
            raw_lines = f.readlines()

        valid_lines = []
        truncated_count = 0
        skipped_count = 0
        in_header = True
        found_fullname = False

        for raw in raw_lines:
            line = raw.rstrip("\r\n")
            if not line.strip():
                skipped_count += 1
                continue

            # 自定义格式检测
            m = self._CUSTOM_FMT_RE.match(line)
            if m:
                self._has_custom_format = True
                depth = int(m.group(1))
                path_str = m.group(2).strip()
                is_dir = m.group(3) == "DIR"
                self._type_map[path_str] = is_dir
                self._depth_map[path_str] = depth
                valid_lines.append(path_str)
                continue

            # PowerShell 表头处理
            if in_header:
                stripped = line.strip()
                if stripped == "FullName" or "FullName" in stripped:
                    found_fullname = True
                    skipped_count += 1
                    continue
                if stripped.startswith("-" * 8):
                    in_header = False
                    skipped_count += 1
                    continue
                if not found_fullname:
                    in_header = False
                else:
                    in_header = False

            # 截断检测
            line = line.rstrip()
            if TRUNCATED_LINE_RE.search(line):
                truncated_count += 1
                continue

            valid_lines.append(line)

        return valid_lines, truncated_count, skipped_count

    def parse_to_virtual_nodes(self, lines: List[str]) -> Tuple[List[VirtualFileNode], str, Dict]:
        """将路径列表解析为虚拟节点"""
        SEP_WIN = "\\"
        use_custom = self._has_custom_format and bool(self._type_map)

        # 推断公共根
        root_parts = None
        for line in lines:
            parts = line.split(SEP_WIN)
            if root_parts is None:
                root_parts = list(parts)
            else:
                new_root = []
                for a, b in zip(root_parts, parts):
                    if a == b:
                        new_root.append(a)
                    else:
                        break
                root_parts = new_root

        root = SEP_WIN.join(root_parts) if root_parts else ""
        if re.match(r'^[A-Z]:$', root, re.IGNORECASE):
            root = root + SEP_WIN
        root_depth = len(root_parts) if root_parts else 1

        # 确定 is_dir
        all_intermediate_dirs: Set[str] = set()
        for line in lines:
            parts = line.split(SEP_WIN)
            for i in range(root_depth, len(parts) - 1):
                all_intermediate_dirs.add(SEP_WIN.join(parts[:i + 1]))

        all_known = set(lines) | all_intermediate_dirs
        sorted_paths = sorted(all_known, key=lambda p: p.lower())

        def has_children(path: str) -> bool:
            prefix = path.lower() + SEP_WIN
            lo = bisect.bisect_left(sorted_paths, prefix, key=lambda p: p.lower())
            if lo < len(sorted_paths):
                return sorted_paths[lo].lower().startswith(prefix)
            return False

        is_dir_map = {}
        for line in lines:
            if line in all_intermediate_dirs:
                is_dir_map[line] = True
            elif use_custom and line in self._type_map:
                is_dir_map[line] = self._type_map[line]
            elif has_children(line):
                is_dir_map[line] = True
            else:
                is_dir_map[line] = False

        # 创建节点
        nodes = []
        existing_paths = set()

        for line in lines:
            parts = line.split(SEP_WIN)
            rel_depth = self._depth_map.get(line, len(parts) - root_depth) if use_custom else len(parts) - root_depth
            fname = parts[-1] if parts else ""
            is_dir = is_dir_map.get(line, False)
            ext = "" if is_dir else ("." + fname.rsplit(".", 1)[1].lower() if "." in fname else "")

            nodes.append(VirtualFileNode(
                full_path=line, file_name=fname, extension=ext,
                depth=rel_depth, is_dir=is_dir,
            ))
            existing_paths.add(line)

        # 补充隐含目录
        implicit_count = 0
        for dir_path in all_intermediate_dirs:
            if dir_path not in existing_paths:
                dir_parts = dir_path.split(SEP_WIN)
                nodes.append(VirtualFileNode(
                    full_path=dir_path, file_name=dir_parts[-1],
                    extension="", depth=len(dir_parts) - root_depth, is_dir=True,
                ))
                implicit_count += 1

        parse_stats = {
            "root": root,
            "total_input_lines": len(lines),
            "dirs_detected": sum(1 for v in is_dir_map.values() if v),
            "files_detected": sum(1 for v in is_dir_map.values() if not v),
            "implicit_dirs": implicit_count,
            "custom_format": use_custom,
        }

        return nodes, root, parse_stats


# ============================================================
# 离线分析引擎
# ============================================================

class OfflineDiagnosticEngine:
    """离线诊断：基于虚拟节点统计"""

    def __init__(self, nodes: List[VirtualFileNode], root: str):
        self.nodes = nodes
        self.root = root

    def get_stats(self, top_n: int = 10) -> Dict:
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
            "root": self.root,
            "total_items": len(self.nodes),
            "total_files": len(files),
            "total_dirs": len(dirs),
            "max_depth": max((n.depth for n in self.nodes), default=0),
            "long_path_files": len(long_names),
            "long_path_examples": long_names[:5],
            "deepest_files": [(n.full_path, n.depth) for n in deepest[:5]],
            "extension_stats": dict(sorted(ext_count.items(), key=lambda x: x[1], reverse=True)[:15]),
        }


class OfflineEliminationEngine:
    """离线剔除：基于虚拟节点的缓存标记和去重"""

    def __init__(self, nodes: List[VirtualFileNode]):
        self.nodes = nodes
        self.duplicate_groups: List[Dict] = []

    def mark_cache_dirs(self, extra_patterns: List[str] = None) -> None:
        """标记缓存目录和垃圾文件"""
        patterns = list(CACHE_DIR_PATTERNS)
        if extra_patterns:
            patterns.extend(extra_patterns)

        for node in self.nodes:
            if node.action != NodeAction.NONE:
                continue
            if not node.is_dir:
                for pattern in JUNK_FILE_PATTERNS:
                    if fnmatch.fnmatch(node.file_name.lower(), pattern.lower()):
                        node.action = NodeAction.DELETE
                        node.reason = f"临时文件: {pattern}"
                        break

        cache_roots: List[VirtualFileNode] = []
        for node in self.nodes:
            if node.action != NodeAction.NONE:
                continue
            for pattern in patterns:
                if fnmatch.fnmatch(node.file_name.lower(), pattern.lower()):
                    cache_roots.append(node)
                    node.action = NodeAction.DELETE
                    node.reason = f"缓存目录/文件: {pattern}"
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

    def deduplicate_by_name(self) -> int:
        """基于文件名的离线去重"""
        files = [n for n in self.nodes if not n.is_dir and n.action == NodeAction.NONE]

        name_groups: Dict[str, List[VirtualFileNode]] = defaultdict(list)
        for f in files:
            name_groups[f.file_name.lower()].append(f)

        dup_count = 0
        for name, group in name_groups.items():
            if len(group) < 2:
                continue

            has_size_data = any(n.size_bytes > 0 for n in group)

            if has_size_data:
                size_subgroups: Dict[int, List[VirtualFileNode]] = defaultdict(list)
                for n in group:
                    size_subgroups[n.size_bytes].append(n)

                for size, subgroup in size_subgroups.items():
                    if len(subgroup) < 2:
                        continue
                    keep = min(subgroup, key=lambda n: n.depth)
                    self.duplicate_groups.append({
                        "name": name, "size": size,
                        "keep": keep.full_path,
                        "delete": [n.full_path for n in subgroup if n.full_path != keep.full_path],
                    })
                    for n in subgroup:
                        if n.full_path != keep.full_path:
                            n.action = NodeAction.DELETE
                            n.reason = f"同名同大小重复 (保留: 深度{keep.depth})"
                            dup_count += 1
            else:
                keep_candidate = min(group, key=lambda n: n.depth)
                for n in group:
                    if n.action == NodeAction.NONE:
                        n.reason = "同名疑似重复，请手动确认"
                self.duplicate_groups.append({
                    "name": name, "size": None,
                    "keep": keep_candidate.full_path,
                    "delete": [m.full_path for m in group if m.full_path != keep_candidate.full_path],
                })

        return dup_count


class OfflineRestructureEngine:
    """离线重组：基于虚拟节点的冗余解包"""

    def __init__(self, nodes: List[VirtualFileNode], root: str):
        self.nodes = nodes
        self.root = root

    def unpack_redundant(self) -> int:
        """解包冗余目录结构"""
        unpacked = 0
        SEP_WIN = "\\"
        dirs = [n for n in self.nodes if n.is_dir and n.action == NodeAction.NONE]
        dir_children = defaultdict(list)
        for d in dirs:
            parent = SEP_WIN.join(d.full_path.split(SEP_WIN)[:-1])
            dir_children[parent.lower()].append(d)

        for parent_lower, children in dir_children.items():
            if len(children) != 1:
                continue
            child = children[0]
            parent_name = parent_lower.split(SEP_WIN)[-1] if parent_lower != self.root.lower() else ""
            if not parent_name or parent_name != child.file_name.lower():
                continue

            actual_parent = SEP_WIN.join(child.full_path.split(SEP_WIN)[:-1])
            child_prefix = child.full_path.lower() + SEP_WIN
            for node in self.nodes:
                if node.full_path.lower().startswith(child_prefix) and node.action == NodeAction.NONE:
                    rel = node.full_path[len(child.full_path):].lstrip(SEP_WIN)
                    node.target_path = SEP_WIN.join([actual_parent, rel]) if rel else actual_parent
                    node.reason = f"解包: {child.file_name}"
                    node.action = NodeAction.MOVE

            child.action = NodeAction.DELETE
            child.reason = "冗余目录（子项已提级）"
            unpacked += 1

        return unpacked


# ============================================================
# 主引擎 — 统一编排器
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

    def run_safety_check(self) -> Tuple[bool, str]:
        """运行前置安全检查"""
        level, msg = SafetyChecker.check(self.target_path)
        icons = {SafetyLevel.FULL: "🟢", SafetyLevel.RESTRICTED: "🟡", SafetyLevel.BLOCKED: "🔴"}
        print(f"\n🔒 安全检查: {icons.get(level, '?')} Level {level.value} — {msg}")
        if level == SafetyLevel.BLOCKED:
            return False, msg
        elif level == SafetyLevel.RESTRICTED:
            print("   ⚠️  受限模式：仅允许预览和诊断操作")
        return True, msg

    def diagnose(self, detect_zone: bool = False, verbose: bool = False) -> Dict:
        """执行目录诊断扫描"""
        print(f"\n🔍 扫描目录: {self.target_path}")
        self.diagnostic = DiagnosticEngine(self.target_path)
        self.nodes = self.diagnostic.scan(detect_zone=detect_zone, verbose=verbose)
        self._scan_ts = self.diagnostic._scan_timestamp
        stats = self.diagnostic.get_stats()
        self._print_stats(stats)
        return stats

    def analyze(self, cache_patterns: List[str] = None,
                dedup_strategy: str = "shallowest") -> Dict:
        """分析文件结构并标记待处理项"""
        result: Dict[str, int] = {}

        self.elim_engine = EliminationEngine(self.nodes, self.errors)
        self.elim_engine.mark_cache_dirs(cache_patterns)
        result["cache_marked"] = sum(1 for n in self.nodes if n.action == NodeAction.DELETE)

        result["duplicates_marked"] = self.elim_engine.deduplicate(dedup_strategy)

        restruct = RestructureEngine(self.nodes, self.target_path, self.errors)
        result["redundant_unpacked"] = restruct.unpack_redundant()

        return result

    def fix_permissions(self, actions: Set[PermissionAction],
                        dry_run: bool = True) -> Dict[str, int]:
        """修复文件权限和属性"""
        self.perm_engine = PermissionEngine(self.nodes)
        return self.perm_engine.apply_all(actions, dry_run=dry_run)

    def preview(self) -> List[Dict]:
        """预览待处理的操作清单"""
        actions = []
        for n in self.nodes:
            if n.action != NodeAction.NONE:
                actions.append({
                    "action": n.action.value,
                    "source": n.full_path,
                    "target": getattr(n, "target_path", ""),
                    "size": _fmt_size(n.size_bytes) if not n.is_dir else "[目录]",
                    "depth": n.depth,
                    "attrs": _node_attrs_str(n),
                })
        return actions

    def get_duplicate_preview(self) -> List[Dict]:
        """获取重复文件预览"""
        if self.elim_engine:
            return self.elim_engine.get_duplicate_groups()
        return []

    def execute(self, dry_run: bool = True) -> Dict[str, int]:
        """执行待处理的操作"""
        engine = ExecutionEngine(self.nodes, self.target_path,
                                 self.log_dir, self.trash_dir)
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
                    json.dump([asdict(e) for e in self.errors.errors],
                             f, indent=2, ensure_ascii=False)
                print(f"⚠️  错误报告: {error_path}")

        return stats

    def _print_stats(self, stats: Dict) -> None:
        """打印诊断统计信息"""
        print(f"\n{'=' * 55}")
        print(f"📊 目录诊断报告")
        print(f"{'=' * 55}")
        print(f"  路径: {stats['root']}")
        print(f"  总项数: {stats['total_items']} (文件 {stats['total_files']}, 目录 {stats['total_dirs']})")
        print(f"  总大小: {stats['total_size_mb']} MB")
        print(f"  最大深度: {stats['max_depth']}")
        print(f"  符号链接: {stats['symlinks']}  |  Junction: {stats['junctions']}  |  硬链接: {stats['hard_links']}")

        perm_parts = []
        if stats["readonly_files"]:
            perm_parts.append(f"只读 {stats['readonly_files']}")
        if stats["hidden_files"]:
            perm_parts.append(f"隐藏 {stats['hidden_files']}")
        if stats["system_files"]:
            perm_parts.append(f"系统 {stats['system_files']}")
        if stats["zone_identifier_files"]:
            perm_parts.append(f"Zone标识 {stats['zone_identifier_files']}")
        if perm_parts:
            print(f"  🔑 属性: {' / '.join(perm_parts)}")

        if stats["long_path_files"]:
            print(f"  ⚠️  超长路径 (>260字节): {stats['long_path_files']} 个文件")

        if stats["deepest_files"]:
            print(f"\n  📁 层级最深 (Top 5):")
            for path, depth in stats["deepest_files"][:5]:
                print(f"    深度 {depth}: {path}")

        if stats["largest_dirs"]:
            print(f"\n  📦 体积最大目录 (Top 5):")
            for path, size in stats["largest_dirs"][:5]:
                print(f"    {size}: {path}")

        if stats["extension_stats"]:
            print(f"\n  📋 扩展名分布 (Top 10):")
            for ext, count in sorted(stats["extension_stats"].items(),
                                     key=lambda x: x[1], reverse=True)[:10]:
                print(f"    {ext}: {count} 个文件")
        print(f"{'=' * 55}\n")


# ============================================================
# 离线分析报告输出
# ============================================================

def print_offline_report(stats: Dict, parse_stats: Dict,
                         cache_count: int, junk_count: int,
                         dup_count: int, dup_groups: List[Dict],
                         unpacked: int, nodes: List[VirtualFileNode],
                         truncated_count: int, skipped_count: int,
                         flatten_count: int = 0) -> None:
    """打印离线诊断报告"""
    sep_line = "=" * 65
    print(f"\n{sep_line}")
    print(f"📊 离线诊断报告 — 文件夹重组引擎 V{VERSION}")
    print(sep_line)
    print(f"  路径: {stats['root']}")
    print(f"  总项数: {stats['total_items']} (文件 {stats['total_files']}, 目录 {stats['total_dirs']})")
    print(f"  最大深度: {stats['max_depth']}")

    if truncated_count > 0:
        print(f"\n  ⛔ 截断警告: {truncated_count} 行被截断，已跳过")
        print(f"     解决: (Get-ChildItem -Path 'F:\\' -Recurse).FullName | Out-File 'list.txt' -Encoding UTF8")

    print(f"\n  📂 路径解析: {parse_stats['files_detected']} 文件 + {parse_stats['dirs_detected']} 目录")

    if stats["long_path_files"]:
        print(f"  ⚠️  超长路径: {stats['long_path_files']} 个")

    print(f"\n📋 扩展名分布:")
    for ext, count in sorted(stats["extension_stats"].items(), key=lambda x: x[1], reverse=True)[:10]:
        bar = "█" * min(40, count // 500)
        print(f"  {ext:15s} {count:6d}  {bar}")

    if stats["deepest_files"]:
        print(f"\n📁 层级最深:")
        for path, depth in stats["deepest_files"][:5]:
            print(f"  深度 {depth}: {path[:100]}")

    total_delete = junk_count + cache_count + dup_count
    print(f"\n🔧 推荐操作:")
    print(f"  🗑️ 垃圾文件: {junk_count} 项")
    print(f"  🗑️ 缓存目录: {cache_count} 项")
    print(f"  🔄 冗余目录解包: {unpacked} 项")
    print(f"  🗑️ 同名重复文件: {dup_count} 项")
    print(f"\n  📊 总计可清理: {total_delete} 项")

    if dup_groups:
        print(f"\n📋 重复文件详情 (前 15 组):")
        for i, g in enumerate(dup_groups[:15], 1):
            print(f"  组 {i}: {g['name'][:60]}")
            print(f"    ✅ 保留: {g['keep'][:90]}")
            for d in g["delete"][:3]:
                print(f"    🗑️ 删除: {d[:90]}")
            if len(g["delete"]) > 3:
                print(f"    ... +{len(g['delete'])-3} 个")

    deleted = [n for n in nodes if n.action == NodeAction.DELETE]
    if deleted:
        print(f"\n🗑️ 删除预览 (前 30 项):")
        for i, n in enumerate(deleted[:30], 1):
            print(f"  {i:4d}. {n.file_name[:50]:50s} ← {n.reason}")

    print(f"\n{sep_line}")
    print(f"✅ 诊断完成")
    print(f"   可删除: {total_delete} 项")
    print(f"   可移动: {sum(1 for n in nodes if n.action == NodeAction.MOVE)} 项")
    print(f"   无操作: {sum(1 for n in nodes if n.action == NodeAction.NONE)} 项")
    print(sep_line)


# ============================================================
# CLI
# ============================================================

def _update_workers(n: int) -> None:
    """更新全局线程数配置"""
    global MAX_WORKERS
    MAX_WORKERS = max(1, n)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=f"文件夹结构智能重组引擎 V{VERSION} (Windows + 离线分析 | 多线程加速)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python 文件夹重组工具V6.6.py D:\\Projects --diagnose
  python 文件夹重组工具V6.6.py D:\\Projects --preview
  python 文件夹重组工具V6.6.py D:\\Projects --execute
  python 文件夹重组工具V6.6.py --from-list list.txt
        """
    )

    parser.add_argument("target", nargs="?", help="目标目录路径 (在线模式)")
    parser.add_argument("--from-list", nargs="+", metavar="FILE", help="离线模式：从文件列表分析")
    parser.add_argument("--diagnose", action="store_true", help="仅诊断，不修改")
    parser.add_argument("--preview", action="store_true", help="分析 + 预览操作清单")
    parser.add_argument("--execute", action="store_true", help="执行操作")
    parser.add_argument("--yes", "-y", action="store_true", help="跳过确认")
    parser.add_argument("--detect-zone", action="store_true", help="扫描时检测 Zone.Identifier")
    parser.add_argument("--export", metavar="FILE", help="导出报告 (CSV/JSON)")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")
    parser.add_argument("--extra-patterns", nargs="+", help="额外的缓存匹配模式")
    parser.add_argument("--dedup-strategy", choices=["shallowest", "newest"], default="shallowest")
    parser.add_argument("--fix-attr", metavar="ACTIONS", help="修复权限/属性: perm,readonly,hidden,zone,all")
    parser.add_argument("--flatten", nargs="?", const="", metavar="TARGET_DIR", help="层级扁平化")
    parser.add_argument("--force-lock", action="store_true", help="强制清除锁文件")
    parser.add_argument("--threads", "-j", type=int, metavar="N", help=f"并行线程数 (默认: {MAX_WORKERS})")
    parser.add_argument("--test", action="store_true", help="运行内置单元测试")

    args = parser.parse_args()

    if args.threads and args.threads > 0:
        _update_workers(args.threads)

    # 单元测试模式
    if "--test" in sys.argv or args.test:
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
        elim.mark_cache_dirs(extra_patterns=args.extra_patterns)
        cache_count = sum(1 for n in nodes if n.action == NodeAction.DELETE and "缓存" in n.reason)
        junk_count = sum(1 for n in nodes if n.action == NodeAction.DELETE and "临时文件" in n.reason)

        dup_count = elim.deduplicate_by_name()
        dup_groups = elim.duplicate_groups

        restruct = OfflineRestructureEngine(nodes, root)
        unpacked = restruct.unpack_redundant()

        flatten_count = 0
        if args.flatten is not None:
            flatten_count = sum(1 for n in nodes if not n.is_dir and n.action == NodeAction.NONE)
            print("  ⚠️  扁平化仅在线模式可执行")

        print_offline_report(stats, parse_stats, cache_count, junk_count,
                            dup_count, dup_groups, unpacked, nodes,
                            parser_obj.total_truncated, parser_obj.total_skipped,
                            flatten_count=flatten_count)

        if args.export:
            report = {
                "engine_version": VERSION,
                "mode": "offline",
                "stats": stats,
                "parse_stats": parse_stats,
                "operations": {"cache_cleanup": cache_count, "junk_cleanup": junk_count,
                              "duplicates": dup_count, "redundant_unpack": unpacked},
                "duplicate_groups": dup_groups[:50],
            }
            with open(args.export, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, ensure_ascii=False)
            print(f"\n📄 JSON 报告已导出: {args.export}")
        return

    # 在线模式 (仅 Windows)
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

    safe, msg = engine.run_safety_check()
    if not safe:
        print(f"❌ 操作被禁止: {msg}")
        sys.exit(1)

    # 锁管理
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

        analysis = engine.analyze(
            cache_patterns=args.extra_patterns,
            dedup_strategy=args.dedup_strategy,
        )
        print(f"  缓存目录标记: {analysis['cache_marked']} 项")
        print(f"  重复文件标记: {analysis['duplicates_marked']} 项")
        print(f"  冗余结构解包: {analysis['redundant_unpacked']} 项")

        flatten_count = 0
        if args.flatten is not None:
            flatten_target = args.flatten if args.flatten else os.path.join(engine.target_path, "_flattened")
            print(f"\n📦 层级扁平化: {flatten_target}")
            flatten_engine = FlattenEngine(engine.nodes, engine.target_path, engine.errors)
            flatten_engine.extract_files(flatten_target)
            flatten_count = sum(1 for n in engine.nodes if n.action == NodeAction.MOVE and n.target_path)
            print(f"  扁平化文件: {flatten_count} 项")

        dup_groups = engine.get_duplicate_preview()
        if dup_groups:
            print(f"\n📋 重复文件清单 ({len(dup_groups)} 组):")
            for g in dup_groups[:20]:
                print(f"  组 {g['group_id']}: {g['size']}")
                print(f"    ✅ 保留: {g['keep']['path']} (深度 {g['keep']['depth']})")
                for d in g["delete"]:
                    print(f"    🗑️ 删除: {d['path']} (深度 {d['depth']})")

        perm_stats: Dict[str, int] = {}
        if args.fix_attr:
            perm_actions = _parse_actions(args.fix_attr)
            if perm_actions:
                perm_stats = engine.fix_permissions(perm_actions, dry_run=not args.execute)

        preview = engine.preview()
        if preview:
            print(f"\n📋 结构变更清单 ({len(preview)} 项):")
            print("-" * 65)
            for i, item in enumerate(preview[:50], 1):
                icon = {"DELETE": "🗑️", "MOVE": "📦", "RENAME": "✏️"}.get(item["action"], "❓")
                if item["action"] == "DELETE":
                    print(f"  {i:4d}. {icon} DELETE {item['source']} ({item['size']})")
                else:
                    print(f"  {i:4d}. {icon} {item['source']}")
                    print(f"         → {item['target']}")
            if len(preview) > 50:
                print(f"  ... 还有 {len(preview) - 50} 项")
            print("-" * 65)
        else:
            print("\n✅ 结构方面无需变更")

        if args.preview:
            print(f"\n💡 使用 --execute 参数执行实际操作")
            return

        if args.execute:
            total_ops = len(preview) + sum(v for k, v in perm_stats.items() if k not in ("failed", "skipped"))
            if not args.yes and total_ops > 0:
                confirm = input(f"\n⚠️  即将执行 {total_ops} 项操作。确认？(yes/no): ")
                if confirm.lower() not in ("yes", "y"):
                    print("❌ 已取消")
                    return

            engine.execute(dry_run=False)

    except KeyboardInterrupt:
        print("\n\n⚠️  用户中断")
    finally:
        if lock_acquired:
            lock.release()


# ============================================================
# 单元测试
# ============================================================

def run_tests() -> bool:
    """运行内置单元测试"""
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
        def test_dots_spaces(self):
            result = _sanitize_filename("  ...  ")
            self.assertNotEqual(result, "")
            self.assertFalse(result.startswith(" "))

    class TestTruncateName(unittest.TestCase):
        def test_short_name_unchanged(self):
            self.assertEqual(_truncate_name("short.txt", 100), "short.txt")
        def test_long_name_truncated(self):
            result = _truncate_name("a" * 300 + ".txt", 50)
            self.assertLessEqual(len(result.encode("utf-8")), 50)
            self.assertTrue(result.endswith(".txt"))
        def test_hash_appended(self):
            result = _truncate_name("x" * 200 + ".dat", 40)
            self.assertIn("_", result)

    class TestFmtSize(unittest.TestCase):
        def test_bytes(self): self.assertIn("B", _fmt_size(500))
        def test_kb(self): self.assertIn("KB", _fmt_size(2048))
        def test_mb(self): self.assertIn("MB", _fmt_size(5 * 1024 * 1024))
        def test_gb(self): self.assertIn("GB", _fmt_size(3 * 1024 * 1024 * 1024))
        def test_zero(self): self.assertIn("B", _fmt_size(0))
        def test_input_unchanged(self):
            original = 5 * 1024 * 1024
            _ = _fmt_size(original)
            self.assertEqual(original, 5 * 1024 * 1024)

    class TestWinPath(unittest.TestCase):
        def test_roundtrip_normal(self):
            path = r"C:\Users\test\file.txt"
            self.assertEqual(_short_path(_win_path(path)), path)
        def test_unc_path(self):
            path = r"\\server\share\file.txt"
            wp = _win_path(path)
            if IS_WINDOWS:
                self.assertTrue(wp.startswith("\\\\?\\UNC\\"))

    class TestVirtualFileNode(unittest.TestCase):
        def test_creation(self):
            node = VirtualFileNode(r"C:\test\file.txt", "file.txt", ".txt", 1024, 1)
            self.assertEqual(node.file_name, "file.txt")
            self.assertEqual(node.size_bytes, 1024)
            self.assertFalse(node.is_dir)

    class TestOfflineDedupByName(unittest.TestCase):
        def test_size_aware_dedup(self):
            nodes = [
                VirtualFileNode(r"C:\a\video.mp4", "video.mp4", ".mp4", 1024 * 1024, 1),
                VirtualFileNode(r"C:\b\video.mp4", "video.mp4", ".mp4", 1024 * 1024, 2),
                VirtualFileNode(r"C:\c\video.mp4", "video.mp4", ".mp4", 100, 1),
            ]
            engine = OfflineEliminationEngine(nodes)
            count = engine.deduplicate_by_name()
            self.assertEqual(count, 1)
            self.assertEqual(nodes[0].action, NodeAction.NONE)
            self.assertEqual(nodes[1].action, NodeAction.DELETE)
            self.assertEqual(nodes[2].action, NodeAction.NONE)

        def test_no_size_data_marks_review(self):
            nodes = [
                VirtualFileNode(r"C:\a\doc.txt", "doc.txt", ".txt", 0, 1),
                VirtualFileNode(r"C:\b\doc.txt", "doc.txt", ".txt", 0, 2),
            ]
            engine = OfflineEliminationEngine(nodes)
            count = engine.deduplicate_by_name()
            self.assertEqual(count, 0)
            self.assertIn("手动确认", nodes[0].reason)

    class TestFileListParser(unittest.TestCase):
        def test_pure_path_list(self):
            with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
                f.write("C:\\Projects\\src\\main.py\nC:\\Projects\\README.md\n")
                tmppath = f.name
            parser = FileListParser()
            lines = parser.load_files([tmppath])
            os.unlink(tmppath)
            self.assertEqual(len(lines), 2)
            self.assertFalse(parser._has_custom_format)

        def test_truncated_line_detection(self):
            with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
                f.write("C:\\Projects\\normal.txt\nC:\\Projects\\long_path...\nC:\\Projects\\another.txt\n")
                tmppath = f.name
            parser = FileListParser()
            lines = parser.load_files([tmppath])
            os.unlink(tmppath)
            self.assertEqual(len(lines), 2)
            self.assertEqual(parser.total_truncated, 1)

    class TestRollbackScript(unittest.TestCase):
        def test_rollback_generates_python(self):
            engine = ExecutionEngine([], "/tmp/test", "/tmp/log", "/tmp/trash")
            engine.op_logs.append(OperationLog(1, "MOVE", r"C:\file.txt", r"C:\trash\file.txt", "2026-01-01T00:00:00"))
            script = engine.generate_rollback_script()
            self.assertIn("shutil.move", script)
            self.assertIn("import shutil", script)

        def test_empty_rollback(self):
            engine = ExecutionEngine([], "/tmp/test", "/tmp/log", "/tmp/trash")
            script = engine.generate_rollback_script()
            self.assertIn("无需回滚", script)

        def test_rollback_path_injection_protection(self):
            engine = ExecutionEngine([], "/tmp/test", "/tmp/log", "/tmp/trash")
            engine.op_logs.append(OperationLog(1, "MOVE", r'C:\test"file.txt', r'C:\trash"file.txt', "2026-01-01T00:00:00"))
            script = engine.generate_rollback_script()
            self.assertIn('\\"', script)
            self.assertNotIn('r"C:\\test', script)

    class TestTrashPathConflict(unittest.TestCase):
        def test_trash_path_always_unique(self):
            with tempfile.TemporaryDirectory() as tmpdir:
                trash_dir = os.path.join(tmpdir, ".trash")
                os.makedirs(trash_dir)
                engine = ExecutionEngine([], tmpdir, os.path.join(tmpdir, "log"), trash_dir)
                paths = [engine._get_trash_path(os.path.join(tmpdir, "subdir", "test.txt")) for _ in range(10)]
                self.assertEqual(len(paths), len(set(paths)))
                for path in paths:
                    self.assertRegex(os.path.basename(path), r'.*_\d{8}_\d{6}_[a-f0-9]{8}\.txt$')

    class TestOfflineDedupScopeFix(unittest.TestCase):
        def test_dedup_no_size_single_group(self):
            nodes = [
                VirtualFileNode(r"C:\a\doc.txt", "doc.txt", ".txt", 0, 1),
                VirtualFileNode(r"C:\b\doc.txt", "doc.txt", ".txt", 0, 2),
                VirtualFileNode(r"C:\c\doc.txt", "doc.txt", ".txt", 0, 3),
            ]
            engine = OfflineEliminationEngine(nodes)
            count = engine.deduplicate_by_name()
            self.assertEqual(count, 0)
            self.assertEqual(len(engine.duplicate_groups), 1)
            self.assertEqual(engine.duplicate_groups[0]["keep"], r"C:\a\doc.txt")
            self.assertEqual(len(engine.duplicate_groups[0]["delete"]), 2)

    class TestNodeAttrsStr(unittest.TestCase):
        def test_no_attrs(self):
            node = VirtualFileNode(r"C:\test.txt", "test.txt", ".txt")
            self.assertEqual(_node_attrs_str(node), "-")
        def test_hidden_readonly(self):
            node = FileNode(r"C:\test.txt", "test.txt", ".txt", is_hidden=True, is_readonly=True)
            self.assertEqual(_node_attrs_str(node), "HR")
        def test_reparse_symlink(self):
            node = FileNode(r"C:\link", "link", "", reparse_type=ReparseType.SYMLINK)
            self.assertIn("S", _node_attrs_str(node))

    # 运行测试
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for test_class in [
        TestSanitizeFilename, TestTruncateName, TestFmtSize,
        TestWinPath, TestVirtualFileNode, TestOfflineDedupByName,
        TestFileListParser, TestRollbackScript, TestTrashPathConflict,
        TestOfflineDedupScopeFix, TestNodeAttrsStr,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(test_class))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return result.wasSuccessful()


if __name__ == "__main__":
    main()
