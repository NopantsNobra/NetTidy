"""空壳清理器：Move 模式下物理删除多余的空壳目录。 """
from __future__ import annotations
import os
import shutil
from pathlib import Path
from typing import List, Set

from nesttidy.enhance.system_filter import system_filter
from nesttidy.runtime.logger import get_logger

logger = get_logger(__name__)

# ══════════════════════════════════════════════════
# 【QC修复1】安全阈值：超过此大小的“系统文件”绝不盲目删除，防止竞态误杀
# 10MB 足以覆盖任何正常的 OS 生成文件（如 Thumbs.db 缩略图缓存）
# ══════════════════════════════════════════════════
MAX_SAFE_SYSTEM_FILE_SIZE = 10 * 1024 * 1024  


class ShellCleaner:
    """空壳目录物理清理器。"""

    def __init__(
        self,
        dry_run: bool = False,
        preserve_root: bool = True,
    ):
        self.dry_run = dry_run
        self.preserve_root = preserve_root
        self.deleted_dirs: List[Path] = []
        self.skipped_dirs: List[tuple[Path, str]] = []
        self._source_root: Path = Path(".")  # 用于报告脱敏

    def clean(self, source: Path) -> int:
        """清理源目录中的空壳目录。"""
        self.deleted_dirs = []
        self.skipped_dirs = []
        self._source_root = source  # 记录根路径用于后续相对化

        if not source.exists():
            logger.warning(f"源目录不存在: {source.name}")
            return 0
            
        # 【QC修复】日志脱敏
        logger.info(f"开始清理空壳目录: {source.name}")

        all_dirs = self._collect_dirs_bottom_up(source)

        for dir_path in all_dirs:
            if self.preserve_root and dir_path == source:
                continue
                
            if self._is_shell_dir(dir_path):
                self._delete_dir(dir_path)
                
        logger.info(
            f"清理完成: 删除 {len(self.deleted_dirs)} 个空壳目录，"
            f"跳过 {len(self.skipped_dirs)} 个目录"
        )
        return len(self.deleted_dirs)

    def _collect_dirs_bottom_up(self, root: Path) -> List[Path]:
        """收集所有目录，按深度降序排列（自底向上）。"""
        dirs = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirs.append(Path(dirpath))
        # 按路径深度降序排序（深层目录在前）
        dirs.sort(key=lambda p: len(p.parts), reverse=True)
        return dirs

    def _is_shell_dir(self, dir_path: Path) -> bool:
        """判断一个目录是否为空壳目录。"""
        try:
            # ══════════════════════════════════════════════════
            # 【QC修复3】使用 any() 替代 list()，零内存消耗且极速短路
            # ══════════════════════════════════════════════════
            has_entries = any(dir_path.iterdir())
            if not has_entries:
                return True
                
            # 如果有内容，检查是否全是系统垃圾
            for entry in dir_path.iterdir():
                if entry.is_file():
                    if not system_filter.is_system_file(entry.name):
                        return False
                    # 防御性检查：如果系统文件大得离谱，拒绝认定为空壳
                    try:
                        if entry.stat().st_size > MAX_SAFE_SYSTEM_FILE_SIZE:
                            return False
                    except OSError:
                        return False
                else:
                    # ══════════════════════════════════════════════════
                    # 【QC修复3】清理迷惑逻辑：只要 iterdir 能看到子目录，
                    # 说明子目录没被删掉（不是空壳），直接返回 False。
                    # ══════════════════════════════════════════════════
                    return False
            return True
            
        except PermissionError:
            self.skipped_dirs.append((dir_path, "权限不足"))
            return False
        except OSError as e:
            self.skipped_dirs.append((dir_path, str(e)))
            return False

    def _delete_dir(self, dir_path: Path) -> bool:
        """删除一个空壳目录。"""
        if self.dry_run:
            logger.info(f"[演练] 将删除空壳目录: {dir_path.name}")
            self.deleted_dirs.append(dir_path)
            return True
            
        try:
            # 清理内部系统文件
            for entry in dir_path.iterdir():
                if entry.is_file() and system_filter.is_system_file(entry.name):
                    # ══════════════════════════════════════════════════
                    # 【QC修复1】终极防线：物理删除前的最后二次校验
                    # 防止在检查和删除之间发生竞态修改（文件被替换成大文件）
                    # ══════════════════════════════════════════════════
                    try:
                        if entry.stat().st_size <= MAX_SAFE_SYSTEM_FILE_SIZE:
                            entry.unlink()
                        else:
                            raise OSError(f"系统文件异常过大，拒绝删除")
                    except OSError as e:
                        self.skipped_dirs.append((dir_path, f"安全拦截: {e}"))
                        logger.warning(f"中止删除（安全拦截）: {dir_path.name}")
                        return False # 发现异常，立刻中止当前目录的删除

            dir_path.rmdir()
            self.deleted_dirs.append(dir_path)
            logger.debug(f"已删除空壳目录: {dir_path.name}")
            return True
            
        except PermissionError as e:
            self.skipped_dirs.append((dir_path, f"权限不足: {e}"))
            # 【QC修复】日志脱敏
            logger.warning(f"无法删除目录（权限不足）: {dir_path.name}")
            return False
        except OSError as e:
            self.skipped_dirs.append((dir_path, str(e)))
            logger.warning(f"无法删除目录: {dir_path.name} — {e}")
            return False

    def get_report(self) -> dict:
        """获取清理报告。"""
        # ══════════════════════════════════════════════════
        # 【QC修复2】报告脱敏：绝对路径转换为相对路径
        # ══════════════════════════════════════════════════
        def to_rel(p: Path) -> str:
            try:
                return str(p.relative_to(self._source_root))
            except ValueError:
                return f"../{p.name}" # 兜底脱敏

        return {
            "deleted_count": len(self.deleted_dirs),
            "skipped_count": len(self.skipped_dirs),
            "deleted_dirs": [to_rel(p) for p in self.deleted_dirs],
            "skipped_dirs": [
                {"path": to_rel(p), "reason": r} for p, r in self.skipped_dirs
            ],
        }


def clean_empty_dirs(
    source: Path,
    dry_run: bool = False,
    preserve_root: bool = True,
) -> int:
    """便捷函数：清理空壳目录。"""
    cleaner = ShellCleaner(
        dry_run=dry_run,
        preserve_root=preserve_root,
    )
    return cleaner.clean(source)
