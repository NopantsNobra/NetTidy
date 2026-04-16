"""同卷极速移动策略：利用文件系统原子操作实现瞬间移动。 """
from __future__ import annotations
import time
from pathlib import Path

from nesttidy.safety.strategies.base import (
    ExecutionStrategy,
    ExecutionResult,
)
from nesttidy.utils.path import is_same_volume
from nesttidy.runtime.logger import get_logger

logger = get_logger(__name__)


class SameVolMoveStrategy(ExecutionStrategy):
    """同卷极速移动策略：利用文件系统原子操作。"""

    @property
    def name(self) -> str:
        return "同卷移动策略"

    @property
    def action_type(self) -> str:
        return "move"

    def execute(
        self,
        source: Path,
        target: Path,
        file_size: int = 0,
    ) -> ExecutionResult:
        """执行同卷移动。"""
        start_time = time.perf_counter()

        # 1. 预检查（已包含同卷校验）
        error = self.pre_check(source, target)
        if error:
            return ExecutionResult(
                success=False, source=source, target=target,
                action=self.action_type, error_message=error,
            )

        # 2. 幂等性安全处理：清理已存在的目标，防止 Windows 底层拒绝覆盖
        if target.exists():
            try:
                target.unlink()
                logger.debug(f"同卷移动: 清理已存在的目标文件: {target.name}")
            except OSError as e:
                duration_ms = (time.perf_counter() - start_time) * 1000
                return ExecutionResult(
                    success=False, source=source, target=target,
                    action=self.action_type, error_message=f"无法覆盖目标文件: {e}",
                    duration_ms=duration_ms,
                )

        # 3. 执行原子移动
        success, error = self._safe_move_file(source, target)
        duration_ms = (time.perf_counter() - start_time) * 1000

        if not success:
            return ExecutionResult(
                success=False, source=source, target=target,
                action=self.action_type, error_message=error,
                duration_ms=duration_ms,
            )

        # 4. 严格后验证
        if not target.exists():
            return ExecutionResult(
                success=False, source=source, target=target,
                action=self.action_type, error_message="后验证失败: 目标文件消失",
                duration_ms=duration_ms,
            )
            
        # ══════════════════════════════════════════════════
        # 【QC修复1】铁律：同卷移动后源文件必须消失，否则视为严重失败！
        # 绝对不允许“静默变复制”导致磁盘空间翻倍
        # ══════════════════════════════════════════════════
        if source.exists():
            return ExecutionResult(
                success=False, source=source, target=target,
                action=self.action_type, 
                error_message=f"后验证失败: 源文件未删除 (疑似发生竞态复制)",
                duration_ms=duration_ms,
            )

        # 【QC修复2】日志脱敏
        logger.debug(f"同卷移动成功: {source.name} → {target.name}")
        return ExecutionResult(
            success=True, source=source, target=target,
            action=self.action_type, bytes_transferred=file_size,
            duration_ms=duration_ms,
        )

    def pre_check(self, source: Path, target: Path) -> str | None:
        """执行前检查。"""
        # ══════════════════════════════════════════════════
        # 【QC修复2】修补契约：遵守基类的脱敏规范
        # ══════════════════════════════════════════════════
        if not source.exists():
            return f"源文件不存在: {source.name}"
        if not source.is_file():
            return f"源路径不是文件: {source.name}"
            
        if not is_same_volume(source, target):
            return f"源和目标不在同一卷 ({source.anchor} vs {target.anchor})，拒绝执行同卷原子操作"
            
        return None


# 模块级单例
same_vol_move_strategy = SameVolMoveStrategy()
