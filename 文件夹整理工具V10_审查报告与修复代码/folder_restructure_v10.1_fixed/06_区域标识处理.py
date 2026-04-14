"""
区域标识处理模块
=============
职责：处理 Windows Zone.Identifier 备用数据流

对外接口：
    - ZoneIdentifierHandler  Zone.Identifier 处理器

依赖关系：
    - 常量定义 (IS_WINDOWS)

使用示例：
    from 区域标识处理 import ZoneIdentifierHandler
    if ZoneIdentifierHandler.has_zone_identifier("download.exe"):
        ZoneIdentifierHandler.clear_zone_identifier("download.exe")

版本历史：
    10.0: 最终审计版本，修正逻辑错误与文档一致性
    9.0: 简化实现、错误处理增强
    8.0: 初始模块化版本
"""

from __future__ import annotations

import os
from typing import Optional, List, Dict, Tuple

from .常量定义 import IS_WINDOWS


# ═══════════════════════════════════════════════════════════════════
# ZoneIdentifierHandler
# ═══════════════════════════════════════════════════════════════════

class ZoneIdentifierHandler:
    """
    Zone.Identifier 处理器
    
    Zone.Identifier 是 Windows 从互联网下载文件时添加的
    备用数据流（ADS），用于标识文件来源区域。
    
    Zone ID 值:
        0 - 本地计算机
        1 - 本地 Intranet
        2 - 受信任的站点
        3 - Internet
        4 - 受限制的站点
    
    使用示例:
        >>> if ZoneIdentifierHandler.has_zone_identifier(file):
        ...     zone = ZoneIdentifierHandler.get_zone_id(file)
        ...     print(f"来自: {ZoneIdentifierHandler.get_zone_description(zone)}")
        ...     ZoneIdentifierHandler.clear_zone_identifier(file)
    """
    
    STREAM_NAME = ":Zone.Identifier"
    """备用数据流名称"""
    
    # Zone ID 描述
    ZONE_DESCRIPTIONS: Dict[int, str] = {
        0: "本地计算机",
        1: "本地 Intranet",
        2: "受信任的站点",
        3: "Internet",
        4: "受限制的站点",
    }
    
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
        
        zone_path = file_path + cls.STREAM_NAME
        
        try:
            # 方法1: 直接检查流文件
            if os.path.exists(zone_path):
                return cls._has_valid_zone_content(zone_path)
            
            # 方法2: 使用 PowerShell
            import subprocess
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"Test-Path -LiteralPath '{zone_path}'"],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            return "True" in result.stdout
        
        except (OSError, subprocess.TimeoutExpired):
            return False
    
    @classmethod
    def _has_valid_zone_content(cls, zone_path: str) -> bool:
        """检查 Zone.Identifier 内容是否有效"""
        try:
            with open(zone_path, "r", encoding="utf-8") as f:
                content = f.read()
                return "ZoneId" in content
        except (OSError, UnicodeDecodeError):
            return False
    
    @classmethod
    def get_zone_id(cls, file_path: str) -> Optional[int]:
        """
        获取文件的 Zone ID
        
        Args:
            file_path: 文件路径
            
        Returns:
            Zone ID，或 None
        """
        if not IS_WINDOWS:
            return None
        
        zone_path = file_path + cls.STREAM_NAME
        
        try:
            with open(zone_path, "r", encoding="utf-8") as f:
                content = f.read()
                
            for line in content.splitlines():
                if line.startswith("ZoneId="):
                    try:
                        return int(line.split("=")[1].strip())
                    except (ValueError, IndexError):
                        pass
        
        except (OSError, UnicodeDecodeError, FileNotFoundError):
            pass
        
        return None
    
    @classmethod
    def get_zone_description(cls, zone_id: Optional[int]) -> str:
        """
        获取 Zone ID 的描述
        
        Args:
            zone_id: Zone ID
            
        Returns:
            描述字符串
        """
        if zone_id is None:
            return "未知"
        return cls.ZONE_DESCRIPTIONS.get(zone_id, "未知区域")
    
    @classmethod
    def clear_zone_identifier(cls, file_path: str) -> bool:
        """
        清除 Zone.Identifier
        
        Args:
            file_path: 文件路径
            
        Returns:
            是否成功
        """
        if not IS_WINDOWS:
            return False
        
        zone_path = file_path + cls.STREAM_NAME
        
        # 方法1: 直接删除
        if os.path.exists(zone_path):
            try:
                os.remove(zone_path)
                return True
            except OSError:
                pass
        
        # 方法2: 截断文件
        try:
            with open(zone_path, "w") as f:
                f.truncate(0)
            os.remove(zone_path)
            return True
        except OSError:
            pass
        
        # 方法3: 使用 PowerShell
        try:
            import subprocess
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"Remove-Item -LiteralPath '{file_path}' -Stream 'Zone.Identifier' -ErrorAction SilentlyContinue"],
                capture_output=True,
                text=True,
                timeout=10
            )
            return result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False
    
    @classmethod
    def set_zone_identifier(cls, file_path: str, zone_id: int) -> bool:
        """
        设置 Zone.Identifier
        
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
        
        zone_path = file_path + cls.STREAM_NAME
        
        content = f"[ZoneTransfer]\nZoneId={zone_id}\n"
        
        try:
            with open(zone_path, "w", encoding="utf-8") as f:
                f.write(content)
            return True
        except OSError:
            return False
    
    @classmethod
    def get_zone_info(cls, file_path: str) -> Dict:
        """
        获取完整的 Zone 信息
        
        Args:
            file_path: 文件路径
            
        Returns:
            包含 zone_id, description, exists 的字典
        """
        return {
            "exists": cls.has_zone_identifier(file_path),
            "zone_id": cls.get_zone_id(file_path),
            "description": cls.get_zone_description(cls.get_zone_id(file_path)),
        }
    
    @classmethod
    def scan_directory(
        cls,
        directory: str,
        recursive: bool = True,
    ) -> List[Dict]:
        """
        扫描目录中带有 Zone.Identifier 的文件
        
        Args:
            directory: 目录路径
            recursive: 是否递归扫描
            
        Returns:
            文件信息列表
        """
        results = []
        
        if not os.path.isdir(directory):
            return results
        
        try:
            for root, dirs, files in os.walk(directory):
                for filename in files:
                    file_path = os.path.join(root, filename)
                    
                    if cls.has_zone_identifier(file_path):
                        zone_id = cls.get_zone_id(file_path)
                        results.append({
                            "path": file_path,
                            "zone_id": zone_id,
                            "description": cls.get_zone_description(zone_id),
                        })
                
                if not recursive:
                    break
        
        except OSError:
            pass
        
        return results
    
    @classmethod
    def clear_directory(
        cls,
        directory: str,
        recursive: bool = True,
        dry_run: bool = True,
    ) -> Tuple[int, int]:
        """
        清除目录中所有 Zone.Identifier
        
        Args:
            directory: 目录路径
            recursive: 是否递归
            dry_run: 是否仅报告
            
        Returns:
            (处理数量, 成功数量)
        """
        files = cls.scan_directory(directory, recursive)
        total = len(files)
        success = 0
        
        if dry_run:
            return total, total  # 假设全部成功
        
        for info in files:
            if cls.clear_zone_identifier(info["path"]):
                success += 1
        
        return total, success
    
    @classmethod
    def get_statistics(cls, directory: str) -> Dict:
        """
        获取 Zone.Identifier 统计
        
        Args:
            directory: 目录路径
            
        Returns:
            统计信息字典
        """
        files = cls.scan_directory(directory, recursive=True)
        
        zone_counts: Dict[int, int] = {}
        for info in files:
            zid = info["zone_id"] or -1
            zone_counts[zid] = zone_counts.get(zid, 0) + 1
        
        return {
            "total": len(files),
            "by_zone": {
                cls.get_zone_description(k): v
                for k, v in zone_counts.items()
            },
        }


# ═══════════════════════════════════════════════════════════════════
# 导出
# ═══════════════════════════════════════════════════════════════════

__all__ = [
    "ZoneIdentifierHandler",
]
