from __future__ import annotations

import errno
import json
import socket
import subprocess
import threading
from datetime import UTC, datetime
from email.message import Message
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import REPORT_DIR_NAME, load_local_config, remember_selected_root
from .duplicate import PHASE1_METHOD, detect_duplicates, group_rows, summarize
from .executor import DeleteEntry, delete_duplicates, execute_plan
from .healthcheck import run_healthcheck
from .manual_pdf import write_manual_pdf
from .model import MappingEntry, OperationPlan
from .planner import build_plan
from .recovery import rows_as_dicts, undo_transaction
from .repair import repair_permissions
from .reports import build_mapping_index, plan_display_stats, readable_group_rows, write_plan_report
from .storage import list_history
from .ui import INDEX_HTML


class PlanConflictError(ValueError):
    """计划已经开始执行，禁止再次提交。"""


class _PlanPageIndex:
    """惰性构建，每份计划单独锁，不占用注册表全局锁。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._index: dict[str, list[MappingEntry]] | None = None

    def get(self, plan: OperationPlan) -> dict[str, list[MappingEntry]]:
        with self._lock:
            if self._index is None:
                self._index = build_mapping_index(plan)
            return self._index


class PlanRegistry:
    """冻结计划的身份与执行生命周期分离；执行结果只更新状态元数据。"""

    def __init__(self, max_plans: int = 16) -> None:
        self._plans: dict[str, OperationPlan] = {}
        self._states: dict[str, str] = {}
        self._pages: dict[str, _PlanPageIndex] = {}
        self._lock = threading.Lock()
        self._max = max(1, max_plans)

    def put(self, plan: OperationPlan) -> str:
        with self._lock:
            if plan.plan_id in self._plans:
                raise ValueError("计划标识重复")
            while len(self._plans) >= self._max:
                victim = next((pid for pid in self._plans if self._states[pid] != "running"), None)
                if victim is None:
                    raise PlanConflictError("所有缓存计划正在执行，请稍后重新试运行")
                del self._plans[victim]
                del self._states[victim]
                del self._pages[victim]
            self._plans[plan.plan_id] = plan
            self._states[plan.plan_id] = "ready"
            self._pages[plan.plan_id] = _PlanPageIndex()
        return plan.plan_id

    def get(self, plan_id: str) -> OperationPlan:
        with self._lock:
            plan = self._plans.get(plan_id)
        if plan is None:
            raise ValueError("计划不存在或已被淘汰，请重新生成试运行计划")
        return plan

    def page(self, plan_id: str, root: str, offset: int, limit: int) -> dict:
        with self._lock:
            plan = self._plans.get(plan_id)
            holder = self._pages.get(plan_id)
        if plan is None or holder is None:
            raise ValueError("计划不存在或已被淘汰，请重新生成试运行计划")
        return readable_group_rows(plan, root, offset, limit, index=holder.get(plan))

    def claim(self, plan_id: str) -> OperationPlan:
        """首次实际执行前原子消耗计划；终态不随 progress_key 淘汰重置。"""
        with self._lock:
            plan = self._plans.get(plan_id)
            if plan is None:
                raise ValueError("计划不存在或已被淘汰，请重新生成试运行计划")
            if self._states[plan_id] != "ready":
                raise PlanConflictError("该计划已开始或结束执行，请重新试运行")
            self._states[plan_id] = "running"
            return plan

    def finish(self, plan_id: str, ok: bool) -> None:
        with self._lock:
            if self._states.get(plan_id) == "running":
                self._states[plan_id] = "finished" if ok else "failed"

    def clear(self) -> None:
        with self._lock:
            self._plans.clear()
            self._states.clear()
            self._pages.clear()


PLANS = PlanRegistry()

PROGRESS: dict[str, dict] = {}
# 多标签页并发读/写/淘汰进度槽：三处必须在同一把锁里，
# 否则两个请求同时 pop(next(iter(...))) 会互踩出 KeyError / RuntimeError。
_PROGRESS_LOCK = threading.Lock()


class ExecutionGuard:
    """执行类端点的两层防护：per-plan 执行锁 + per-key 请求幂等（P1-2）。

    - 同一 plan_id 并发执行：第二个请求拿不到锁，返回 409，不排队不合并。
    - 同一 progress_key 重复提交：返回「进行中/已提交」，不重复执行、不产生第二份
      事务日志或报告；完成态也保留一段时间用于幂等，按 64 条有界淘汰。键满时优先
      淘汰已完成键；若全部仍在运行则拒绝新任务，绝不淘汰正在执行的键。
    两层状态用同一把锁做「检查 + 登记」的原子操作，避免检查与登记之间被抢占。
    """

    def __init__(self, max_keys: int = 64) -> None:
        self._lock = threading.Lock()
        self._inflight: dict[str, str] = {}
        self._locked_plans: set[str] = set()
        self._active_scopes: dict[object, tuple[tuple[Path, ...], bool]] = {}
        self._max_keys = max(1, max_keys)

    def acquire(self, plan_id: str, progress_key: str, scopes: tuple[Path, ...] = (),
                exclusive: bool = False, token: object | None = None) -> str | None:
        """返回 None 表示获得执行权；否则返回应展示给用户的冲突原因。"""
        with self._lock:
            if progress_key and progress_key in self._inflight:
                return "任务进行中或已提交，请勿重复操作"
            if plan_id and plan_id in self._locked_plans:
                return "该计划正在执行中，请勿重复提交"
            if scopes or exclusive:
                if token is None:
                    raise ValueError("目录锁需要请求标识")
                for active, active_exclusive in self._active_scopes.values():
                    if exclusive or active_exclusive or any(a.is_relative_to(b) or b.is_relative_to(a) for a in scopes for b in active):
                        return "所选目录与正在执行的任务重叠，请等待该任务结束"
            if progress_key:
                # 只淘汰已完成的幂等键；仍在运行的键被淘汰会让同一 key 再次进入，
                # 形成“看起来幂等、实际并发”的漏洞。全部在跑时宁可拒绝新任务。
                while len(self._inflight) >= self._max_keys:
                    completed = next((k for k, v in self._inflight.items() if v != "running"), None)
                    if completed is None:
                        return "并发任务过多，请稍后重试"
                    self._inflight.pop(completed)
                self._inflight[progress_key] = "running"
            if plan_id:
                self._locked_plans.add(plan_id)
            if scopes or exclusive:
                self._active_scopes[token] = (scopes, exclusive)
        return None

    def release(self, plan_id: str, progress_key: str, ok: bool = True, token: object | None = None) -> None:
        with self._lock:
            if token is not None:
                self._active_scopes.pop(token, None)
            if plan_id:
                self._locked_plans.discard(plan_id)
            if progress_key and progress_key in self._inflight:
                self._inflight[progress_key] = "ok" if ok else "failed"


EXECUTION_GUARD = ExecutionGuard()

ROW_LIMIT = 80          # 每个资源包一次最多下发的对照行数：28 万个文件全量渲染会把浏览器卡死
ROW_PAGE = 200          # 「显示更多」每页追加的行数
FOLDER_LIMIT = 300      # 空文件夹清单同样截断
ISSUE_LIMIT = 200       # 异常项同样只下发前若干条，余下按总数提示
DUPLICATE_ROW_LIMIT = 400
ROW_TOTAL = 1500        # 所有资源包合计下发行数上限：280 个包各 80 行也有 2 万行，浏览器照样卡


def _plan_payload(plan: OperationPlan) -> dict:
    """下发精简计划：mappings 一条不下发（28 万条就是上百 MB JSON），operations/issues 截断，明细走 /api/plan/rows 分页。"""
    return {
        "plan_id": plan.plan_id,
        "app_version": plan.app_version,
        "rule_version": plan.rule_version,
        "mode": plan.mode,
        "selected_root": plan.selected_root,
        "output_root": plan.output_root,
        "config_snapshot": plan.config_snapshot,
        "source_snapshot_digest": plan.source_snapshot_digest,
        "resource_roots": plan.resource_roots,
        "operations": [op.to_dict() for op in plan.operations[:ROW_LIMIT]],
        "operation_count": len(plan.operations),
        "issues": [issue.to_dict() for issue in plan.issues[:ISSUE_LIMIT]],
        "issue_total": len(plan.issues),
        "mapping_count": len(plan.mappings),
        "created_at": plan.created_at,
        "frozen": plan.frozen,
        "estimated_space": plan.estimated_space,
        "display": plan_display_stats(plan, ROW_LIMIT, FOLDER_LIMIT, ROW_TOTAL),
    }


def _progress_slot(key: str) -> dict:
    """未知 key 也返回完整契约，前端不必为「还没开始」和「已淘汰」写两套渲染。"""
    with _PROGRESS_LOCK:
        state = PROGRESS.get(key)
        if state is None:
            return {"phase": "", "status": "running", "done": 0, "total": 0, "label": "", "note": "", "updated_at": ""}
        return dict(state)


def _trim_progress_locked() -> None:
    """调用方必须已持有 _PROGRESS_LOCK。口径与 ExecutionGuard 幂等键一致：
    优先淘汰已完成键；全部仍在运行时才按插入顺序兜底保上限（槽 ≤65）。"""
    while len(PROGRESS) > 64:
        finished = next((item for item, state in PROGRESS.items() if state.get("status") != "running"), None)
        PROGRESS.pop(finished if finished is not None else next(iter(PROGRESS)))


def _track(body: dict, phase: str = "执行"):
    """progress_key → 旁路进度槽；回调保持 done/total/label 三参，第四参用于覆盖阶段。"""
    key = str(body.get("progress_key") or "")
    if not key:
        return "", None

    def report(done: int, total: int, label: str = "", step: str | None = None) -> None:
        with _PROGRESS_LOCK:
            # 只在插入新键时淘汰：已在跑的槽每报一次进度就淘汰一个历史槽会让别的页签丢结果。
            if key not in PROGRESS:
                _trim_progress_locked()
            PROGRESS[key] = {
                "phase": step or phase,
                "status": "running",
                "done": done,
                "total": total,
                "label": label,
                "note": "",
                "updated_at": datetime.now(UTC).isoformat(),
            }

    report(0, 0, "正在准备", phase)
    return key, report


def _finish_progress(key: str, ok: bool, note: str = "") -> None:
    """终态由后端写：失败保留最后一帧，只在 status/note/updated_at 上收口。"""
    with _PROGRESS_LOCK:
        state = PROGRESS.get(key)
        if state is None:
            return
        state["status"] = "ok" if ok else "failed"
        state["note"] = "" if ok else str(note)
        state["updated_at"] = datetime.now(UTC).isoformat()


def _duplicate_group_payload(group) -> dict:
    """单组去重结果：可删路径截断到一页，余下只报总数（一次下发几十万条同样会卡死页面）。"""
    item = group.to_dict()
    total = len(group.deletable_paths)
    item["deletable_paths"] = group.deletable_paths[:DUPLICATE_ROW_LIMIT]
    item["deletable_total"] = total
    return item


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


def _remember_root_quietly(root: str) -> None:
    """成功操作后记住「上次的源目录」。写不进去只损失下次的预填便利，不能让已成功的操作报错。"""
    try:
        remember_selected_root(root)
    except OSError:
        pass


def reveal_in_explorer(raw: str, select: bool) -> str:
    """在资源管理器里定位：select=True 选中这个文件，否则打开它所在的文件夹。"""
    path = Path(str(raw).strip()).resolve()
    if not path.exists():
        raise ValueError(f"路径不存在: {path}")
    subprocess.Popen(["explorer", f"/select,{path}"] if select else ["explorer", str(path if path.is_dir() else path.parent)])
    return str(path)


def _host_name(raw: str) -> str:
    """Host / Origin 里的主机名：去掉端口、IPv6 方括号与尾随点，统一小写。"""
    text = (raw or "").strip().lower()
    if text.startswith("["):
        return text.split("]")[0].lstrip("[")
    return text.split(":")[0].rstrip(".")


def request_source_error(headers: Message, server_address: tuple[str, int]) -> str | None:
    """本地服务的浏览器侧防线，返回拒绝原因（None 表示放行）。

    - Host 白名单：挡 DNS 重绑定——恶意域名解析到 127.0.0.1 后页面即「同源」，可读响应；
    - Origin 校验：挡跨站伪造——任意网页都能向 127.0.0.1 POST 执行 / 删除 / 撤销，
      而理巢的删除底线是「逐个点选 + 二次确认」，被网页代点就破了这条底线；
    - POST 另需 application/json（见 do_POST）：表单 / text/plain 是免预检的简单请求。

    无 Origin 的请求（curl、命令行脚本）放行；服务只绑 127.0.0.1，不是对外接口。
    """
    allowed = {"127.0.0.1", "localhost"}
    bound = str(server_address[0])
    if bound and bound not in {"0.0.0.0", "::"}:
        allowed.add(_host_name(bound))
    if _host_name(headers.get("Host") or "") not in allowed:
        return "拒绝来自非本机 Host 的请求"
    origin = (headers.get("Origin") or "").strip()
    if origin:
        parsed = urlparse(origin)
        if (
            parsed.scheme != "http"
            or _host_name(parsed.hostname or "") not in allowed
            or (parsed.port or 80) != server_address[1]
        ):
            return "拒绝跨站来源的请求（Origin 校验未通过）"
    return None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return

    def do_GET(self) -> None:
        refused = request_source_error(self.headers, self.server.server_address)
        if refused:
            self.send_json({"error": refused}, status=403)
            return
        if self.path == "/" or self.path.startswith("/index"):
            self.send_html(INDEX_HTML)
        elif self.path == "/api/health":
            rows = [item.__dict__ for item in run_healthcheck()]
            self.send_json(rows)
        elif self.path == "/api/history":
            self.send_json(list_history())
        elif self.path == "/api/config":
            try:
                config = load_local_config()
            except ValueError as exc:
                self.send_json({"selected_root": "", "error": str(exc)}, status=400)
            else:
                self.send_json({"selected_root": config.selected_root})
        elif self.path.startswith("/api/progress"):
            key = (parse_qs(urlparse(self.path).query).get("key") or [""])[0]
            self.send_json(_progress_slot(key))
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def send_tracked(self, key: str, obj) -> None:
        """成功响应前由后端落下 ok 终态；没有 progress_key 时是空操作。"""
        _finish_progress(key, True)
        self.send_json(obj)

    # 会产生持久副作用的端点：per-key 幂等 +（执行类额外）per-plan 锁。
    # 只读端点（plan / plan/rows / duplicates 诊断 / validate-root / reveal）不设防。
    _GUARD_PATHS = frozenset(
        {
            "/api/report", "/api/duplicates/delete", "/api/undo-delete", "/api/undo",
            "/api/execute-copy", "/api/execute-move", "/api/repair",
        }
    )
    _PLAN_LOCK_PATHS = frozenset({"/api/execute-copy", "/api/execute-move"})

    def do_POST(self) -> None:
        refused = request_source_error(self.headers, self.server.server_address)
        if refused:
            self.send_json({"error": refused}, status=403)
            return
        # 只收 JSON：表单 / text/plain 属浏览器「简单请求」，免预检就能跨站发出；
        # 卡住 Content-Type 就堵死了 CSRF 的低成本入口（页面自身 fetch 一直是 JSON）。
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
            self.send_json({"error": "请求体必须是 application/json"}, status=415)
            return
        key = ""
        guard_plan = ""
        guard_key = ""
        guard_token = object()
        acquired = False
        claimed = False
        execution_ok = False
        failed = False
        try:
            body = self.read_json()
            # 在 _track 之前 acquire：被拒请求不得重置正在执行请求的进度槽。
            if self.path in self._GUARD_PATHS:
                guard_key = str(body.get("progress_key") or "")
                scopes: tuple[Path, ...] = ()
                exclusive = False
                if self.path in self._PLAN_LOCK_PATHS:
                    guard_plan = _plan_id(body)
                    guarded_plan = PLANS.get(guard_plan)
                    paths = [guarded_plan.selected_root]
                    if guarded_plan.output_root:
                        paths.append(guarded_plan.output_root)
                    scopes = tuple(dict.fromkeys(Path(path).resolve() for path in paths))
                elif self.path in {"/api/duplicates/delete", "/api/repair"}:
                    scopes = (Path(_existing_root(body)).resolve(),)
                elif self.path in {"/api/undo", "/api/undo-delete"}:
                    # 日志可能来自旧版本，且可能跨资源根；不能从任意一条日志安全推断作用域。
                    # A3 固化事务级元数据前，撤销保守独占写任务，不影响普通只读请求。
                    exclusive = True
                conflict = EXECUTION_GUARD.acquire(guard_plan, guard_key, scopes, exclusive, guard_token)
                if conflict:
                    self.send_json({"error": conflict}, status=409)
                    return
                acquired = True
            if self.path == "/api/plan":
                key, progress = _track(body, "计划")
                plan = build_plan(
                    _selected_root(body), body.get("mode", "batch"), body.get("output_root") or None, progress=progress
                )
                PLANS.put(plan)
                _remember_root_quietly(plan.selected_root)
                self.send_tracked(key, _plan_payload(plan))
            elif self.path == "/api/plan/rows":
                self.send_json(
                    PLANS.page(
                        _plan_id(body),
                        str(body.get("root") or ""),
                        int(body.get("offset") or 0),
                        min(int(body.get("limit") or ROW_PAGE), 1000),
                    )
                )
            elif self.path == "/api/report":
                key, progress = _track(body, "报告")
                report_dir = write_plan_report(PLANS.get(_plan_id(body)), progress=progress)
                self.send_tracked(key, {"report_dir": str(report_dir)})
            elif self.path == "/api/duplicates":
                key, progress = _track(body, "去重")
                self.send_tracked(key, self.detect_duplicates_payload(body, progress))
            elif self.path == "/api/duplicates/delete":
                key, progress = _track(body, "去重")
                result = delete_duplicates(
                    _existing_root(body),
                    _delete_entries(body),
                    mode=body.get("mode", "recycle"),
                    confirm=bool(body.get("confirm")),
                    progress=progress,
                )
                self.send_tracked(key, result.to_dict())
            elif self.path == "/api/validate-root":
                self.send_json(self.validate_root_payload(body))
            elif self.path == "/api/reveal":
                self.send_json({"opened": reveal_in_explorer(body.get("path", ""), bool(body.get("select")))})
            elif self.path == "/api/undo-delete":
                log_path = body.get("transaction_log")
                if not isinstance(log_path, str) or not log_path:
                    raise ValueError("缺少 transaction_log（删除日志路径）")
                key, progress = _track(body, "撤销")
                self.send_tracked(key, {"rows": rows_as_dicts(undo_transaction(log_path, progress))})
            elif self.path == "/api/repair":
                key, progress = _track(body, "修复")
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
                    # 校验放行的五项必须都转发：旧版接受后两项又丢弃，用户勾了静默不生效。
                    grant_current_user=bool(body.get("grant_current_user")),
                    fix_inheritance=bool(body.get("fix_inheritance")),
                    progress=progress,
                )
                bad = [row for row in rows if row.status != "ok"]
                _remember_root_quietly(str(Path(root).resolve()))
                self.send_tracked(key, {"rows": rows_as_dicts(bad), "checked": len(rows), "passed": len(rows) - len(bad)})
            elif self.path == "/api/execute-copy":
                plan = PLANS.get(guard_plan)
                if not plan.output_root:
                    raise ValueError("复制执行需要带输出目录的试运行计划")
                plan = PLANS.claim(guard_plan)
                claimed = True
                key, progress = _track(body, "执行")
                result = execute_plan(plan, "copy", progress=progress)
                execution_ok = not result.failed
                self.send_tracked(key, {"completed": result.completed, "skipped": result.skipped, "failed": result.failed, "issues": [row.to_dict() for row in result.issues]})
            elif self.path == "/api/execute-move":
                plan = PLANS.get(_plan_id(body))
                if body.get("confirm") is not True:
                    raise ValueError("原地整理会修改源目录；请先看预览，再点确认执行")
                if plan.output_root:
                    raise ValueError("复制计划不能原地执行，请重新试运行")
                plan = PLANS.claim(guard_plan)
                claimed = True
                key, progress = _track(body, "执行")
                result = execute_plan(plan, "move", confirm_move=True, transaction_log=str(_move_log_path(plan)), progress=progress)
                execution_ok = not result.failed
                self.send_tracked(key, {
                    "completed": result.completed,
                    "skipped": result.skipped,
                    "failed": result.failed,
                    "transaction_log": result.transaction_log,
                    "swept": getattr(result, "swept", 0),
                    "issues": [row.to_dict() for row in result.issues],
                })
            elif self.path == "/api/undo":
                log_path = body.get("transaction_log")
                if not isinstance(log_path, str) or not log_path:
                    raise ValueError("缺少 transaction_log（事务日志路径）")
                key, progress = _track(body, "撤销")
                self.send_tracked(key, {"rows": rows_as_dicts(undo_transaction(log_path, progress))})
            elif self.path == "/api/manual":
                self.send_json({"manual_pdf": str(write_manual_pdf("理巢_NestTidy_简要操作手册.pdf"))})
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except Exception as exc:
            failed = True
            _finish_progress(key, False, str(exc))
            self.send_json({"error": str(exc)}, status=409 if isinstance(exc, PlanConflictError) else 400)
        finally:
            if claimed:
                PLANS.finish(guard_plan, execution_ok and not failed)
            if acquired:
                EXECUTION_GUARD.release(guard_plan, guard_key, ok=not failed, token=guard_token)

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

    def detect_duplicates_payload(self, body: dict, progress=None) -> dict:
        """两阶段诊断的统一出口：rows 供 CSV/明细，groups 供分组卡片与删除按钮。"""
        root = _existing_root(body)
        level = body.get("level", "standard")
        verify = body.get("verify", True) is not False
        rows = detect_duplicates(root, level, verify=verify, progress=progress)
        resolved = str(Path(root).resolve())
        _remember_root_quietly(resolved)
        return {
            "selected_root": resolved,
            "phase1_level": level,
            "phase1_label": PHASE1_METHOD.get(level, level),
            "verify": verify and level != "deep",
            "stats": summarize(rows),
            "groups": [_duplicate_group_payload(group) for group in group_rows(rows)],
            "rows": [row.to_dict() for row in rows[:DUPLICATE_ROW_LIMIT]],
            "row_total": len(rows),
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
