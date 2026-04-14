"""
Win32底层IO模块
Win32IO 底层封装 + RecycleBin 真回收站
仅 Windows 平台有效
"""

import ctypes
import os
import stat
from ctypes import wintypes
from typing import Optional, Tuple

from .常量定义 import (
    IS_WINDOWS, FILE_ATTRIBUTE_HIDDEN, FILE_ATTRIBUTE_READONLY,
    FILE_ATTRIBUTE_SYSTEM, FILE_ATTRIBUTE_REPARSE,
    IO_REPARSE_TAG_SYMLINK, IO_REPARSE_TAG_MOUNT_POINT,
    MOVEFILE_REPLACE_EXISTING, MOVEFILE_COPY_ALLOWED, MOVEFILE_WRITE_THROUGH,
)


# ═══════════════════════════════════════════════════════════
# Win32 API 绑定
# ═══════════════════════════════════════════════════════════

if IS_WINDOWS:
    _kernel32 = ctypes.windll.kernel32
    _kernel32.SetLastError(0)
    
    # 设置函数签名
    _kernel32.GetFileAttributesW.argtypes = [wintypes.LPCWSTR]
    _kernel32.GetFileAttributesW.restype = wintypes.DWORD
    
    _kernel32.SetFileAttributesW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    _kernel32.SetFileAttributesW.restype = wintypes.BOOL
    
    _kernel32.MoveFileExW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    _kernel32.MoveFileExW.restype = wintypes.BOOL
    
    _kernel32.DeleteFileW.argtypes = [wintypes.LPCWSTR]
    _kernel32.DeleteFileW.restype = wintypes.BOOL
    
    _kernel32.RemoveDirectoryW.argtypes = [wintypes.LPCWSTR]
    _kernel32.RemoveDirectoryW.restype = wintypes.BOOL
    
    _kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE
    ]
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    
    _kernel32.DeviceIoControl.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
        wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID
    ]
    _kernel32.DeviceIoControl.restype = wintypes.BOOL


def _win_path(path: str) -> str:
    """转换为 \\?\ 长路径格式"""
    if not IS_WINDOWS:
        return path
    path = os.path.abspath(path)
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


# ═══════════════════════════════════════════════════════════
# Win32IO 类
# ═══════════════════════════════════════════════════════════

class Win32IO:
    """Windows 底层 IO 操作封装"""
    
    INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
    
    @classmethod
    def exists(cls, path: str) -> bool:
        """
        检查文件是否存在
        
        Args:
            path: 文件路径
            
        Returns:
            是否存在
        """
        if not IS_WINDOWS:
            return os.path.exists(path)
        
        attrs = cls.get_file_attributes(path)
        return attrs is not None and attrs != cls.INVALID_FILE_ATTRIBUTES
    
    @classmethod
    def get_file_attributes(cls, path: str) -> Optional[int]:
        """
        获取文件属性
        
        Args:
            path: 文件路径
            
        Returns:
            文件属性值或 None
        """
        if not IS_WINDOWS:
            return None
        
        for p in (_win_path(path), path):
            attrs = _kernel32.GetFileAttributesW(p)
            if attrs != cls.INVALID_FILE_ATTRIBUTES:
                return attrs
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
        if not IS_WINDOWS:
            return False
        
        for p in (_win_path(path), path):
            if _kernel32.SetFileAttributesW(p, attrs):
                return True
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
        if new_attrs != attrs:
            return cls.set_file_attributes(path, new_attrs)
        return True
    
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
        if new_attrs != attrs:
            return cls.set_file_attributes(path, new_attrs)
        return True
    
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
        if new_attrs != attrs:
            return cls.set_file_attributes(path, new_attrs)
        return True
    
    @classmethod
    def safe_stat(cls, path: str):
        """
        安全获取文件状态
        处理符号链接和长路径
        
        Args:
            path: 文件路径
            
        Returns:
            stat 结果或 None
        """
        if not IS_WINDOWS:
            try:
                return os.lstat(path)
            except OSError:
                return None
        
        for p in (_win_path(path), path):
            try:
                return os.lstat(p)
            except OSError:
                continue
        return None
    
    @classmethod
    def move_file(cls, src: str, dst: str) -> bool:
        """
        移动文件
        
        Args:
            src: 源路径
            dst: 目标路径
            
        Returns:
            是否成功
        """
        if not IS_WINDOWS:
            try:
                os.rename(src, dst)
                return True
            except OSError:
                return False
        
        for s in (_win_path(src), src):
            for d in (_win_path(dst), dst):
                if _kernel32.MoveFileExW(
                    s, d,
                    MOVEFILE_COPY_ALLOWED | MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH
                ):
                    return True
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
        if not IS_WINDOWS:
            try:
                os.remove(path)
                return True
            except OSError:
                return False
        
        for p in (_win_path(path), path):
            if _kernel32.DeleteFileW(p):
                return True
        return False
    
    @classmethod
    def remove_directory(cls, path: str) -> bool:
        """
        删除空目录
        
        Args:
            path: 目录路径
            
        Returns:
            是否成功
        """
        if not IS_WINDOWS:
            try:
                os.rmdir(path)
                return True
            except OSError:
                return False
        
        for p in (_win_path(path), path):
            if _kernel32.RemoveDirectoryW(p):
                return True
        return False
    
    @classmethod
    def get_reparse_tag(cls, path: str) -> Optional[int]:
        """
        获取重解析点标签
        
        Args:
            path: 文件路径
            
        Returns:
            重解析标签值或 None
        """
        if not IS_WINDOWS:
            return None
        
        try:
            import struct
            wp = _win_path(path)
            handle = _kernel32.CreateFileW(
                wp, 0x80000000, 0x00000001 | 0x00000002 | 0x00000004,
                None, 3, 0x00200000 | 0x02000000, None
            )
            if handle == -1 or handle is None:
                return None
            
            try:
                buf = ctypes.create_string_buffer(1024)
                br = wintypes.DWORD()
                if _kernel32.DeviceIoControl(
                    handle, 0x000900A8, None, 0, buf, 1024,
                    ctypes.byref(br), None
                ):
                    return struct.unpack_from("<I", buf.raw, 0)[0]
            finally:
                _kernel32.CloseHandle(handle)
        except Exception:
            pass
        return None

    @classmethod
    def create_hard_link(cls, new_path: str, existing_path: str) -> bool:
        """
        创建硬链接
        
        Args:
            new_path: 新文件路径（硬链接）
            existing_path: 已存在的文件路径
            
        Returns:
            是否成功
        """
        if not IS_WINDOWS:
            try:
                os.link(existing_path, new_path)
                return True
            except OSError:
                return False
        
        # Windows 使用 CreateHardLinkW
        try:
            kernel32 = ctypes.windll.kernel32
            kernel32.CreateHardLinkW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPVOID]
            kernel32.CreateHardLinkW.restype = wintypes.BOOL
            
            result = kernel32.CreateHardLinkW(new_path, existing_path, None)
            return bool(result)
        except Exception:
            return False

# ═══════════════════════════════════════════════════════════
# Windows 系统回收站集成 (V8.0+)
# ═══════════════════════════════════════════════════════════

class _SHFILEOPSTRUCTW(ctypes.Structure):
    """SHFileOperationW 所需结构体"""
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


# 回收站错误码映射
RECYCLE_BIN_ERRORS = {
    0: "成功",
    2: "文件不存在",
    3: "路径不存在",
    5: "访问被拒绝",
    7: "文件名语法错误",
    87: "参数错误",
    1155: "无法创建回收站目录",
    1223: "操作被用户取消",
    1247: "回收站已禁用",
    1314: "权限不足",
    1317: "用户不存在",
}


class RecycleBin:
    """Windows 系统回收站操作封装"""
    
    # SHFileOperationW 常量
    FO_DELETE = 0x3
    FOF_ALLOWUNDO = 0x40
    FOF_NOCONFIRMATION = 0x10
    FOF_SILENT = 0x4
    FOF_NOERRORUI = 0x400
    
    @classmethod
    def check_support(cls) -> Tuple[bool, str]:
        """
        检查系统回收站是否可用
        
        Returns:
            (是否支持, 原因说明)
        """
        if not IS_WINDOWS:
            return False, "仅支持 Windows 系统"
        
        try:
            # 检查 shell32.dll 是否可用
            shell32 = ctypes.windll.shell32
            if not hasattr(shell32, 'SHFileOperationW'):
                return False, "SHFileOperationW API 不可用"
            return True, "支持"
        except Exception as e:
            return False, str(e)
    
    @classmethod
    def move_to_recycle_bin(cls, path: str, silent: bool = True) -> Tuple[bool, str]:
        """
        将文件移动到系统回收站
        
        Args:
            path: 文件路径
            silent: 是否静默模式（不显示确认对话框）
            
        Returns:
            (是否成功, 错误信息)
        """
        if not IS_WINDOWS:
            return False, "仅支持 Windows 系统"
        
        try:
            shell32 = ctypes.windll.shell32
            
            # 准备路径（需要双空结尾）
            # 对于长路径，不使用 \\?\ 前缀，因为 SHFileOperationW 不支持
            norm_path = os.path.abspath(path)
            if norm_path.startswith("\\\\?\\"):
                norm_path = norm_path[4:]
            elif norm_path.startswith("\\\\?\\UNC\\"):
                norm_path = "\\" + norm_path[7:]
            
            # 双空结尾
            path_buf = (norm_path + "\0\0").encode('utf-16-le')
            
            # 构建结构体
            op = _SHFILEOPSTRUCTW()
            op.hwnd = None
            op.wFunc = cls.FO_DELETE
            op.pFrom = path_buf
            op.pTo = None
            op.fFlags = cls.FOF_ALLOWUNDO
            if silent:
                op.fFlags |= cls.FOF_NOCONFIRMATION | cls.FOF_SILENT | cls.FOF_NOERRORUI
            op.fAnyOperationsAborted = False
            op.hNameMappings = None
            op.lpszProgressTitle = None
            
            # 执行操作
            result = shell32.SHFileOperationW(ctypes.byref(op))
            
            if result == 0:
                return True, "成功"
            
            error_msg = RECYCLE_BIN_ERRORS.get(result, f"未知错误 (0x{result:X})")
            return False, error_msg
            
        except Exception as e:
            return False, str(e)
    
    @classmethod
    def is_recycle_bin_enabled(cls) -> bool:
        """
        检查回收站是否启用
        
        Returns:
            是否启用
        """
        if not IS_WINDOWS:
            return False
        
        try:
            import winreg
            # 检查回收站配置
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer",
                0, winreg.KEY_READ
            )
            try:
                value, _ = winreg.QueryValueEx(key, "NoRecycleFiles")
                return value == 0
            except FileNotFoundError:
                return True  # 默认启用
            finally:
                winreg.CloseKey(key)
        except Exception:
            return True  # 假设启用
