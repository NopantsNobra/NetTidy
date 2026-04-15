"""预检器：在执行前进行安全检查。 """
from __future__ import annotations
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from nesttidy.utils.path import is_same_volume
from nesttidy.runtime.logger import get_logger

logger = get_logger(__name__)


@dataclass
class PreCheckResult:
    """预检结果。"""
    passed: bool
    source: Path
    target: Path
    mode: str
    source_readable: bool = True
    target_writable: bool = True
    same_volume: bool = True
    disk_space_ok: bool = True
    no_conflicts: bool = True
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    total_files: int = 0
    total_bytes: int = 0
    required_space: int = 0

    def add_error(self, message: str) -> None:
        self.errors.append(message)
        self.passed = False

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)


class PreChecker:
    """执行前预检器。"""

    def __init__(
        self,
        check_disk_space: bool = True,
        check_conflicts: bool = True,
        warn_cross_volume: bool = True,
    ):
        self.check_disk_space = check_disk_space
        self.check_conflicts = check_conflicts
        self.warn_cross_volume = warn_cross_volume

    def check(
        self,
        source: Path,
        target: Path,
        mode: str = "copy",
        estimated_bytes: int = 0,
    ) -> PreCheckResult:
        """执行预检。"""
        result = PreCheckResult(
            passed=True,
            source=source,
            target=target,
            mode=mode,
            required_space=estimated_bytes,
        )
        # 日志脱敏：只显示目录名，不显示绝对路径
        logger.info(f"开始预检: {source.name} → {target.name} ({mode})")

        self._check_source(source, result)
        self._check_target(target, result)
        self._check_volume(source, target, mode, result)
        
        if self.check_disk_space and estimated_bytes > 0:
            self._check_disk_space(target, estimated_bytes, result)
            
        if self.check_conflicts:
            self._check_conflicts(source, target, result)

        if result.passed:
            logger.info("预检通过")
        else:
            logger.error(f"预检失败: {len(result.errors)} 个错误")
            
        for warn in result.warnings:
            logger.warning(warn)
            
        return result

    def _check_source(self, source: Path, result: PreCheckResult) -> None:
        """检查源目录。"""
        if not source.exists():
            # 【QC修复2】日志脱敏
            result.add_error(f"源目录不存在: {source.name}")
            result.source_readable = False
            return
            
        if not source.is_dir():
            result.add_error(f"源路径不是目录: {source.name}")
            result.source_readable = False
            return

        # 检查可读性
        try:
            # ══════════════════════════════════════════════════
            # 【QC修复1】禁止 list()！只尝试取第一个元素，防止百万文件内存爆炸
            # ══════════════════════════════════════════════════
            next(iter(source.iterdir()), None)
        except PermissionError:
            result.add_error(f"源目录无读取权限: {source.name}")
            result.source_readable = False
        except OSError as e:
            result.add_error(f"无法访问源目录: {source.name} — {e}")
            result.source_readable = False

    def _check_target(self, target: Path, result: PreCheckResult) -> None:
        """检查目标目录。"""
        if not target.exists():
            parent = target.parent
            if not parent.exists():
                try:
                    parent.mkdir(parents=True, exist_ok=True)
                except PermissionError:
                    result.add_error(f"无法创建目标目录: {target.name}")
                    result.target_writable = False
                    return
                except OSError as e:
                    result.add_error(f"创建目标目录失败: {target.name} — {e}")
                    result.target_writable = False
                    return
                    
        try:
            target.mkdir(parents=True, exist_ok=True)
            # 写入探针测试
            test_file = target / ".nesttidy_write_test"
            test_file.touch()
            test_file.unlink()
        except PermissionError:
            result.add_error(f"目标目录无写入权限: {target.name}")
            result.target_writable = False
        except OSError as e:
            # 探针失败只记警告（某些只读文件系统允许 mkdir 但不允许 touch）
            result.add_warning(f"目标目录检查异常: {target.name} — {e}")

    def _check_volume(
        self,
        source: Path,
        target: Path,
        mode: str,
        result: PreCheckResult,
    ) -> None:
        """检查卷关系。"""
        same_vol = is_same_volume(source, target)
        result.same_volume = same_vol
        
        if not same_vol and self.warn_cross_volume:
            if mode == "move":
                result.add_warning(
                    f"跨卷移动操作: {source.anchor} → {target.anchor}\n"
                    f" 将使用复制+删除策略，耗时较长"
                )
            else:
                result.add_warning(
                    f"跨卷操作: {source.anchor} → {target.anchor}"
                )

    def _check_disk_space(
        self,
        target: Path,
        required_bytes: int,
        result: PreCheckResult,
    ) -> None:
        """检查磁盘空间。"""
        try:
            # ══════════════════════════════════════════════════
            # 【QC修复3】清理智障三元表达式，直接取盘符根目录查询，最稳健
            # ══════════════════════════════════════════════════
            root_dir = target.resolve().anchor
            # 兼容处理：如果是 Unix 的空字符串（相对路径），退回 parent
            check_path = Path(root_dir) if root_dir else target.parent
            
            usage = shutil.disk_usage(check_path)
            available = usage.free

            required_with_margin = int(required_bytes * 1.1)
            
            if available < required_with_margin:
                result.add_error(
                    f"磁盘空间不足: 需要 {self._format_size(required_with_margin)}，"
                    f"可用 {self._format_size(available)}"
                )
                result.disk_space_ok = False
            elif available < required_bytes * 2:
                result.add_warning(
                    f"磁盘空间紧张: 需要 {self._format_size(required_bytes)}，"
                    f"可用 {self._format_size(available)}"
                )
        except OSError as e:
            result.add_warning(f"无法检查磁盘空间: {e}")

    def _check_conflicts(
        self,
        source: Path,
        target: Path,
        result: PreCheckResult,
    ) -> None:
        """检查路径冲突。"""
        try:
            source.resolve().relative_to(target.resolve())
            result.add_error("源目录不能是目标目录的子目录")
            result.no_conflicts = False
        except ValueError:
            pass

        try:
            target.resolve().relative_to(source.resolve())
            result.add_warning("目标目录在源目录内部，可能导致意外行为")
        except ValueError:
            pass

        if source.resolve() == target.resolve():
            result.add_error("源目录和目标目录不能相同")
            result.no_conflicts = False

    @staticmethod
    def _format_size(size: int) -> str:
        """格式化文件大小。"""
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} PB"


def pre_check(
    source: Path,
    target: Path,
    mode: str = "copy",
    estimated_bytes: int = 0,
) -> PreCheckResult:
    """执行预检的便捷函数。"""
    checker = PreChecker()
    return checker.check(source, target, mode, estimated_bytes)
