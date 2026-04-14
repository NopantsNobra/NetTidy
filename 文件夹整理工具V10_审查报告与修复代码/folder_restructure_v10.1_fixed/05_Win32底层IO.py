"""
Win32底层IO模块
=============
职责：Windows API 封装，提供底层文件和目录操作

对外接口：
    - Win32IO           Windows 文件操作封装
    - RecycleBin        Windows 系统回收站集成
    - create_hard_link()  创建硬链接函数

依赖关系：
    - 常量定义 (所有 Windows API 常量)
    - 工具函数 (_win_path)

使用示例：
    from Win32底层IO import Win32IO, RecycleBin
    Win32IO.move_file("C:/old.txt", "C:/new.txt")
    RecycleBin.move_to_recycle_bin("C:/temp.txt")

注意事项：
    - 仅在 Windows 平台有效
    - 非 Windows 平台所有操作会返回失败或使用替代方案

版本历史：
    10.0: 最终审计版本，修正逻辑错误与文档一致性
    9.0: API 签名完善、错误处理增强
    8.0: 初始模块化版本
"""

from __future__ import annotations

import ctypes
import os
import shutil
import stat
import time
from ctypes import wintypes, POINTER, byref
from typing import Optional, Tuple, Callable, Dict

from .常量定义 import (
    IS_WINDOWS,
    # 文件属性
    FILE_ATTRIBUTE_HIDDEN,
    FILE_ATTRIBUTE_READONLY,
    FILE_ATTRIBUTE_SYSTEM,
    FILE_ATTRIBUTE_REPARSE,
    FILE_ATTRIBUTE_DIRECTORY,
    # 重解析点标签
    IO_REPARSE_TAG_SYMLINK,
    IO_REPARSE_TAG_MOUNT_POINT,
    # MoveFileEx 标志
    MOVEFILE_REPLACE_EXISTING,
    MOVEFILE_COPY_ALLOWED,
    MOVEFILE_WRITE_THROUGH,
    # SHFileOperation 标志
    FO_DELETE,
    FOF_ALLOWUNDO,
    FOF_NOCONFIRMATION,
    FOF_SILENT,
    FOF_NOERRORUI,
)
from .工具函数 import _win_path


# ═══════════════════════════════════════════════════════════════════
# Win32 API 初始化
# ═══════════════════════════════════════════════════════════════════

_WIN32_AVAILABLE: bool = False
"""Win32 API 是否实际可用"""

if IS_WINDOWS:
    try:
        _kernel32 = ctypes.windll.kernel32
        _shell32 = ctypes.windll.shell32
        
        # 设置错误模式
        _kernel32.SetErrorMode(0x0003)  # SEM_FAILCRITICALERRORS | SEM_NOALIGNMENTFAULTEXCEPT
        _WIN32_AVAILABLE = True
        
    except (AttributeError, OSError):
        _kernel32 = None
        _shell32 = None


# ═══════════════════════════════════════════════════════════════════
# Win32Handle - 文件句柄上下文管理器
# ═══════════════════════════════════════════════════════════════════

class Win32Handle:
    """
    Windows 文件句柄上下文管理器
    
    确保在任何情况下都能正确释放文件句柄资源。
    使用 with 语句管理句柄生命周期，防止资源泄漏。
    
    使用示例:
        >>> with Win32Handle(path, access, flags) as handle:
        ...     if handle.is_valid():
        ...         # 执行操作
        ...         pass
    """
    
    def __init__(self, path: str, access: int = 0x80000000, 
                 share: int = 0x00000007, flags: int = 0):
        """
        初始化句柄管理器
        
        Args:
            path: 文件路径
            access: 访问模式 (默认 GENERIC_READ)
            share: 共享模式 (默认 FILE_SHARE_ALL)
            flags: 打开标志
        """
        self._path = path
        self._access = access
        self._share = share
        self._flags = flags
        self._handle = None
    
    def __enter__(self) -> 'Win32Handle':
        """进入上下文，打开文件句柄"""
        if not _WIN32_AVAILABLE or _kernel32 is None:
            return self
        
        OPEN_EXISTING = 3
        
        try:
            wp = _win_path(self._path)
            self._handle = _kernel32.CreateFileW(
                wp,
                self._access,
                self._share,
                None,
                OPEN_EXISTING,
                self._flags,
                None
            )
        except Exception:
            self._handle = None
        
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出上下文，关闭文件句柄"""
        self.close()
        return False
    
    def is_valid(self) -> bool:
        """检查句柄是否有效"""
        return self._handle is not None and self._handle != -1
    
    @property
    def handle(self):
        """获取原始句柄"""
        return self._handle
    
    def close(self):
        """显式关闭句柄"""
        if self._handle is not None and self._handle != -1:
            try:
                if _kernel32 is not None:
                    _kernel32.CloseHandle(self._handle)
            except Exception:
                pass
            finally:
                self._handle = None


# ═══════════════════════════════════════════════════════════════════
# Win32IO - Windows 文件操作封装
# ═══════════════════════════════════════════════════════════════════

class Win32IO:
    """
    Windows 底层 IO 操作封装类
    
    提供对 Windows API 的安全封装，包括：
    - 文件属性获取/设置
    - 文件移动/删除
    - 目录创建/删除
    - 重解析点检测
    - 硬链接创建
    
    所有方法都尝试处理长路径（\\\\?\\ 前缀）。
    
    使用示例:
        >>> Win32IO.get_file_attributes("C:/test.txt")
        32
        >>> Win32IO.move_file("C:/old.txt", "C:/new.txt")
        True
    """
    
    INVALID_FILE_ATTRIBUTES: int = -1
    
    # ─────────────────────────────────────────────────────────────
    # 文件存在性检查
    # ─────────────────────────────────────────────────────────────
    
    @classmethod
    def exists(cls, path: str) -> bool:
        """
        检查文件是否存在
        
        Args:
            path: 文件路径
            
        Returns:
            是否存在
        """
        if not _WIN32_AVAILABLE:
            return os.path.exists(path)
        
        attrs = cls.get_file_attributes(path)
        return attrs is not None and attrs != cls.INVALID_FILE_ATTRIBUTES
    
    @classmethod
    def is_file(cls, path: str) -> bool:
        """检查是否为文件"""
        if not _WIN32_AVAILABLE:
            return os.path.isfile(path)
        
        attrs = cls.get_file_attributes(path)
        if attrs is None:
            return False
        return not (attrs & FILE_ATTRIBUTE_DIRECTORY)
    
    @classmethod
    def is_dir(cls, path: str) -> bool:
        """检查是否为目录"""
        if not _WIN32_AVAILABLE:
            return os.path.isdir(path)
        
        attrs = cls.get_file_attributes(path)
        if attrs is None:
            return False
        return bool(attrs & FILE_ATTRIBUTE_DIRECTORY)
    
    # ─────────────────────────────────────────────────────────────
    # 文件属性操作
    # ─────────────────────────────────────────────────────────────
    
    @classmethod
    def get_file_attributes(cls, path: str) -> Optional[int]:
        """
        获取文件属性
        
        Args:
            path: 文件路径
            
        Returns:
            文件属性值，失败返回 None
        """
        if not _WIN32_AVAILABLE:
            return None
        
        # 尝试长路径
        paths_to_try = [_win_path(path), path]
        
        for p in paths_to_try:
            try:
                attrs = _kernel32.GetFileAttributesW(p)
                if attrs != cls.INVALID_FILE_ATTRIBUTES:
                    return attrs
            except OSError:
                continue
        
        return None
    
    @classmethod
    def set_file_attributes(cls, path: str, attrs: int) -> bool:
        """
        设置文件属性
        
        Args:
            path: 文件路径
            attrs: 属性值
            
        Returns:
            是否成功
        """
        if not _WIN32_AVAILABLE:
            return False
        
        paths_to_try = [_win_path(path), path]
        
        for p in paths_to_try:
            try:
                if _kernel32.SetFileAttributesW(p, attrs):
                    return True
            except OSError:
                continue
        
        return False
    
    @classmethod
    def remove_readonly(cls, path: str) -> bool:
        """
        移除只读属性
        
        Args:
            path: 文件路径
            
        Returns:
            是否成功
        """
        attrs = cls.get_file_attributes(path)
        if attrs is None:
            return False
        
        new_attrs = attrs & ~FILE_ATTRIBUTE_READONLY
        return cls.set_file_attributes(path, new_attrs)
    
    @classmethod
    def remove_hidden(cls, path: str) -> bool:
        """
        移除隐藏属性
        
        Args:
            path: 文件路径
            
        Returns:
            是否成功
        """
        attrs = cls.get_file_attributes(path)
        if attrs is None:
            return False
        
        new_attrs = attrs & ~FILE_ATTRIBUTE_HIDDEN
        return cls.set_file_attributes(path, new_attrs)
    
    @classmethod
    def remove_system(cls, path: str) -> bool:
        """
        移除系统属性
        
        Args:
            path: 文件路径
            
        Returns:
            是否成功
        """
        attrs = cls.get_file_attributes(path)
        if attrs is None:
            return False
        
        new_attrs = attrs & ~FILE_ATTRIBUTE_SYSTEM
        return cls.set_file_attributes(path, new_attrs)
    
    @classmethod
    def make_normal(cls, path: str) -> bool:
        """
        设置为常规属性（移除特殊属性）
        
        Args:
            path: 文件路径
            
        Returns:
            是否成功
        """
        return cls.set_file_attributes(path, 0)
    
    # ─────────────────────────────────────────────────────────────
    # 文件状态获取
    # ─────────────────────────────────────────────────────────────
    
    @classmethod
    def safe_stat(cls, path: str):
        """
        安全获取文件状态
        
        自动尝试长路径，处理符号链接。
        
        Args:
            path: 文件路径
            
        Returns:
            stat_result 或 None
        """
        if not _WIN32_AVAILABLE:
            try:
                return os.lstat(path)
            except OSError:
                return None
        
        paths_to_try = [_win_path(path), path]
        
        for p in paths_to_try:
            try:
                if os.path.exists(p):
                    return os.lstat(p)
            except OSError:
                continue
        
        return None
    
    @classmethod
    def safe_listdir(cls, path: str) -> list:
        """
        安全列出目录内容
        
        Args:
            path: 目录路径
            
        Returns:
            文件/目录名列表
        """
        paths_to_try = [_win_path(path), path]
        
        for p in paths_to_try:
            try:
                return os.listdir(p)
            except OSError:
                continue
        
        return []
    
    # ─────────────────────────────────────────────────────────────
    # 文件移动/复制/删除
    # ─────────────────────────────────────────────────────────────
    
    @classmethod
    def move_file(cls, src: str, dst: str) -> bool:
        """
        移动文件
        
        使用 MoveFileEx，支持跨卷移动和原子替换。
        
        Args:
            src: 源路径
            dst: 目标路径
            
        Returns:
            是否成功
        """
        if not _WIN32_AVAILABLE:
            try:
                shutil.move(src, dst)
                return True
            except OSError:
                return False
        
        # 确保目标目录存在
        dst_dir = os.path.dirname(dst)
        if dst_dir:
            try:
                os.makedirs(dst_dir, exist_ok=True)
            except OSError:
                pass
        
        # 尝试 MoveFileEx
        flags = MOVEFILE_COPY_ALLOWED | MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH
        
        src_paths = [_win_path(src), src]
        dst_paths = [_win_path(dst), dst]
        
        for s in src_paths:
            for d in dst_paths:
                try:
                    if _kernel32.MoveFileExW(s, d, flags):
                        return True
                except OSError:
                    continue
        
        # 回退：复制+删除
        try:
            shutil.copy2(src, dst)
            os.remove(src)
            return True
        except OSError:
            return False
    
    @classmethod
    def copy_file(cls, src: str, dst: str, overwrite: bool = True) -> bool:
        """
        复制文件
        
        Args:
            src: 源路径
            dst: 目标路径
            overwrite: 是否覆盖
            
        Returns:
            是否成功
        """
        try:
            if overwrite:
                shutil.copy2(src, dst)
            else:
                if os.path.exists(dst):
                    return False
                shutil.copy2(src, dst)
            return True
        except OSError:
            return False
    
    @classmethod
    def delete_file(cls, path: str) -> bool:
        """
        删除文件
        
        Args:
            path: 文件路径
            
        Returns:
            是否成功
        """
        if not _WIN32_AVAILABLE:
            try:
                os.remove(path)
                return True
            except OSError:
                return False
        
        # 先尝试移除只读属性
        cls.remove_readonly(path)
        
        paths_to_try = [_win_path(path), path]
        
        for p in paths_to_try:
            try:
                if _kernel32.DeleteFileW(p):
                    return True
            except OSError:
                continue
        
        # 回退：使用 Python
        try:
            os.remove(path)
            return True
        except OSError:
            return False
    
    @classmethod
    def remove_directory(cls, path: str, recursive: bool = False) -> bool:
        """
        删除目录
        
        Args:
            path: 目录路径
            recursive: 是否递归删除
            
        Returns:
            是否成功
        """
        if not _WIN32_AVAILABLE:
            try:
                if recursive:
                    shutil.rmtree(path)
                else:
                    os.rmdir(path)
                return True
            except OSError:
                return False
        
        if recursive:
            return cls._remove_directory_recursive(path)
        
        paths_to_try = [_win_path(path), path]
        
        for p in paths_to_try:
            try:
                if _kernel32.RemoveDirectoryW(p):
                    return True
            except OSError:
                continue
        
        return False
    
    @classmethod
    def _remove_directory_recursive(cls, path: str) -> bool:
        """
        递归删除目录
        
        Args:
            path: 目录路径
            
        Returns:
            是否成功
        """
        try:
            shutil.rmtree(path)
            return True
        except OSError:
            return False
    
    # ─────────────────────────────────────────────────────────────
    # 硬链接操作
    # ─────────────────────────────────────────────────────────────
    
    @classmethod
    def create_hard_link(cls, new_path: str, existing_path: str) -> bool:
        """
        创建硬链接
        
        跨卷操作会失败并返回 False（不静默回退为复制）。
        
        Args:
            new_path: 新文件路径（硬链接）
            existing_path: 已存在文件的路径
            
        Returns:
            是否成功
        """
        if not _WIN32_AVAILABLE:
            try:
                os.link(existing_path, new_path)
                return True
            except OSError:
                return False
        
        # 检查是否跨卷
        src_drive = os.path.splitdrive(os.path.abspath(existing_path))[0].lower()
        dst_drive = os.path.splitdrive(os.path.abspath(new_path))[0].lower()
        if src_drive and dst_drive and src_drive != dst_drive:
            return False
        
        try:
            _kernel32.CreateHardLinkW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPVOID]
            _kernel32.CreateHardLinkW.restype = wintypes.BOOL
            
            result = _kernel32.CreateHardLinkW(new_path, existing_path, None)
            return bool(result)
        
        except (AttributeError, OSError):
            return False
    
    @classmethod
    def create_symlink(cls, link_path: str, target_path: str, is_dir: bool = False) -> bool:
        """
        创建符号链接（需要管理员权限）
        
        Args:
            link_path: 链接路径
            target_path: 目标路径
            is_dir: 目标是否为目录
            
        Returns:
            是否成功
        """
        if not _WIN32_AVAILABLE:
            try:
                os.symlink(target_path, link_path)
                return True
            except OSError:
                return False
        
        try:
            SYMBOLIC_LINK_FLAG_FILE = 0x0
            SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED_CREATE = 0x2
            flags = SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED_CREATE
            if not is_dir:
                flags |= SYMBOLIC_LINK_FLAG_FILE
            
            _kernel32.CreateSymbolicLinkW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
            _kernel32.CreateSymbolicLinkW.restype = wintypes.BOOL
            
            result = _kernel32.CreateSymbolicLinkW(link_path, target_path, flags)
            return bool(result)
        
        except (AttributeError, OSError):
            return False
    
    # ─────────────────────────────────────────────────────────────
    # 重解析点操作
    # ─────────────────────────────────────────────────────────────
    
    @classmethod
    def get_reparse_tag(cls, path: str) -> Optional[int]:
        """
        获取重解析点标签
        
        Args:
            path: 文件路径
            
        Returns:
            重解析点标签值，或 None
        """
        if not _WIN32_AVAILABLE:
            return None
        
        try:
            import struct
            
            FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
            FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
            FSCTL_GET_REPARSE_POINT = 0x000900A8
            
            flags = FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS
            
            # 使用上下文管理器确保句柄正确释放
            with Win32Handle(path, flags=flags) as handle_ctx:
                if not handle_ctx.is_valid():
                    return None
                
                handle = handle_ctx.handle
                buf = ctypes.create_string_buffer(1024)
                bytes_returned = wintypes.DWORD()
                
                result = _kernel32.DeviceIoControl(
                    handle,
                    FSCTL_GET_REPARSE_POINT,
                    None,
                    0,
                    buf,
                    1024,
                    byref(bytes_returned),
                    None
                )
                
                if result:
                    tag = struct.unpack_from("<I", buf.raw, 0)[0]
                    return tag
        
        except Exception:
            pass
        
        return None
    
    @classmethod
    def is_symlink(cls, path: str) -> bool:
        """检查是否为符号链接"""
        tag = cls.get_reparse_tag(path)
        return tag == IO_REPARSE_TAG_SYMLINK if tag else False
    
    @classmethod
    def is_junction(cls, path: str) -> bool:
        """检查是否为目录联接点"""
        tag = cls.get_reparse_tag(path)
        return tag == IO_REPARSE_TAG_MOUNT_POINT if tag else False


# ═══════════════════════════════════════════════════════════════════
# RecycleBin - 系统回收站
# ═══════════════════════════════════════════════════════════════════

class RecycleBin:
    """
    Windows 系统回收站封装
    
    提供将文件移动到系统回收站的功能。
    使用 SHFileOperation API 实现。
    
    使用示例:
        >>> ok, msg = RecycleBin.move_to_recycle_bin("C:/temp.txt")
        >>> if ok:
        ...     print("已移入回收站")
    """
    
    # 错误码映射
    ERROR_CODES: Dict[int, str] = {
        0: "成功",
        2: "文件不存在",
        3: "路径不存在",
        5: "访问被拒绝",
        7: "文件名语法错误",
        87: "参数错误",
        1155: "无法创建目录",
        1223: "操作已取消",
        1247: "回收站已禁用",
        1314: "权限不足",
        1317: "用户不存在",
    }
    
    # SHFileOperation 结构体
    class _SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", ctypes.c_uint),
            ("pFrom", ctypes.c_wchar_p),
            ("pTo", ctypes.c_wchar_p),
            ("fFlags", ctypes.c_ushort),
            ("fAnyOperationsAborted", ctypes.c_bool),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", ctypes.c_wchar_p),
        ]
    
    @classmethod
    def check_support(cls) -> Tuple[bool, str]:
        """
        检查回收站功能是否可用
        
        Returns:
            (是否支持, 说明消息)
        """
        if not _WIN32_AVAILABLE:
            return False, "仅支持 Windows 系统"
        
        if _shell32 is None:
            return False, "shell32.dll 不可用"
        
        if not hasattr(_shell32, "SHFileOperationW"):
            return False, "SHFileOperationW API 不可用"
        
        return True, "支持"
    
    @classmethod
    def is_enabled(cls) -> bool:
        """
        检查回收站是否启用
        
        Returns:
            是否启用
        """
        if not _WIN32_AVAILABLE:
            return False
        
        try:
            import winreg
            
            # 检查是否禁用回收站
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer"
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ)
            
            try:
                value, _ = winreg.QueryValueEx(key, "NoRecycleFiles")
                return value == 0
            except FileNotFoundError:
                return True
            finally:
                winreg.CloseKey(key)
        
        except OSError:
            return True
    
    @classmethod
    def move_to_recycle_bin(
        cls,
        path: str,
        silent: bool = True,
        allow_undo: bool = True,
    ) -> Tuple[bool, str]:
        """
        将文件移动到系统回收站
        
        Args:
            path: 文件路径
            silent: 是否静默模式
            allow_undo: 是否允许撤销
            
        Returns:
            (是否成功, 消息)
        """
        if not _WIN32_AVAILABLE:
            return False, "仅支持 Windows 系统"
        
        support, msg = cls.check_support()
        if not support:
            return False, msg
        
        try:
            # 规范化路径
            abs_path = os.path.abspath(path)
            
            # SHFileOperationW 不支持 \\?\ 前缀
            if abs_path.startswith("\\\\?\\"):
                abs_path = abs_path[4:]
            if abs_path.startswith("\\\\?\\UNC\\"):
                abs_path = "\\" + abs_path[8:]
            
            # 构建文件列表（双空结尾）
            file_list = abs_path + "\0\0"
            
            # 构建标志
            flags = FOF_ALLOWUNDO
            if silent:
                flags |= FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
            
            # 执行操作
            op = cls._SHFILEOPSTRUCTW()
            op.hwnd = None
            op.wFunc = FO_DELETE
            op.pFrom = file_list
            op.pTo = None
            op.fFlags = flags
            op.fAnyOperationsAborted = False
            op.hNameMappings = None
            op.lpszProgressTitle = None
            
            result = _shell32.SHFileOperationW(byref(op))
            
            if result == 0:
                if op.fAnyOperationsAborted:
                    return False, "操作被用户取消"
                return True, "成功"
            
            error_msg = cls.ERROR_CODES.get(result, f"未知错误 (0x{result:08X})")
            return False, error_msg
        
        except Exception as e:
            return False, str(e)
    
    @classmethod
    def empty_recycle_bin(
        cls,
        window_title: str = "清空回收站",
        confirm: bool = True,
    ) -> Tuple[bool, str]:
        """
        清空回收站
        
        Args:
            window_title: 窗口标题
            confirm: 是否显示确认对话框
            
        Returns:
            (是否成功, 消息)
        """
        if not _WIN32_AVAILABLE:
            return False, "仅支持 Windows 系统"
        
        try:
            SHERB_NOCONFIRMATION = 0x0001
            SHERB_NOSOUND = 0x0002
            SHERB_NOPROGRESSUI = 0x0004
            
            flags = SHERB_NOPROGRESSUI | SHERB_NOSOUND
            if not confirm:
                flags |= SHERB_NOCONFIRMATION
            
            _shell32.SHEmptyRecycleBinW(None, None, flags)
            return True, "成功"
        
        except Exception as e:
            return False, str(e)


# ═══════════════════════════════════════════════════════════════════
# 便捷函数
# ═══════════════════════════════════════════════════════════════════

def safe_remove(path: str) -> bool:
    """
    安全删除文件或目录
    
    自动处理只读属性和子目录。
    
    Args:
        path: 路径
        
    Returns:
        是否成功
    """
    if os.path.isdir(path):
        return Win32IO.remove_directory(path, recursive=True)
    else:
        return Win32IO.delete_file(path)


def atomic_move(src: str, dst: str, max_retries: int = 3) -> bool:
    """
    原子移动（带重试）
    
    Args:
        src: 源路径
        dst: 目标路径
        max_retries: 最大重试次数
        
    Returns:
        是否成功
    """
    for attempt in range(max_retries):
        if Win32IO.move_file(src, dst):
            return True
        time.sleep(0.5)
    return False


# ═══════════════════════════════════════════════════════════════════
# 导出
# ═══════════════════════════════════════════════════════════════════

__all__ = [
    "Win32IO",
    "Win32Handle",
    "RecycleBin",
    "safe_remove",
    "atomic_move",
]
