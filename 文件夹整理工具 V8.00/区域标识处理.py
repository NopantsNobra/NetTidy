"""
区域标识处理模块
ZoneIdentifierHandler - 处理 Windows Zone.Identifier 备用数据流
"""

import os
from typing import Optional

from .常量定义 import IS_WINDOWS


# ═══════════════════════════════════════════════════════════
# ZoneIdentifierHandler 类
# ═══════════════════════════════════════════════════════════

class ZoneIdentifierHandler:
    """
    Zone.Identifier 处理器
    用于检测和清除 Windows 下载文件的 Zone.Identifier 备用数据流
    
    Zone.Identifier 是 Windows 从互联网下载文件时添加的安全标记，
    用于标识文件的来源区域（如互联网、本地网络等）。
    """
    
    ZONE_IDENTIFIER = ":Zone.Identifier"
    
    # 区域类型
    ZONE_LOCAL_MACHINE = 0  # 本地计算机
    ZONE_LOCAL_INTRANET = 1  # 本地 Intranet
    ZONE_TRUSTED_SITES = 2  # 受信任的站点
    ZONE_INTERNET = 3  # Internet
    ZONE_RESTRICTED_SITES = 4  # 受限制的站点
    
    @classmethod
    def has_zone_identifier(cls, file_path: str) -> bool:
        """
        检查文件是否有 Zone.Identifier
        
        Args:
            file_path: 文件路径
            
        Returns:
            是否存在 Zone.Identifier
        """
        if not IS_WINDOWS:
            return False
        
        try:
            # 尝试读取 Zone.Identifier 流
            zone_path = file_path + cls.ZONE_IDENTIFIER
            with open(zone_path, "r", encoding="utf-8") as f:
                content = f.read()
                # 检查是否包含 ZoneId
                return "ZoneId" in content
        except (OSError, IOError, UnicodeDecodeError):
            return False
    
    @classmethod
    def get_zone_id(cls, file_path: str) -> Optional[int]:
        """
        获取文件的 Zone ID
        
        Args:
            file_path: 文件路径
            
        Returns:
            Zone ID 或 None
        """
        if not IS_WINDOWS:
            return None
        
        try:
            zone_path = file_path + cls.ZONE_IDENTIFIER
            with open(zone_path, "r", encoding="utf-8") as f:
                content = f.read()
                # 解析 ZoneId
                for line in content.splitlines():
                    if line.startswith("ZoneId="):
                        return int(line.split("=")[1].strip())
        except (OSError, IOError, ValueError, UnicodeDecodeError):
            pass
        
        return None
    
    @classmethod
    def clear_zone_identifier(cls, file_path: str) -> bool:
        """
        清除文件的 Zone.Identifier
        
        Args:
            file_path: 文件路径
            
        Returns:
            是否成功
        """
        if not IS_WINDOWS:
            return False
        
        try:
            zone_path = file_path + cls.ZONE_IDENTIFIER
            
            # 方法1: 使用 DeleteFile 删除流
            try:
                import ctypes
                from ctypes import wintypes
                
                kernel32 = ctypes.windll.kernel32
                kernel32.DeleteFileW.argtypes = [wintypes.LPCWSTR]
                kernel32.DeleteFileW.restype = wintypes.BOOL
                
                # 需要使用 \\?\ 前缀处理流
                if not file_path.startswith("\\\\?\\"):
                    zone_path_extended = "\\\\?\\" + os.path.abspath(file_path) + cls.ZONE_IDENTIFIER
                else:
                    zone_path_extended = file_path + cls.ZONE_IDENTIFIER
                
                if kernel32.DeleteFileW(zone_path_extended):
                    return True
            except Exception:
                pass
            
            # 方法2: 使用 open 写入空内容
            try:
                with open(zone_path, "w") as f:
                    f.truncate(0)
                os.remove(zone_path)
                return True
            except (OSError, IOError):
                pass
            
            return False
            
        except Exception:
            return False
    
    @classmethod
    def set_zone_identifier(cls, file_path: str, zone_id: int) -> bool:
        """
        设置文件的 Zone.Identifier
        
        Args:
            file_path: 文件路径
            zone_id: Zone ID (0-4)
            
        Returns:
            是否成功
        """
        if not IS_WINDOWS:
            return False
        
        if zone_id < 0 or zone_id > 4:
            return False
        
        try:
            zone_path = file_path + cls.ZONE_IDENTIFIER
            content = f"[ZoneTransfer]\nZoneId={zone_id}\n"
            
            with open(zone_path, "w", encoding="utf-8") as f:
                f.write(content)
            
            return True
        except (OSError, IOError):
            return False
    
    @classmethod
    def get_zone_description(cls, zone_id: int) -> str:
        """
        获取 Zone ID 的描述
        
        Args:
            zone_id: Zone ID
            
        Returns:
            描述字符串
        """
        descriptions = {
            cls.ZONE_LOCAL_MACHINE: "本地计算机",
            cls.ZONE_LOCAL_INTRANET: "本地 Intranet",
            cls.ZONE_TRUSTED_SITES: "受信任的站点",
            cls.ZONE_INTERNET: "Internet",
            cls.ZONE_RESTRICTED_SITES: "受限制的站点",
        }
        return descriptions.get(zone_id, "未知区域")
    
    @classmethod
    def scan_directory(cls, directory: str, recursive: bool = True) -> list:
        """
        扫描目录中带有 Zone.Identifier 的文件
        
        Args:
            directory: 目录路径
            recursive: 是否递归扫描
            
        Returns:
            包含 Zone.Identifier 的文件列表
        """
        result = []
        
        if not os.path.isdir(directory):
            return result
        
        try:
            if recursive:
                for root, dirs, files in os.walk(directory):
                    for filename in files:
                        file_path = os.path.join(root, filename)
                        if cls.has_zone_identifier(file_path):
                            zone_id = cls.get_zone_id(file_path)
                            result.append({
                                'path': file_path,
                                'zone_id': zone_id,
                                'zone_desc': cls.get_zone_description(zone_id) if zone_id else "未知",
                            })
            else:
                for item in os.listdir(directory):
                    file_path = os.path.join(directory, item)
                    if os.path.isfile(file_path) and cls.has_zone_identifier(file_path):
                        zone_id = cls.get_zone_id(file_path)
                        result.append({
                            'path': file_path,
                            'zone_id': zone_id,
                            'zone_desc': cls.get_zone_description(zone_id) if zone_id else "未知",
                        })
        except OSError:
            pass
        
        return result
