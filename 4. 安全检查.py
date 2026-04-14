"""
安全检查模块
=============
职责：系统安全检查、目录锁管理、错误收集

对外接口：
    - SafetyChecker     安全检查器
    - LockManager       目录锁管理器
    - ErrorCollector    错误收集器

依赖关系：
    - 常量定义 (SafetyLevel, ERROR_* 常量)
    - 数据模型 (ErrorEntry)

使用示例：
    from 安全检查 import SafetyChecker, LockManager
    level, msg = SafetyChecker.check("D:\\Projects")
    with LockManager(target_dir).acquire():
        pass

版本历史：
    9.0: 类结构修正、方法归位、类型注解完善
    8.0: 初始模块化版本
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import List, Dict, Set, Optional, Tuple, Any

from .常量定义 import (
    IS_WINDOWS,
    SafetyLevel,
    LOCK_FILE,
)
from .数据模型 import ErrorEntry


# ═══════════════════════════════════════════════════════════════════
# ErrorCollector - 错误收集器
# ═══════════════════════════════════════════════════════════════════

class ErrorCollector:
    """
    错误收集器
    
    用于收集和管理操作过程中的错误。
    提供线程安全的错误添加和查询功能。
    
    使用示例:
        >>> collector = ErrorCollector()
        >>> collector.add("/test/file.txt", "ReadError", "无法读取文件")
        >>> collector.has_errors()
        True
        >>> collector.count()
        1
    """
    
    _SEQ_COUNTER: int = 0
    _COUNTER_LOCK = None  # 简化版本，实际应使用 threading.Lock
    
    def __init__(self):
        """初始化错误收集器"""
        self.errors: List[ErrorEntry] = []
        self._lock = __import__('threading').Lock()
    
    def add(
        self,
        path: str,
        error_type: str,
        message: str,
    ) -> ErrorEntry:
        """
        添加错误条目
        
        Args:
            path: 相关路径
            error_type: 错误类型标识
            message: 错误消息
            
        Returns:
            创建的错误条目
        """
        with self._lock:
            ErrorCollector._SEQ_COUNTER += 1
            seq = ErrorCollector._SEQ_COUNTER
        
        entry = ErrorEntry(
            seq=seq,
            path=path,
            error_type=error_type,
            message=message,
        )
        
        with self._lock:
            self.errors.append(entry)
        
        return entry
    
    def add_from_exception(
        self,
        path: str,
        error_type: str,
        exception: Exception,
    ) -> ErrorEntry:
        """
        从异常添加错误
        
        Args:
            path: 相关路径
            error_type: 错误类型
            exception: 异常对象
            
        Returns:
            创建的错误条目
        """
        message = f"{type(exception).__name__}: {str(exception)}"
        return self.add(path, error_type, message)
    
    def has_errors(self) -> bool:
        """是否有错误"""
        return len(self.errors) > 0
    
    def count(self) -> int:
        """错误数量"""
        return len(self.errors)
    
    def clear(self) -> None:
        """清空错误列表"""
        with self._lock:
            self.errors.clear()
    
    def get_all(self) -> List[ErrorEntry]:
        """获取所有错误"""
        with self._lock:
            return list(self.errors)
    
    def get_by_type(self, error_type: str) -> List[ErrorEntry]:
        """
        按类型获取错误
        
        Args:
            error_type: 错误类型
            
        Returns:
            匹配的错误列表
        """
        with self._lock:
            return [e for e in self.errors if e.error_type == error_type]
    
    def get_by_paths(self, paths: Set[str]) -> List[ErrorEntry]:
        """
        获取与指定路径相关的错误
        
        Args:
            paths: 路径集合
            
        Returns:
            匹配的错误列表
        """
        with self._lock:
            return [e for e in self.errors if e.path in paths]
    
    def to_dict(self) -> List[Dict[str, Any]]:
        """
        转换为字典列表
        
        Returns:
            字典列表
        """
        with self._lock:
            return [e.to_dict() for e in self.errors]
    
    def summary(self) -> Dict[str, int]:
        """
        获取错误统计摘要
        
        Returns:
            按类型统计的错误数量
        """
        with self._lock:
            summary: Dict[str, int] = {}
            for e in self.errors:
                summary[e.error_type] = summary.get(e.error_type, 0) + 1
            return summary
    
    def __len__(self) -> int:
        return self.count()
    
    def __bool__(self) -> bool:
        return self.has_errors()


# ═══════════════════════════════════════════════════════════════════
# LockManager - 锁管理器
# ═══════════════════════════════════════════════════════════════════

class LockManager:
    """
    目录锁管理器
    
    防止多个进程同时操作同一目录。
    使用 JSON 格式的锁文件，支持过期检测。
    
    使用示例:
        >>> lock = LockManager("/target/directory")
        >>> if lock.acquire():
        ...     try:
        ...         # 执行操作
        ...     finally:
        ...         lock.release()
        ... else:
        ...     print("目录被锁定")
    """
    
    def __init__(
        self,
        target_path: str,
        lock_filename: str = LOCK_FILE,
        expiry_hours: float = 12.0,
    ):
        """
        初始化锁管理器
        
        Args:
            target_path: 目标目录路径
            lock_filename: 锁文件名
            expiry_hours: 锁过期时间（小时）
        """
        self.target_path = os.path.abspath(target_path)
        self.lock_path = os.path.join(self.target_path, lock_filename)
        self.expiry_seconds = expiry_hours * 3600
        self.session_id = uuid.uuid4().hex[:8]
        self._acquired = False
    
    def acquire(self, force: bool = False) -> bool:
        """
        获取锁
        
        Args:
            force: 是否强制获取（清除现有锁）
            
        Returns:
            是否成功获取锁
        """
        # 检查现有锁
        if os.path.exists(self.lock_path):
            try:
                with open(self.lock_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                
                # 检查过期时间
                expires_at = data.get("expires_at", "")
                if expires_at:
                    expiry_time = datetime.fromisoformat(expires_at)
                    if datetime.now(timezone.utc) < expiry_time:
                        if not force:
                            self._print_lock_info(data)
                            return False
                
                # 锁已过期或强制获取，删除旧锁
                os.remove(self.lock_path)
            
            except (json.JSONDecodeError, OSError, ValueError) as e:
                if not force:
                    print("⚠️ 发现损坏的锁文件，使用 --force-lock 清除")
                    return False
                try:
                    os.remove(self.lock_path)
                except OSError:
                    pass
        
        # 创建新锁
        lock_data = {
            "pid": os.getpid(),
            "hostname": os.environ.get("COMPUTERNAME", os.environ.get("HOSTNAME", "unknown")),
            "user": os.environ.get("USERNAME", os.environ.get("USER", "unknown")),
            "session_id": self.session_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": datetime.fromtimestamp(
                time.time() + self.expiry_seconds,
                tz=timezone.utc
            ).isoformat(),
            "command": " ".join(sys.argv),
        }
        
        try:
            with open(self.lock_path, "w", encoding="utf-8") as f:
                json.dump(lock_data, f, indent=2)
            self._acquired = True
            return True
        
        except OSError as e:
            print(f"❌ 无法创建锁文件: {e}")
            return False
    
    def release(self) -> None:
        """
        释放锁
        
        如果锁不是当前会话创建的，则不释放。
        """
        if not self._acquired:
            return
        
        try:
            # 检查锁是否仍为当前会话持有
            if os.path.exists(self.lock_path):
                try:
                    with open(self.lock_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    
                    # 只释放自己创建的锁
                    if data.get("session_id") == self.session_id:
                        os.remove(self.lock_path)
                except (json.JSONDecodeError, OSError):
                    pass
        
        finally:
            self._acquired = False
    
    def _print_lock_info(self, lock_data: Dict) -> None:
        """
        打印锁信息
        
        Args:
            lock_data: 锁数据字典
        """
        print("⚠️ 目录正在被处理:")
        print(f"  主机: {lock_data.get('hostname', '?')}")
        print(f"  进程: {lock_data.get('pid', '?')}")
        print(f"  用户: {lock_data.get('user', '?')}")
        print(f"  开始: {lock_data.get('started_at', '?')}")
        print(f"  过期: {lock_data.get('expires_at', '?')}")
    
    def __enter__(self) -> "LockManager":
        """上下文管理器入口"""
        self.acquire()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        """上下文管理器出口"""
        self.release()
        return False


# ═══════════════════════════════════════════════════════════════════
# SafetyChecker - 安全检查器
# ═══════════════════════════════════════════════════════════════════

class SafetyChecker:
    """
    安全检查器
    
    检查目标目录是否安全进行操作。
    包括系统目录保护、权限检查、空间检查等。
    
    使用示例:
        >>> level, msg = SafetyChecker.check("D:\\Projects")
        >>> if level == SafetyLevel.BLOCKED:
        ...     print(f"禁止操作: {msg}")
        ... elif level.can_execute():
        ...     print("安全，可以执行")
    """
    
    # Windows 系统关键目录名称（用于检测）
    _SYSTEM_DIR_NAMES: Set[str] = {
        "windows", "program files", "program files (x86)", "programdata",
        "$recycle.bin", "system volume information",
        "boot", "efi", "recovery", "config.msi",
    }
    
    # 用户目录名称
    _USER_DIR_NAMES: Set[str] = {
        "desktop", "documents", "downloads", "appdata",
        "application data", "local settings",
    }
    
    @classmethod
    def _get_system_dirs(cls) -> Set[str]:
        """
        获取系统目录集合
        
        Returns:
            系统目录路径集合（小写）
        """
        dirs: Set[str] = set()
        
        # 从环境变量获取
        for env_var in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
            val = os.environ.get(env_var, "")
            if val:
                dirs.add(val.lower())
                dirs.add(os.path.normpath(val).lower())
        
        # 扫描各驱动器根目录
        for drive in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            root = f"{drive}:\\"
            if os.path.exists(root):
                for subdir in cls._SYSTEM_DIR_NAMES:
                    # 添加系统目录
                    system_path = os.path.join(root, subdir)
                    if os.path.exists(system_path):
                        dirs.add(system_path.lower())
                    
                    # 添加用户目录
                    user_path = os.path.join(root, "Users")
                    if os.path.exists(user_path):
                        dirs.add(user_path.lower())
        
        return dirs
    
    @classmethod
    def _get_user_dirs(cls) -> Set[str]:
        """
        获取用户主目录及其子目录集合
        
        Returns:
            用户目录路径集合（小写）
        """
        dirs: Set[str] = set()
        
        userprofile = os.environ.get("USERPROFILE", "")
        if userprofile:
            dirs.add(userprofile.lower())
            
            # 添加关键子目录
            for subdir in cls._USER_DIR_NAMES:
                full_path = os.path.join(userprofile, subdir)
                if os.path.exists(full_path):
                    dirs.add(full_path.lower())
        
        return dirs
    
    @classmethod
    def check(cls, target_path: str) -> Tuple[SafetyLevel, str]:
        """
        执行安全检查
        
        Args:
            target_path: 目标路径
            
        Returns:
            (安全级别, 说明消息)
        """
        abs_path = os.path.abspath(target_path).lower()
        
        # 1. 检查是否为驱动器根目录
        drive, _ = os.path.splitdrive(abs_path)
        if abs_path == drive + "\\" or abs_path == drive + "/":
            return SafetyLevel.BLOCKED, "禁止操作驱动器根目录"
        
        # 2. 检查系统目录
        system_dirs = cls._get_system_dirs()
        for sys_dir in system_dirs:
            # 精确匹配或子目录
            if abs_path == sys_dir or abs_path.startswith(sys_dir.rstrip("\\") + "\\"):
                return SafetyLevel.BLOCKED, f"命中系统保护目录: {sys_dir}"
        
        # 3. 检查用户主目录（限制模式）
        user_dirs = cls._get_user_dirs()
        for user_dir in user_dirs:
            if abs_path == user_dir:
                return SafetyLevel.RESTRICTED, "用户主目录，建议指定子目录"
        
        # 4. 检查路径深度
        depth = abs_path.count(os.sep)
        if depth < 2:
            return SafetyLevel.RESTRICTED, "路径层级过浅，建议指定更深层目录"
        
        # 5. 检查网络路径
        if abs_path.startswith("\\\\") and not abs_path.startswith("\\\\?\\"):
            return SafetyLevel.RESTRICTED, "网络路径，部分功能可能受限"
        
        # 6. 检查写入权限
        if os.path.exists(abs_path):
            if not os.access(abs_path, os.W_OK):
                return SafetyLevel.RESTRICTED, "目标目录无写入权限"
        
        # 7. 检查磁盘空间
        try:
            import shutil
            usage = shutil.disk_usage(abs_path)
            if usage.free < 200 * 1024 * 1024:  # 200MB
                return SafetyLevel.RESTRICTED, "磁盘剩余空间不足 200MB"
        except (OSError, ValueError):
            pass
        
        # 8. 写入测试
        if os.path.isdir(abs_path):
            test_file = os.path.join(abs_path, f"._safety_test_{os.getpid()}")
            try:
                with open(test_file, "w") as f:
                    f.write("test")
                os.remove(test_file)
            except (OSError, PermissionError):
                return SafetyLevel.RESTRICTED, "目标目录所在文件系统为只读"
        
        return SafetyLevel.FULL, "安全"
    
    @classmethod
    def is_protected_path(cls, path: str) -> bool:
        """
        检查是否为受保护路径
        
        Args:
            path: 路径
            
        Returns:
            是否受保护
        """
        abs_path = os.path.abspath(path).lower()
        
        system_dirs = cls._get_system_dirs()
        for sys_dir in system_dirs:
            if abs_path == sys_dir or abs_path.startswith(sys_dir.rstrip("\\") + "\\"):
                return True
        
        return False
    
    @classmethod
    def get_safe_parent(cls, path: str) -> Optional[str]:
        """
        获取安全的父目录
        
        向上遍历直到找到安全的目录。
        
        Args:
            path: 原始路径
            
        Returns:
            安全的父目录路径，或 None
        """
        current = os.path.abspath(path)
        
        while current:
            level, _ = cls.check(current)
            if level == SafetyLevel.FULL:
                return current
            
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
        
        return None
    
    @classmethod
    def verify_path_traversal(cls, paths: List[str]) -> List[str]:
        """
        验证路径安全性
        
        过滤出存在路径穿越风险的路径。
        
        Args:
            paths: 路径列表
            
        Returns:
            不安全的路径列表
        """
        unsafe = []
        for path in paths:
            try:
                normalized = os.path.normpath(path)
                if ".." in normalized.split(os.sep):
                    unsafe.append(path)
            except (ValueError, OSError):
                unsafe.append(path)
        
        return unsafe


# ═══════════════════════════════════════════════════════════════════
# 导出
# ═══════════════════════════════════════════════════════════════════

__all__ = [
    "ErrorCollector",
    "LockManager",
    "SafetyChecker",
]
