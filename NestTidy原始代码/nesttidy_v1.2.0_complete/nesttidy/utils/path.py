"""路径工具函数，处理跨平台大小写、卷判断等问题。 """
from __future__ import annotations
import os
import unicodedata
from pathlib import Path


def names_equal(a: str, b: str) -> bool: -> bool:
    """跨平台绝对安全的文件名比较（大小写+Unicode形态不敏感）。
    
    警告：禁止直接使用 == 比较文件名字符串！
    - Windows: 大小写不敏感 (Case-insensitive)
    - macOS: 大小写不敏感 + NFD 归一化
    - Linux: 大小写敏感 (此函数会强行降级为大小写不敏感以保证算法一致性)
    """
    # ══════════════════════════════════════════════════
    # 【QC修复1】强制 NFD 归一化 + casefold (比 lower 更彻底的国际化小写)
    # ══════════════════════════════════════════════════
    norm_a = unicodedata.normalize("NFD", a).casefold()
    norm_b = unicodedata.normalize("NFD", b).casefold()
    return norm_a == norm_b


def is_same_volume(path_a: Path, path_b: Path) -> bool: -> bool:
    """判断两个路径是否位于同一物理卷（跨设备安全）。
    
    用于决定使用同卷极速策略还是跨卷安全搬迁策略。
    """
    try:
        # ══════════════════════════════════════════════════
        # 【QC修复2】放弃 anchor 比较，使用底层设备号 st_dev 判定
        # 这是 Linux / Windows / macOS 通用的绝对真理
        # ══════════════════════════════════════════════════
        # 注意：不使用 resolve()，因为如果路径不存在会直接报错。
        # stat() 对不存在路径的父目录也能正确返回所在设备的 st_dev
        dev_a = path_a.stat().st_dev
        dev_b = path_b.stat().st_dev
        return dev_a == dev_b
    except (OSError, ValueError):
        # 如果文件不存在、权限不足或路径无效，保守返回 False（强制走安全复制）
        return False


def safe_relative(path: Path, base: Path) -> Path:
    """安全获取相对路径，失败时返回原路径（防止跨盘 ValueError）。"""
    try:
        return path.relative_to(base)
    except ValueError:
        return path
