"""
配置管理模块
配置加载/验证、SSD检测、tqdm包装、RuntimeConfig
"""

import json
import os
import platform
from typing import Dict, Tuple, Any, Optional
from dataclasses import dataclass, field

from .常量定义 import (
    DEFAULT_CONFIG, DEFAULT_MAX_WORKERS, IS_WINDOWS, VERSION,
)


# ═══════════════════════════════════════════════════════════
# tqdm 可选导入
# ═══════════════════════════════════════════════════════════
try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False
    tqdm = None


# ═══════════════════════════════════════════════════════════
# 运行时配置
# ═══════════════════════════════════════════════════════════
@dataclass
class RuntimeConfig:
    """运行时配置"""
    max_workers: int = DEFAULT_MAX_WORKERS
    throttle_sleep: float = 0.05
    use_recycle_bin: bool = False
    verbose: bool = False
    session_id: str = ""


# 全局运行时配置实例
runtime = RuntimeConfig()


# ═══════════════════════════════════════════════════════════
# 配置加载与验证
# ═══════════════════════════════════════════════════════════

def load_config(config_path: str) -> Dict[str, Any]:
    """
    加载配置文件
    
    Args:
        config_path: 配置文件路径
        
    Returns:
        配置字典
    """
    if not config_path or not os.path.isfile(config_path):
        return DEFAULT_CONFIG.copy()
    
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            user_config = json.load(f)
        
        # 合并默认配置
        result = DEFAULT_CONFIG.copy()
        result.update(user_config)
        return result
    except (json.JSONDecodeError, OSError) as e:
        print(f"⚠️ 配置文件加载失败: {e}")
        return DEFAULT_CONFIG.copy()


def save_default_config(output_path: str = "restructure_config.json") -> None:
    """
    保存默认配置模板
    
    Args:
        output_path: 输出文件路径
    """
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)
    print(f"✅ 配置模板已保存: {output_path}")


def validate_config(config: Dict[str, Any]) -> Tuple[bool, list]:
    """
    验证配置有效性
    
    Args:
        config: 配置字典
        
    Returns:
        (是否有效, 错误列表)
    """
    errors = []
    
    # 验证去重策略
    valid_strategies = {"shallowest", "newest", "largest", "hardlink"}
    strategy = config.get("dedup_strategy", "shallowest")
    if strategy not in valid_strategies:
        errors.append(f"无效的去重策略: {strategy}，有效值: {valid_strategies}")
    
    # 验证扁平化模式
    valid_modes = {"single", "ext", "date", "prefix"}
    mode = config.get("flatten_mode", "single")
    if mode not in valid_modes:
        errors.append(f"无效的扁平化模式: {mode}，有效值: {valid_modes}")
    
    # 验证线程数
    threads = config.get("threads")
    if threads is not None and (not isinstance(threads, int) or threads < 1):
        errors.append(f"无效的线程数: {threads}，应为正整数")
    
    # 验证节流时间
    throttle = config.get("throttle_ms")
    if throttle is not None and (not isinstance(throttle, (int, float)) or throttle < 0):
        errors.append(f"无效的节流时间: {throttle}，应为非负数")
    
    return len(errors) == 0, errors


# ═══════════════════════════════════════════════════════════
# 磁盘类型检测与调优
# ═══════════════════════════════════════════════════════════

def detect_disk_type(path: str) -> str:
    """
    检测磁盘类型（SSD/HDD/Unknown）
    
    Args:
        path: 路径
        
    Returns:
        磁盘类型字符串
    """
    if not IS_WINDOWS:
        return "Unknown"
    
    try:
        import ctypes
        from ctypes import wintypes
        
        # 获取驱动器号
        drive = os.path.splitdrive(os.path.abspath(path))[0]
        if not drive:
            return "Unknown"
        
        # 使用 DeviceIoControl 检测 SSD
        kernel32 = ctypes.windll.kernel32
        
        # 打开驱动器
        handle = kernel32.CreateFileW(
            f"\\\\.\\{drive}",
            0x80000000,  # GENERIC_READ
            0x00000001 | 0x00000002,  # FILE_SHARE_READ | FILE_SHARE_WRITE
            None,
            3,  # OPEN_EXISTING
            0,
            None
        )
        
        if handle == -1 or handle is None:
            return "Unknown"
        
        try:
            # STORAGE_PROPERTY_QUERY
            query = ctypes.create_string_buffer(8 + 4 + 4)
            struct.pack_into("<III", query, 0, 0, 0, 0)  # PropertyId, QueryType, AdditionalParameters
            
            # STORAGE_DEVICE_DESCRIPTOR
            buffer = ctypes.create_string_buffer(1024)
            bytes_returned = wintypes.DWORD()
            
            result = kernel32.DeviceIoControl(
                handle,
                0x002D1400,  # IOCTL_STORAGE_QUERY_PROPERTY
                query, len(query),
                buffer, len(buffer),
                ctypes.byref(bytes_returned),
                None
            )
            
            if result:
                # 检查 SeekPenalty (偏移 44)
                # 如果 HasSeekPenalty 为 0，则为 SSD
                import struct
                has_seek_penalty = struct.unpack_from("<?", buffer.raw, 44)[0]
                return "HDD" if has_seek_penalty else "SSD"
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        pass
    
    return "Unknown"


def apply_disk_tuning(path: str, config: Dict[str, Any]) -> None:
    """
    根据磁盘类型应用性能调优
    
    Args:
        path: 目标路径
        config: 配置字典
    """
    drive = os.path.splitdrive(os.path.abspath(path))[0] or path
    disk_type = detect_disk_type(path)
    
    manual = config.get("threads")
    
    if disk_type == "SSD":
        runtime.max_workers = manual or min(16, (os.cpu_count() or 4) * 2)
        runtime.throttle_sleep = (config.get("throttle_ms", 20)) / 1000.0
        print(f"💾 检测到 SSD ({drive}), 线程: {runtime.max_workers}, 节流: {runtime.throttle_sleep*1000:.0f}ms")
    elif disk_type == "HDD":
        runtime.max_workers = manual or 2
        runtime.throttle_sleep = (config.get("throttle_ms", 100)) / 1000.0
        print(f"💿 检测到 HDD ({drive}), 线程: {runtime.max_workers}, 节流: {runtime.throttle_sleep*1000:.0f}ms")
    else:
        runtime.max_workers = manual or DEFAULT_MAX_WORKERS
        runtime.throttle_sleep = (config.get("throttle_ms", 50)) / 1000.0
        print(f"💿 磁盘未知 ({drive}), 线程: {runtime.max_workers}, 节流: {runtime.throttle_sleep*1000:.0f}ms")


def update_workers(n: int) -> None:
    """
    更新工作线程数
    
    Args:
        n: 新的线程数
    """
    runtime.max_workers = max(1, n)


# ═══════════════════════════════════════════════════════════
# 进度条包装
# ═══════════════════════════════════════════════════════════

def make_progress(iterable, total=None, desc="", unit="", disable=False):
    """
    创建进度条包装器
    
    Args:
        iterable: 可迭代对象
        total: 总数
        desc: 描述
        unit: 单位
        disable: 是否禁用
        
    Returns:
        进度条迭代器或原迭代器
    """
    if HAS_TQDM and not disable:
        return tqdm(
            iterable, 
            total=total, 
            desc=desc, 
            unit=unit,
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]"
        )
    return iterable
