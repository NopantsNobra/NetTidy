"""路径工具函数，处理 Windows 大小写不敏感等问题。

【开发铁律】所有文件名比较必须通过 names_equal()，
禁止直接使用 == 比较字符串，防止 Windows 大小写陷阱。
"""
from pathlib import Path


def names_equal(a: str, b: str) -> bool:
    """Windows 安全的大小写不敏感文件名比较。

    Windows 文件系统对大小写不敏感，但 Path("A.txt") == Path("a.txt")
    依赖系统注册表设置，不可靠。所有比较必须经过此函数统一转小写后再比。
    """
    return a.lower() == b.lower()


def is_same_volume(path_a: Path, path_b: Path) -> bool:
    """判断两个路径是否位于同一卷（驱动器）。

    用于决定使用同卷极速移动策略还是跨卷安全搬迁策略。
    """
    try:
        anchor_a = path_a.resolve().anchor.upper()
        anchor_b = path_b.resolve().anchor.upper()
        return anchor_a == anchor_b
    except Exception:
        # 无法解析时保守处理，认为不同卷
        return False


def safe_relative(path: Path, base: Path) -> Path:
    """安全获取相对路径，失败时返回原路径（防止跨盘 ValueError）。"""
    try:
        return path.relative_to(base)
    except ValueError:
        return path
