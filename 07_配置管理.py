"""
配置管理模块
=============
职责：配置加载、验证、运行时设置

对外接口：
    - RuntimeConfig     运行时配置（数据类）
    - load_config()    加载配置文件
    - save_config()    保存配置文件
    - validate_config() 验证配置有效性
    - detect_disk_type() 检测磁盘类型
    - apply_disk_tuning() 应用磁盘调优
    - runtime          全局运行时配置实例
    - make_progress()  进度条创建函数

依赖关系：
    - 常量定义 (所有配置常量)
    - 工具函数 (_fmt_size, _fmt_duration)

使用示例：
    from 配置管理 import load_config, runtime
    config = load_config("config.json")
    runtime.max_workers = 8

版本历史：
    10.0: 最终审计版本，修正逻辑错误与文档一致性
    9.0: 磁盘检测重写、类型注解完善
    8.0: 初始模块化版本
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional, Tuple, Callable, Iterable

from .常量定义 import (
    IS_WINDOWS,
    VERSION,
    DEFAULT_CONFIG,
    DEFAULT_MAX_WORKERS,
    THROTTLE_SLEEP_DEFAULT,
    SSD_MAX_WORKERS,
    HDD_MAX_WORKERS,
    DedupStrategy,
    FlattenMode,
)


# ═══════════════════════════════════════════════════════════════════
# tqdm 可选导入
# ═══════════════════════════════════════════════════════════════════

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False
    tqdm = None


# ═══════════════════════════════════════════════════════════════════
# RuntimeConfig - 运行时配置
# ═══════════════════════════════════════════════════════════════════

@dataclass
class RuntimeConfig:
    """
    运行时配置
    
    存储程序运行时的全局配置。
    可以被命令行参数或配置文件覆盖。
    
    使用示例:
        >>> from 配置管理 import runtime
        >>> runtime.max_workers = 8
        >>> runtime.verbose = True
    """
    
    # 并发配置
    max_workers: int = DEFAULT_MAX_WORKERS
    """最大工作线程数"""
    
    throttle_sleep: float = THROTTLE_SLEEP_DEFAULT
    """节流休眠时间（秒）"""
    
    throttle_batch: int = 200
    """节流批次大小"""
    
    # 行为配置
    verbose: bool = False
    """详细输出模式"""
    
    dry_run: bool = True
    """模拟运行模式"""
    
    use_recycle_bin: bool = False
    """是否使用系统回收站"""
    
    # 会话信息
    session_id: str = ""
    """会话 ID"""
    
    # 磁盘信息
    disk_type: str = "Unknown"
    """检测到的磁盘类型"""
    
    def __post_init__(self):
        """初始化后处理"""
        if not self.session_id:
            import uuid
            self.session_id = uuid.uuid4().hex[:8]
    
    @property
    def is_ssd(self) -> bool:
        """是否为 SSD"""
        return self.disk_type == "SSD"
    
    @property
    def is_hdd(self) -> bool:
        """是否为 HDD"""
        return self.disk_type == "HDD"
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "max_workers": self.max_workers,
            "throttle_sleep": self.throttle_sleep,
            "throttle_batch": self.throttle_batch,
            "verbose": self.verbose,
            "dry_run": self.dry_run,
            "use_recycle_bin": self.use_recycle_bin,
            "session_id": self.session_id,
            "disk_type": self.disk_type,
        }


# 全局运行时配置实例
runtime = RuntimeConfig()


# ═══════════════════════════════════════════════════════════════════
# 配置加载与验证
# ═══════════════════════════════════════════════════════════════════

def load_config(config_path: Optional[str]) -> Dict[str, Any]:
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
        
        # 深度合并配置
        result = DEFAULT_CONFIG.copy()
        _deep_merge(result, user_config)
        
        return result
    
    except (json.JSONDecodeError, OSError) as e:
        print(f"⚠️ 配置文件加载失败: {e}")
        return DEFAULT_CONFIG.copy()


def _deep_merge(base: Dict, overlay: Dict) -> None:
    """
    深度合并字典
    
    Args:
        base: 基础字典（会被修改）
        overlay: 覆盖字典
    """
    for key, value in overlay.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def save_config(config: Dict[str, Any], output_path: str) -> bool:
    """
    保存配置到文件
    
    Args:
        config: 配置字典
        output_path: 输出路径
        
    Returns:
        是否成功
    """
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        return True
    except OSError as e:
        print(f"❌ 保存配置失败: {e}")
        return False


def validate_config(config: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    验证配置有效性
    
    Args:
        config: 配置字典
        
    Returns:
        (是否有效, 错误消息列表)
    """
    errors = []
    
    # 验证去重策略
    valid_strategies = {s.value for s in DedupStrategy}
    strategy = config.get("dedup_strategy", "shallowest")
    if strategy not in valid_strategies:
        errors.append(
            f"无效的去重策略: {strategy}，有效值: {valid_strategies}"
        )
    
    # 验证扁平化模式
    valid_modes = {m.value for m in FlattenMode}
    mode = config.get("flatten_mode", "single")
    if mode not in valid_modes:
        errors.append(
            f"无效的扁平化模式: {mode}，有效值: {valid_modes}"
        )
    
    # 验证线程数
    threads = config.get("threads")
    if threads is not None:
        if not isinstance(threads, int) or threads < 1:
            errors.append("threads 必须为正整数")
    
    # 验证节流时间
    throttle = config.get("throttle_ms")
    if throttle is not None:
        if not isinstance(throttle, (int, float)) or throttle < 0:
            errors.append("throttle_ms 必须为非负数")
    
    # 验证排除路径
    exclude_paths = config.get("exclude_paths", [])
    if not isinstance(exclude_paths, list):
        errors.append("exclude_paths 必须为列表")
    else:
        for path in exclude_paths:
            if ".." in os.path.normpath(path):
                errors.append(f"排除路径不允许路径穿越: {path}")
    
    # 验证 ext_group_map
    ext_map = config.get("ext_group_map")
    if ext_map is not None and not isinstance(ext_map, dict):
        errors.append("ext_group_map 必须为字典")
    
    return len(errors) == 0, errors


def get_default_config_template() -> Dict[str, Any]:
    """
    获取默认配置模板
    
    Returns:
        默认配置字典
    """
    return DEFAULT_CONFIG.copy()


# ═══════════════════════════════════════════════════════════════════
# 磁盘类型检测
# ═══════════════════════════════════════════════════════════════════

def detect_disk_type(path: str) -> str:
    """
    检测磁盘类型（SSD / HDD / Unknown）
    
    Args:
        path: 路径
        
    Returns:
        磁盘类型字符串
    """
    if not IS_WINDOWS:
        return "Unknown"
    
    try:
        drive = os.path.splitdrive(os.path.abspath(path))[0]
        if not drive:
            return "Unknown"
        
        # 使用 PowerShell 获取磁盘媒体类型
        ps_script = f"""
        $disk = Get-Partition -DriveLetter '{drive[0]}' -ErrorAction SilentlyContinue |
                 Get-Disk -ErrorAction SilentlyContinue
        if ($disk) {{
            $disk.MediaType
        }}
        """
        
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=15
        )
        
        media = result.stdout.strip().upper()
        
        if "SSD" in media or "SOLID" in media:
            return "SSD"
        elif "HDD" in media or "ROTATIONAL" in media or "FIXED" in media:
            return "HDD"
        
        return "Unknown"
    
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return "Unknown"


def apply_disk_tuning(path: str, config: Optional[Dict] = None) -> None:
    """
    根据磁盘类型应用性能调优
    
    Args:
        path: 目标路径
        config: 配置字典
    """
    global runtime
    
    config = config or {}
    disk_type = detect_disk_type(path)
    runtime.disk_type = disk_type
    
    drive = os.path.splitdrive(os.path.abspath(path))[0] or path
    
    # 手动设置优先
    manual_threads = config.get("threads")
    manual_throttle = config.get("throttle_ms")
    
    if disk_type == "SSD":
        runtime.max_workers = manual_threads or SSD_MAX_WORKERS
        runtime.throttle_sleep = (manual_throttle or 20) / 1000.0
        print(f"💾 检测到 SSD ({drive}), 线程: {runtime.max_workers}, "
              f"节流: {runtime.throttle_sleep * 1000:.0f}ms")
    
    elif disk_type == "HDD":
        runtime.max_workers = manual_threads or HDD_MAX_WORKERS
        runtime.throttle_sleep = (manual_throttle or 100) / 1000.0
        print(f"💿 检测到 HDD ({drive}), 线程: {runtime.max_workers}, "
              f"节流: {runtime.throttle_sleep * 1000:.0f}ms")
    
    else:
        runtime.max_workers = manual_threads or DEFAULT_MAX_WORKERS
        runtime.throttle_sleep = (manual_throttle or 50) / 1000.0
        print(f"💿 磁盘类型未知 ({drive}), 线程: {runtime.max_workers}, "
              f"节流: {runtime.throttle_sleep * 1000:.0f}ms")


def update_workers(n: int) -> None:
    """
    更新工作线程数
    
    Args:
        n: 新的线程数
    """
    global runtime
    runtime.max_workers = max(1, n)


# ═══════════════════════════════════════════════════════════════════
# 进度条
# ═══════════════════════════════════════════════════════════════════

def make_progress(
    iterable: Iterable,
    total: Optional[int] = None,
    desc: str = "",
    unit: str = "",
    disable: bool = False,
) -> Iterable:
    """
    创建进度条
    
    如果 tqdm 可用，返回 tqdm 进度条。
    否则返回原始迭代器。
    
    Args:
        iterable: 可迭代对象
        total: 总数
        desc: 描述
        unit: 单位
        disable: 是否禁用
        
    Returns:
        进度条迭代器或原始迭代器
    """
    if HAS_TQDM and not disable and runtime.verbose:
        return tqdm(
            iterable,
            total=total,
            desc=desc,
            unit=unit,
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} "
                       "[{elapsed}<{remaining}, {rate_fmt}]"
        )
    return iterable


def progress_callback(
    current: int,
    total: int,
    desc: str = "",
    unit: str = "项",
) -> None:
    """
    进度回调函数
    
    用于在不支持 tqdm 的环境中显示进度。
    
    Args:
        current: 当前进度
        total: 总数
        desc: 描述
        unit: 单位
    """
    if current % 100 == 0 or current == total:
        pct = (current / total * 100) if total > 0 else 0
        print(f"  {desc}: {current}/{total} ({pct:.0f}%) {unit}")


# ═══════════════════════════════════════════════════════════════════
# 其他工具
# ═══════════════════════════════════════════════════════════════════

def get_system_info() -> Dict[str, Any]:
    """
    获取系统信息
    
    Returns:
        系统信息字典
    """
    info = {
        "platform": "Windows" if IS_WINDOWS else "Other",
        "version": VERSION,
        "cpu_count": os.cpu_count() or 1,
        "disk_type": runtime.disk_type,
        "max_workers": runtime.max_workers,
    }
    
    if IS_WINDOWS:
        try:
            import platform
            info["windows_version"] = platform.version()
            info["windows_release"] = platform.release()
        except ImportError:
            pass
    
    return info


def check_dependencies() -> Dict[str, bool]:
    """
    检查依赖项是否可用
    
    Returns:
        依赖项状态字典
    """
    return {
        "tqdm": HAS_TQDM,
        "ctypes": True,  # 始终可用
        "shutil": True,
    }


# ═══════════════════════════════════════════════════════════════════
# 导出
# ═══════════════════════════════════════════════════════════════════

__all__ = [
    # 配置
    "RuntimeConfig",
    "runtime",
    "load_config",
    "save_config",
    "validate_config",
    "get_default_config_template",
    # 磁盘
    "detect_disk_type",
    "apply_disk_tuning",
    "update_workers",
    # 进度条
    "make_progress",
    "progress_callback",
    # 工具
    "get_system_info",
    "check_dependencies",
]
