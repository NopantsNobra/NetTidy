from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import DEFAULT_HOST, DEFAULT_PORT
from .executor import execute_plan
from .healthcheck import run_healthcheck
from .planner import build_plan
from .reports import new_report_dir, write_plan_report
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
    move_cmd.add_argument("--report-dir")

    sub.add_parser("health", help="运行启动健康检查")

    args = parser.parse_args()
    if args.command == "health":
        for item in run_healthcheck(args.host):
            print(f"{'OK' if item.ok else 'FAIL'}\t{item.name}\t{item.detail}")
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
        report_dir = Path(args.report_dir).resolve() if args.report_dir else new_report_dir()
        plan = build_plan(args.selected_root, args.mode)
        result = execute_plan(plan, "move", confirm_move=True, transaction_log=str(report_dir / "transaction_log.jsonl"))
        write_plan_report(plan, report_dir)
        print(json.dumps({"report_dir": str(report_dir), **result.__dict__}, ensure_ascii=False, indent=2, default=str))
        return
    serve(args.host, args.port)


if __name__ == "__main__":
    main()

