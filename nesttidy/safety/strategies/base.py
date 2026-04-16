"""执行策略抽象基类：定义文件操作策略的统一接口。 """
from __future__ import annotations
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Callable

from nesttidy.core.node import MapEntry
from nesttidy.runtime.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ExecutionResult:
    """单次文件操作结果。"""
    success: bool
    source: Path
    target: Path
    action: str
    bytes_transferred: int = 0
    error_message: Optional[str] = None
    duration_ms: float = 0.0


@dataclass
class ExecutionSummary:
    """批量执行结果摘要。"""
    total_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    # ══════════════════════════════════════════════════
    # 【QC修复2】删除冗余的 skip_count，全栈统一使用 skipped_count
    # ══════════════════════════════════════════════════
    skipped_count: int = 0  
    total_bytes: int = 0
    transferred_bytes: int = 0
    duration_ms: float = 0.0
    results: List[ExecutionResult] = None

    def __post_init__(self):
        if self.results is None:
            self.results = []

    @property
    def throughput_mbps(self) -> float:
        """计算吞吐量。"""
        if self.duration_ms <= 0:
            return 0.0
        return (self.transferred_bytes / (1024 * 1024)) / (self.duration_ms / 1000)


class ExecutionStrategy(ABC):
    """文件执行策略抽象基类。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """策略名称（用于日志和报告）。"""
        pass

    @property
    @abstractmethod
    def action_type(self) -> str:
        """操作类型（copy/move/cross_vol_move）。"""
        pass

    @abstractmethod
    def execute(
        self,
        source: Path,
        target: Path,
        file_size: int = 0,
    ) -> ExecutionResult:
        """执行单次文件操作。"""
        pass

    def pre_check(self, source: Path, target: Path) -> Optional[str]:
        """执行前检查（可选）。"""
        # ══════════════════════════════════════════════════
        # 【QC修复1】绝对红线：基类错误信息强制脱敏，只允许暴露文件名
        # 防止下游执行器的心血被绕过
        # ══════════════════════════════════════════════════
        if not source.exists():
            return f"源文件不存在: {source.name}"
        if not source.is_file():
            return f"源路径不是文件: {source.name}"
        return None

    def post_verify(self, source: Path, target: Path) -> Optional[str]:
        """执行后验证（可选）。"""
        # 【QC修复1】同步脱敏
        if not target.exists():
            return f"目标文件不存在: {target.name}"
        return None

    def prepare_target(self, target: Path) -> None:
        """准备目标路径（创建父目录）。"""
        target.parent.mkdir(parents=True, exist_ok=True)

    def _safe_copy_file(self, source: Path, target: Path) -> tuple[bool, Optional[str]]:
        """安全复制文件（内部方法）。"""
        try:
            self.prepare_target(target)
            shutil.copy2(source, target)
            return True, None
        except PermissionError as e:
            return False, f"权限不足: {e}"
        except OSError as e:
            return False, f"操作系统错误: {e}"
        except Exception as e:
            return False, f"未知错误: {e}"

    def _safe_move_file(self, source: Path, target: Path) -> tuple[bool, Optional[str]]:
        """安全移动文件（内部方法）。"""
        try:
            self.prepare_target(target)
            # shutil.move 跨盘时内部会调用 copy+unlink，同盘时是原子 rename
            shutil.move(str(source), str(target))
            return True, None
        except PermissionError as e:
            return False, f"权限不足: {e}"
        except OSError as e:
            return False, f"操作系统错误: {e}"
        except Exception as e:
            return False, f"未知错误: {e}"
