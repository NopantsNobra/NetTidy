"""
命令行接口模块
=============
职责：CLI 参数解析、主引擎编排

对外接口：
    - FolderRestructureEngine  主编排器
    - create_parser()          创建参数解析器
    - main()                   主入口

依赖关系：
    - 所有业务模块

版本历史：
    10.0: 最终审计版本，修正逻辑错误与文档一致性
    9.0: 现代化 CLI、类型注解完善
    8.0: 初始模块化版本
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Optional, Dict, Any

from .常量定义 import VERSION, DedupStrategy, FlattenMode
from .配置管理 import load_config, validate_config, apply_disk_tuning, runtime
from .诊断引擎 import DiagnosticEngine
from .剔除引擎 import EliminationEngine
from .扁平化引擎 import FlattenEngine
from .重组引擎 import RestructureEngine
from .执行引擎 import ExecutionEngine
from .权限引擎 import PermissionEngine
from .安全检查 import SafetyChecker, LockManager
from .报告生成 import HTMLReportGenerator
from .交互预览 import InteractivePreview
from .离线分析 import OfflineAnalyzer
from .工具函数 import _fmt_size


# ═══════════════════════════════════════════════════════════════════
# FolderRestructureEngine
# ═══════════════════════════════════════════════════════════════════

class FolderRestructureEngine:
    """
    文件夹重组引擎
    
    主编排器，协调各模块完成文件夹整理任务。
    
    使用示例：
        >>> engine = FolderRestructureEngine("/path/to/folder")
        >>> engine.scan()
        >>> engine.analyze()
        >>> engine.preview()
    """
    
    def __init__(self, root_path: str, config: Optional[Dict] = None):
        """
        初始化引擎
        
        Args:
            root_path: 目标目录
            config: 配置字典
        """
        self.root_path = os.path.abspath(root_path)
        self.config = config or {}
        
        # 子引擎
        self.diagnostic: Optional[DiagnosticEngine] = None
        self.elimination: Optional[EliminationEngine] = None
        self.flatten: Optional[FlattenEngine] = None
        self.restructure: Optional[RestructureEngine] = None
        self.execution: Optional[ExecutionEngine] = None
        self.permission: Optional[PermissionEngine] = None
        
        # 目录
        self.log_dir = os.path.join(self.root_path, ".restructure_logs")
        self.trash_dir = os.path.join(self.root_path, ".restructure_trash")
        
        # 统计
        self.stats: Dict[str, Any] = {}
    
    # ─────────────────────────────────────────────────────────────
    # 扫描
    # ─────────────────────────────────────────────────────────────
    
    def scan(self, detect_zone: bool = False, verbose: bool = False) -> Dict:
        """
        扫描目录
        
        Args:
            detect_zone: 是否检测 Zone.Identifier
            verbose: 是否详细输出
            
        Returns:
            扫描统计
        """
        print(f"\n📂 扫描目录: {self.root_path}")
        
        self.diagnostic = DiagnosticEngine(self.root_path)
        
        exclude_paths = set(self.config.get("exclude_paths", []))
        self.diagnostic.exclude_paths = exclude_paths
        
        self.diagnostic.scan(
            detect_zone=detect_zone,
            verbose=verbose,
        )
        
        self.stats["scan"] = self.diagnostic.get_stats()
        return self.stats["scan"]
    
    # ─────────────────────────────────────────────────────────────
    # 分析
    # ─────────────────────────────────────────────────────────────
    
    def analyze(
        self,
        mode: str = "all",
        dedup_strategy: str = "shallowest",
    ) -> Dict:
        """
        分析目录
        
        Args:
            mode: 分析模式 (all/cache/junk/dup)
            dedup_strategy: 去重策略
            
        Returns:
            分析结果
        """
        if not self.diagnostic:
            print("❌ 请先扫描目录")
            return {}
        
        self.elimination = EliminationEngine(
            self.diagnostic.nodes,
            self.config,
        )
        
        results = {}
        
        if mode in ("all", "cache"):
            results["cache"] = self.elimination.mark_cache_dirs()
        
        if mode in ("all", "junk"):
            results["junk"] = self.elimination.mark_junk_files()
        
        if mode in ("all", "dup"):
            results["dup"] = self.elimination.deduplicate(dedup_strategy)
        
        self.stats["analyze"] = results
        return results
    
    # ─────────────────────────────────────────────────────────────
    # 扁平化
    # ─────────────────────────────────────────────────────────────
    
    def flatten(
        self,
        target_dir: Optional[str] = None,
        mode: str = "single",
        extensions: Optional[set] = None,
    ) -> Dict:
        """
        扁平化目录
        
        Args:
            target_dir: 目标目录
            mode: 分组模式
            extensions: 处理的扩展名
            
        Returns:
            统计结果
        """
        if not self.diagnostic:
            print("❌ 请先扫描目录")
            return {}
        
        if target_dir is None:
            target_dir = os.path.join(self.root_path, "_flattened")
        
        self.flatten = FlattenEngine(
            self.diagnostic.nodes,
            self.root_path,
            self.config,
        )
        
        result = self.flatten.run(
            target_dir=target_dir,
            mode=mode,
            extensions=extensions,
        )
        
        self.stats["flatten"] = result
        return result
    
    # ─────────────────────────────────────────────────────────────
    # 执行
    # ─────────────────────────────────────────────────────────────
    
    def execute(
        self,
        dry_run: bool = True,
        use_recycle_bin: bool = False,
    ) -> Dict:
        """
        执行操作
        
        Args:
            dry_run: 是否模拟运行
            use_recycle_bin: 是否使用系统回收站
            
        Returns:
            执行统计
        """
        if not self.diagnostic:
            print("❌ 请先扫描目录")
            return {}
        
        runtime.dry_run = dry_run
        runtime.use_recycle_bin = use_recycle_bin
        
        self.execution = ExecutionEngine(
            self.diagnostic.nodes,
            self.root_path,
            self.log_dir,
            self.trash_dir,
        )
        
        if self.diagnostic._scan_timestamp:
            self.execution.set_scan_timestamp(self.diagnostic._scan_timestamp)
        
        result = self.execution.execute(dry_run=dry_run)
        
        # 生成回滚脚本
        if not dry_run:
            self.execution.save_rollback_script()
        
        self.stats["execution"] = result
        return result
    
    # ─────────────────────────────────────────────────────────────
    # 预览
    # ─────────────────────────────────────────────────────────────
    
    def preview(self) -> None:
        """显示操作预览"""
        if not self.diagnostic:
            print("❌ 请先扫描目录")
            return
        
        preview = InteractivePreview(self.diagnostic.nodes)
        preview.preview()
        preview.show_summary()
    
    # ─────────────────────────────────────────────────────────────
    # 报告
    # ─────────────────────────────────────────────────────────────
    
    def generate_report(self, output_path: Optional[str] = None) -> str:
        """
        生成报告
        
        Args:
            output_path: 输出路径
            
        Returns:
            报告路径
        """
        if not self.diagnostic:
            print("❌ 请先扫描目录")
            return ""
        
        if output_path is None:
            output_path = os.path.join(
                self.log_dir,
                f"report_{int(time.time())}.html"
            )
        
        # 计算释放空间
        freed_bytes = 0
        if self.elimination:
            freed_bytes = (
                sum(n.size_bytes for n in self.diagnostic.nodes
                    if n.action.value in ("DELETE", "HARDLINK"))
            )
        
        # 获取重复文件组
        dup_groups = []
        if self.elimination:
            dup_groups = self.elimination.get_duplicate_groups()
        
        generator = HTMLReportGenerator()
        generator.generate(
            stats=self.stats.get("scan", {}),
            nodes=self.diagnostic.nodes,
            dup_groups=dup_groups,
            freed_bytes=freed_bytes,
            output_path=output_path,
        )
        
        return output_path
    
    # ─────────────────────────────────────────────────────────────
    # 完整流程
    # ─────────────────────────────────────────────────────────────
    
    def run_full(
        self,
        clean_cache: bool = True,
        clean_junk: bool = True,
        dedup: bool = True,
        flatten_mode: Optional[str] = None,
        dedup_strategy: str = "shallowest",
        dry_run: bool = True,
        use_recycle_bin: bool = False,
    ) -> Dict:
        """
        运行完整流程
        
        Args:
            clean_cache: 是否清理缓存
            clean_junk: 是否清理垃圾
            dedup: 是否去重
            flatten_mode: 扁平化模式
            dedup_strategy: 去重策略
            dry_run: 是否模拟运行
            use_recycle_bin: 是否使用回收站
            
        Returns:
            完整统计
        """
        # 扫描
        self.scan()
        
        # 分析
        mode = "all" if (clean_cache or clean_junk or dedup) else "none"
        if mode != "none":
            self.analyze(mode=mode, dedup_strategy=dedup_strategy)
        
        # 扁平化
        if flatten_mode:
            self.flatten(mode=flatten_mode)
        
        # 预览
        self.preview()
        
        # 执行
        self.execute(dry_run=dry_run, use_recycle_bin=use_recycle_bin)
        
        return self.stats


# ═══════════════════════════════════════════════════════════════════
# CLI 解析
# ═══════════════════════════════════════════════════════════════════

def create_parser() -> argparse.ArgumentParser:
    """创建命令行参数解析器"""
    parser = argparse.ArgumentParser(
        prog="文件夹整理工具",
        description=f"模块化文件夹结构智能重组引擎 V{VERSION}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s /path/to/folder --diagnose          # 诊断
  %(prog)s /path/to/folder --preview            # 预览
  %(prog)s /path/to/folder --execute           # 执行
  %(prog)s /path/to/folder --full             # 完整流程
  %(prog)s /path/to/folder --full --yes       # 自动确认
        """,
    )
    
    # 位置参数
    parser.add_argument(
        "path", nargs="?", default=".",
        help="目标目录路径（默认当前目录）"
    )
    
    # 操作模式
    group = parser.add_argument_group("操作模式")
    group.add_argument("--diagnose", "-d", action="store_true", help="诊断模式")
    group.add_argument("--preview", "-p", action="store_true", help="预览模式")
    group.add_argument("--execute", "-e", action="store_true", help="执行模式")
    group.add_argument("--full", "-f", action="store_true", help="完整流程")
    group.add_argument("--yes", "-y", action="store_true", help="跳过确认")
    
    # 精细控制
    group = parser.add_argument_group("精细控制")
    group.add_argument("--only-cache", action="store_true", help="仅清理缓存")
    group.add_argument("--only-junk", action="store_true", help="仅清理垃圾")
    group.add_argument("--only-dedup", action="store_true", help="仅去重")
    group.add_argument("--only-flatten", action="store_true", help="仅扁平化")
    group.add_argument("--only-perm", action="store_true", help="仅权限修复")
    
    # 配置
    group = parser.add_argument_group("配置")
    group.add_argument("--config", "-c", metavar="FILE", help="配置文件")
    group.add_argument("--init-config", action="store_true", help="生成默认配置")
    group.add_argument("--dedup-strategy", choices=["shallowest", "newest", "largest", "hardlink"],
                       default="shallowest", help="去重策略")
    group.add_argument("--flatten-by", choices=["single", "ext", "date", "prefix", "size"],
                       help="扁平化模式")
    
    # 诊断选项
    group = parser.add_argument_group("诊断选项")
    group.add_argument("--detect-zone", action="store_true", help="检测 Zone.Identifier")
    group.add_argument("--export", metavar="FILE", help="导出扫描结果")
    group.add_argument("--html-report", metavar="FILE", help="生成 HTML 报告")
    group.add_argument("--verbose", "-v", action="store_true", help="详细输出")
    
    # 执行选项
    group = parser.add_argument_group("执行选项")
    group.add_argument("--use-recycle-bin", action="store_true", help="使用系统回收站")
    group.add_argument("--dry-run", action="store_true", default=True, help="模拟运行")
    group.add_argument("--no-dry-run", dest="dry_run", action="store_false", help="实际执行")
    
    # 其他
    group = parser.add_argument_group("其他")
    group.add_argument("--threads", "-j", type=int, help="线程数")
    group.add_argument("--from-list", metavar="FILE", help="离线模式：从文件列表分析")
    group.add_argument("--test", action="store_true", help="运行测试")
    group.add_argument("--version", action="version", version=f"%(prog)s V{VERSION}")
    
    return parser


# ═══════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════

def main():
    """主入口函数"""
    parser = create_parser()
    args = parser.parse_args()
    
    # 打印版本
    print(f"\n{'='*55}")
    print(f"  📁 文件夹整理工具 V{VERSION}")
    print(f"{'='*55}")
    
    # 运行测试
    if args.test:
        from .单元测试 import run_tests
        success = run_tests()
        sys.exit(0 if success else 1)
    
    # 生成默认配置
    if args.init_config:
        from .配置管理 import get_default_config_template, save_config
        config = get_default_config_template()
        save_config(config, "restructure_config.json")
        print("✅ 配置模板已生成: restructure_config.json")
        return
    
    # 离线模式
    if args.from_list:
        analyzer = OfflineAnalyzer()
        analyzer.load_from_list(args.from_list)
        analyzer.analyze()
        
        if args.html_report:
            analyzer.generate_html_report(args.html_report)
        
        if args.export:
            analyzer.generate_report(args.export)
        return
    
    # 在线模式
    if not os.path.isdir(args.path):
        print(f"❌ 不是有效目录: {args.path}")
        sys.exit(1)
    
    # 加载配置
    config = load_config(args.config)
    
    # 应用磁盘调优
    apply_disk_tuning(args.path, config)
    
    # 手动线程数
    if args.threads:
        from .配置管理 import update_workers
        update_workers(args.threads)
    
    # 安全检查
    from .常量定义 import SafetyLevel
    level, msg = SafetyChecker.check(args.path)
    
    print(f"\n🔒 安全检查: {msg}")
    
    if level == SafetyLevel.BLOCKED:
        print("❌ 操作被禁止")
        sys.exit(1)
    
    if level == SafetyLevel.RESTRICTED:
        print("⚠️ 受限模式，仅预览")
    
    # 创建引擎
    engine = FolderRestructureEngine(args.path, config)
    
    try:
        # 确定运行模式
        run_mode = "full" if args.full else (
            "diagnose" if args.diagnose else (
                "preview" if args.preview else "execute" if args.execute else "diagnose"
            )
        )
        
        # 执行操作
        if run_mode == "diagnose":
            engine.scan(detect_zone=args.detect_zone, verbose=args.verbose)
            
            stats = engine.stats.get("scan", {})
            print(f"\n📊 统计:")
            print(f"  文件: {stats.get('total_files', 0):,}")
            print(f"  目录: {stats.get('total_dirs', 0):,}")
            print(f"  大小: {stats.get('total_size_str', '?')}")
            if stats.get('hidden_count', 0) > 0:
                print(f"  隐藏: {stats['hidden_count']}")
            if stats.get('readonly_count', 0) > 0:
                print(f"  只读: {stats['readonly_count']}")
            if stats.get('symlink_count', 0) > 0:
                print(f"  符号链接: {stats['symlink_count']}")
            if stats.get('junction_count', 0) > 0:
                print(f"  Junction: {stats['junction_count']}")
            if stats.get('hardlink_count', 0) > 0:
                print(f"  硬链接: {stats['hardlink_count']}")
            if stats.get('zone_count', 0) > 0:
                print(f"  🌐 Zone.Identifier: {stats['zone_count']}")
            if stats.get('long_path_count', 0) > 0:
                print(f"  ⚠️ 超长路径: {stats['long_path_count']}")
        
        elif run_mode in ("preview", "execute", "full"):
            # 确定分析模式
            if args.only_cache:
                analyze_mode = "cache"
            elif args.only_junk:
                analyze_mode = "junk"
            elif args.only_dedup:
                analyze_mode = "dup"
            elif args.only_flatten:
                analyze_mode = "none"  # 仅扁平化，跳过分析
            else:
                analyze_mode = "all"
            
            # 扫描
            engine.scan(detect_zone=args.detect_zone, verbose=args.verbose)
            
            # 分析
            if args.only_perm or analyze_mode == "none":
                pass  # 跳过分析
            else:
                engine.analyze(mode=analyze_mode, dedup_strategy=args.dedup_strategy)
            
            # 扁平化
            if args.flatten_by or args.only_flatten:
                mode = args.flatten_by or "single"
                engine.flatten(mode=mode)
            
            # 预览
            if run_mode == "preview" or args.preview:
                engine.preview()
                
                if args.html_report:
                    path = engine.generate_report(args.html_report)
                    print(f"\n📊 HTML 报告: {path}")
            
            # 执行
            if run_mode == "execute" or run_mode == "full":
                dry_run = args.dry_run and not args.execute
                
                if not args.yes and not dry_run:
                    response = input("\n确认执行? [y/N]: ").strip().lower()
                    if response not in ('y', 'yes'):
                        print("❌ 已取消")
                        return
                
                engine.execute(dry_run=dry_run, use_recycle_bin=args.use_recycle_bin)
        
        # 导出
        if args.export and engine.diagnostic:
            success = engine.diagnostic.export_json(args.export)
            if success:
                print(f"\n📤 已导出: {args.export}")
        
        # HTML 报告
        if args.html_report and run_mode != "preview":
            path = engine.generate_report(args.html_report)
            print(f"\n📊 HTML 报告: {path}")
        
        print("\n✅ 完成")
    
    except KeyboardInterrupt:
        print("\n\n⚠️ 操作已取消")
        sys.exit(1)
    
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
