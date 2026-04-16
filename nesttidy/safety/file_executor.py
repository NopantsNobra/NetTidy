"""文件执行器：策略调度器，统筹复制/移动策略的选择与执行。"""
from __future__ import annotations
import json
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional, Callable, Set

from nesttidy.core.node import MapEntry
from nesttidy.safety.strategies.base import (
    ExecutionStrategy,
    ExecutionResult,
    ExecutionSummary,
)
from nesttidy.safety.strategies.copy_strategy import copy_strategy
from nesttidy.safety.strategies.same_vol_move import same_vol_move_strategy
from nesttidy.safety.strategies.cross_vol_move import cross_vol_move_strategy
from nesttidy.utils.path import is_same_volume
from nesttidy.runtime.logger import get_logger

logger = get_logger(__name__)


class FileExecutor:
    """文件执行器：策略调度器。"""

    def __init__(
        self,
        mode: str = "copy",
        max_workers: int = 4,
        force_cross_volume: bool = False,
        progress_callback: Optional[Callable] = None,
    ):
        self.mode = mode
        self.max_workers = max_workers
        self.force_cross_volume = force_cross_volume
        self.progress_callback = progress_callback
        self._copy_strategy = copy_strategy
        self._same_vol_move_strategy = same_vol_move_strategy
        self._cross_vol_move_strategy = cross_vol_move_strategy

    def select_strategy(
        self,
        source: Path,
        target: Path,
        entry_mode: str | None = None,
    ) -> ExecutionStrategy:
        """根据模式和路径选择执行策略。"""
        mode = entry_mode or self.mode
        same_vol = is_same_volume(source, target)

        if mode == "copy":
            return self._copy_strategy
        elif mode == "move":
            if same_vol:
                return self._same_vol_move_strategy
            else:
                if self.force_cross_volume:
                    logger.warning(
                        f"跨卷移动已强制启用: {source.anchor} → {target.anchor}"
                    )
                    return self._cross_vol_move_strategy
                else:
                    logger.warning(
                        f"跨卷移动被阻止，回退到复制模式: {source.anchor} → {target.anchor}"
                    )
                    return self._copy_strategy
        elif mode == "auto":
            if same_vol:
                return self._same_vol_move_strategy
            else:
                return self._copy_strategy
        else:
            logger.warning(f"未知模式 {mode!r}，使用复制策略")
            return self._copy_strategy

    def execute_single(
        self,
        entry: MapEntry,
        source_base: Path,
        target_base: Path,
    ) -> ExecutionResult:
        """执行单个文件操作。"""
        strategy = self.select_strategy(
            entry.source_path,
            entry.target_path,
            entry.action,
        )
        # ══════════════════════════════════════════════════
        # 【QC修复1】捕获特定操作系统异常，转化为结构化结果
        # 确保调用方永远收到 ExecutionResult，而不是裸异常
        # ══════════════════════════════════════════════════
        try:
            return strategy.execute(
                source=entry.source_path,
                target=entry.target_path,
                file_size=entry.file_size,
            )
        except PermissionError as exc:
            logger.error(f"权限不足: {entry.source_path.name} — {exc}")
            return ExecutionResult(
                success=False,
                source=entry.source_path,
                target=entry.target_path,
                action=entry.action,
                error_message=f"权限不足: {exc}",
            )
        except FileNotFoundError as exc:
            logger.error(f"文件已不存在: {entry.source_path.name} — {exc}")
            return ExecutionResult(
                success=False,
                source=entry.source_path,
                target=entry.target_path,
                action=entry.action,
                error_message=f"文件已不存在（可能被外部程序删除）: {exc}",
            )
        except OSError as exc:
            logger.error(f"系统IO错误: {entry.source_path.name} — {exc}")
            return ExecutionResult(
                success=False,
                source=entry.source_path,
                target=entry.target_path,
                action=entry.action,
                error_message=f"系统IO错误: {exc}",
            )

    def execute_batch(
        self,
        entries: List[MapEntry],
        source_base: Path,
        target_base: Path,
        skip_paths: Optional[Set[Path]] = None,
    ) -> ExecutionSummary:
        """批量执行文件操作。"""
        skip_paths = skip_paths or set()

        # 去重拦截
        actual_entries = []
        skipped_in_batch = 0
        for entry in entries:
            if entry.source_path in skip_paths:
                skipped_in_batch += 1
                logger.debug(f"跳过重复文件: {entry.source_path.name}")
            else:
                actual_entries.append(entry)

        if skipped_in_batch > 0:
            logger.info(f"去重拦截: 已跳过 {skipped_in_batch} 个重复文件")

        total_bytes = sum(e.file_size for e in actual_entries)
        summary = ExecutionSummary(
            total_count=len(entries),
            skipped_count=skipped_in_batch,
            total_bytes=total_bytes,
        )

        if not actual_entries:
            logger.info("所有文件均被跳过，无需实际执行。")
            return summary

        logger.info(
            f"开始执行 {len(actual_entries)} 个文件操作，"
            f"总大小 {self._format_size(total_bytes)}"
        )

        start_time = time.perf_counter()
        completed_count = 0
        lock = threading.Lock()

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(
                    self.execute_single, entry, source_base, target_base
                ): entry
                for entry in actual_entries
            }

            for future in as_completed(futures):
                entry = futures[future]
                try:
                    result = future.result()
                    summary.results.append(result)
                    if result.success:
                        summary.success_count += 1
                        summary.transferred_bytes += result.bytes_transferred
                    else:
                        summary.failure_count += 1
                        # execute_single 内部已做脱敏日志，此处仅追加结构化记录
                        logger.error(
                            f"执行失败: {entry.source_path.name} — "
                            f"{result.error_message}"
                        )
                except Exception as e:
                    # ══════════════════════════════════════
                    # 【QC修复2】防御性兜底：理论上 execute_single 已处理所有 OS 异常
                    # 此处仅捕获意外的编程级异常（如 NoneType 等），不应在生产中出现
                    # ══════════════════════════════════════
                    summary.failure_count += 1
                    logger.exception(
                        f"执行异常（非预期）: {entry.source_path.name} — {e}"
                    )
                    summary.results.append(
                        ExecutionResult(
                            success=False,
                            source=entry.source_path,
                            target=entry.target_path,
                            action=entry.action,
                            error_message=str(e),
                        )
                    )

                # 线程安全的进度更新
                with lock:
                    completed_count += 1
                    if self.progress_callback:
                        self.progress_callback(
                            completed_count,
                            len(actual_entries),
                            summary.results[-1],
                        )

        duration = time.perf_counter() - start_time
        summary.duration_ms = duration * 1000
        logger.info(
            f"执行完成: 成功 {summary.success_count}，"
            f"失败 {summary.failure_count}，"
            f"去重跳过 {summary.skipped_count}，"
            f"耗时 {duration:.2f}s"
        )
        return summary

    def write_map_json(
        self,
        entries: List[MapEntry],
        output_path: Path,
    ) -> None:
        """写入映射表 JSON 文件。"""
        data = {
            "schema_version": "1.1",
            "total_files": len(entries),
            "total_bytes": sum(e.file_size for e in entries),
            "entries": [
                {
                    "source": str(e.source_path),
                    "target": str(e.target_path),
                    "action": e.action,
                    "action_reason": e.action_reason,
                    "rename_reason": e.rename_reason,
                    "file_size": e.file_size,
                }
                for e in entries
            ],
        }
        # ══════════════════════════════════════════════════
        # 【QC修复3】为纯 I/O 操作增加异常保护
        # ══════════════════════════════════════════════════
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"映射表已写入: {output_path.name}")
        except PermissionError as exc:
            logger.error(f"映射表写入失败（权限不足）: {output_path.parent} — {exc}")
            raise RuntimeError(f"映射表写入失败: 权限不足 — {exc}") from exc
        except OSError as exc:
            logger.error(f"映射表写入失败（IO错误）: {output_path.parent} — {exc}")
            raise RuntimeError(f"映射表写入失败: IO错误 — {exc}") from exc

    @staticmethod
    def _format_size(size: int) -> str:
        """格式化文件大小。"""
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} PB"
