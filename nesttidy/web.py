from __future__ import annotations

import errno
import json
import socket
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .duplicate import detect_duplicates
from .executor import execute_plan
from .healthcheck import run_healthcheck
from .manual_pdf import write_manual_pdf
from .model import OperationPlan
from .planner import build_plan
from .repair import repair_permissions, rows_as_dicts
from .reports import plan_display_stats, write_plan_report
from .storage import list_history


class PlanRegistry:
    """按 plan_id 索引冻结计划，替代模块级单值 LAST_PLAN。

    LAST_PLAN 是单值 + ThreadingHTTPServer 多线程：任何一次预览都会覆盖上一次，
    B 标签页生成计划后，A 标签页点「执行安全复制」执行的就是 B 的计划。
    按 id 索引后每个标签页只持有自己的 id；服务端仍只执行自己保存的冻结计划，
    不信任前端回传的计划对象（不变量 6：正式执行前必须有冻结计划）。
    """

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


def _plan_id(body: dict) -> str:
    plan_id = body.get("plan_id")
    if not isinstance(plan_id, str) or not plan_id:
        raise ValueError("缺少 plan_id，请重新生成试运行计划")
    return plan_id


INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>理巢 NestTidy</title>
  <style>
    :root { color-scheme: light; font-family: "Segoe UI", "Microsoft YaHei", sans-serif; }
    body { margin: 0; background: #f7f7f5; color: #242424; }
    header { padding: 20px 28px; background: #174b4f; color: white; }
    main { max-width: 1180px; margin: 0 auto; padding: 22px; }
    section { margin: 18px 0; }
    label { display: block; font-weight: 600; margin: 12px 0 6px; }
    input, select { box-sizing: border-box; width: 100%; padding: 10px 12px; border: 1px solid #b8bfbd; border-radius: 6px; background: white; }
    button { padding: 10px 14px; border: 0; border-radius: 6px; background: #196f5f; color: white; cursor: pointer; margin-right: 8px; }
    button.secondary { background: #495057; }
    table { width: 100%; border-collapse: collapse; background: white; border: 1px solid #d8ddda; }
    th, td { padding: 8px; border-bottom: 1px solid #e6e8e7; text-align: left; font-size: 13px; vertical-align: top; }
    th { background: #edf2f0; }
    .grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
    .stat { background: white; border: 1px solid #d8ddda; border-radius: 8px; padding: 12px; }
    .muted { color: #687370; }
    .error { color: #9b1c1c; }
    @media (max-width: 720px) { .grid { grid-template-columns: 1fr 1fr; } }
  </style>
</head>
<body>
  <header><h1>理巢 NestTidy</h1><div>本地离线资源目录剥壳整理工具</div></header>
  <main>
    <section>
      <label>源目录</label>
      <input id="root" placeholder="例如 D:\\下载\\资源包">
      <label>整理模式</label>
      <select id="mode"><option value="batch">批量整理：整理所选目录下的每个子文件夹</option><option value="single">单个资源整理：整理所选目录本身</option></select>
      <label>copy 输出目录（只在安全复制时需要）</label>
      <input id="output" placeholder="例如 D:\\下载\\理巢输出_20260514_2130">
      <label>重复文件检测级别</label>
      <select id="dupLevel"><option value="standard">标准检查：大小 + 首尾哈希</option><option value="fast">快速检查：大小 + 文件名</option><option value="deep">深度检查：完整哈希</option></select>
      <p>
        <button onclick="makePlan()">生成试运行计划</button>
        <button onclick="runDuplicate()">重复文件诊断</button>
        <button onclick="executeCopy()">执行安全复制</button>
        <button class="secondary" onclick="exportReport()">导出报告</button>
        <button class="secondary" onclick="loadHistory()">查看历史</button>
        <button class="secondary" onclick="makeManual()">生成 PDF 手册</button>
      </p>
      <p>
        <label><input type="checkbox" id="fixReadonly" style="width:auto"> 清除只读属性</label>
        <label><input type="checkbox" id="fixHidden" style="width:auto"> 清除普通隐藏属性</label>
        <label><input type="checkbox" id="fixZone" style="width:auto"> 清除 Zone.Identifier</label>
        <button class="secondary" onclick="runRepair()">执行所选权限修复</button>
      </p>
      <p id="msg" class="muted"></p>
    </section>
    <section class="grid" id="stats"></section>
    <section id="ruleStats"></section>
    <section id="r5Samples"></section>
    <section id="boundaryNote"></section>
    <section><h2>预览映射</h2><div id="preview"></div></section>
    <section><h2>异常项</h2><div id="issues"></div></section>
    <section><h2>诊断 / 历史</h2><div id="diagnostics"></div></section>
    <section><h2>健康检查</h2><div id="health"></div></section>
  </main>
<script>
let currentPlanId = null;
async function api(path, body) {
  const res = await fetch(path, {method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body || {})});
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}
function esc(s){ return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
async function makePlan() {
  document.getElementById('msg').textContent = '正在扫描并生成冻结计划...';
  try {
    const data = await api('/api/plan', {selected_root: root.value, mode: mode.value, output_root: output.value || null});
    currentPlanId = data.plan_id;
    render(data);
    document.getElementById('msg').textContent = '计划已生成。默认未修改磁盘。';
  } catch(e) { document.getElementById('msg').innerHTML = '<span class="error">' + esc(e.message) + '</span>'; }
}
async function exportReport() {
  if (!currentPlanId) { document.getElementById('msg').innerHTML = '<span class="error">请先生成计划。</span>'; return; }
  try {
    const data = await api('/api/report', {plan_id: currentPlanId});
    document.getElementById('msg').textContent = '报告已导出：' + data.report_dir;
  } catch(e) { document.getElementById('msg').innerHTML = '<span class="error">' + esc(e.message) + '</span>'; }
}
async function runDuplicate() {
  document.getElementById('msg').textContent = '正在执行重复文件诊断...';
  try {
    const data = await api('/api/duplicates', {selected_root: root.value, level: dupLevel.value});
    diagnostics.innerHTML = table(data.rows.slice(0, 500), ['duplicate_group_id','path','size','hash_method','suggested_keep','reason']);
    document.getElementById('msg').textContent = '诊断完成；仅报告，不自动删除。';
  } catch(e) { document.getElementById('msg').innerHTML = '<span class="error">' + esc(e.message) + '</span>'; }
}
async function runRepair() {
  document.getElementById('msg').textContent = '正在执行所选权限修复...';
  try {
    const data = await api('/api/repair', {selected_root: root.value, clear_readonly: fixReadonly.checked, clear_hidden: fixHidden.checked, clear_zone_identifier: fixZone.checked});
    diagnostics.innerHTML = table(data.rows.slice(0, 500), ['path','action','status','message']);
    document.getElementById('msg').textContent = '权限修复完成。';
  } catch(e) { document.getElementById('msg').innerHTML = '<span class="error">' + esc(e.message) + '</span>'; }
}
async function executeCopy() {
  if (!output.value) { document.getElementById('msg').innerHTML = '<span class="error">安全复制需要填写输出目录。</span>'; return; }
  if (!currentPlanId) { document.getElementById('msg').innerHTML = '<span class="error">请先生成计划。</span>'; return; }
  try {
    const data = await api('/api/execute-copy', {plan_id: currentPlanId});
    diagnostics.innerHTML = table(data.issues || [], ['path','stage','code','message','final_status']);
    document.getElementById('msg').textContent = `copy 完成：完成 ${data.completed}，跳过 ${data.skipped}，失败 ${data.failed}`;
  } catch(e) { document.getElementById('msg').innerHTML = '<span class="error">' + esc(e.message) + '</span>'; }
}
async function loadHistory() {
  try {
    const data = await fetch('/api/history').then(r=>r.json());
    diagnostics.innerHTML = table(data, ['created_at','mode','selected_root','operation_count','issue_count','report_dir']);
  } catch(e) { document.getElementById('msg').innerHTML = '<span class="error">' + esc(e.message) + '</span>'; }
}
async function makeManual() {
  try {
    const data = await api('/api/manual', {});
    document.getElementById('msg').textContent = 'PDF 手册已生成：' + data.manual_pdf;
  } catch(e) { document.getElementById('msg').innerHTML = '<span class="error">' + esc(e.message) + '</span>'; }
}
function render(plan) {
  stats.innerHTML = [
    ['资源根', plan.resource_roots.length],
    ['映射', plan.mappings.length],
    ['操作', plan.operations.length],
    ['异常', plan.issues.length]
  ].map(x => `<div class="stat"><b>${x[0]}</b><br>${x[1]}</div>`).join('');
  const d = plan.display;
  ruleStats.innerHTML = '<h2>规则分类计数</h2><p class="muted">口径：R0=operations 中 rmdir 条数；R1–R5=mappings[].rule_trace 行首命中条目数（同一条目可命中多条）。来源：冻结计划，未新增字段。P0 是边界不变量、无逐条痕迹，结果见下方边界检查。</p>' +
    table(d.rule_rows, ['rule','name','count']);
  r5Samples.innerHTML = '<h2>R5 撞名前缀样例</h2><p class="muted">口径：conflict_resolution_trace 含 R5 的前 5 条；原名/结果名取路径末段，证据为轨迹原文。</p>' +
    table(d.r5_samples, ['original_name','final_name','trace']);
  const b = d.boundary;
  boundaryNote.innerHTML = '<h2>边界检查</h2><p>已校验 <b>' + b.checked + '</b> 条文件操作，目标越界 <b>' + b.out_of_bound + '</b> 条。</p>' +
    '<p class="muted">口径：边界取 ' + esc(b.label) + '，逐条比对 operations[].target_path 是否落在边界内（词法归一化、不读磁盘）。数字由当前计划实时计算，非固定值。</p>' +
    '<p class="muted">盲区：计划期词法归一化、不读磁盘，不解析 junction / 符号链接，此类场景以执行层复查为准（执行层真实路径解析，实测可拦截越界）；本检查只验证目标落在资源根并集内，跨资源根由 planner 结构保证、展示层不做逐条跨根核对（P0 不变量 2 口径更强）；分母仅含 op_type ∈ {move,copy,keep} 且 target_path 非空者，rmdir 无目标不计。</p>';
  preview.innerHTML = table(plan.mappings.slice(0, 500), ['original_path','planned_target_path','operation_type','rule_trace','risk_level']);
  issues.innerHTML = table(plan.issues, ['path','stage','code','message','final_status']);
}
function table(rows, cols) {
  if (!rows.length) return '<p class="muted">无</p>';
  return '<table><thead><tr>' + cols.map(c=>`<th>${esc(c)}</th>`).join('') + '</tr></thead><tbody>' +
    rows.map(r => '<tr>' + cols.map(c=>`<td>${esc(Array.isArray(r[c]) ? r[c].join(' | ') : r[c])}</td>`).join('') + '</tr>').join('') +
    '</tbody></table>';
}
fetch('/api/health').then(r=>r.json()).then(rows => { health.innerHTML = table(rows, ['name','ok','detail']); });
</script>
</body>
</html>"""


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
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        try:
            body = self.read_json()
            if self.path == "/api/plan":
                plan = build_plan(body.get("selected_root", ""), body.get("mode", "batch"), body.get("output_root") or None)
                PLANS.put(plan)
                data = plan.to_dict()
                data["display"] = plan_display_stats(plan)
                self.send_json(data)
            elif self.path == "/api/report":
                report_dir = write_plan_report(PLANS.get(_plan_id(body)))
                self.send_json({"report_dir": str(report_dir)})
            elif self.path == "/api/duplicates":
                rows = detect_duplicates(body.get("selected_root", ""), body.get("level", "standard"))
                self.send_json({"rows": [row.__dict__ for row in rows]})
            elif self.path == "/api/repair":
                rows = repair_permissions(
                    body.get("selected_root", ""),
                    clear_readonly=bool(body.get("clear_readonly")),
                    clear_hidden=bool(body.get("clear_hidden")),
                    clear_zone_identifier=bool(body.get("clear_zone_identifier")),
                )
                self.send_json({"rows": rows_as_dicts(rows)})
            elif self.path == "/api/execute-copy":
                result = execute_plan(PLANS.get(_plan_id(body)), "copy")
                self.send_json({"completed": result.completed, "skipped": result.skipped, "failed": result.failed, "issues": [row.to_dict() for row in result.issues]})
            elif self.path == "/api/manual":
                self.send_json({"manual_pdf": str(write_manual_pdf("理巢_NestTidy_简要操作手册.pdf"))})
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=400)

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
