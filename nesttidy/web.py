from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .duplicate import detect_duplicates
from .executor import execute_plan
from .healthcheck import run_healthcheck
from .manual_pdf import write_manual_pdf
from .planner import build_plan
from .repair import repair_permissions, rows_as_dicts
from .reports import write_plan_report
from .storage import list_history

LAST_PLAN = None


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
    <section><h2>预览映射</h2><div id="preview"></div></section>
    <section><h2>异常项</h2><div id="issues"></div></section>
    <section><h2>诊断 / 历史</h2><div id="diagnostics"></div></section>
    <section><h2>健康检查</h2><div id="health"></div></section>
  </main>
<script>
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
    render(data);
    document.getElementById('msg').textContent = '计划已生成。默认未修改磁盘。';
  } catch(e) { document.getElementById('msg').innerHTML = '<span class="error">' + esc(e.message) + '</span>'; }
}
async function exportReport() {
  try {
    const data = await api('/api/report', {});
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
  try {
    const data = await api('/api/execute-copy', {});
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
  const counts = {};
  for (const op of plan.operations) counts[op.op_type] = (counts[op.op_type] || 0) + 1;
  stats.innerHTML = [
    ['资源根', plan.resource_roots.length],
    ['映射', plan.mappings.length],
    ['操作', plan.operations.length],
    ['异常', plan.issues.length]
  ].map(x => `<div class="stat"><b>${x[0]}</b><br>${x[1]}</div>`).join('');
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
        global LAST_PLAN
        try:
            body = self.read_json()
            if self.path == "/api/plan":
                LAST_PLAN = build_plan(body.get("selected_root", ""), body.get("mode", "batch"), body.get("output_root") or None)
                self.send_json(LAST_PLAN.to_dict())
            elif self.path == "/api/report":
                if LAST_PLAN is None:
                    raise ValueError("尚未生成计划")
                report_dir = write_plan_report(LAST_PLAN)
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
                if LAST_PLAN is None:
                    raise ValueError("尚未生成计划")
                result = execute_plan(LAST_PLAN, "copy")
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


def serve(host: str, port: int) -> None:
    if host != "127.0.0.1":
        raise ValueError("理巢默认只允许绑定 127.0.0.1")
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"理巢 NestTidy 已启动：http://{host}:{port}")
    httpd.serve_forever()
