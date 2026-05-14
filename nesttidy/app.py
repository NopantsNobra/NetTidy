from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import DEFAULT_HOST, DEFAULT_PORT
from .duplicate import detect_duplicates, write_duplicate_report
from .elevation import is_admin, relaunch_as_admin
from .executor import execute_plan
from .healthcheck import run_healthcheck
from .manual_pdf import write_manual_pdf
from .planner import build_plan
from .repair import repair_permissions, rows_as_dicts
from .recovery import rows_as_dicts as undo_rows_as_dicts
from .recovery import undo_transaction
from .reports import new_report_dir, write_plan_report
from .storage import list_history
from .web import serve


def main() -> None:
    parser = argparse.ArgumentParser(prog="nesttidy", description="理巢 NestTidy 本地目录整理工具")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
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
    undo_cmd = sub.add_parser("undo", help="根据 transaction_log.jsonl 尽力撤销 move 操作")
    undo_cmd.add_argument("transaction_log")

    sub.add_parser("health", help="运行启动健康检查")
    sub.add_parser("history", help="查看最近任务历史")

    dup_cmd = sub.add_parser("duplicates", help="重复文件诊断，只报告不删除")
    dup_cmd.add_argument("selected_root")
    dup_cmd.add_argument("--level", choices=["fast", "standard", "deep"], default="standard")
    dup_cmd.add_argument("--workers", type=int, default=4)
    dup_cmd.add_argument("--report-dir")

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
        rows = detect_duplicates(args.selected_root, args.level, args.workers)
        csv_path = write_duplicate_report(rows, report_dir / "duplicate_report.csv")
        print(json.dumps({"duplicate_rows": len(rows), "duplicate_report": str(csv_path)}, ensure_ascii=False, indent=2))
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
    serve(args.host, args.port)


if __name__ == "__main__":
    main()
