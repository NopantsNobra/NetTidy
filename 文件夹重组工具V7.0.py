#!/usr/bin/env python3
""" 文件夹结构智能重组工具 V7.0.0 (Windows 专用 + 离线分析模式)
Folder Structure Intelligent Restructuring Engine for Windows

核心原则：绝对防丢、极度容错、用户可控
默认为预览模式，必须显式确认才执行物理操作。

V7.0 相比 V6.6 核心变更：
- 🔴 Bug修复：ExecutionEngine._log_op 死锁风险（移除内部不可重入锁）
- 🔴 Bug修复：FileListParser._load_single found_fullname 永远为 False
- 🟡 优化：移除未使用的 auto 导入
- 🟡 优化：execute() Phase 编号与产品手册对齐，补充 Phase 1 标签
- 🟢 完善：更正手册中单元测试数量为 29 项

V6.6 相比 V6.0 核心变更：
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
VERSION = "7.0.0"
LOCK_FILENAME = ".restructure.lock"
LOG_DIR = ".restructure_log"
TRASH_DIR = ".restructure_trash"
MAX_LOG_FILES = 10

# 多线程配置
MAX_WORKERS = min(8, (os.cpu_count() or 4))

# 自动根据 CPU 核心数调整
HASH_BATCH_SIZE = 50
IO_BATCH_SIZE = 200

# Windows 保留设备名
WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

# 常见缓存/临时目录名 (可由用户扩展)
CACHE_DIR_PATTERNS = [
    "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".tox", ".eggs", "*.egg-info", ".gradle", ".mvn", "target",
    "dist", ".next", ".nuxt", "vendor", ".terraform", ".serverless",
    ".cache", ".parcel-cache", ".webpack", "Thumbs.db", "desktop.ini",
    ".DS_Store", "ehthumbs.db", "System Volume Information", "$RECYCLE.BIN",
    "RECYCLER",
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
                wp,
                GENERIC_READ,
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
                seq=self._seq, path=path, error_type=error_type,
                message=message,
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
                        print(f" 主机: {lock_data.get('hostname', 'unknown')}")
                        print(f" 进程: {lock_data.get('pid', 'unknown')}")
                        print(f" 开始: {lock_data.get('started_at', 'unknown')}")
                        print(f" 过期: {expires}")
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
                 "-Stream", "*", "|", "Select-Object", "-ExpandProperty", "Stream"],
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
            "perm_granted": 0,
            "readonly_removed": 0,
            "hidden_removed": 0,
            "zone_cleared": 0,
            "failed": 0,
            "skipped": 0,
        }

    def grant_full_control(self, targets: List[FileNode] = None, dry_run: bool = True) -> Dict[str, int]:
        items = targets or self.nodes
        username = os.environ.get("USERNAME", "")
        if not username:
            print("⚠️ 无法获取当前用户名，跳过权限修改")
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
                    self._errors.add(node.full_path, "PermGrantFailed", result.stderr.strip()[:200])
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

    def remove_readonly(self, targets: List[FileNode] = None, dry_run: bool = True) -> Dict[str, int]:
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

    def remove_hidden(self, targets: List[FileNode] = None, dry_run: bool = True) -> Dict[str, int]:
        items = targets or [n for n in self.nodes if n.is_hidden]
        items = [n for n in items if not n.file_name.startswith(".restructure")]
        total = len(items)
        print(f"\n👁️ 去除隐藏属性: {total} 个文件")

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

    def clear_zone_identifiers(self, targets: List[FileNode] = None, dry_run: bool = True) -> Dict[str, int]:
        items = targets or [n for n in self.nodes if n.has_zone_identifier and not n.is_dir]
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
                self._errors.add(node.full_path, "ZoneClearFailed", "无法清除 Zone.Identifier")
                self.stats["failed"] += 1

            if i % 100 == 0:
                print(f"  Zone 清除进度: {i}/{total}")

        return self.stats

    def apply_all(self, actions: Set[PermissionAction], dry_run: bool = True) -> Dict[str, int]:
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
            print(f"⚠️ 跳过 {skipped_dirs} 个无权限目录")

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
                    pass
            else:
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
