"""纯复制策略：将源文件复制到目标位置，保留源文件不变。 """
from __future__ import annotations
import time
from pathlib import Path

from nesttidy.safety.strategies.base import (
    ExecutionStrategy,
    ExecutionResult,
)
from nesttidy.runtime.logger import get_logger

logger = get_logger(__name__)


class CopyStrategy(ExecutionStrategy):
    """纯复制策略：复制文件到目标位置，源文件保持不变。"""

    @property
    def name(self) -> str:
        return "复制策略"

    @property
    def action_type(self) -> str:
        return "copy"

    def execute(
        self,
        source: Path,
        target: Path,
        file_size: int = 0,
    ) -> ExecutionResult:
        """执行文件复制。"""
        start_time = time.perf_counter()

        # 1. 预检查（基类已脱敏）
        error = self.pre_check(source, target)
        if error:
            return ExecutionResult(
                success=False,
                source=source,
                target=target,
                action=self.action_type,
                error_message=error,
            )

        # 2. 幂等性检查：目标已存在时的智能处理
        if target.exists():
            try:
                existing_size = target.stat().st_size
                # ══════════════════════════════════════════════════
                # 【QC修复2】幂等性设计：大小一致直接跳过，避免无脑覆盖浪费 IO
                # ══════════════════════════════════════════════════
                if file_size > 0 and existing_size == file_size:
                    logger.debug(f"目标已存在且大小一致，跳过复制: {target.name}")
                    return ExecutionResult(
                        success=True,
                        source=source,
                        target=target,
                        action=self.action_type,
                        bytes_transferred=0, # 未产生实际传输
                        duration_ms=(time.perf_counter() - start_time) * 1000,
                    )
                else:
                    # 大小不一致或未知大小，执行覆盖
                    logger.warning(f"目标已存在但大小不一致，将覆盖: {target.name}")
            except OSError:
                # stat 失败（如权限问题），降级为强制覆盖
                logger.warning(f"无法获取目标信息，将强制覆盖: {target.name}")

        # 3. 执行复制
        success, error = self._safe_copy_file(source, target)
        duration_ms = (time.perf_counter() - start_time) * 1000

        if not success:
            return ExecutionResult(
                success=False,
                source=source,
                target=target,
                action=self.action_type,
                error_message=error,
                duration_ms=duration_ms,
            )

        # 4. 后验证（基类已脱敏）
        verify_error = self.post_verify(source, target)
        if verify_error:
            return ExecutionResult(
                success=False,
                source=source,
                target=target,
                action=self.action_type,
                error_message=f"后验证失败: {verify_error}",
                duration_ms=duration_ms,
            )

        # ══════════════════════════════════════════════════
        # 【QC修复1】日志脱敏：只打印文件名
        # ══════════════════════════════════════════════════
        logger.debug(f"复制成功: {source.name} → {target.name}")
        
        return ExecutionResult(
            success=True,
            source=source,
            target=target,
            action=self.action_type,
            bytes_transferred=file_size,
            duration_ms=duration_ms,
        )


# 模块级单例
copy_strategy = CopyStrategy()
