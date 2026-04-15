"""任务调度器：协调扫描、分析、执行各阶段的调度。

职责：
  - 管理任务生命周期
  - 协调各模块的执行顺序
  - 处理中断和异常
  - 生成最终报告
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Callable

from nesttidy.core.engine import RestructureEngine
from nesttidy.core.node import MapEntry
from nesttidy.enhance.dup_detector import DupDetector
from nesttidy.enhance.diff_generator import DiffGenerator
from nesttidy.safety.file_executor import FileExecutor, ExecutionSummary
from nesttidy.safety.pre_checker import PreChecker, PreCheckResult
from nesttidy.safety.shell_cleaner import ShellCleaner
from nesttidy.runtime.logger import get_logger
from nesttidy.runtime.changes_writer import ChangesWriter

logger = get_logger(__name__)


@dataclass
class TaskResult:
    """任务执行结果。"""
    success: bool
    source: Path
    target: Path
    mode: str
    scan_time_ms: float = 0.0
    analyze_time_ms: float = 0.0
    execute_time_ms: float = 0.0
    total_time_ms: float = 0.0
    file_count: int = 0
    total_bytes: int = 0
    duplicate_groups: int = 0
    pre_check: Optional[PreCheckResult] = None
    execution_summary: Optional[ExecutionSummary] = None
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class Scheduler:
    """任务调度器。"""

    def __init__(
        self,
        mode: str = "copy",
        max_workers: int = 4,
        detect_duplicates: bool = True,
        dry_run: bool = False,
        force_cross_volume: bool = False,
        progress_callback: Optional[Callable] = None,
    ):
        """
        Args:
            mode: 执行模式（copy/move/auto）
            max_workers: 最大并发线程数
            detect_duplicates: 是否检测重复文件
            dry_run: 是否为演练模式
            force_cross_volume: 是否强制允许跨卷移动
            progress_callback: 进度回调函数
        """
        self.mode = mode
        self.max_workers = max_workers
        self.detect_duplicates = detect_duplicates
        self.dry_run = dry_run
        self.force_cross_volume = force_cross_volume
        self.progress_callback = progress_callback

    def run(
        self,
        source: Path,
        target: Path,
        output_dir: Optional[Path] = None,
    ) -> TaskResult:
        """执行完整的重构任务。

        流程：
        1. 预检
        2. 扫描
        3. 分析
        4. 重复检测（可选）
        5. 执行（非 dry_run）
        6. 清理空壳（move 模式）
        7. 生成报告

        Args:
            source: 源目录路径
            target: 目标目录路径
            output_dir: 报告输出目录（默认为目标目录）

        Returns:
            TaskResult: 任务执行结果
        """
        result = TaskResult(
            success=False,
            source=source,
            target=target,
            mode=self.mode,
        )

        total_start = time.perf_counter()

        try:
            # ══════════════════════════════════════════════════════════════
            # 阶段 0：预检
            # ══════════════════════════════════════════════════════════════
            logger.info(f"[阶段 0/5] 预检: {source} → {target}")

            pre_checker = PreChecker()
            result.pre_check = pre_checker.check([], target, source)

            if not result.pre_check.can_proceed:
                result.errors.extend(result.pre_check.errors)
                return result

            result.warnings.extend(result.pre_check.warnings)

            # ══════════════════════════════════════════════════════════════
            # 阶段 1：扫描
            # ══════════════════════════════════════════════════════════════
            logger.info("[阶段 1/5] 扫描源目录...")

            scan_start = time.perf_counter()
            engine = RestructureEngine(source=source, target=target, mode=self.mode)
            engine.scan()
            result.scan_time_ms = (time.perf_counter() - scan_start) * 1000

            # 计算文件数量
            file_count = sum(1 for _ in self._iter_files(engine.root))
            logger.info(f"扫描完成: {file_count} 个文件")

            # ══════════════════════════════════════════════════════════════
            # 阶段 2：分析
            # ══════════════════════════════════════════════════════════════
            logger.info("[阶段 2/5] 分析目录结构...")

            analyze_start = time.perf_counter()
            engine.analyze()
            engine.build_map()
            result.analyze_time_ms = (time.perf_counter() - analyze_start) * 1000

            entries = engine.map
            result.file_count = len(entries)
            result.total_bytes = sum(e.file_size for e in entries)

            logger.info(f"分析完成: {result.file_count} 个文件需要处理")

            # ══════════════════════════════════════════════════════════════
            # 阶段 3：重复检测（可选）
            # ══════════════════════════════════════════════════════════════
            if self.detect_duplicates:
                logger.info("[阶段 3/5] 检测重复文件...")

                dup_detector = DupDetector()
                dup_groups = dup_detector.detect(engine.root)
                result.duplicate_groups = len(dup_groups)

                if dup_groups:
                    logger.info(f"发现 {result.duplicate_groups} 组重复文件")
            else:
                logger.info("[阶段 3/5] 跳过重复检测")

            # ══════════════════════════════════════════════════════════════
            # 阶段 4：执行（非 dry_run）
            # ══════════════════════════════════════════════════════════════
            if self.dry_run:
                logger.info("[阶段 4/5] 演练模式，跳过实际执行")
            else:
                logger.info("[阶段 4/5] 执行文件操作...")

                execute_start = time.perf_counter()
                executor = FileExecutor(
                    mode=self.mode,
                    max_workers=self.max_workers,
                    force_cross_volume=self.force_cross_volume,
                    progress_callback=self.progress_callback,
                )
                result.execution_summary = executor.execute_batch(entries, source, target)
                result.execute_time_ms = (time.perf_counter() - execute_start) * 1000

                if result.execution_summary.failure_count > 0:
                    result.errors.append(
                        f"执行失败: {result.execution_summary.failure_count} 个文件"
                    )

            # ══════════════════════════════════════════════════════════════
            # 阶段 5：清理空壳（move 模式）
            # ══════════════════════════════════════════════════════════════
            if self.mode == "move" and not self.dry_run:
                logger.info("[阶段 5/5] 清理空壳目录...")

                cleaner = ShellCleaner(dry_run=self.dry_run)
                cleaner.clean(source)
            else:
                logger.info("[阶段 5/5] 跳过空壳清理")

            # ══════════════════════════════════════════════════════════════
            # 生成报告
            # ══════════════════════════════════════════════════════════════
            output_dir = output_dir or target
            self._write_reports(entries, source, target, output_dir, result)

            result.success = len(result.errors) == 0

        except Exception as exc:
            logger.exception(f"任务执行异常: {exc}")
            result.errors.append(str(exc))

        result.total_time_ms = (time.perf_counter() - total_start) * 1000

        logger.info(
            f"任务完成: {'成功' if result.success else '失败'}，"
            f"总耗时 {result.total_time_ms/1000:.2f}s"
        )

        return result

    def _write_reports(
        self,
        entries: List[MapEntry],
        source: Path,
        target: Path,
        output_dir: Path,
        result: TaskResult,
    ) -> None:
        """生成报告文件。"""
        # 确保 output_dir 存在
        output_dir.mkdir(parents=True, exist_ok=True)

        # 写入映射表 JSON
        map_json_path = output_dir / "map.json"
        executor = FileExecutor(mode=self.mode)
        executor.write_map_json(entries, map_json_path)

        # 写入变更摘要
        changes_path = output_dir / "changes.txt"
        writer = ChangesWriter()
        writer.write(entries, source, target, changes_path)

        # 写入 HTML 报告
        diff_gen = DiffGenerator()
        html_report = diff_gen.generate_html(entries, source, target)
        html_path = output_dir / "report.html"
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_report)

        logger.info(f"报告已生成: {output_dir}")

    def _iter_files(self, node):
        """递归遍历文件树，生成所有文件节点。"""
        if node.is_file:
            yield node
        else:
            for child in node.children:
                yield from self._iter_files(child)


# 模块级单例工厂
def create_scheduler(**kwargs) -> Scheduler:
    """创建调度器实例。"""
    return Scheduler(**kwargs)
