# -*- coding: utf-8 -*-
"""
文件夹重组工具 V7.0 — 独立命令行入口
直接运行此文件即可启动命令行模式
"""

import argparse
import os
import sys

from . import 公共定义
from .公共定义 import _fmt_size
from .数据模型 import SafetyLevel
from .执行引擎 import FolderRestructureEngine, print_offline_report
from .扫描引擎 import _parse_actions
from .单元测试 import run_tests

# ============================================================
# CLI
# ============================================================

def _update_workers(n: int) -> None:
    """更新全局线程数配置"""
    公共定义.MAX_WORKERS = max(1, n)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=f"文件夹结构智能重组引擎 V{VERSION} (Windows + 离线分析 | 多线程加速)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python 文件夹重组工具V7.0.py D:\\Projects --diagnose
  python 文件夹重组工具V7.0.py D:\\Projects --preview
  python 文件夹重组工具V7.0.py D:\\Projects --execute
  python 文件夹重组工具V7.0.py --from-list list.txt
        """
    )

    parser.add_argument("target", nargs="?", help="目标目录路径 (在线模式)")
    parser.add_argument("--from-list", nargs="+", metavar="FILE", help="离线模式：从文件列表分析")
    parser.add_argument("--diagnose", action="store_true", help="仅诊断，不修改")
    parser.add_argument("--preview", action="store_true", help="分析 + 预览操作清单")
    parser.add_argument("--execute", action="store_true", help="执行操作")
    parser.add_argument("--yes", "-y", action="store_true", help="跳过确认")
    parser.add_argument("--detect-zone", action="store_true", help="扫描时检测 Zone.Identifier")
    parser.add_argument("--export", metavar="FILE", help="导出报告 (CSV/JSON)")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")
    parser.add_argument("--extra-patterns", nargs="+", help="额外的缓存匹配模式")
    parser.add_argument("--dedup-strategy", choices=["shallowest", "newest"], default="shallowest")
    parser.add_argument("--fix-attr", metavar="ACTIONS", help="修复权限/属性: perm,readonly,hidden,zone,all")
    parser.add_argument("--flatten", nargs="?", const="", metavar="TARGET_DIR", help="层级扁平化")
    parser.add_argument("--force-lock", action="store_true", help="强制清除锁文件")
    parser.add_argument("--threads", "-j", type=int, metavar="N", help=f"并行线程数 (默认: {MAX_WORKERS})")
    parser.add_argument("--test", action="store_true", help="运行内置单元测试")

    args = parser.parse_args()

    if args.threads and args.threads > 0:
        _update_workers(args.threads)

    # 单元测试模式
    if "--test" in sys.argv or args.test:
        success = run_tests()
        sys.exit(0 if success else 1)

    # 离线模式
    if args.from_list:
        parser_obj = FileListParser()
        all_lines = parser_obj.load_files(args.from_list)

        if not all_lines:
            print("❌ 没有有效的路径数据")
            sys.exit(1)

        nodes, root, parse_stats = parser_obj.parse_to_virtual_nodes(all_lines)
        
        diag = OfflineDiagnosticEngine(nodes, root)
        stats = diag.get_stats()

        elim = OfflineEliminationEngine(nodes)
        elim.mark_cache_dirs(extra_patterns=args.extra_patterns)
        cache_count = sum(1 for n in nodes if n.action == NodeAction.DELETE and "缓存" in n.reason)
        junk_count = sum(1 for n in nodes if n.action == NodeAction.DELETE and "临时文件" in n.reason)

        dup_count = elim.deduplicate_by_name()
        dup_groups = elim.duplicate_groups

        restruct = OfflineRestructureEngine(nodes, root)
        unpacked = restruct.unpack_redundant()

        flatten_count = 0
        if args.flatten is not None:
            flatten_count = sum(1 for n in nodes if not n.is_dir and n.action == NodeAction.NONE)
            print("  ⚠️  扁平化仅在线模式可执行")

        print_offline_report(stats, parse_stats, cache_count, junk_count,
                            dup_count, dup_groups, unpacked, nodes,
                            parser_obj.total_truncated, parser_obj.total_skipped,
                            flatten_count=flatten_count)

        if args.export:
            report = {
                "engine_version": VERSION,
                "mode": "offline",
                "stats": stats,
                "parse_stats": parse_stats,
                "operations": {"cache_cleanup": cache_count, "junk_cleanup": junk_count,
                              "duplicates": dup_count, "redundant_unpack": unpacked},
                "duplicate_groups": dup_groups[:50],
            }
            with open(args.export, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, ensure_ascii=False)
            print(f"\n📄 JSON 报告已导出: {args.export}")
        return

    # 在线模式 (仅 Windows)
    if not IS_WINDOWS:
        print("❌ 在线模式仅支持 Windows 系统")
        sys.exit(1)

    if not args.target:
        parser.print_help()
        sys.exit(1)

    if not os.path.isdir(args.target):
        print(f"❌ 不是有效目录: {args.target}")
        sys.exit(1)

    engine = FolderRestructureEngine(args.target)

    safe, msg = engine.run_safety_check()
    if not safe:
        print(f"❌ 操作被禁止: {msg}")
        sys.exit(1)

    # 锁管理
    lock = LockManager(args.target)
    lock_acquired = False
    if not args.diagnose:
        if not lock.acquire(force=args.force_lock):
            print("❌ 无法获取目录锁")
            sys.exit(1)
        lock_acquired = True
        import atexit
        atexit.register(lock.release)

    try:
        engine.diagnose(detect_zone=args.detect_zone, verbose=args.verbose)

        if args.export:
            if args.export.endswith(".csv"):
                engine.diagnostic.export_csv(args.export)
            else:
                engine.diagnostic.export_json(args.export)
            print(f"📄 报告已导出: {args.export}")

        if args.diagnose:
            return

        analysis = engine.analyze(
            cache_patterns=args.extra_patterns,
            dedup_strategy=args.dedup_strategy,
        )
        print(f"  缓存目录标记: {analysis['cache_marked']} 项")
        print(f"  重复文件标记: {analysis['duplicates_marked']} 项")
        print(f"  冗余结构解包: {analysis['redundant_unpacked']} 项")

        flatten_count = 0
        if args.flatten is not None:
            flatten_target = args.flatten if args.flatten else os.path.join(engine.target_path, "_flattened")
            print(f"\n📦 层级扁平化: {flatten_target}")
            flatten_engine = FlattenEngine(engine.nodes, engine.target_path, engine.errors)
            flatten_engine.extract_files(flatten_target)
            flatten_count = sum(1 for n in engine.nodes if n.action == NodeAction.MOVE and n.target_path)
            print(f"  扁平化文件: {flatten_count} 项")

        dup_groups = engine.get_duplicate_preview()
        if dup_groups:
            print(f"\n📋 重复文件清单 ({len(dup_groups)} 组):")
            for g in dup_groups[:20]:
                print(f"  组 {g['group_id']}: {g['size']}")
                print(f"    ✅ 保留: {g['keep']['path']} (深度 {g['keep']['depth']})")
                for d in g["delete"]:
                    print(f"    🗑️ 删除: {d['path']} (深度 {d['depth']})")

        perm_stats: Dict[str, int] = {}
        if args.fix_attr:
            perm_actions = _parse_actions(args.fix_attr)
            if perm_actions:
                perm_stats = engine.fix_permissions(perm_actions, dry_run=not args.execute)

        preview = engine.preview()
        if preview:
            print(f"\n📋 结构变更清单 ({len(preview)} 项):")
            print("-" * 65)
            for i, item in enumerate(preview[:50], 1):
                icon = {"DELETE": "🗑️", "MOVE": "📦", "RENAME": "✏️"}.get(item["action"], "❓")
                if item["action"] == "DELETE":
                    print(f"  {i:4d}. {icon} DELETE {item['source']} ({item['size']})")
                else:
                    print(f"  {i:4d}. {icon} {item['source']}")
                    print(f"         → {item['target']}")
            if len(preview) > 50:
                print(f"  ... 还有 {len(preview) - 50} 项")
            print("-" * 65)
        else:
            print("\n✅ 结构方面无需变更")

        if args.preview:
            print(f"\n💡 使用 --execute 参数执行实际操作")
            return

        if args.execute:
            total_ops = len(preview) + sum(v for k, v in perm_stats.items() if k not in ("failed", "skipped"))
            if not args.yes and total_ops > 0:
                confirm = input(f"\n⚠️  即将执行 {total_ops} 项操作。确认？(yes/no): ")
                if confirm.lower() not in ("yes", "y"):
                    print("❌ 已取消")
                    return

            engine.execute(dry_run=False)

    except KeyboardInterrupt:
        print("\n\n⚠️  用户中断")
    finally:
        if lock_acquired:
            lock.release()

