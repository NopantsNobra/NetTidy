#!/usr/bin/env python3
"""
文件夹结构智能重组引擎 V5.0 (Windows 专用)
Folder Structure Intelligent Restructuring Engine for Windows

核心原则：绝对防丢、极度容错、用户可控
默认为预览模式，必须显式确认才执行物理操作。

环境要求: Windows 7+ / Python 3.9+ / 管理员权限(推荐)
"""

import os
import sys
import json
import time
import hashlib
import shutil
import stat
import subprocess
import ctypes
from ctypes import wintypes
import argparse
from pathlib import Path, PureWindowsPath
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from enum import Enum, auto
from typing import List, Dict, Optional, Tuple, Set
from collections import defaultdict
import threading
import uuid
import fnmatch
import re
import tempfile

# ============================================================
# 版本与常量
# ============================================================

VERSION = "5.0.0"
LOCK_FILENAME = ".restructure.lock"
LOG_DIR = ".restructure_log"
TRASH_DIR = ".restructure_trash"

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

# ============================================================
# Win32 API 封装
# ============================================================

def _win_path(path: str) -> str:
    r"""将路径转换为 Windows 长路径格式 (\\?\ )"""
    path = os.path.abspath(path)
    if path.startswith("\\\\"):
        # UNC 路径 → \\?\UNC\server\share\...
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


def _short_path(path: str) -> str:
    r"""从 \\?\ 格式还原为普通路径"""
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[7:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def _safe_exists(path: str) -> bool:
    """支持长路径的文件存在性检查"""
    try:
        win_path = _win_path(path)
        return os.path.exists(win_path) or os.path.exists(path)
    except (OSError, ValueError):
        return False


def _safe_stat(path: str):
    """支持长路径的 stat"""
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


def _safe_listdir(path: str) -> List[str]:
    """支持长路径的 listdir"""
    try:
        wp = _win_path(path)
        if os.path.isdir(wp):
            return os.listdir(wp)
    except (OSError, ValueError):
        pass
    try:
        return os.listdir(path)
    except (OSError, ValueError):
        return []


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
            self._quick_hash = hashlib.md5(data).hexdigest()
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
            h = hashlib.md5()
            with open(fp, "rb") as f:
                while chunk := f.read(65536):
                    h.update(chunk)
            self._full_hash = h.hexdigest()
        except (OSError, IOError):
            self._full_hash = ""
        return self._full_hash


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

    def add(self, path: str, error_type: str, message: str):
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
        except OSError as e:
            print(f"❌ 无法创建锁文件: {e}")
            return False
        return True

    def release(self):
        try:
            if os.path.exists(self.lock_path):
                os.remove(self.lock_path)
        except OSError:
            pass


# ============================================================
# 安全检查
# ============================================================

class SafetyChecker:
    SYSTEM_DIRS = {
        "C:\\Windows", "C:\\Program Files", "C:\\Program Files (x86)",
        "C:\\ProgramData", "C:\\$Recycle.Bin", "C:\\System Volume Information",
    }

    @classmethod
    def _get_user_profile_dirs(cls) -> Set[str]:
        """获取当前用户 AppData 等受保护目录"""
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

        if not _safe_exists(target_path):
            return SafetyLevel.BLOCKED, f"路径不存在: {target_path}"

        # 驱动器根目录
        drive, _ = os.path.splitdrive(target_path)
        if target_path == drive + "\\" or target_path == drive + "/":
            return SafetyLevel.BLOCKED, "禁止操作驱动器根目录"

        # 系统目录
        all_protected = cls.SYSTEM_DIRS | cls._get_user_profile_dirs()
        for sd in all_protected:
            if target_path.lower() == sd.lower() or \
               target_path.lower().startswith(sd.lower() + "\\"):
                return SafetyLevel.BLOCKED, f"命中系统保护目录: {sd}"

        # 写入权限
        if not os.access(target_path, os.W_OK):
            return SafetyLevel.RESTRICTED, "目标目录无写入权限"

        # 磁盘空间
        try:
            usage = shutil.disk_usage(target_path)
            if usage.free < 200 * 1024 * 1024:
                return SafetyLevel.RESTRICTED, "磁盘剩余空间不足 200MB"
        except OSError:
            pass

        # 只读文件系统测试
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
    """
    处理 NTFS 备用数据流 Zone.Identifier
    下载的文件带有此标识时，Windows 会阻止预览/执行
    """

    @staticmethod
    def has_zone_identifier(file_path: str) -> bool:
        """检测文件是否含 Zone.Identifier ADS"""
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
        """清除 Zone.Identifier ADS"""
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

        # 回退方案：使用 PowerShell
        try:
            ps_path = file_path.replace("'", "''")
            cmd = f'Remove-Item -Path \'{ps_path}\' -Stream Zone.Identifier -ErrorAction SilentlyContinue'
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", cmd],
                capture_output=True, text=True, timeout=10
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    @staticmethod
    def list_ads(file_path: str) -> List[str]:
        """列出文件的所有备用数据流"""
        try:
            cmd = f'Get-Item -Path \'{file_path.replace(chr(39), chr(39)*2)}\' -Stream * | Select-Object -ExpandProperty Stream'
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", cmd],
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
    """统一修改文件权限和属性"""

    def __init__(self, nodes: List[FileNode], errors: ErrorCollector):
        self.nodes = nodes
        self.errors = errors
        self.stats = {
            "perm_granted": 0, "readonly_removed": 0,
            "hidden_removed": 0, "zone_cleared": 0,
            "failed": 0, "skipped": 0,
        }

    def grant_full_control(self, targets: List[FileNode] = None,
                           dry_run: bool = True) -> Dict:
        """
        为当前用户授予完全控制权限
        使用 icacls 命令
        """
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
            target = wp if _safe_exists(node.full_path) else node.full_path

            if dry_run:
                if i <= 20:
                    print(f"  [PERM] grant:F {node.file_name}")
                elif i == 21:
                    print(f"  ... 还有 {total - 20} 项")
                continue

            try:
                result = subprocess.run(
                    ["icacls", target, "/grant", f"{username}:(F)", "/T", "/Q"],
                    capture_output=True, text=True, timeout=30
                )
                if result.returncode == 0:
                    node.status = NodeStatus.PERM_FIXED
                    self.stats["perm_granted"] += 1
                else:
                    self.errors.add(node.full_path, "PermGrantFailed",
                                   result.stderr.strip()[:200])
                    self.stats["failed"] += 1
            except subprocess.TimeoutExpired:
                self.errors.add(node.full_path, "PermTimeout", "icacls 超时")
                self.stats["failed"] += 1
            except FileNotFoundError:
                print("❌ icacls 命令不可用")
                return self.stats

            if i % 100 == 0:
                print(f"  权限进度: {i}/{total}")

        return self.stats

    def remove_readonly(self, targets: List[FileNode] = None,
                        dry_run: bool = True) -> Dict:
        """统一去除只读属性"""
        items = targets or [n for n in self.nodes if n.is_readonly]
        total = len(items)
        print(f"\n🔓 去除只读属性: {total} 个文件")

        for i, node in enumerate(items, 1):
            wp = _win_path(node.full_path)
            target = wp if _safe_exists(node.full_path) else node.full_path

            if dry_run:
                if i <= 20:
                    print(f"  [ATTR] -R {node.file_name}")
                elif i == 21:
                    print(f"  ... 还有 {total - 20} 项")
                continue

            try:
                # 先用 os 模块
                st = _safe_stat(node.full_path)
                if st:
                    os.chmod(target, st.st_mode | stat.S_IWRITE)
                # 再用 attrib 确保
                subprocess.run(
                    ["attrib", "-R", target],
                    capture_output=True, timeout=10
                )
                node.is_readonly = False
                node.status = NodeStatus.ATTR_FIXED
                self.stats["readonly_removed"] += 1
            except (OSError, subprocess.TimeoutExpired) as e:
                self.errors.add(node.full_path, "ReadonlyRemoveFailed", str(e))
                self.stats["failed"] += 1

            if i % 100 == 0:
                print(f"  只读去除进度: {i}/{total}")

        return self.stats

    def remove_hidden(self, targets: List[FileNode] = None,
                      dry_run: bool = True) -> Dict:
        """统一去除隐藏属性"""
        items = targets or [n for n in self.nodes if n.is_hidden]
        # 排除 .restructure 自身文件
        items = [n for n in items if not n.file_name.startswith(".restructure")]
        total = len(items)
        print(f"\n👁️  去除隐藏属性: {total} 个文件")

        for i, node in enumerate(items, 1):
            wp = _win_path(node.full_path)
            target = wp if _safe_exists(node.full_path) else node.full_path

            if dry_run:
                if i <= 20:
                    print(f"  [ATTR] -H {node.file_name}")
                elif i == 21:
                    print(f"  ... 还有 {total - 20} 项")
                continue

            try:
                subprocess.run(
                    ["attrib", "-H", target],
                    capture_output=True, timeout=10
                )
                node.is_hidden = False
                node.status = NodeStatus.ATTR_FIXED
                self.stats["hidden_removed"] += 1
            except (OSError, subprocess.TimeoutExpired) as e:
                self.errors.add(node.full_path, "HiddenRemoveFailed", str(e))
                self.stats["failed"] += 1

            if i % 100 == 0:
                print(f"  隐藏去除进度: {i}/{total}")

        return self.stats

    def clear_zone_identifiers(self, targets: List[FileNode] = None,
                                dry_run: bool = True) -> Dict:
        """统一清除网络文件标识 (Zone.Identifier ADS)"""
        items = targets or [n for n in self.nodes
                           if n.has_zone_identifier and not n.is_dir]
        if not targets:
            # 重新检测（scan 时可能未检测）
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
            target = wp if _safe_exists(node.full_path) else node.full_path

            if ZoneIdentifierHandler.clear_zone_identifier(target):
                node.has_zone_identifier = False
                node.status = NodeStatus.ZONE_CLEARED
                self.stats["zone_cleared"] += 1
            else:
                self.errors.add(node.full_path, "ZoneClearFailed",
                               "无法清除 Zone.Identifier")
                self.stats["failed"] += 1

            if i % 100 == 0:
                print(f"  Zone 清除进度: {i}/{total}")

        return self.stats

    def apply_all(self, actions: Set[PermissionAction],
                  dry_run: bool = True) -> Dict:
        """批量应用所有选定的权限/属性操作"""
        if PermissionAction.GRANT_FULL_CONTROL in actions or \
           PermissionAction.ALL in actions:
            self.grant_full_control(dry_run=dry_run)

        if PermissionAction.REMOVE_READONLY in actions or \
           PermissionAction.ALL in actions:
            self.remove_readonly(dry_run=dry_run)

        if PermissionAction.REMOVE_HIDDEN in actions or \
           PermissionAction.ALL in actions:
            self.remove_hidden(dry_run=dry_run)

        if PermissionAction.CLEAR_ZONE_IDENTIFIER in actions or \
           PermissionAction.ALL in actions:
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
        """
        全量扫描目标目录
        detect_zone: 是否检测 Zone.Identifier (较慢)
        """
        self._scan_timestamp = time.time()
        self.nodes = []
        root_depth = self.root_path.count(os.sep)
        skipped_dirs = 0

        for dirpath, dirnames, filenames in os.walk(self.root_path, topdown=True):
            # 权限检查
            try:
                wp = _win_path(dirpath)
                test_dir = wp if os.path.exists(wp) else dirpath
                os.listdir(test_dir)
            except PermissionError:
                skipped_dirs += 1
                self._errors.add(dirpath, "PermissionDenied",
                                "无权限访问，已跳过子项")
                dirnames.clear()
                continue

            rel_depth = dirpath.count(os.sep) - root_depth

            if dirpath != self.root_path:
                self.nodes.append(self._make_node(dirpath, rel_depth, is_dir=True))

            for fname in filenames:
                fpath = os.path.join(dirpath, fname)
                try:
                    node = self._make_node(fpath, rel_depth)

                    # Zone.Identifier 检测 (按需)
                    if detect_zone and not node.is_dir:
                        wp = _win_path(fpath)
                        check_path = wp if os.path.exists(wp) else fpath
                        node.has_zone_identifier = ZoneIdentifierHandler.has_zone_identifier(check_path)

                    self.nodes.append(node)
                except (OSError, IOError):
                    continue

            if verbose and len(self.nodes) % 50000 == 0:
                print(f"  已扫描 {len(self.nodes)} 项...")

        if skipped_dirs:
            print(f"⚠️  跳过 {skipped_dirs} 个无权限目录")

        return self.nodes

    def _make_node(self, path: str, depth: int, is_dir: bool = False) -> FileNode:
        st = _safe_stat(path)
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

            # Windows 隐藏/只读/系统属性
            try:
                attrs = ctypes.windll.kernel32.GetFileAttributesW(
                    _win_path(path) if not _safe_exists(path) else path
                )
                if attrs != -1:
                    is_hidden = bool(attrs & 0x02)   # FILE_ATTRIBUTE_HIDDEN
                    is_readonly = bool(attrs & 0x01)  # FILE_ATTRIBUTE_READONLY
                    is_system = bool(attrs & 0x04)    # FILE_ATTRIBUTE_SYSTEM
                    if attrs & 0x400:  # FILE_ATTRIBUTE_REPARSE_POINT
                        if reparse == ReparseType.NONE:
                            reparse = ReparseType.SYMLINK
            except (AttributeError, OSError):
                # windll 不可用或调用失败
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

        # 长文件名检测
        long_names = []
        for n in self.nodes:
            try:
                name_bytes = len(n.file_name.encode("utf-8"))
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
            "junctions": sum(1 for n in self.nodes if n.reparse_type == ReparseType.JUNCTION),
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

    def export_csv(self, output_path: str):
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
                    datetime.fromtimestamp(n.last_modified).isoformat() if n.last_modified else "",
                    n.reparse_type.value, n.is_hidden, n.is_readonly, n.is_system,
                    n.hard_link_count, n.has_zone_identifier, n.is_dir
                ])

    def export_json(self, output_path: str):
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

    def mark_cache_dirs(self, extra_patterns: List[str] = None):
        patterns = list(CACHE_DIR_PATTERNS)
        if extra_patterns:
            patterns.extend(extra_patterns)

        for node in self.nodes:
            if node.action != NodeAction.NONE:
                continue
            for pattern in patterns:
                if fnmatch.fnmatch(node.file_name.lower(), pattern.lower()):
                    if node.is_dir:
                        prefix = node.full_path.lower() + os.sep
                        for other in self.nodes:
                            if other.full_path.lower().startswith(prefix) or \
                               other.full_path.lower() == node.full_path.lower():
                                other.action = NodeAction.DELETE
                    else:
                        node.action = NodeAction.DELETE
                    break

    def deduplicate(self, strategy: str = "shallowest") -> int:
        files = [n for n in self.nodes
                 if not n.is_dir and n.action == NodeAction.NONE
                 and n.reparse_type != ReparseType.HARDLINK]

        size_groups: Dict[int, List[FileNode]] = defaultdict(list)
        for f in files:
            size_groups[f.size_bytes].append(f)

        dup_count = 0
        for size, group in size_groups.items():
            if len(group) < 2:
                continue

            qhash_groups: Dict[str, List[FileNode]] = defaultdict(list)
            for f in group:
                qh = f.quick_hash()
                if qh:
                    qhash_groups[qh].append(f)

            for qh, qgroup in qhash_groups.items():
                if len(qgroup) < 2:
                    continue

                fhash_groups: Dict[str, List[FileNode]] = defaultdict(list)
                for f in qgroup:
                    fh = f.full_hash()
                    if fh:
                        fhash_groups[fh].append(f)

                for fh, fgroup in fhash_groups.items():
                    if len(fgroup) < 2:
                        continue

                    if strategy == "shallowest":
                        keep = min(fgroup, key=lambda n: n.depth)
                    else:
                        keep = max(fgroup, key=lambda n: n.last_modified)

                    for f in fgroup:
                        if f.full_path != keep.full_path:
                            f.action = NodeAction.DELETE
                            dup_count += 1

        return dup_count


# ============================================================
# 扁平化引擎
# ============================================================

class FlattenEngine:
    def __init__(self, nodes: List[FileNode], root_path: str,
                 errors: ErrorCollector):
        self.nodes = nodes
        self.root_path = root_path
        self.errors = errors

    def extract_files(self, target_dir: str, extensions: List[str] = None):
        files = [n for n in self.nodes
                 if not n.is_dir and n.action == NodeAction.NONE]

        if extensions:
            ext_set = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in extensions}
            files = [f for f in files if f.extension in ext_set]

        existing_names: Set[str] = set()
        os.makedirs(target_dir, exist_ok=True)

        for f in files:
            target_name = f.file_name
            if target_name in existing_names:
                base, ext = os.path.splitext(target_name)
                counter = 1
                while target_name in existing_names:
                    target_name = f"{base}_{counter}{ext}"
                    counter += 1

            name_no_ext = os.path.splitext(target_name)[0].upper()
            if name_no_ext in WINDOWS_RESERVED:
                target_name = f"_{target_name}"

            target_path = os.path.join(target_dir, target_name)
            if len(target_path.encode("utf-8")) >= MAX_PATH_EXTENDED:
                safe_name = _truncate_name(target_name,
                    MAX_PATH_EXTENDED - len(target_dir.encode("utf-8")) - 2)
                target_path = os.path.join(target_dir, safe_name)

            f.target_path = target_path
            f.action = NodeAction.MOVE
            existing_names.add(target_name)


# ============================================================
# 结构重组引擎
# ============================================================

class RestructureEngine:
    def __init__(self, nodes: List[FileNode], root_path: str,
                 errors: ErrorCollector):
        self.nodes = nodes
        self.root_path = root_path
        self.errors = errors

    def unpack_redundant(self) -> int:
        unpacked = 0
        dirs = [n for n in self.nodes if n.is_dir and n.action == NodeAction.NONE]
        dir_map = {d.full_path.lower(): d for d in dirs}

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
# 执行引擎
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
        self.stats = {
            "success": 0, "vanished": 0, "changed": 0,
            "locked": 0, "skipped": 0, "conflict": 0,
            "failed": 0, "trashed": 0,
        }
        self._throttle = 0

    def set_scan_timestamp(self, ts: float):
        self._scan_timestamp = ts

    def execute(self, dry_run: bool = True) -> Dict:
        os.makedirs(self.log_dir, exist_ok=True)
        os.makedirs(self.trash_dir, exist_ok=True)

        to_execute = [n for n in self.nodes
                      if n.action in (NodeAction.MOVE, NodeAction.DELETE)]
        total = len(to_execute)
        if total == 0:
            print("✅ 无需执行任何操作")
            return self.stats

        moved = [n for n in to_execute if n.action == NodeAction.MOVE]
        deleted = [n for n in to_execute if n.action == NodeAction.DELETE]

        mode_label = "🔍 预览模式" if dry_run else "⚡ 执行模式"
        print(f"\n{mode_label}: 共 {total} 项操作 (移动 {len(moved)} / 删除 {len(deleted)})\n")

        for i, node in enumerate(moved, 1):
            self._process_move(node, dry_run)
            self._maybe_throttle()
            if not dry_run and i % 200 == 0:
                print(f"  移动进度: {i}/{len(moved)}")

        for i, node in enumerate(deleted, 1):
            self._process_delete(node, dry_run)
            self._maybe_throttle()
            if not dry_run and i % 200 == 0:
                print(f"  删除进度: {i}/{len(deleted)}")

        if not dry_run:
            self._cleanup_empty_dirs()
            self._write_log()

        return self.stats

    def _process_move(self, node: FileNode, dry_run: bool):
        if not _safe_exists(node.full_path):
            node.status = NodeStatus.VANISHED
            self.stats["vanished"] += 1
            return

        if self._scan_timestamp:
            try:
                st = _safe_stat(node.full_path)
                if st and st.st_mtime > self._scan_timestamp:
                    node.status = NodeStatus.CHANGED
                    self.stats["changed"] += 1
                    return
            except OSError:
                pass

        target = node.target_path
        if not target:
            return

        if dry_run:
            node.status = NodeStatus.SUCCESS
            self.stats["success"] += 1
            return

        target_dir = os.path.dirname(target)
        os.makedirs(target_dir, exist_ok=True)

        try:
            if _safe_exists(target):
                target = self._resolve_conflict(target)
                node.target_path = target

            src = _win_path(node.full_path) if _safe_exists(node.full_path) else node.full_path
            dst = _win_path(target)
            shutil.move(src, dst)
            self._log_op("MOVE", node.full_path, target)
            node.status = NodeStatus.SUCCESS
            self.stats["success"] += 1
        except OSError as e:
            node.status = NodeStatus.LOCKED
            node.error_msg = str(e)
            self.stats["locked"] += 1
            self.errors.add(node.full_path, "MoveFailed", str(e))

    def _process_delete(self, node: FileNode, dry_run: bool):
        if not _safe_exists(node.full_path):
            node.status = NodeStatus.VANISHED
            self.stats["vanished"] += 1
            return

        if dry_run:
            node.status = NodeStatus.SUCCESS
            self.stats["success"] += 1
            return

        try:
            trash_target = self._get_trash_path(node.full_path)
            os.makedirs(os.path.dirname(trash_target), exist_ok=True)
            src = _win_path(node.full_path) if _safe_exists(node.full_path) else node.full_path
            dst = _win_path(trash_target)
            shutil.move(src, dst)
            self._log_op("DELETE", node.full_path, trash_target, status="TRASHED")
            node.status = NodeStatus.TRASHED
            self.stats["success"] += 1
            self.stats["trashed"] += 1
        except OSError as e:
            node.status = NodeStatus.LOCKED
            node.error_msg = str(e)
            self.stats["locked"] += 1
            self.errors.add(node.full_path, "DeleteFailed", str(e))

    def _cleanup_empty_dirs(self):
        dirs = sorted(
            [n for n in self.nodes if n.is_dir],
            key=lambda n: n.depth, reverse=True
        )
        for d in dirs:
            if d.action == NodeAction.DELETE:
                continue
            try:
                wp = _win_path(d.full_path)
                check = wp if os.path.exists(wp) else d.full_path
                if os.path.isdir(check) and not os.listdir(check):
                    os.rmdir(check)
                    self._log_op("RMDIR", d.full_path, "")
            except OSError:
                pass

    def _resolve_conflict(self, target_path: str) -> str:
        dir_name = os.path.dirname(target_path)
        base, ext = os.path.splitext(os.path.basename(target_path))
        counter = 1
        while True:
            new_name = f"{base}_{counter}{ext}"
            new_path = os.path.join(dir_name, new_name)
            if not _safe_exists(new_path):
                return new_path
            counter += 1

    def _get_trash_path(self, original_path: str) -> str:
        try:
            rel = os.path.relpath(original_path, self.root_path)
        except ValueError:
            rel = os.path.basename(original_path)
        return os.path.join(self.trash_dir, rel)

    def _log_op(self, action: str, source: str, target: str, status: str = "SUCCESS"):
        self._seq += 1
        self.op_logs.append(OperationLog(
            seq=self._seq, action=action, source=source, target=target,
            timestamp=datetime.now(timezone.utc).isoformat(), status=status
        ))

    def _maybe_throttle(self):
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

    def generate_rollback_script(self) -> str:
        if not self.op_logs:
            return "@echo off\nrem 无操作记录，无需回滚\n"
        lines = [
            "@echo off",
            "chcp 65001 >nul",
            "rem ============================================",
            "rem 回滚脚本 - 自动生成",
            f"rem 生成时间: {datetime.now().isoformat()}",
            f"rem 共 {len(self.op_logs)} 条操作记录",
            "rem ============================================",
            "",
        ]
        for entry in reversed(self.op_logs):
            src = entry.source.replace('"', '""')
            dst = entry.target.replace('"', '""')
            if entry.action == "MOVE":
                lines.append(f'move /Y "{dst}" "{src}"')
            elif entry.action == "DELETE":
                lines.append(f'move /Y "{dst}" "{src}"')
            elif entry.action == "RMDIR":
                lines.append(f'mkdir "{src}"')
        lines.append("")
        lines.append('echo ✅ 回滚完成')
        return "\r\n".join(lines)

    def generate_report(self) -> str:
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
    clean = "".join(c if c not in ILLEGAL_CHARS else "_" for c in name)
    clean = clean.strip(" .")
    if not clean:
        clean = "_unnamed"
    name_no_ext = os.path.splitext(clean)[0].upper()
    if name_no_ext in WINDOWS_RESERVED:
        clean = f"_{clean}"
    return clean


def _truncate_name(name: str, max_bytes: int) -> str:
    encoded = name.encode("utf-8")
    if len(encoded) <= max_bytes:
        return name
    base, ext = os.path.splitext(name)
    ext_bytes = len(ext.encode("utf-8"))
    available = max_bytes - ext_bytes - 9
    if available < 1:
        available = 1
    truncated_base = base[:available]
    name_hash = hashlib.md5(name.encode("utf-8")).hexdigest()[:8]
    return f"{truncated_base}_{name_hash}{ext}"


def _fmt_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


# ============================================================
# 主引擎
# ============================================================

class FolderRestructureEngine:
    def __init__(self, target_path: str):
        self.target_path = os.path.abspath(target_path)
        self.log_dir = os.path.join(self.target_path, LOG_DIR)
        self.trash_dir = os.path.join(self.target_path, TRASH_DIR)
        self.errors = ErrorCollector()
        self.diagnostic: Optional[DiagnosticEngine] = None
        self.perm_engine: Optional[PermissionEngine] = None
        self.nodes: List[FileNode] = []
        self._scan_ts: float = 0

    def run_safety_check(self) -> Tuple[bool, str]:
        level, msg = SafetyChecker.check(self.target_path)
        icons = {SafetyLevel.FULL: "🟢", SafetyLevel.RESTRICTED: "🟡", SafetyLevel.BLOCKED: "🔴"}
        print(f"\n🔒 安全检查: {icons.get(level, '?')} Level {level.value} — {msg}")
        if level == SafetyLevel.BLOCKED:
            return False, msg
        elif level == SafetyLevel.RESTRICTED:
            print("   ⚠️  受限模式：仅允许预览和诊断操作")
        return True, msg

    def diagnose(self, detect_zone: bool = False, verbose: bool = False) -> Dict:
        print(f"\n🔍 扫描目录: {self.target_path}")
        self.diagnostic = DiagnosticEngine(self.target_path)
        self.nodes = self.diagnostic.scan(detect_zone=detect_zone, verbose=verbose)
        self._scan_ts = self.diagnostic._scan_timestamp
        stats = self.diagnostic.get_stats()
        self._print_stats(stats)
        return stats

    def analyze(self, cache_patterns: List[str] = None,
                dedup_strategy: str = "shallowest") -> Dict:
        result = {}

        elim = EliminationEngine(self.nodes, self.errors)
        elim.mark_cache_dirs(cache_patterns)
        result["cache_marked"] = sum(1 for n in self.nodes if n.action == NodeAction.DELETE)

        result["duplicates_marked"] = elim.deduplicate(dedup_strategy)

        restruct = RestructureEngine(self.nodes, self.target_path, self.errors)
        result["redundant_unpacked"] = restruct.unpack_redundant()

        return result

    def fix_permissions(self, actions: Set[PermissionAction],
                        dry_run: bool = True) -> Dict:
        """执行权限/属性修改"""
        self.perm_engine = PermissionEngine(self.nodes, self.errors)
        return self.perm_engine.apply_all(actions, dry_run=dry_run)

    def preview(self) -> List[Dict]:
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

    def execute(self, dry_run: bool = True) -> Dict:
        engine = ExecutionEngine(self.nodes, self.target_path,
                                 self.log_dir, self.trash_dir)
        engine.set_scan_timestamp(self._scan_ts)
        stats = engine.execute(dry_run=dry_run)

        if not dry_run:
            print(engine.generate_report())

            rollback = engine.generate_rollback_script()
            rollback_path = os.path.join(self.log_dir, "rollback.bat")
            with open(rollback_path, "w", encoding="utf-8-sig") as f:
                f.write(rollback)
            print(f"🔄 回滚脚本: {rollback_path}")

            if self.errors.has_errors():
                error_path = os.path.join(self.log_dir, "errors.json")
                with open(error_path, "w", encoding="utf-8") as f:
                    json.dump([asdict(e) for e in self.errors.errors],
                             f, indent=2, ensure_ascii=False)
                print(f"⚠️  错误报告: {error_path}")

        return stats

    def _print_stats(self, stats: Dict):
        print(f"\n{'=' * 55}")
        print(f"📊 目录诊断报告")
        print(f"{'=' * 55}")
        print(f"  路径: {stats['root']}")
        print(f"  总项数: {stats['total_items']} (文件 {stats['total_files']}, 目录 {stats['total_dirs']})")
        print(f"  总大小: {stats['total_size_mb']} MB")
        print(f"  最大深度: {stats['max_depth']}")
        print(f"  符号链接: {stats['symlinks']}  |  Junction: {stats['junctions']}  |  硬链接: {stats['hard_links']}")

        # 权限/属性概览
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


def _node_attrs_str(n: FileNode) -> str:
    parts = []
    if n.is_hidden:
        parts.append("H")
    if n.is_readonly:
        parts.append("R")
    if n.is_system:
        parts.append("S")
    if n.has_zone_identifier:
        parts.append("Z")
    return "".join(parts) if parts else "-"


# ============================================================
# CLI
# ============================================================

def _parse_actions(action_str: str) -> Set[PermissionAction]:
    """解析权限操作字符串"""
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


def main():
    parser = argparse.ArgumentParser(
        description="文件夹结构智能重组引擎 V5.0 (Windows 专用)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 仅诊断
  python folder_restructure.py D:\\Projects --diagnose

  # 诊断 + 检测网络文件标识
  python folder_restructure.py D:\\Projects --diagnose --detect-zone

  # 分析 + 预览
  python folder_restructure.py D:\\Projects --preview

  # 清除网络文件标识 (Zone.Identifier)
  python folder_restructure.py D:\\Projects --fix-attr zone --preview
  python folder_restructure.py D:\\Projects --fix-attr zone --execute

  # 去除所有只读 + 隐藏属性
  python folder_restructure.py D:\\Projects --fix-attr readonly,hidden --execute

  # 全部权限修复 (授予权限+去只读+去隐藏+清Zone)
  python folder_restructure.py D:\\Projects --fix-attr all --execute

  # 组合操作: 清理 + 修复权限
  python folder_restructure.py D:\\Projects --preview --fix-attr zone,readonly

  # 导出诊断报告
  python folder_restructure.py D:\\Projects --export report.json
        """
    )

    parser.add_argument("target", help="目标目录路径")
    parser.add_argument("--diagnose", action="store_true", help="仅诊断，不修改")
    parser.add_argument("--preview", action="store_true", help="分析 + 预览操作清单")
    parser.add_argument("--execute", action="store_true", help="执行操作")
    parser.add_argument("--yes", "-y", action="store_true", help="跳过确认")
    parser.add_argument("--export", metavar="FILE", help="导出报告 (CSV/JSON)")
    parser.add_argument("--extra-patterns", nargs="+", help="额外的缓存匹配模式")
    parser.add_argument("--dedup-strategy", choices=["shallowest", "newest"],
                       default="shallowest", help="去重保留策略")
    parser.add_argument("--detect-zone", action="store_true",
                       help="扫描时检测 Zone.Identifier (较慢)")
    parser.add_argument("--fix-attr", metavar="ACTIONS",
                       help="修复权限/属性: perm,readonly,hidden,zone,all (逗号分隔)")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")
    parser.add_argument("--force-lock", action="store_true", help="强制清除锁文件")

    args = parser.parse_args()

    if not os.path.isdir(args.target):
        print(f"❌ 不是有效目录: {args.target}")
        sys.exit(1)

    engine = FolderRestructureEngine(args.target)

    # 安全检查
    safe, msg = engine.run_safety_check()
    if not safe:
        print(f"❌ 操作被禁止: {msg}")
        sys.exit(1)

    # 锁
    lock = LockManager(args.target)
    if not args.diagnose:
        if not lock.acquire(force=args.force_lock):
            print("❌ 无法获取目录锁，使用 --force-lock 强制清除")
            sys.exit(1)
        import atexit
        atexit.register(lock.release)

    try:
        # 诊断
        stats = engine.diagnose(detect_zone=args.detect_zone, verbose=args.verbose)

        # 导出
        if args.export:
            ext = args.export.rsplit(".", 1)[-1].lower()
            if ext == "csv":
                engine.diagnostic.export_csv(args.export)
            else:
                engine.diagnostic.export_json(args.export)
            print(f"📄 报告已导出: {args.export}")
            if args.diagnose:
                return

        if args.diagnose:
            return

        # 分析结构
        print("🔧 分析文件结构...")
        analysis = engine.analyze(
            cache_patterns=args.extra_patterns,
            dedup_strategy=args.dedup_strategy,
        )
        print(f"  缓存目录标记: {analysis['cache_marked']} 项")
        print(f"  重复文件标记: {analysis['duplicates_marked']} 项")
        print(f"  冗余结构解包: {analysis['redundant_unpacked']} 项")

        # 权限/属性修复
        perm_stats = {}
        if args.fix_attr:
            perm_actions = _parse_actions(args.fix_attr)
            if perm_actions:
                do_execute = args.execute and not args.preview
                perm_stats = engine.fix_permissions(perm_actions, dry_run=not do_execute)

        # 预览
        preview = engine.preview()
        if preview:
            print(f"\n📋 结构变更清单 ({len(preview)} 项):")
            print("-" * 65)
            for i, item in enumerate(preview[:50], 1):
                icon = {"DELETE": "🗑️", "MOVE": "📦", "RENAME": "✏️"}.get(item["action"], "❓")
                if item["action"] == "DELETE":
                    print(f"  {i:4d}. {icon} DELETE {item['source']} ({item['size']}) [{item['attrs']}]")
                else:
                    print(f"  {i:4d}. {icon} {item['source']} [{item['attrs']}]")
                    print(f"         → {item['target']}")
            if len(preview) > 50:
                print(f"  ... 还有 {len(preview) - 50} 项")
            print("-" * 65)
        else:
            print("\n✅ 结构方面无需变更")

        # 权限操作汇总
        if perm_stats:
            print(f"\n🔑 权限/属性操作汇总:")
            for key, label in [
                ("perm_granted", "授予权限"), ("readonly_removed", "去除只读"),
                ("hidden_removed", "去除隐藏"), ("zone_cleared", "清除 Zone 标识"),
                ("failed", "失败"), ("skipped", "跳过"),
            ]:
                if perm_stats.get(key, 0):
                    print(f"  {label}: {perm_stats[key]} 项")

        if args.preview:
            if not args.execute:
                print(f"\n💡 使用 --execute 参数执行实际操作")
            return

        # 执行
        if args.execute:
            total_ops = len(preview) + sum(v for k, v in perm_stats.items()
                                            if k not in ("failed", "skipped"))
            if not args.yes and total_ops > 0:
                confirm = input(f"\n⚠️  即将执行 {total_ops} 项操作。确认？(yes/no): ")
                if confirm.lower() not in ("yes", "y"):
                    print("❌ 已取消")
                    return

            engine.execute(dry_run=False)

    except KeyboardInterrupt:
        print("\n\n⚠️  用户中断")
    finally:
        lock.release()


if __name__ == "__main__":
    main()
