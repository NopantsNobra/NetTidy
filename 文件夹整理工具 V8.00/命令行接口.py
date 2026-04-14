"""
命令行接口模块
CLI 解析 + 主编排器
"""

import argparse
import json
import os
import sys
from datetime import datetime

from .常量定义 import VERSION, NodeAction, PermissionAction
from .配置管理 import load_config, detect_disk_type, update_workers, runtime
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


# ═══════════════════════════════════════════════════════════
# FolderRestructureEngine 主编排器
# ═══════════════════════════════════════════════════════════

class FolderRestructureEngine:
    """
    文件夹重组引擎
    主编排器，协调各模块完成文件夹整理任务
    """
    
    def __init__(self, root_path: str, config: dict = None):
        """
        初始化重组引擎
        
        Args:
            root_path: 根目录路径
            config: 配置字典
        """
        self.root_path = os.path.abspath(root_path)
        self.config = config or {}
        
        # 初始化各引擎
        self.diagnostic = None
        self.elimination = None
        self.flatten = None
        self.restructure = None
        self.execution = None
        self.permission = None
        
        # 日志和回收站目录
        self.log_dir = os.path.join(self.root_path, ".restructure_logs")
        self.trash_dir = os.path.join(self.root_path, ".restructure_trash")
        
        # 统计
        self.stats = {}
    
    def scan(self, detect_zone: bool = False, verbose: bool = False) -> dict:
        """
        扫描目录
        
        Args:
            detect_zone: 是否检测 Zone.Identifier
            verbose: 是否显示详细信息
            
        Returns:
            扫描统计
        """
        print(f"\n📂 扫描目录: {self.root_path}")
        
        self.diagnostic = DiagnosticEngine(self.root_path)
        self.diagnostic.scan(detect_zone=detect_zone, verbose=verbose, config=self.config)
        
        self.stats["scan"] = self.diagnostic.get_stats()
        return self.stats["scan"]
    
    def analyze(self, mode: str = "all") -> dict:
        """
        分析目录
        
        Args:
            mode: 分析模式
            
        Returns:
            分析结果
        """
        if not self.diagnostic or not self.diagnostic.nodes:
            print("❌ 请先扫描目录")
            return {}
        
        nodes = self.diagnostic.nodes
        
        # 初始化剔除引擎
        self.elimination = EliminationEngine(nodes, self.config)
        
        results = {}
        
        if mode in ("cache", "all"):
            results["cache"] = self.elimination.mark_cache_dirs()
        
        if mode in ("junk", "all"):
            results["junk"] = self.elimination.mark_junk_files()
        
        if mode in ("dup", "all"):
            results["dup"] = self.elimination.deduplicate()
        
        self.stats["analyze"] = results
        return results
    
    def flatten(self, unpack: bool = True, merge_empty: bool = True) -> dict:
        """
        扁平化目录
        
        Args:
            unpack: 是否解包单子目录
            merge_empty: 是否合并空目录
            
        Returns:
            扁平化统计
        """
        if not self.diagnostic or not self.diagnostic.nodes:
            print("❌ 请先扫描目录")
            return {}
        
        self.flatten = FlattenEngine(self.diagnostic.nodes, self.root_path)
        result = self.flatten.run(unpack=unpack, merge_empty=merge_empty)
        
        self.stats["flatten"] = result
        return result
    
    def restructure(self, mode: str = "type", **kwargs) -> dict:
        """
        重组目录
        
        Args:
            mode: 重组模式
            **kwargs: 额外参数
            
        Returns:
            重组统计
        """
        if not self.diagnostic or not self.diagnostic.nodes:
            print("❌ 请先扫描目录")
            return {}
        
        self.restructure = RestructureEngine(
            self.diagnostic.nodes, self.root_path, self.config
        )
        result = self.restructure.run(mode=mode, **kwargs)
        
        self.stats["restructure"] = result
        return result
    
    def fix_permissions(self, actions: set = None, dry_run: bool = True) -> dict:
        """
        修复权限
        
        Args:
            actions: 要执行的操作集合
            dry_run: 是否为模拟运行
            
        Returns:
            权限修复统计
        """
        if not self.diagnostic or not self.diagnostic.nodes:
            print("❌ 请先扫描目录")
            return {}
        
        actions = actions or {PermissionAction.ALL}
        
        self.permission = PermissionEngine(self.diagnostic.nodes)
        result = self.permission.apply_all(actions, dry_run=dry_run)
        
        self.stats["permissions"] = result
        return result
    
    def execute(self, dry_run: bool = True, use_recycle_bin: bool = False) -> dict:
        """
        执行操作
        
        Args:
            dry_run: 是否为模拟运行
            use_recycle_bin: 是否使用系统回收站
            
        Returns:
            执行统计
        """
        if not self.diagnostic or not self.diagnostic.nodes:
            print("❌ 请先扫描目录")
            return {}
        
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
        
        self.stats["execution"] = result
        return result
    
    def generate_report(self, output_path: str = None) -> str:
        """
        生成报告
        
        Args:
            output_path: 输出文件路径
            
        Returns:
            报告路径
        """
        if not self.diagnostic:
            print("❌ 请先扫描目录")
            return ""
        
        output_path = output_path or os.path.join(
            self.log_dir,
            f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        )
        
        # 计算释放空间
        freed_bytes = 0
        if self.elimination:
            freed_bytes = self.elimination.get_stats().get("delete_size", 0)
        
        # 获取重复文件组
        dup_groups = []
        if self.elimination:
            dup_groups = self.elimination.get_duplicate_groups()
        
        generator = HTMLReportGenerator()
        success = generator.generate(
            stats=self.stats.get("scan", {}),
            nodes=self.diagnostic.nodes,
            dup_groups=dup_groups,
            freed_bytes=freed_bytes,
            output_path=output_path,
        )
        
        if success:
            print(f"📊 报告已生成: {output_path}")
            return output_path
        
        return ""
    
    def preview(self) -> None:
        """显示操作预览"""
        if not self.diagnostic or not self.diagnostic.nodes:
            print("❌ 请先扫描目录")
            return
        
        preview = InteractivePreview(self.diagnostic.nodes)
        preview.preview()
        preview.show_summary()
    
    def run_full(
        self,
        clean_cache: bool = True,
        clean_junk: bool = True,
        dedup: bool = True,
        flatten: bool = False,
        restructure_mode: str = None,
        dry_run: bool = True,
        use_recycle_bin: bool = False,
    ) -> dict:
        """
        运行完整流程
        
        Args:
            clean_cache: 是否清理缓存
            clean_junk: 是否清理垃圾文件
            dedup: 是否去重
            flatten: 是否扁平化
            restructure_mode: 重组模式
            dry_run: 是否为模拟运行
            use_recycle_bin: 是否使用系统回收站
            
        Returns:
            完整统计
        """
        # 扫描
        self.scan()
        
        # 分析
        self.analyze(
            mode="all" if (clean_cache or clean_junk or dedup) else "none"
        )
        
        # 扁平化
        if flatten:
            self.flatten()
        
        # 重组
        if restructure_mode:
            self.restructure(mode=restructure_mode)
        
        # 预览
        self.preview()
        
        # 执行
        self.execute(dry_run=dry_run, use_recycle_bin=use_recycle_bin)
        
        # 生成报告
        self.generate_report()
        
        return self.stats


# ═══════════════════════════════════════════════════════════
# CLI 解析
# ═══════════════════════════════════════════════════════════

def create_parser() -> argparse.ArgumentParser:
    """创建命令行参数解析器"""
    parser = argparse.ArgumentParser(
        prog="文件夹整理工具",
        description=f"模块化文件夹结构智能重组引擎 V{VERSION}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s /path/to/folder --scan              # 扫描目录
  %(prog)s /path/to/folder --analyze           # 分析目录
  %(prog)s /path/to/folder --execute           # 执行操作
  %(prog)s /path/to/folder --full              # 完整流程
  %(prog)s /path/to/folder --full --yes        # 完整流程（自动确认）
        """,
    )
    
    # 位置参数
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="目标目录路径（默认当前目录）",
    )
    
    # 操作选项
    parser.add_argument(
        "--scan", "-s",
        action="store_true",
        help="扫描目录",
    )
    parser.add_argument(
        "--analyze", "-a",
        action="store_true",
        help="分析目录（缓存、垃圾、重复）",
    )
    parser.add_argument(
        "--execute", "-e",
        action="store_true",
        help="执行操作",
    )
    parser.add_argument(
        "--full", "-f",
        action="store_true",
        help="运行完整流程",
    )
    
    # 分析选项
    parser.add_argument(
        "--cache",
        action="store_true",
        help="清理缓存目录",
    )
    parser.add_argument(
        "--junk",
        action="store_true",
        help="清理垃圾文件",
    )
    parser.add_argument(
        "--dedup",
        action="store_true",
        help="重复文件去重",
    )
    
    # 重组选项
    parser.add_argument(
        "--flatten",
        action="store_true",
        help="扁平化目录结构",
    )
    parser.add_argument(
        "--restructure",
        choices=["extension", "type", "date", "size"],
        help="重组模式",
    )
    
    # 执行选项
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="自动确认（跳过交互确认）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="模拟运行（不实际执行）",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_false",
        dest="dry_run",
        help="实际执行操作",
    )
    parser.add_argument(
        "--recycle-bin",
        action="store_true",
        help="使用系统回收站",
    )
    
    # 输出选项
    parser.add_argument(
        "--report", "-r",
        metavar="PATH",
        help="报告输出路径",
    )
    parser.add_argument(
        "--export",
        metavar="PATH",
        help="导出扫描结果",
    )
    parser.add_argument(
        "--config", "-c",
        metavar="PATH",
        help="配置文件路径",
    )
    
    # 其他选项
    parser.add_argument(
        "--threads", "-t",
        type=int,
        help="工作线程数",
    )
    parser.add_argument(
        "--zone",
        action="store_true",
        help="检测 Zone.Identifier",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="详细输出",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s V{VERSION}",
    )
    
    return parser


def main():
    """主入口"""
    parser = create_parser()
    args = parser.parse_args()
    
    # 显示版本
    print(f"\n{'='*55}")
    print(f"  文件夹整理工具 V{VERSION}")
    print(f"{'='*55}")
    
    # 加载配置
    config = load_config(args.config)
    
    # 检测磁盘类型
    detect_disk_type(args.path, config, args.threads)
    
    # 检查安全级别
    from .安全检查 import SafetyLevel
    level, msg = SafetyChecker.check(args.path)
    if level == SafetyLevel.BLOCKED:
        print(f"❌ 安全检查失败: {msg}")
        sys.exit(1)
    elif level == SafetyLevel.RESTRICTED:
        print(f"⚠️ 安全警告: {msg}")
    
    # 创建引擎
    engine = FolderRestructureEngine(args.path, config)
    
    try:
        # 完整流程
        if args.full:
            engine.run_full(
                clean_cache=args.cache or True,
                clean_junk=args.junk or True,
                dedup=args.dedup or True,
                flatten=args.flatten,
                restructure_mode=args.restructure,
                dry_run=args.dry_run,
                use_recycle_bin=args.recycle_bin,
            )
        
        # 单独操作
        elif args.scan:
            engine.scan(detect_zone=args.zone, verbose=args.verbose)
            stats = engine.stats.get("scan", {})
            print(f"\n📊 扫描完成: {stats.get('total_files', 0)} 文件, "
                  f"{stats.get('total_dirs', 0)} 目录, "
                  f"{_fmt_size(stats.get('total_size', 0))}")
        
        elif args.analyze:
            engine.scan(detect_zone=args.zone)
            engine.analyze()
            engine.preview()
        
        elif args.execute:
            engine.scan(detect_zone=args.zone)
            engine.analyze()
            engine.preview()
            
            if args.yes or input("\n确认执行? [y/N]: ").lower() == 'y':
                engine.execute(dry_run=args.dry_run, use_recycle_bin=args.recycle_bin)
        
        # 默认：扫描并显示统计
        else:
            engine.scan(detect_zone=args.zone, verbose=args.verbose)
            stats = engine.stats.get("scan", {})
            
            print(f"\n📊 目录统计:")
            print(f"  文件: {stats.get('total_files', 0)}")
            print(f"  目录: {stats.get('total_dirs', 0)}")
            print(f"  大小: {_fmt_size(stats.get('total_size', 0))}")
            print(f"  最大深度: {stats.get('max_depth', 0)}")
        
        # 导出扫描结果
        if args.export and engine.diagnostic:
            engine.diagnostic.export_json(args.export)
            print(f"📤 扫描结果已导出: {args.export}")
        
        # 生成报告
        if args.report:
            engine.generate_report(args.report)
    
    except KeyboardInterrupt:
        print("\n\n⚠️ 操作已取消")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)
    
    print("\n✅ 完成")


if __name__ == "__main__":
    main()
