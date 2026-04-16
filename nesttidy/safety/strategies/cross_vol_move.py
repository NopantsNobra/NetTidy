"""跨卷安全搬迁策略：复制后删除，支持完整性校验。 """
from __future__ import annotations
import time
from pathlib import Path

from nesttidy.safety.strategies.base import (
    ExecutionStrategy,
    ExecutionResult,
)
from nesttidy.utils.path import is_same_volume
from nesttidy.utils.hash import file_sha256
from nesttidy.runtime.logger import get_logger

logger = get_logger(__name__)

# 哈希校验的性能阈值（超过此大小仅校验文件尺寸，不再计算哈希）
_LARGE_FILE_HASH_THRESHOLD = 100 * 1024 * 1024  # 100 MB


class CrossVolMoveStrategy(ExecutionStrategy):
    """跨卷安全搬迁策略：复制-校验-删除三步走。"""

    def __init__(self, verify_integrity: bool = True):
        self.verify_integrity = verify_integrity

    @property
    def name(self) -> str:
        return "跨卷移动策略"

    @property
    def action_type(self) -> str:
        return "cross_vol_move"

    def execute(
        self,
        source: Path,
        target: Path,
        file_size: int = 0,
    ) -> ExecutionResult:
        """执行跨卷移动。"""
        start_time = time.perf_counter()

        # 预检查（调用重写后的安全版本）
        error = self.pre_check(source, target)
        if error:
            return ExecutionResult(
                success=False, source=source, target=target,
                action=self.action_type, error_message=error,
            )

        # 幂等性检查：目标已存在且大小一致时跳过
        if target.exists():
            try:
                existing_size = target.stat().st_size
                if file_size > 0 and existing_size == file_size:
                    logger.debug(f"跨卷移动: 目标已存在且大小一致，直接删除源文件: {source.name}")
                    # 既然目标完好，直接进入删除源文件阶段
                    remove_success, remove_error = self._safe_remove(source)
                    if not remove_success:
                        logger.warning(f"源文件删除失败（目标已完好）: {remove_error}")
                    return ExecutionResult(
                        success=True, source=source, target=target,
                        action=self.action_type, bytes_transferred=0,
                        duration_ms=(time.perf_counter() - start_time) * 1000,
                    )
                else:
                    logger.warning(f"跨卷移动: 目标已存在但大小不一致，将覆盖: {target.name}")
            except OSError:
                logger.warning(f"跨卷移动: 无法获取目标信息，将强制覆盖: {target.name}")

        # 步骤 1：复制文件
        success, error = self._safe_copy_file(source, target)
        if not success:
            duration_ms = (time.perf_counter() - start_time) * 1000
            return ExecutionResult(
                success=False, source=source, target=target,
                action=self.action_type, error_message=f"复制失败: {error}",
                duration_ms=duration_ms,
            )

        # 步骤 2：完整性校验（可选）
        if self.verify_integrity and file_size > 0:
            verify_error = self._verify_integrity(source, target, file_size)
            if verify_error:
                # 校验失败，删除目标文件，保留源文件（极致的安全退让）
                self._safe_remove(target)
                duration_ms = (time.perf_counter() - start_time) * 1000
                return ExecutionResult(
                    success=False, source=source, target=target,
                    action=self.action_type, error_message=f"完整性校验失败: {verify_error}",
                    duration_ms=duration_ms,
                )

        # 步骤 3：删除源文件
        remove_success, remove_error = self._safe_remove(source)
        if not remove_success:
            logger.warning(f"跨卷移动成功，但源文件残留: {source.name} ({remove_error})")

        duration_ms = (time.perf_counter() - start_time) * 1000

        # 后验证
        if not target.exists():
            return ExecutionResult(
                success=False, source=source, target=target,
                action=self.action_type, error_message="后验证失败: 目标文件消失",
                duration_ms=duration_ms,
            )

        # 【QC修复】日志脱敏
        logger.debug(f"跨卷移动成功: {source.name} → {target.name}")
        return ExecutionResult(
            success=True, source=source, target=target,
            action=self.action_type, bytes_transferred=file_size,
            duration_ms=duration_ms,
        )

    def pre_check(self, source: Path, target: Path) -> str | None:
        """执行前检查。"""
        # ══════════════════════════════════════════════════
        # 【QC修复2】修补契约：子类重写必须遵守基类的脱敏规范
        # ══════════════════════════════════════════════════
        if not source.exists():
            return f"源文件不存在: {source.name}"
        if not source.is_file():
            return f"源路径不是文件: {source.name}"

        if is_same_volume(source, target):
            logger.warning(
                f"检测到同卷操作 ({source.anchor})，建议使用同卷移动策略以获得更好性能"
            )
        return None

    def _verify_integrity(self, source: Path, target: Path, expected_size: int) -> str | None:
        """校验源文件和目标文件的完整性。"""
        try:
            source_size = source.stat().st_size
            target_size = target.stat().st_size
            
            if source_size != target_size:
                return f"文件大小不匹配 (源:{source_size} vs 目标:{target_size})"

            # ══════════════════════════════════════════════════
            # 【QC修复1】消灭静默降级：大文件跳过哈希时，必须强制 Warning 告知用户
            # 绝不允许在 verify_integrity=True 时假装一切安全
            # ══════════════════════════════════════════════════
            if source_size > _LARGE_FILE_HASH_THRESHOLD:
                logger.warning(
                    f"文件较大 ({source_size / (1024*1024):.1f} MB)，"
                    f"已跳过哈希比对，仅依赖文件大小校验"
                )
                return None

            # 小文件严格计算哈希
            source_hash = file_sha256(source)
            target_hash = file_sha256(target)
            if source_hash != target_hash:
                return f"SHA-256 哈希不匹配"
                
            return None
        except Exception as e:
            return f"校验过程异常: {e}"

    def _safe_remove(self, path: Path) -> tuple[bool, str | None]:
        """安全删除文件。"""
        try:
            path.unlink()
            return True, None
        except PermissionError as e:
            return False, f"权限不足: {e}"
        except OSError as e:
            return False, f"系统错误: {e}"
        except Exception as e:
            return False, f"未知错误: {e}"


# 模块级单例
cross_vol_move_strategy = CrossVolMoveStrategy(verify_integrity=True)
