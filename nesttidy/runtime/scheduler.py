"""任务调度器：协调扫描、分析、执行各阶段的调度。"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Callable, Set

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
    VALID_MODES = {"copy", "move"}

    def __init__(
        self,
        mode: str = "copy",
        max_workers: int = 4,
        detect_duplicates: bool = True,
        dry_run: bool = False,
        force_cross_volume: bool = False,
        progress_callback: Optional[Callable] = None,
    ):
        if mode not in self.VALID_MODES:
            raise ValueError(f"不支持的执行模式: {mode!r}，仅允许 {self.VALID_MODES}")
        if max_workers < 1:
            raise ValueError(f"max_workers 必须 >= 1，收到: {max_workers}")

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
        """执行完整的重构任务。"""
        result = TaskResult(
            success=False,
            source=source,
            target=target,
            mode=self.mode,
        )
        total_start = time.perf_counter()

        try:
            # ════════════════ 阶段 0：预检 ════════════════
            logger.info(f"[阶段 0/5] 预检: {source} → {target}")
            pre_checker = PreChecker()
            result.pre_check = pre_checker.check(source, target, self.mode)

            if not result.pre_check.passed:
                result.errors.extend(result.pre_check.errors)
                return result
            result.warnings.extend(result.pre_check.warnings)

            # ════════════════ 阶段 1：扫描 ════════════════
            logger.info("[阶段 1/5] 扫描源目录...")
            scan_start = time.perf_counter()
            engine = RestructureEngine(source=source, target=target, mode=self.mode)
            engine.scan()
            result.scan_time_ms = (time.perf_counter() - scan_start) * 1000

            # ════════════════ 阶段 2：分析 ════════════════
            logger.info("[阶段 2/5] 分析目录结构...")
            analyze_start = time.perf_counter()
            engine.analyze()
            engine.build_map()
            result.analyze_time_ms = (time.perf_counter() - analyze_start) * 1000

            entries = engine.map
            result.file_count = len(entries)
            result.total_bytes = sum(e.file_size for e in entries)
            
            # 【关键修复】将映射表提前挂载到 result 上
            result._engine_map = entries

            logger.info(f"扫描+分析完成: {result.file_count} 个文件需要处理")

            # ════════════════ 阶段 3：重复检测 ════════════════
            skip_paths: Set[Path] = set()

            if self.detect_duplicates:
                logger.info("[阶段 3/5] 检测重复文件...")
                dup_detector = DupDetector()
                dup_groups = dup_detector.detect(engine.root)
                result.duplicate_groups = len(dup_groups)

                if dup_groups:
                    for group in dup_groups:
                        if len(group) > 1:
                            for dup_node in group[1:]:
                                # 【QC修复】防御性类型兼容
                                if hasattr(dup_node, 'path'):
                                    node_path = dup_node.path
                                elif isinstance(dup_node, Path):
                                    node_path = dup_node
                                elif isinstance(dup_node, str):
                                    node_path = Path(dup_node)
                                else:
                                    continue
                                
                                skip_paths.add(node_path)

                    logger.info(
                        f"发现 {result.duplicate_groups} 组重复文件，"
                        f"将跳过 {len(skip_paths)} 个冗余副本。"
                    )
                else:
                    logger.info("[阶段 3/5] 未发现重复文件")
            else:
                logger.info("[阶段 3/5] 跳过重复检测")

            # ════════════════ 阶段 4：执行 ════════════════
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

                result.execution_summary = executor.execute_batch(
                    entries, source, target, skip_paths=skip_paths
                )
                result.execute_time_ms = (time.perf_counter() - execute_start) * 1000

                if result.execution_summary.failure_count > 0:
                    result.errors.append(
                        f"执行失败: {result.execution_summary.failure_count} 个文件"
                    )

            # ════════════════ 阶段 5：清理空壳 ════════════════
            if self.mode == "move" and not self.dry_run:
                logger.info("[阶段 5/5] 清理空壳目录...")
                cleaner = ShellCleaner(dry_run=False)
                cleaner.clean(source)
            else:
                logger.info("[阶段 5/5] 跳过空壳清理")

            # ════════════════ 生成报告 ════════════════
            # ══════════════════════════════════════════════════
            # 【新增】演练模式报告收纳箱
            # 如果是演练模式，将报告统一放入 "🖼️ 预运行结果" 子目录
            # 防止污染目标目录，方便用户查找
            # ══════════════════════════════════════════════════
            base_report_dir = output_dir or target
            if self.dry_run:
                report_dir = base_report_dir / "🖼️ 预运行结果"
                logger.info(f"演练模式：报告将写入子目录 {report_dir.name}")
            else:
                report_dir = base_report_dir

            self._write_reports(entries, source, target, report_dir, result)

            result.success = len(result.errors) == 0

        except Exception as exc:
            logger.exception(f"任务执行演练异常: {exc}")
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
        try:
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
        except Exception as exc:
            logger.warning(f"报告生成失败（不影响文件操作结果）: {exc}")
            result.warnings.append(f"报告生成失败: {exc}")


def create_scheduler(**kwargs) -> Scheduler:
    """创建调度器实例。"""
    return Scheduler(**kwargs)
