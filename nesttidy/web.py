from __future__ import annotations

import errno
import json
import socket
import subprocess
import threading
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import REPORT_DIR_NAME
from .duplicate import PHASE1_METHOD, detect_duplicates, group_rows, summarize
from .executor import DeleteEntry, delete_duplicates, execute_plan
from .healthcheck import run_healthcheck
from .manual_pdf import write_manual_pdf
from .model import OperationPlan
from .planner import build_plan
from .recovery import rows_as_dicts, undo_transaction
from .repair import repair_permissions
from .reports import plan_display_stats, write_plan_report
from .storage import list_history
from .ui import INDEX_HTML


class PlanRegistry:
    """按 plan_id 索引冻结计划：多标签页各持自己的 id，服务端不信任前端回传的计划对象（不变量 6）。"""

    def __init__(self, max_plans: int = 16) -> None:
        self._plans: dict[str, OperationPlan] = {}
        self._lock = threading.Lock()
        self._max = max_plans

    def put(self, plan: OperationPlan) -> str:
        with self._lock:
            self._plans[plan.plan_id] = plan
            while len(self._plans) > self._max:
                self._plans.pop(next(iter(self._plans)))
        return plan.plan_id

    def get(self, plan_id: str) -> OperationPlan:
        with self._lock:
            plan = self._plans.get(plan_id)
        if plan is None:
            raise ValueError("计划不存在或已被淘汰，请重新生成试运行计划")
        return plan

    def clear(self) -> None:
        with self._lock:
            self._plans.clear()


PLANS = PlanRegistry()

PROGRESS: dict[str, dict] = {}


def _track(body: dict):
    """progress_key → 旁路进度槽；执行接口本身仍是同步的。"""
    key = str(body.get("progress_key") or "")
    if not key:
        return "", None
    while len(PROGRESS) > 64:
        PROGRESS.pop(next(iter(PROGRESS)))

    def report(done: int, total: int, label: str = "") -> None:
        PROGRESS[key] = {"done": done, "total": total, "label": label}

    PROGRESS[key] = {"done": 0, "total": 0, "label": "正在准备"}
    return key, report


def _plan_id(body: dict) -> str:
    plan_id = body.get("plan_id")
    if not isinstance(plan_id, str) or not plan_id:
        raise ValueError("缺少 plan_id，请重新生成试运行计划")
    return plan_id


def _selected_root(body: dict) -> str:
    """需要源目录的接口统一入口：空/空白路径必须显式报错。

    历史 bug：Path('').resolve() 会落到进程 CWD，空路径曾被静默当成
    「扫描理巢程序自己的目录」并生成可执行计划（v1.4.0 修复）。
    """
    raw = body.get("selected_root")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("请先填写源目录（当前为空）")
    return raw


def _existing_root(body: dict) -> str:
    """在 _selected_root 基础上要求目录真实存在（repair/duplicates/delete）。"""
    root = _selected_root(body)
    if not Path(root).is_dir():
        raise ValueError(f"目录不存在或不是文件夹: {root}")
    return root


def _delete_entries(body: dict) -> list[DeleteEntry]:
    raw = body.get("entries")
    if not isinstance(raw, list) or not raw:
        raise ValueError("没有要删除的文件（entries 为空）")
    entries: list[DeleteEntry] = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("path") or not item.get("keep_path"):
            raise ValueError("每条删除请求都必须带 path 与 keep_path")
        entries.append(
            DeleteEntry(
                path=str(item["path"]),
                keep_path=str(item["keep_path"]),
                expected_sha256=str(item.get("expected_sha256") or ""),
            )
        )
    return entries


def _move_log_path(plan: OperationPlan) -> Path:
    """move 的事务日志与隔离区同级：选中目录的上一级 / 理巢报告 / 事务日志_<时间>.jsonl。"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = Path(plan.selected_root).resolve()
    return base.parent / REPORT_DIR_NAME / f"事务日志_{stamp}.jsonl"


def reveal_in_explorer(raw: str, select: bool) -> str:
    """在资源管理器里定位：select=True 选中这个文件，否则打开它所在的文件夹。"""
    path = Path(str(raw).strip()).resolve()
    if not path.exists():
        raise ValueError(f"路径不存在: {path}")
    subprocess.Popen(["explorer", f"/select,{path}"] if select else ["explorer", str(path if path.is_dir() else path.parent)])
    return str(path)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return

    def do_GET(self) -> None:
        if self.path == "/" or self.path.startswith("/index"):
            self.send_html(INDEX_HTML)
        elif self.path == "/api/health":
            rows = [item.__dict__ for item in run_healthcheck()]
            self.send_json(rows)
        elif self.path == "/api/history":
            self.send_json(list_history())
        elif self.path.startswith("/api/progress"):
            key = (parse_qs(urlparse(self.path).query).get("key") or [""])[0]
            self.send_json(PROGRESS.get(key) or {"done": 0, "total": 0, "label": ""})
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        try:
            body = self.read_json()
            if self.path == "/api/plan":
                plan = build_plan(_selected_root(body), body.get("mode", "batch"), body.get("output_root") or None)
                PLANS.put(plan)
                data = plan.to_dict()
                data["display"] = plan_display_stats(plan)
                self.send_json(data)
            elif self.path == "/api/report":
                report_dir = write_plan_report(PLANS.get(_plan_id(body)))
                self.send_json({"report_dir": str(report_dir)})
            elif self.path == "/api/duplicates":
                self.send_json(self.detect_duplicates_payload(body))
            elif self.path == "/api/duplicates/delete":
                _, progress = _track(body)
                result = delete_duplicates(
                    _existing_root(body),
                    _delete_entries(body),
                    mode=body.get("mode", "recycle"),
                    confirm=bool(body.get("confirm")),
                    progress=progress,
                )
                self.send_json(result.to_dict())
            elif self.path == "/api/validate-root":
                self.send_json(self.validate_root_payload(body))
            elif self.path == "/api/reveal":
                self.send_json({"opened": reveal_in_explorer(body.get("path", ""), bool(body.get("select")))})
            elif self.path == "/api/undo-delete":
                log_path = body.get("transaction_log")
                if not isinstance(log_path, str) or not log_path:
                    raise ValueError("缺少 transaction_log（删除日志路径）")
                self.send_json({"rows": rows_as_dicts(undo_transaction(log_path))})
            elif self.path == "/api/repair":
                _, progress = _track(body)
                root = _existing_root(body)
                if not any(
                    bool(body.get(key))
                    for key in ("clear_readonly", "clear_hidden", "clear_zone_identifier", "grant_current_user", "fix_inheritance")
                ):
                    raise ValueError("请先勾选至少一项修复动作，再执行")
                rows = repair_permissions(
                    root,
                    clear_readonly=bool(body.get("clear_readonly")),
                    clear_hidden=bool(body.get("clear_hidden")),
                    clear_zone_identifier=bool(body.get("clear_zone_identifier")),
                    progress=progress,
                )
                bad = [row for row in rows if row.status != "ok"]
                self.send_json({"rows": rows_as_dicts(bad), "checked": len(rows), "passed": len(rows) - len(bad)})
            elif self.path == "/api/execute-copy":
                _, progress = _track(body)
                result = execute_plan(PLANS.get(_plan_id(body)), "copy", progress=progress)
                self.send_json({"completed": result.completed, "skipped": result.skipped, "failed": result.failed, "issues": [row.to_dict() for row in result.issues]})
            elif self.path == "/api/execute-move":
                plan = PLANS.get(_plan_id(body))
                if body.get("confirm") is not True:
                    raise ValueError("原地整理会修改源目录；请先看预览，再点确认执行")
                _, progress = _track(body)
                result = execute_plan(plan, "move", confirm_move=True, transaction_log=str(_move_log_path(plan)), progress=progress)
                self.send_json({
                    "completed": result.completed,
                    "skipped": result.skipped,
                    "failed": result.failed,
                    "transaction_log": result.transaction_log,
                    "issues": [row.to_dict() for row in result.issues],
                })
            elif self.path == "/api/undo":
                log_path = body.get("transaction_log")
                if not isinstance(log_path, str) or not log_path:
                    raise ValueError("缺少 transaction_log（事务日志路径）")
                self.send_json({"rows": rows_as_dicts(undo_transaction(log_path))})
            elif self.path == "/api/manual":
                self.send_json({"manual_pdf": str(write_manual_pdf("理巢_NestTidy_简要操作手册.pdf"))})
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=400)

    def validate_root_payload(self, body: dict) -> dict:
        """输入框失焦预检：只判存在性，不扫描；空路径返回 ok=false 而非异常。"""
        raw = body.get("path")
        if not isinstance(raw, str) or not raw.strip():
            return {"ok": False, "error": "路径为空"}
        cleaned = raw.strip().strip('"').strip("'")
        path = Path(cleaned)
        if not path.is_absolute():
            return {"ok": False, "error": "请使用完整路径（例如 D:\\下载\\资源包）"}
        if not path.exists():
            return {"ok": False, "error": "目录不存在"}
        if not path.is_dir():
            return {"ok": False, "error": "这是一个文件，不是文件夹"}
        return {"ok": True, "resolved": str(path.resolve())}

    def detect_duplicates_payload(self, body: dict) -> dict:
        """两阶段诊断的统一出口：rows 供 CSV/明细，groups 供分组卡片与删除按钮。"""
        root = _existing_root(body)
        level = body.get("level", "standard")
        verify = body.get("verify", True) is not False
        rows = detect_duplicates(root, level, verify=verify, progress=_track(body)[1])
        return {
            "selected_root": str(Path(root).resolve()) if root else "",
            "phase1_level": level,
            "phase1_label": PHASE1_METHOD.get(level, level),
            "verify": verify and level != "deep",
            "stats": summarize(rows),
            "groups": [group.to_dict() for group in group_rows(rows)],
            "rows": [row.to_dict() for row in rows],
        }

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if not length:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def send_html(self, text: str) -> None:
        data = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, obj, status: int = 200) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class PortInUseError(RuntimeError):
    """绑定阶段判定为端口占用时的明确错误类型。"""

    def __init__(self, port: int, cause: OSError) -> None:
        self.port = port
        self.cause = cause
        super().__init__(f"端口 {port} 已被占用，请关闭占用该端口的程序后重试")


_PORT_IN_USE_CODES: frozenset[int] = frozenset({errno.EADDRINUSE, 10048})
_WINDOWS_ACCESS_DENIED = 10013  # WSAEACCES：占用方未设 SO_REUSEADDR 时 Windows 的实际报错


def _is_port_in_use(exc: OSError, host: str, port: int) -> bool:
    """只把真实占用归类；其他 OSError 一律返回 False 由调用方原样抛。"""
    winerror = getattr(exc, "winerror", None)
    if exc.errno in _PORT_IN_USE_CODES or winerror in _PORT_IN_USE_CODES:
        return True
    if winerror == _WINDOWS_ACCESS_DENIED:
        # 用真实连接确认该端口确有监听者，避免把权限问题误报成端口占用。
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.2)
            return probe.connect_ex((host, port)) == 0
    return False


class NestTidyHTTPServer(ThreadingHTTPServer):
    """关闭 SO_REUSEADDR：否则 Windows 上与同为 reuse 的占用方会静默共存。"""

    allow_reuse_address = False


def create_server(host: str, port: int) -> NestTidyHTTPServer:
    """创建并绑定 HTTP 服务；仅转译绑定阶段的端口占用错误。"""
    try:
        return NestTidyHTTPServer((host, port), Handler)
    except OSError as exc:
        if _is_port_in_use(exc, host, port):
            raise PortInUseError(port, exc) from exc
        raise


def serve(host: str, port: int) -> None:
    if host != "127.0.0.1":
        raise ValueError("理巢默认只允许绑定 127.0.0.1")
    httpd = create_server(host, port)
    print(f"理巢 NestTidy 已启动：http://{host}:{port}")
    httpd.serve_forever()
