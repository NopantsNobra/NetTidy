"""进度报告器：提供终端进度条和实时状态显示。

依赖 tqdm 库实现美观的进度条，无依赖时降级为简单计数。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, Callable

from nesttidy.runtime.logger import get_logger

logger = get_logger(__name__)

# 尝试导入 tqdm
try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False
    logger.debug("tqdm 未安装，使用简单进度显示")


class ProgressReporter:
    """进度报告器。"""

    def __init__(
        self,
        total: int = 0,
        desc: str = "处理中",
        unit: str = "文件",
        disable: bool = False,
        bar_format: Optional[str] = None,
    ):
        """
        Args:
            total: 总任务数
            desc: 进度条描述
            unit: 单位名称
            disable: 是否禁用进度条
            bar_format: 自定义进度条格式
        """
        self.total = total
        self.desc = desc
        self.unit = unit
        self.disable = disable
        self.bar_format = bar_format

        self._current = 0
        self._pbar: Optional[tqdm] = None

    def __enter__(self) -> "ProgressReporter":
        """上下文管理器入口。"""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """上下文管理器出口。"""
        self.close()

    def start(self) -> None:
        """启动进度条。"""
        if self.disable:
            return

        if HAS_TQDM:
            self._pbar = tqdm(
                total=self.total,
                desc=self.desc,
                unit=self.unit,
                bar_format=self.bar_format,
            )
        else:
            logger.info(f"{self.desc}: 0/{self.total}")

    def update(self, n: int = 1, **kwargs) -> None:
        """更新进度。"""
        self._current += n

        if self.disable:
            return

        if self._pbar:
            self._pbar.update(n, **kwargs)
        else:
            # 简单进度显示（每 10% 或每 100 个输出一次）
            if self.total > 0 and (
                self._current % max(1, self.total // 10) == 0
                or self._current % 100 == 0
                or self._current >= self.total
            ):
                percent = self._current / self.total * 100
                logger.info(f"{self.desc}: {self._current}/{self.total} ({percent:.1f}%)")

    def set_description(self, desc: str) -> None:
        """设置进度条描述。"""
        self.desc = desc
        if self._pbar:
            self._pbar.set_description(desc)

    def set_postfix(self, **kwargs) -> None:
        """设置进度条后缀信息。"""
        if self._pbar:
            self._pbar.set_postfix(**kwargs)

    def close(self) -> None:
        """关闭进度条。"""
        if self._pbar:
            self._pbar.close()
            self._pbar = None

    @property
    def current(self) -> int:
        """当前进度。"""
        return self._current


class MultiPhaseProgress:
    """多阶段进度报告器。"""

    def __init__(self, phases: list, disable: bool = False):
        """
        Args:
            phases: 阶段配置列表 [{"name": "扫描", "total": 100}, ...]
            disable: 是否禁用进度条
        """
        self.phases = phases
        self.disable = disable
        self._current_phase = 0
        self._reporter: Optional[ProgressReporter] = None

    def start_phase(self, phase_name: Optional[str] = None) -> None:
        """开始新阶段。"""
        if self._reporter:
            self._reporter.close()

        if self._current_phase < len(self.phases):
            phase = self.phases[self._current_phase]
            name = phase_name or phase.get("name", f"阶段 {self._current_phase + 1}")
            total = phase.get("total", 0)

            self._reporter = ProgressReporter(
                total=total,
                desc=name,
                disable=self.disable,
            )
            self._reporter.start()

            self._current_phase += 1

    def update(self, n: int = 1, **kwargs) -> None:
        """更新当前阶段进度。"""
        if self._reporter:
            self._reporter.update(n, **kwargs)

    def close(self) -> None:
        """关闭所有进度条。"""
        if self._reporter:
            self._reporter.close()
            self._reporter = None


def create_file_progress(
    total_files: int,
    desc: str = "处理文件",
    **kwargs,
) -> ProgressReporter:
    """创建文件处理进度条。"""
    return ProgressReporter(total=total_files, desc=desc, **kwargs)


def create_bytes_progress(
    total_bytes: int,
    desc: str = "传输数据",
    **kwargs,
) -> ProgressReporter:
    """创建字节传输进度条。"""
    # 自定义格式：显示 MB/s
    bar_format = (
        "{desc}: {percentage:3.0f}%|{bar:25}| "
        "{n_fmt}/{total_fmt} [{rate_fmt}]"
    )
    return ProgressReporter(
        total=total_bytes,
        desc=desc,
        unit="B",
        bar_format=bar_format,
        **kwargs,
    )
