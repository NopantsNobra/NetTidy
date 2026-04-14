"""
安全检查模块
SafetyChecker + LockManager + ErrorCollector
"""

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Set, List, Optional

from .常量定义 import IS_WINDOWS, SafetyLevel
from .数据模型 import ErrorEntry


# ═══════════════════════════════════════════════════════════
# ErrorCollector - 错误收集器
# ═══════════════════════════════════════════════════════════

class ErrorCollector:
    """错误收集器，用于收集和管理操作过程中的错误"""
    
    def __init__(self):
        self.errors: List[ErrorEntry] = []
    
    def add(self, path: str, error_type: str, message: str) -> None:
        """
        添加错误条目
        
        Args:
            path: 相关路径
            error_type: 错误类型
            message: 错误消息
        """
        entry = ErrorEntry(path=path, error_type=error_type, message=message)
        self.errors.append(entry)
    
    def has_errors(self) -> bool:
        """是否有错误"""
        return len(self.errors) > 0
    
    def count(self) -> int:
        """错误数量"""
        return len(self.errors)
    
    def get_by_type(self, error_type: str) -> List[ErrorEntry]:
        """
        按类型获取错误
        
        Args:
            error_type: 错误类型
            
        Returns:
            错误列表
        """
        return [e for e in self.errors if e.error_type == error_type]
    
    def clear(self) -> None:
        """清空错误列表"""
        self.errors.clear()
    
    def to_list(self) -> List[dict]:
        """转换为字典列表"""
        return [e.to_dict() for e in self.errors]


# ═══════════════════════════════════════════════════════════
# LockManager - 锁管理器
# ═══════════════════════════════════════════════════════════

class LockManager:
    """
    目录锁管理器
    防止多个进程同时操作同一目录
    """
    
    LOCK_FILE = ".restructure.lock"
    
    def __init__(self, target_path: str):
        """
        初始化锁管理器
        
        Args:
            target_path: 目标目录路径
        """
        self.target_path = os.path.abspath(target_path)
        self.lock_path = os.path.join(self.target_path, self.LOCK_FILE)
        self.session_id = str(uuid.uuid4())[:8]
        self._acquired = False
    
    # 在安全检查.py 的 LockManager 类中修正 acquire 方法:

    def acquire(self, force: bool = False) -> bool:
        """获取锁"""
        if os.path.exists(self.lock_path):
            try:
                with open(self.lock_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                
                exp = data.get("expires_at", "")  # ✅ 使用正确的键名
                if exp and datetime.fromisoformat(exp) > datetime.now(timezone.utc):
                    if not force:
                        print(f"⚠️ 目录正在被处理:")
                        # ✅ 修正打印的键名
                        print(f"  hostname: {data.get('hostname', '?')}")
                        print(f"  pid: {data.get('pid', '?')}")
                        print(f"  started_at: {data.get('started_at', '?')}")
                        print(f"  expires_at: {data.get('expires_at', '?')}")
                        return False
                os.remove(self.lock_path)
            except (json.JSONDecodeError, OSError):
                if not force:
                    print("⚠️ 发现损坏的锁文件，使用 --force-lock 清除")
                    return False
                os.remove(self.lock_path)
        
        # 创建新锁
        data = {
            "pid": os.getpid(),
            "hostname": os.environ.get("COMPUTERNAME", "?"),
            "user": os.environ.get("USERNAME", "?"),
            "session_id": self.session_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": datetime.fromtimestamp(
                time.time() + 12 * 3600, 
                tz=timezone.utc
            ).isoformat(),
        }
        
        try:
            with open(self.lock_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            self._acquired = True
        except OSError as e:
            print(f"❌ 无法创建锁文件: {e}")
        
        return self._acquired

    def release(self) -> None:
        """释放锁"""
        if not self._acquired:
            return
        
        try:
            if os.path.exists(self.lock_path):
                os.remove(self.lock_path)
        except OSError:
            pass
        
        self._acquired = False
    
    def __enter__(self):
        """上下文管理器入口"""
        self.acquire()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口"""
        self.release()
        return False


# ═══════════════════════════════════════════════════════════
# SafetyChecker - 安全检查器
# ═══════════════════════════════════════════════════════════


class SafetyChecker:
    """
    安全检查器
    检查目标目录是否安全进行操作
    """
    
    # 受保护的系统目录
    PROTECTED_PATTERNS = [
        "windows", "program files", "program files (x86)", "programdata",
        "$recycle.bin", "system volume information",
        "boot", "efi", "recovery",
    ]
    
    # 锁文件常量
    LOCK_FILE = ".restructure.lock"
    
    def __init__(self, target_path: str):
        self.target_path = os.path.abspath(target_path)
    
    @classmethod
    def _system_dirs(cls) -> Set[str]:
        """获取系统目录集合"""
        dirs = set()
        
        # 环境变量中的系统目录
        for v in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
            val = os.environ.get(v, "")
            if val:
                dirs.add(val.lower())
        
        # 扫描各驱动器的系统目录
        for dl in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            root = f"{dl}:\\"
            if os.path.exists(root):
                for sub in ("$Recycle.Bin", "System Volume Information",
                            "Windows", "Program Files", "Program Files (x86)", "ProgramData"):
                    dirs.add(os.path.join(root, sub).lower())
        
        return dirs
    
    @classmethod
    def _user_dirs(cls) -> Set[str]:
        """获取用户目录集合"""
        dirs = set()
        
        up = os.environ.get("USERPROFILE", "")
        if up:
            dirs.add(up.lower())
            for sub in ("Desktop", "Documents", "Downloads", "AppData"):
                dirs.add(os.path.join(up, sub).lower())
        
        return dirs
    
    @classmethod
    def check(cls, target_path: str) -> Tuple[SafetyLevel, str]:
        """执行安全检查"""
        path = os.path.abspath(target_path).lower()
        
        # 检查是否为系统目录
        system_dirs = cls._system_dirs()
        for sys_dir in system_dirs:
            if path == sys_dir or path.startswith(sys_dir.rstrip("\\") + "\\"):
                return SafetyLevel.BLOCKED, "系统关键目录，禁止操作"
        
        # 检查是否为用户主目录
        user_dirs = cls._user_dirs()
        for user_dir in user_dirs:
            if path == user_dir:
                return SafetyLevel.RESTRICTED, "用户关键目录，建议谨慎操作"
        
        # 检查驱动器根目录
        if os.path.dirname(path) == "" or (len(path) == 3 and path[1:] == ":\\"):
            return SafetyLevel.RESTRICTED, "驱动器根目录，建议指定子目录"
        
        return SafetyLevel.FULL, "安全"
    
    @classmethod
    def is_protected_path(cls, path: str) -> bool:
        """检查是否为受保护路径"""
        path_lower = os.path.abspath(path).lower()
        system_dirs = cls._system_dirs()
        for sys_dir in system_dirs:
            if path_lower == sys_dir or path_lower.startswith(sys_dir.rstrip("\\") + "\\"):
                return True
        return False