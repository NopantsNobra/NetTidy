from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
from pathlib import Path

from .config import DEFAULT_HOST, load_local_config
from .duplicate import PHASE1_METHOD, detect_duplicates, group_rows, summarize, write_duplicate_report
from .executor import DeleteEntry, delete_duplicates, execute_plan
from .healthcheck import run_healthcheck
from .manual_pdf import write_manual_pdf
from .planner import build_plan
from .repair import repair_permissions, rows_as_dicts
from .recovery import rows_as_dicts as undo_rows_as_dicts
from .recovery import undo_transaction
from .reports import new_report_dir, write_plan_report
from .storage import list_history
from .web import PortInUseError, serve

EXIT_PORT_IN_USE = 23


def is_admin() -> bool:
    """是否已具备管理员权限（提权只为绕过部分受保护目录的移动限制）。"""
    if os.name != "nt":
        return os.geteuid() == 0  # type: ignore[attr-defined]
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin(argv: list[str] | None = None) -> bool:
    args = argv if argv is not None else sys.argv
    params = subprocess.list2cmdline(args[1:])
    try:
        return ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1) > 32
    except Exception:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(prog="nesttidy", description="理巢 NestTidy 本地目录整理工具")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--open-browser", action="store_true", help="服务成功绑定后打开本机浏览器")
    parser.add_argument("--port", type=int, default=None, help="监听端口；缺省读 理巢配置.json，仍缺省为 8766")
    sub = parser.add_subparsers(dest="command")

    plan_cmd = sub.add_parser("plan", help="生成试运行计划并导出报告")
    plan_cmd.add_argument("selected_root")
    plan_cmd.add_argument("--mode", choices=["batch", "single"], default="batch")
    plan_cmd.add_argument("--output-root")
    plan_cmd.add_argument("--report-dir")

    copy_cmd = sub.add_parser("copy", help="按冻结计划执行 copy")
    copy_cmd.add_argument("selected_root")
    copy_cmd.add_argument("output_root")
    copy_cmd.add_argument("--mode", choices=["batch", "single"], default="batch")
    copy_cmd.add_argument("--report-dir")

    move_cmd = sub.add_parser("move", help="按冻结计划执行 move")
    move_cmd.add_argument("selected_root")
    move_cmd.add_argument("--mode", choices=["batch", "single"], default="batch")
    move_cmd.add_argument("--confirm", action="store_true")
    move_cmd.add_argument("--no-elevate", action="store_true", help="Windows 下不自动请求管理员权限")
    move_cmd.add_argument("--report-dir")
    undo_cmd = sub.add_parser("undo", help="根据事务日志撤销 move（原地整理）；回收站删除请在回收站里还原")
    undo_cmd.add_argument("transaction_log")

    sub.add_parser("health", help="运行启动健康检查")
    sub.add_parser("history", help="查看最近任务历史")

    dup_cmd = sub.add_parser("duplicates", help="重复文件诊断（第一遍初筛 + 自动第二遍全量哈希复核），默认不删除")
    dup_cmd.add_argument("selected_root")
    dup_cmd.add_argument("--level", choices=["fast", "standard", "deep"], default="standard")
    dup_cmd.add_argument("--workers", type=int, default=4)
    dup_cmd.add_argument("--no-verify", action="store_true", help="关闭第二遍全量哈希复核（默认开启）")
    dup_cmd.add_argument("--report-dir")

    dedupe_cmd = sub.add_parser("dedupe", help="删除重复副本：默认移入回收站（可在回收站还原），--mode purge 才彻底删除")
    dedupe_cmd.add_argument("selected_root")
    dedupe_cmd.add_argument("--level", choices=["fast", "standard", "deep"], default="standard")
    dedupe_cmd.add_argument("--groups", help="只处理这些组，逗号分隔，例如 dup-1,dup-3；留空表示全部已复核组")
    dedupe_cmd.add_argument("--mode", choices=["recycle", "purge"], default="recycle")
    dedupe_cmd.add_argument("--workers", type=int, default=4)
    dedupe_cmd.add_argument("--report-dir", help="删除日志落到这里（默认 理巢报告/ 下的新目录）")
    dedupe_cmd.add_argument("--confirm", action="store_true", help="确认执行删除（必须显式给出）")

    repair_cmd = sub.add_parser("repair", help="主动权限修复，默认不执行任何修复项")
    repair_cmd.add_argument("selected_root")
    repair_cmd.add_argument("--clear-readonly", action="store_true")
    repair_cmd.add_argument("--clear-hidden", action="store_true")
    repair_cmd.add_argument("--clear-zone-identifier", action="store_true")
    repair_cmd.add_argument("--grant-current-user", action="store_true")
    repair_cmd.add_argument("--fix-inheritance", action="store_true")

    manual_cmd = sub.add_parser("manual", help="生成 PDF 操作手册")
    manual_cmd.add_argument("--output", default="理巢_NestTidy_简要操作手册.pdf")

    args = parser.parse_args()
    if args.command == "health":
        for item in run_healthcheck(args.host):
            print(f"{'OK' if item.ok else 'FAIL'}\t{item.name}\t{item.detail}")
        return
    if args.command == "history":
        print(json.dumps(list_history(), ensure_ascii=False, indent=2))
        return
    if args.command == "duplicates":
        report_dir = Path(args.report_dir).resolve() if args.report_dir else new_report_dir()
        rows = detect_duplicates(args.selected_root, args.level, args.workers, verify=not args.no_verify)
        csv_path = write_duplicate_report(rows, report_dir / "duplicate_report.csv")
        groups = group_rows(rows)
        print(
            json.dumps(
                {
                    "phase1": PHASE1_METHOD[args.level],
                    "verify": not args.no_verify and args.level != "deep",
                    "stats": summarize(rows),
                    "groups": [
                        {
                            "group": group.duplicate_group_id,
                            "verified": group.verified,
                            "keep": group.keep_path,
                            "deletable": len(group.deletable_paths),
                            "reclaimable": group.reclaimable_bytes,
                        }
                        for group in groups
                    ],
                    "duplicate_report": str(csv_path),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.command == "dedupe":
        if not args.confirm:
            raise SystemExit("dedupe 会删除文件；请先跑 duplicates 看清结果，再加 --confirm 执行")
        rows = detect_duplicates(args.selected_root, args.level, workers=args.workers)
        groups = group_rows(rows)
        wanted = {item.strip() for item in args.groups.split(",") if item.strip()} if args.groups else None
        entries: list[DeleteEntry] = []
        for group in groups:
            if wanted is not None and group.duplicate_group_id not in wanted:
                continue
            for path in group.deletable_paths:
                entries.append(DeleteEntry(path=path, keep_path=group.keep_path, expected_sha256=group.verified_hash))
        if not entries:
            raise SystemExit("没有已复核通过、可删除的重复副本")
        result = delete_duplicates(
            args.selected_root,
            entries,
            mode=args.mode,
            confirm=args.confirm,
            transaction_log=str(Path(args.report_dir or new_report_dir()).resolve() / "delete_log.jsonl"),
        )
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return
    if args.command == "repair":
        rows = repair_permissions(
            args.selected_root,
            clear_readonly=args.clear_readonly,
            clear_hidden=args.clear_hidden,
            clear_zone_identifier=args.clear_zone_identifier,
            grant_current_user=args.grant_current_user,
            fix_inheritance=args.fix_inheritance,
        )
        print(json.dumps(rows_as_dicts(rows), ensure_ascii=False, indent=2))
        return
    if args.command == "undo":
        print(json.dumps(undo_rows_as_dicts(undo_transaction(args.transaction_log)), ensure_ascii=False, indent=2))
        return
    if args.command == "manual":
        print(json.dumps({"manual_pdf": str(write_manual_pdf(args.output))}, ensure_ascii=False, indent=2))
        return
    if args.command == "plan":
        plan = build_plan(args.selected_root, args.mode, args.output_root)
        report_dir = write_plan_report(plan, args.report_dir or new_report_dir())
        print(json.dumps({"plan_id": plan.plan_id, "report_dir": str(report_dir), "operations": len(plan.operations), "issues": len(plan.issues)}, ensure_ascii=False, indent=2))
        return
    if args.command == "copy":
        report_dir = Path(args.report_dir).resolve() if args.report_dir else new_report_dir()
        plan = build_plan(args.selected_root, args.mode, args.output_root)
        result = execute_plan(plan, "copy")
        write_plan_report(plan, report_dir)
        print(json.dumps({"report_dir": str(report_dir), **result.__dict__}, ensure_ascii=False, indent=2, default=str))
        return
    if args.command == "move":
        if not args.confirm:
            raise SystemExit("move 模式会修改源目录；请添加 --confirm 表示已查看预览并确认执行")
        if not args.no_elevate and not is_admin():
            if relaunch_as_admin():
                print("已请求管理员权限并重新启动执行器。")
                return
            print("未能请求管理员权限，将以当前权限继续执行。")
        report_dir = Path(args.report_dir).resolve() if args.report_dir else new_report_dir()
        plan = build_plan(args.selected_root, args.mode)
        result = execute_plan(plan, "move", confirm_move=True, transaction_log=str(report_dir / "transaction_log.jsonl"))
        write_plan_report(plan, report_dir)
        print(json.dumps({"report_dir": str(report_dir), **result.__dict__}, ensure_ascii=False, indent=2, default=str))
        return
    try:
        local_config = load_local_config()
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    try:
        port = args.port if args.port is not None else local_config.port
        if args.open_browser:
            serve(args.host, port, open_browser=True)
        else:
            serve(args.host, port)
    except PortInUseError as exc:
        print(f"理巢启动失败：{exc}", file=sys.stderr)
        raise SystemExit(EXIT_PORT_IN_USE) from exc
    except ValueError as exc:
        # --host 非 127.0.0.1 是明确拒绝，给一行人话，不要一屏 traceback；拒绝语义不变。
        raise SystemExit(f"理巢启动失败：{exc}") from exc


if __name__ == "__main__":
    main()
