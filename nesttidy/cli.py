"""命令行界面：提供用户交互入口。"""
from __future__ import annotations
import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

from nesttidy import __version__
from nesttidy.runtime.config import load_config, Config
from nesttidy.runtime.logger import setup_logging, get_logger
from nesttidy.runtime.scheduler import Scheduler, TaskResult
from nesttidy.runtime.progress import ProgressReporter

logger = get_logger(__name__)

def create_parser() -> argparse.ArgumentParser:
    """创建命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="nesttidy",
        description="理巢 — 智能目录重构工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 演练模式（预览变更，不实际执行）
  nesttidy ./messy_folder ./clean_folder --dry-run

  # 复制模式（保留源文件）
  nesttidy ./messy_folder ./clean_folder --mode copy

  # 移动模式（同卷极速移动）
  nesttidy ./messy_folder ./clean_folder --mode move

  # 自动模式（同卷移动，跨卷复制）
  nesttidy ./messy_folder ./clean_folder --mode auto

  # 使用配置文件
  nesttidy ./messy_folder ./clean_folder --config nesttidy.toml
""",
    )

    # 位置参数
    parser.add_argument(
        "source", type=Path, help="源目录路径（待重构的目录）",
    )
    parser.add_argument(
        "target", type=Path, help="目标目录路径（重构结果输出目录）",
    )

    # 执行模式
    parser.add_argument(
        "-m", "--mode", choices=["copy", "move", "auto"], default="copy",
        help="执行模式: copy(复制), move(移动), auto(自动选择) [默认: copy]",
    )

    # 演练模式
    parser.add_argument(
        "-n", "--dry-run", action="store_true",
        help="演练模式：只预览变更，不实际执行",
    )

    # 并发设置
    parser.add_argument(
        "-j", "--jobs", type=int, default=4, metavar="N",
        help="并发线程数 [默认: 4]",
    )

    # 功能开关
    parser.add_argument(
        "--no-dup-detect", action="store_true", help="禁用重复文件检测",
    )
    parser.add_argument(
        "--force-cross-volume", action="store_true",
        help="强制允许跨卷移动（危险操作，需谨慎）",
    )

    # 输出设置
    parser.add_argument(
        "-o", "--output-dir", type=Path, metavar="DIR",
        help="报告输出目录 [默认: 目标目录]",
    )
    parser.add_argument(
        "-l", "--log-file", type=Path, metavar="FILE", help="日志文件路径",
    )
    parser.add_argument(
        "--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO", help="日志级别 [默认: INFO]",
    )

    # 配置文件
    parser.add_argument(
        "-c", "--config", type=Path, metavar="FILE",
        help="配置文件路径 (TOML/YAML)",
    )

    # 进度显示
    parser.add_argument(
        "--no-progress", action="store_true", help="禁用进度条显示",
    )

    # 版本信息
    parser.add_argument(
        "-v", "--version", action="version", version=f"%(prog)s {__version__}",
    )

    return parser


def validate_args(args: argparse.Namespace) -> Optional[str]:
    """验证命令行参数。"""
    # 终端报错允许绝对路径（仅限 stderr 即时反馈，不写入持久化日志）
    if not args.source.exists():
        return f"源目录不存在: {args.source}"
    if not args.source.is_dir():
        return f"源路径不是目录: {args.source}"

    if args.target.exists() and not args.target.is_dir():
        return f"目标路径已存在但不是目录: {args.target}"

    try:
        if args.source.resolve() == args.target.resolve():
            return "源目录和目标目录不能相同"
    except OSError:
        pass

    if args.jobs < 1:
        return "并发线程数必须大于 0"

    return None


def run(args: argparse.Namespace) -> int:
    """执行主逻辑。"""
    # 加载配置
    config = load_config(
        config_path=args.config,
        mode=args.mode,
        max_workers=args.jobs,
        detect_duplicates=not args.no_dup_detect,
        dry_run=args.dry_run,
        force_cross_volume=args.force_cross_volume,
        output_dir=args.output_dir,
        log_file=args.log_file,
        log_level=args.log_level,
        show_progress=not args.no_progress,
    )

    # 配置日志
    log_level = getattr(logging, config.log_level)
    setup_logging(level=log_level, log_file=config.log_file)

    # ══════════════════════════════════════════════════
    # 【QC修复】日志脱敏：持久化日志中严禁出现绝对路径
    # ══════════════════════════════════════════════════
    logger.info(f"理巢 v{__version__} 启动")
    logger.info(f"源目录: {args.source.name}")
    logger.info(f"目标目录: {args.target.name}")
    logger.info(f"执行模式: {config.mode}")
    
    if config.dry_run:
        logger.info("【演练模式】不会实际修改文件")

    # 创建调度器
    scheduler = Scheduler(
        mode=config.mode,
        max_workers=config.max_workers,
        detect_duplicates=config.detect_duplicates,
        dry_run=config.dry_run,
        force_cross_volume=config.force_cross_volume,
    )

    # 执行任务
    result = scheduler.run(
        source=args.source,
        target=args.target,
        output_dir=config.output_dir,
    )

    # 打印结果摘要 (终端 stdout 允许绝对路径以供用户确认)
    print_summary(result)

    return 0 if result.success else 1


def print_summary(result: TaskResult) -> None:
    """打印任务结果摘要。"""
    print()
    print("=" * 60)
    print("任务执行摘要")
    print("=" * 60)
    print()
    
    if result.success:
        print("✓ 任务执行成功")
    else:
        print("✗ 任务执行失败")
        
    print()
    print(f" 源目录: {result.source}")
    print(f" 目标目录: {result.target}")
    print(f" 执行模式: {result.mode}")
    print()
    print(" 文件统计:")
    print(f" 总文件数: {result.file_count:,}")
    print(f" 总大小: {format_size(result.total_bytes)}")
    
    if result.duplicate_groups > 0:
        print(f" 重复文件组: {result.duplicate_groups}")
        
    print()
    print(" 执行耗时:")
    print(f" 扫描: {result.scan_time_ms/1000:.2f}s")
    print(f" 分析: {result.analyze_time_ms/1000:.2f}s")
    
    if result.execution_summary:
        print(f" 执行: {result.execute_time_ms/1000:.2f}s")
        print()
        print(" 执行结果:")
        print(f" 成功: {result.execution_summary.success_count:,}")
        print(f" 失败: {result.execution_summary.failure_count:,}")
        if result.execution_summary.throughput_mbps > 0:
            print(f" 吞吐量: {result.execution_summary.throughput_mbps:.1f} MB/s")
    
    print(f" 总耗时: {result.total_time_ms/1000:.2f}s")

    if result.errors:
        print()
        print(" 错误信息:")
        for err in result.errors[:5]:
            print(f" - {err}")
        if len(result.errors) > 5:
            print(f" ... 还有 {len(result.errors) - 5} 个错误")

    if result.warnings:
        print()
        print(" 警告信息:")
        for warn in result.warnings[:5]:
            print(f" - {warn}")
        if len(result.warnings) > 5:
            print(f" ... 还有 {len(result.warnings) - 5} 个警告")
    print()


def format_size(size: int) -> str:
    """格式化文件大小。"""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def main() -> int:
    """命令行入口函数。"""
    parser = create_parser()
    args = parser.parse_args()

    error = validate_args(args)
    if error:
        print(f"错误: {error}", file=sys.stderr)
        return 1

    try:
        return run(args)
    except KeyboardInterrupt:
        print("\n操作已取消", file=sys.stderr)
        return 130
    except Exception as exc:
        logger.exception(f"未预期的错误: {exc}")
        print(f"错误: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
