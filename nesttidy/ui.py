"""页面外壳：只放静态 HTML/CSS/JS，所有数据来自 /api/*（含启动时 GET /api/config 预填上次的源目录）。

页面状态机：计划冻结在服务端（前端只持 plan_id）；试运行失败即作废旧计划；
执行过的计划不可再执行；切换执行方式后须重新试运行。逐版变更明细见 ROADMAP。
"""

INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>理巢 NestTidy</title>
<style>
:root {
  color-scheme: light;
  font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
  --canvas: #f3f5f4; --surface: #fff; --soft: #f6f8f7;
  --line: #dce4e0; --ink: #172d29; --mute: #586b65;
  --ok: #166451; --ok-hover: #104d3e; --accent: #e7f2ed;
  --bad: #a32932; --warn: #86540a; --focus: #177c72;
}
*{box-sizing:border-box}
body{margin:0;background:var(--canvas);color:var(--ink);font-size:14px;line-height:1.6}
header{background:var(--surface);border-bottom:1px solid var(--line)}
.masthead{max-width:1520px;margin:auto;padding:20px 28px;display:flex;align-items:center;justify-content:space-between;gap:24px}
.brand{display:flex;align-items:center;gap:13px;min-width:0}
.brand-mark{width:42px;height:42px;flex:none;background:#173f36;border-radius:13px;display:grid;place-items:center}
.brand-mark svg{width:28px;height:28px}
header h1{margin:0;font-size:22px;font-weight:700;letter-spacing:.02em;line-height:1.2}
header h1 span{font-size:14px;color:var(--mute);font-weight:500;margin-left:8px;letter-spacing:0}
header p{margin:5px 0 0;color:var(--mute);font-size:12.5px}
.local-state{white-space:nowrap;display:flex;align-items:center;gap:7px;font-size:12px;color:var(--mute)}
.local-state::before{content:"";width:8px;height:8px;border-radius:50%;background:var(--ok)}
main{max-width:1520px;margin:0 auto;padding:20px 28px 64px;min-width:0}
section{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:18px 20px;margin:14px 0;min-width:0}
h2{font-size:18px;margin:0 0 12px;letter-spacing:.01em}
h3{font-size:14px;margin:16px 0 8px;font-weight:650}
p{overflow-wrap:anywhere}
label{display:block;font-weight:600;margin:9px 0 5px;font-size:13px}
input[type=text],select{width:100%;min-width:0;padding:10px 12px;border:1px solid #bfcfc8;border-radius:7px;background:#fff;color:var(--ink);font:inherit;font-size:13px}
input[type=text]::placeholder{color:#71837c}
input[type=checkbox]{accent-color:var(--ok);width:16px;height:16px;vertical-align:middle}
button{font:inherit;font-size:13px;font-weight:600;line-height:1.4;padding:9px 14px;min-height:36px;border:1px solid transparent;border-radius:7px;background:var(--ok);color:#fff;cursor:pointer;margin:4px 6px 4px 0;transition:background .12s,border-color .12s}
button:hover:not(:disabled){background:var(--ok-hover)}
button.secondary,button.ghost{background:var(--surface);border-color:#cbd8d2;color:var(--ink)}
button.secondary:hover:not(:disabled),button.ghost:hover:not(:disabled){background:var(--accent);border-color:#9ebcb0}
button.danger{background:var(--bad);color:#fff}
button.danger:hover:not(:disabled){background:#81212a}
button:disabled{background:#e3e9e6;color:#64746d;border-color:#d6dfda;cursor:not-allowed;opacity:1}
button:focus-visible,input:focus-visible,select:focus-visible,[tabindex]:focus-visible,summary:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
table{width:100%;border-collapse:separate;border-spacing:0;background:var(--surface);font-size:12.5px}
th,td{padding:9px 10px;border-bottom:1px solid #e9eeeb;text-align:left;vertical-align:top;overflow-wrap:anywhere}
th{background:#edf2ef;font-weight:650;color:#415750;white-space:nowrap}
tbody tr:nth-child(even){background:#fafcfb}
tbody tr:hover{background:#f0f7f3}
td:last-child,th:last-child{font-variant-numeric:tabular-nums}
code{font-family:Consolas,"Courier New",monospace;font-size:12px;overflow-wrap:anywhere;word-break:normal}
.row{display:flex;gap:16px;flex-wrap:wrap}
.row>div{flex:1 1 240px;min-width:0}
.workspace-fields>div:first-child{flex:2 1 440px}
.workspace-fields>div:last-child{flex:1 1 240px}
.output-fields>div:first-child{flex:0 1 240px}
.output-fields>div:last-child{flex:1 1 440px}
.task-progress.idle{display:none}
#panel-preview>section>.bar{position:static}
#previewSection{scroll-margin-top:14px}
#previewSection>.bar{padding-top:6px}
#modeHint{font-size:12px;margin:4px 0 8px}
#execHint{font-size:12px;padding:6px 0}
#previewSection>p{margin:8px 0}
.workspace-heading{display:flex;align-items:baseline;justify-content:space-between;gap:10px;flex-wrap:wrap}
.workspace-heading h2{margin-bottom:0}
.workspace-heading small{color:var(--mute);font-size:12px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(144px,1fr));gap:10px;margin-top:12px}
.card{border:1px solid var(--line);border-radius:9px;padding:13px 14px;background:var(--soft);min-width:0}
.card b{display:block;font-size:25px;line-height:1.2;letter-spacing:-.025em;font-variant-numeric:tabular-nums;color:var(--ink);margin:4px 0 5px}
.card .metric-label{color:#40594f;font-size:12px;font-weight:600}
.card span{display:block;color:var(--mute);font-size:11.5px;margin-top:3px}
.muted{color:var(--mute)} .error{color:var(--bad)} .ok{color:var(--ok)} .depth{color:var(--mute);font-size:11px}
.headline{background:var(--accent);border:1px solid #cddfd5;padding:12px 14px;border-radius:8px;font-size:14px;color:#254d3e}
.badge{display:inline-block;padding:1px 7px;border-radius:5px;font-size:11px;border:1px solid;margin-right:5px;white-space:nowrap}
.badge.ok{color:#0d5e4d;border-color:#9ecdc0;background:#eaf6f1}
.badge.warn{color:#79510a;border-color:#e2c98a;background:#fdf6e3}
.badge.bad{color:#9b1c1c;border-color:#e3a8a8;background:#fdeeee}
.chips{display:flex;flex-wrap:wrap;gap:5px;margin:8px 0}
button.chip{padding:5px 9px;min-height:30px;margin:0;border:1px solid var(--line);border-radius:6px;font-size:12px;background:#fff;color:var(--mute)}
button.chip:hover:not(:disabled){background:var(--accent);color:var(--ink)}
button.chip.active{background:var(--accent);color:var(--ok);border-color:#9abdae}
.group{border:1px solid var(--line);border-radius:8px;margin:10px 0;padding:10px 12px}
.group h4{margin:0 0 6px;font-size:13px}
details summary{cursor:pointer;font-weight:600;padding:4px 0}
.filtered{display:none} .hidden{display:none!important}
.pbar{height:6px;background:#e5ece8;border-radius:6px;overflow:hidden;margin:10px 0 6px}
.pfill{height:100%;width:0;background:var(--ok);transition:width .2s}
.pfill.failed{background:var(--bad)}
@keyframes pfslide{from{transform:translateX(-100%)}to{transform:translateX(380%)}}
.pfill.indeterminate{width:26%!important;animation:pfslide 1.4s linear infinite}
.bar{position:sticky;top:0;z-index:6;display:flex;gap:8px;align-items:center;flex-wrap:wrap;background:var(--surface);padding:10px 0;margin:0 0 6px;border-bottom:1px solid var(--line)}
.bar .sp{flex:1 1 24px}
.bar label{margin:0;display:inline-flex;align-items:center;gap:8px;font-weight:600;font-size:13px}
.bar select{width:auto;max-width:300px;padding:7px 9px}
.bar button{margin:0}
.tabs{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:7px;margin:0 0 16px;padding:5px;border:1px solid var(--line);border-radius:11px;background:#e9eeeb}
.tab{position:relative;padding:10px 14px;border:1px solid transparent;border-radius:7px;cursor:pointer;background:transparent;text-align:left;min-width:0}
.tab:hover{background:#f5f8f6}
.tab b{display:block;font-size:14px}
.tab small{display:block;color:var(--mute);font-size:11.5px;margin-top:2px}
.tab.active{background:var(--surface);border-color:#c7d8cf;color:var(--ok);box-shadow:0 1px 3px #173f3610}
.tab.active::after{content:"";position:absolute;left:14px;right:14px;bottom:0;height:2px;background:var(--ok);border-radius:2px}
.tab.active small{color:var(--mute)}
.panel>section>section{background:none;border:0;padding:0;margin:16px 0}
.scrollbox{max-height:60vh;overflow:auto;overscroll-behavior:contain;border:1px solid var(--line);border-radius:8px;padding:0;min-width:0;scrollbar-color:#afc1b8 #f2f5f3}
.scrollbox:empty{display:none}
.scrollbox th{position:sticky;top:0;z-index:2}
.scrollbox>p{margin:12px}
.preview-nav{display:flex;align-items:center;flex-wrap:wrap;gap:7px;margin:8px 0}.preview-nav label{margin:0}.preview-nav select{flex:1 1 220px;width:auto;min-width:0;max-width:100%}.preview-nav button{margin:0}.preview-nav input{width:70px;padding:7px;border:1px solid var(--line);border-radius:6px}.preview-nav #pageCount{flex:1 1 120px}
#preview .gblock{border:0;border-bottom:1px solid var(--line);border-radius:0;padding:0;margin:0}
#preview .gblock:last-of-type{border-bottom:0}
#preview .gblock h3{margin:0;padding:10px 12px;background:#f6f9f7;border-bottom:1px solid var(--line)}
#preview .gblock h3 .muted{font-weight:400;font-size:12px}
#preview table{min-width:800px;table-layout:fixed}
#preview .gblock>p{margin:8px 12px}
#preview tr[data-changed="1"] td:nth-child(2){background:#edf7f0}
#preview tr[data-row]{cursor:default}
#dupResult .group{margin:10px}
#dupResult table{min-width:540px}
pre{white-space:pre-wrap;background:var(--soft);border:1px solid var(--line);border-radius:6px;padding:9px;font-size:12px;margin:6px 0}
.msgbar{background:var(--surface);border:1px solid var(--line);border-left:3px solid var(--ok);border-radius:8px;padding:10px 14px;margin:0 0 14px;font-size:13px}
.msgbar.error{border-left-color:var(--bad);background:#fdf3f3;color:var(--bad)}
.msgbar.error #msg{color:var(--bad)}
.steps{color:var(--mute);font-size:12.5px;margin:9px 0 0}
.steps b{color:var(--ink)}
.roothint{font-size:12px;margin:4px 0 0;min-height:18px}
.roothint.ok{color:var(--ok)} .roothint.err{color:var(--bad)}
.overlay{position:fixed;inset:0;background:#172d2980;z-index:50;display:flex;align-items:center;justify-content:center;padding:20px}
.dialog{background:var(--surface);border:1px solid var(--line);border-radius:13px;max-width:540px;width:100%;max-height:90vh;overflow:auto;padding:22px;box-shadow:0 18px 52px #142c2930}
.dialog h3{margin:0 0 12px;font-size:18px}
.dialog .dbody{font-size:14px;margin:0 0 18px}
.dialog .dbody p{margin:8px 0}
.dialog .dactions{display:flex;justify-content:flex-end;gap:8px;flex-wrap:wrap}
.dialog .dactions button{margin:0}
@media (max-width:900px){
  .row>div,.workspace-fields>div:first-child,.workspace-fields>div:last-child,.output-fields>div:first-child,.output-fields>div:last-child{flex:1 1 100%}
  .bar select{max-width:100%} .bar label{flex:1 1 100%}
  main,.masthead{padding-left:16px;padding-right:16px}
  section{padding:15px}
}
@media (max-width:700px){
  .masthead{padding-top:14px;padding-bottom:14px;gap:10px}
  .local-state{display:none}
  header p{font-size:11px}
  .tabs{grid-template-columns:repeat(2,minmax(0,1fr))}
  .tab{padding:9px 10px}
  .tab small{font-size:11px}
  .card b{font-size:21px}
  .scrollbox{max-height:48vh}
  .bar{position:static}
  .cards{grid-template-columns:repeat(2,minmax(0,1fr))}
  #dupTools{display:flex;flex-wrap:wrap;gap:5px}
}
@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{animation-duration:.01ms!important;transition:none!important}
  .pfill.indeterminate{width:40%!important;animation:none}
}
</style>
</head>
<body>
<header><div class="masthead"><div class="brand"><div class="brand-mark" aria-hidden="true"><svg viewBox="0 0 32 32" fill="none" stroke="#edf7f1" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4 10h9l3 3h12v12H4z"/><path d="M7 7h8l3 3h7M9 18h14M9 22h9"/></svg></div><div><h1>理巢 <span>NestTidy</span></h1><p>本地文件整理工作台 · 先预览，再执行</p></div></div><span class="local-state">本地离线 · 试运行只读</span></div></header>
<main>
<div class="tabs" id="tabs"><span class="tab active" role="tab" tabindex="0" aria-selected="true" id="tab-preview" aria-controls="panel-preview" data-tab="preview"><b>整理预览</b><small>看清会怎么变，再执行</small></span><span class="tab" role="tab" tabindex="-1" aria-selected="false" id="tab-dup" aria-controls="panel-dup" data-tab="dup"><b>重复文件</b><small>两阶段比对，点选删除</small></span><span class="tab" role="tab" tabindex="-1" aria-selected="false" id="tab-repair" aria-controls="panel-repair" data-tab="repair"><b>权限修复</b><small>只读 / 隐藏 / 网络标记</small></span><span class="tab" role="tab" tabindex="-1" aria-selected="false" id="tab-diag" aria-controls="panel-diag" data-tab="diag"><b>诊断信息</b><small>异常项、规则、历史</small></span></div>
<section aria-labelledby="workspaceTitle"><div class="workspace-heading"><h2 id="workspaceTitle">工作目录</h2><small>四个功能页共享源目录</small></div><div class="row workspace-fields"><div><label for="root">源目录</label><input type="text" id="root" placeholder="把文件夹路径粘贴到这里，例如 F:\\私人定制\\【系列作品】" autocomplete="off" spellcheck="false"><p class="roothint" id="rootHint"></p></div><div><label for="mode">整理单位（仅整理预览）</label><select id="mode"><option value="batch">批量：子文件夹各自成包</option><option value="single">单个：整个文件夹算一包</option></select></div></div>
  <p class="steps" id="steps">三步上手：<b>①</b> 粘贴或拖入源目录路径 → <b>②</b> 点「试运行」看对照表 → <b>③</b> 确认执行（原地整理可撤销，复制模式不动源目录）。</p>
  <p class="muted" id="modeHint"></p>
  <div class="row output-fields"><div><label for="execMode">执行方式</label><select id="execMode"><option value="move">原地整理</option><option value="copy">复制到输出目录</option></select></div><div><label for="output">输出目录（复制模式使用）</label><input type="text" id="output" placeholder="例如 F:\\私人定制\\理巢输出_20261118" spellcheck="false"></div></div></section>
<div class="msgbar" id="msgbar" role="status" aria-live="polite"><span class="muted" id="msg">粘贴源目录路径后点「试运行」——试运行只读，不会改动磁盘上的任何东西。</span></div>
<div class="panel" id="panel-preview" role="tabpanel" aria-labelledby="tab-preview"><section><h2>整理预览</h2>
  <div class="bar"><button onclick="makePlan()">试运行</button><button class="secondary" onclick="exportReport()">导出报告文件</button><button class="secondary" onclick="makeManual()">生成 PDF 手册</button><span class="sp"></span></div>
  <div class="pbar"><div class="pfill" id="planFill"></div></div><p class="muted" id="planProg">尚未试运行。扫描时显示已处理的文件数量。</p>
  <section id="summarySection" class="hidden"><div class="headline" id="headline"></div><div class="cards" id="cards"></div></section>
  <section id="previewSection" class="hidden"><div class="bar"><span class="chips" id="filters" style="margin:0"></span><span class="sp"></span><span class="muted" id="execModeSummary"></span><button id="execBtn" onclick="executePlan()">确认执行</button></div>
    <p class="muted" id="execHint" style="margin:0 0 4px"></p>
    <div class="task-progress idle" id="execProgress"><div class="pbar"><div class="pfill" id="execFill"></div></div><p class="muted" id="execProg">尚未开始。</p></div><div id="execResult" class="scrollbox"></div>
    <div class="task-progress idle" id="undoProgress"><div class="pbar"><div class="pfill" id="undoFill"></div></div><p class="muted" id="undoProg">尚未撤销。</p></div>
    <p class="muted">左列是现在的位置（相对资源包），右列是整理后的位置；「藏了 N 层」表示这个文件现在被 N 层壳文件夹压着。<b>双击任意一行</b>在资源管理器里打开它所在的目录。</p><div class="preview-nav"><label for="groupSelect">资源包</label><select id="groupSelect"></select><button class="secondary" id="groupPrev">上一包</button><button class="secondary" id="groupNext">下一包</button><label for="groupJump">包序号</label><input type="number" id="groupJump" min="1" value="1"><button class="secondary" id="groupGo">跳转</button><span id="groupCount" class="muted"></span></div><div id="preview" class="scrollbox" tabindex="0" role="region" aria-label="整理前后对照"></div><div class="preview-nav" id="pageNav"><button class="secondary" id="pagePrev">上一页</button><button class="secondary" id="pageNext">下一页</button><span class="muted" id="pageCount" role="status"></span><label for="pageJump">跳至</label><input type="number" id="pageJump" min="1" value="1"><button class="secondary" id="pageGo">跳转</button></div></section>
  <section id="foldersSection" class="hidden"><h3>会被清掉的空文件夹</h3><p class="muted">只有文件被提走、文件夹真的变空之后才会删；非空目录一律保留。</p><div id="folders" class="scrollbox" tabindex="0" role="region" aria-label="空目录清单"></div></section>
</section></div>
<div class="panel hidden" id="panel-dup" role="tabpanel" aria-labelledby="tab-dup"><section><h2>重复文件</h2>
  <div class="bar"><label>初筛口径<select id="dupLevel"><option value="standard">标准（推荐）</option><option value="fast">快速</option><option value="deep">深度</option></select></label><label>删除方式<select id="delMode"><option value="recycle">移入回收站</option><option value="purge">彻底删除</option></select></label><button onclick="runDuplicate()">诊断重复文件</button><span class="sp"></span><span id="dupTools"></span><span class="muted" id="dupSelNote"></span></div>
  <p class="muted" id="dupLevelHint"></p>
  <p class="muted">第一遍按你选的级别初筛，程序按大小和全文件内容的 SHA-256 内部签名复核（深度模式直接复用签名）：各真实重复子组独立显示，单例标成「复核剔除」，永不进入可删名单。逐个勾选要删的副本（每组至少留一份），再点删除——程序不会自己删任何东西。<b>右键任意一行</b>可在资源管理器里定位到那个文件。</p>
  <div class="pbar"><div class="pfill" id="dupFill"></div></div><p class="muted" id="dupProg">尚未诊断。</p>
  <div id="dupResult" class="scrollbox" tabindex="0" role="region" aria-label="重复文件清单"><p class="muted">尚未诊断。</p></div></section></div>
<div class="panel hidden" id="panel-repair" role="tabpanel" aria-labelledby="tab-repair"><section><h2>权限修复</h2>
  <div class="bar"><label><input type="checkbox" id="fixReadonly" style="width:auto"> 清除只读属性</label><label><input type="checkbox" id="fixHidden" style="width:auto"> 清除普通隐藏属性</label><label><input type="checkbox" id="fixZone" style="width:auto"> 清除 Zone.Identifier（来自网络的标记）</label><button onclick="runRepair()">执行所选权限修复</button></div>
  <div class="pbar"><div class="pfill" id="repairFill"></div></div><p class="muted" id="repairProg">尚未开始。</p>
  <div id="repairResult" class="scrollbox"></div></section></div>
<div class="panel hidden" id="panel-diag" role="tabpanel" aria-labelledby="tab-diag"><section><h2>诊断信息</h2>
  <div class="bar"><button onclick="makePlan(true)">重新扫描异常项</button><button class="secondary" onclick="loadHistory()">刷新历史</button><span class="sp"></span><span class="muted">只读信息；整理前不用看，异常项来自最近一次扫描。</span></div>
  <h3>异常项（这些文件不会被动）</h3><div id="issues" class="scrollbox" tabindex="0" role="region" aria-label="异常项清单"></div>
  <h3>规则分类计数与边界检查</h3><div id="ruleStats" class="scrollbox"></div><div id="boundaryNote"></div>
  <h3>任务与报告历史（最近 50 条）</h3><div id="history" class="scrollbox"></div>
  <label for="manualUndoPath">旧事务日志路径（也可用于历史列表之外的任务）</label><div class="row"><input id="manualUndoPath" type="text" placeholder="粘贴 transaction_log.jsonl 的完整路径"><button class="secondary" id="manualUndoBtn">按日志撤销</button></div>
  <div class="task-progress idle"><div class="pbar"><div class="pfill" id="historyUndoFill"></div></div><p class="muted" id="historyUndoProg">尚未撤销。</p></div><div id="historyUndoResult" class="scrollbox"></div>
  <h3>健康检查</h3><div id="health"></div></section></div>
</main>
<div class="overlay hidden" id="overlay"><div class="dialog" role="dialog" aria-modal="true" aria-labelledby="dialogTitle" aria-describedby="dialogBody" id="dialogBox">
  <h3 class="dtitle" id="dialogTitle"></h3><div class="dbody" id="dialogBody"></div>
  <div class="dactions"><button class="secondary dno">取消</button><button class="dok">确认</button></div>
</div></div>
<script>
let currentPlanId = null, lastPlan = null, lastDup = null, filterMode = 'all', busy = false, planExecuted = false, planMode = null;
const root = document.getElementById('root'), mode = document.getElementById('mode'), output = document.getElementById('output');
const execMode = document.getElementById('execMode'), dupLevel = document.getElementById('dupLevel'), delMode = document.getElementById('delMode');
const fixReadonly = document.getElementById('fixReadonly');
const fixHidden = document.getElementById('fixHidden');
const fixZone = document.getElementById('fixZone');

const ISSUE_TEXT = {
  DOWNLOAD_IN_PROGRESS:'下载还没完成，先不动', SKIPPED_SYMLINK:'符号链接，不跟随不处理',
  SKIPPED_REPARSE_POINT:'目录联接（junction），不进入', SKIPPED_SYSTEM_FILE:'系统文件，跳过',
  SKIPPED_NESTTIDY_OUTPUT:'理巢自己的报告/输出/隔离目录，跳过', IGNORED_ROOT_FILE:'批量模式下的散落文件，原地不动',
  SOURCE_MISSING:'源文件不在了', SOURCE_CHANGED:'计划生成后文件被改过，本次跳过',
  TARGET_CONFLICT:'目标位置已有文件，不覆盖', PERMISSION_DENIED:'没有权限', FILE_LOCKED:'文件被别的程序占用',
  OUT_OF_BOUND:'目标跑到整理范围之外了，已拦截', RMDIR_SKIPPED_NON_EMPTY:'文件夹不是空的，保留'
};
function esc(s){return String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function fmtBytes(n){n=Number(n||0);if(!n)return '0 B';const u=['B','KB','MB','GB','TB'];let i=0,v=n;while(v>=1024&&i<u.length-1){v/=1024;i++;}return (i?(v.toFixed(1)):(v.toFixed(0)))+' '+u[i];}
function pathCell(p){return '<code>'+esc(String(p).split('\\\\').join(' › '))+'</code>';}
function fileName(p){const s=String(p);return s.slice(Math.max(s.lastIndexOf('\\\\'),s.lastIndexOf('/'))+1)||s;}
function issueText(code,message){return ISSUE_TEXT[code] || message || code || '';}
async function api(path, body){
  const res = await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});
  const data = await res.json();
  if(!res.ok) throw new Error(data.error || res.statusText);
  return data;
}
function say(text, isError){
  const bar = document.getElementById('msgbar'), el = document.getElementById('msg');
  el.textContent = text;
  bar.className = 'msgbar' + (isError ? ' error' : '');
}
function setBusy(on){
  if(busy === on) return;
  busy = on;
  document.querySelectorAll('button').forEach(b=>{ if(!b.classList.contains('dok') && !b.classList.contains('dno')) b.disabled = on || b.dataset.pageDisabled === 'true'; });
  if(!on){
    refreshDupControls();
    if(lastPlan){refreshGroupNav();pageControls(previewTotal,previewOffset,previewLoading);}
    const btn = document.getElementById('execBtn');
    if(btn && lastPlan && !planExecuted && planMode !== execMode.value) btn.disabled = true;  // 执行方式漂移后保持禁用
  }
}
function guard(fn){
  return async function(...args){
    if(busy){ say('上一步还在执行，请勿重复操作。',true); return; }
    setBusy(true);
    try{ await fn.apply(this, args); }
    finally{ setBusy(false); }
  };
}
const PHASE_LABEL = {扫描:'扫描目录', 计划:'生成计划', 报告:'导出报告', 执行:'执行整理', 撤销:'撤销整理', 修复:'修复属性', 去重:'重复文件'};
function renderProgress(d, fill, text){
  // 终态只读后端的 status：前端不根据「有没有抛异常」自己判定成功。
  if(d.status === 'ok'){
    fill.classList.remove('failed','indeterminate'); fill.style.width = '100%';
  }else if(d.status === 'failed'){
    fill.classList.remove('indeterminate'); fill.classList.add('failed');
  }else if(d.total){
    fill.classList.remove('indeterminate');
    fill.style.width = Math.min(100, Math.round(d.done / d.total * 100)) + '%';
  }else{
    // total=0 是总量未知：流动条表示在工作，不画假百分比。
    fill.classList.add('indeterminate');
  }
  const pct = d.total ? Math.round(d.done / d.total * 100) : 0;
  const count = d.total ? d.done + ' / ' + d.total + ' · ' + pct + '%' : (d.done ? '已处理 ' + d.done + ' 个' : '正在准备');
  const phase = PHASE_LABEL[d.phase] || d.phase || '处理中';
  const tail = d.status === 'failed' ? ' · 失败：' + (d.note || '请看上方提示')
    : d.status === 'ok' ? ' · 完成' : ' · 请勿重复操作';
  text.textContent = phase + '：' + count + (d.label ? ' · ' + d.label : '') + tail;
  if(d.status === 'failed') text.className = 'error';
  else text.className = 'muted';
}
function trackProgress(fillId, textId){
  const key = 'p' + Math.random().toString(36).slice(2), fill = document.getElementById(fillId), text = document.getElementById(textId);
  const region = fill.closest('.task-progress');
  if(region) region.classList.remove('idle');
  fill.classList.remove('failed','indeterminate'); fill.style.width = '0';
  let transportFailed = false;
  const tick = () => fetch('/api/progress?key=' + key).then(r => r.json()).then(d => {
    if(!transportFailed) renderProgress(d, fill, text);
    else if(d.status === 'failed' || d.status === 'ok') renderProgress(d, fill, text);
  }).catch(() => {});
  const timer = setInterval(tick, 300); tick();
  const fail = () => {
    // 请求没有拿到服务端响应（断网/代理/服务退出）时，后端不可能写终态。
    // 这里只兜底“传输失败”，不把正常响应自行判成成功。
    transportFailed = true;
    clearInterval(timer);
    fill.classList.remove('indeterminate'); fill.classList.add('failed');
    text.className = 'error';
    text.textContent = '请求未完成：未收到服务端的最终进度，请查看上方错误信息。';
    tick();
  };
  return {key: key, fail: fail, stop: () => { clearInterval(timer); if(!transportFailed) tick(); }};
}
function showModal(opts){
  return new Promise(resolve=>{
    const ov = document.getElementById('overlay'), box = document.getElementById('dialogBox');
    box.querySelector('.dtitle').textContent = opts.title || '请确认';
    box.querySelector('.dbody').innerHTML = opts.body || '';
    const okBtn = box.querySelector('.dok'), noBtn = box.querySelector('.dno');
    okBtn.textContent = opts.confirmText || '确认';
    okBtn.className = 'dok' + (opts.danger ? ' danger' : '');
    const previousFocus = document.activeElement;
    function close(v){
      ov.classList.add('hidden'); okBtn.onclick=noBtn.onclick=ov.onclick=null;
      document.removeEventListener('keydown', onKey);
      if(previousFocus && !previousFocus.disabled) previousFocus.focus();
      resolve(v);
    }
    function onKey(e){
      if(e.key==='Escape'){ e.preventDefault(); close(false); }
      if(e.key==='Tab'){
        e.preventDefault();
        if(document.activeElement===okBtn) noBtn.focus(); else okBtn.focus();
      }
    }
    okBtn.onclick = ()=>close(true); noBtn.onclick = ()=>close(false);
    ov.onclick = e=>{ if(e.target===ov) close(false); };
    document.addEventListener('keydown', onKey);
    ov.classList.remove('hidden');
    if(opts.danger) noBtn.focus(); else okBtn.focus();
  });
}
async function validateRoot(){
  const hint = document.getElementById('rootHint');
  const raw = root.value.trim();
  if(!raw){ hint.textContent = ''; hint.className='roothint'; return; }
  try{
    const data = await api('/api/validate-root',{path:raw});
    if(data.ok){ hint.textContent = '✓ 目录存在'+(data.resolved && data.resolved!==raw ? '：'+data.resolved : ''); hint.className='roothint ok'; }
    else { hint.textContent = '✗ '+data.error; hint.className='roothint err'; }
  }catch(e){ hint.textContent = '✗ '+e.message; hint.className='roothint err'; }
}
function cleanPath(v){ return String(v||'').trim().replace(/^["']+/,'').replace(/["']+$/,''); }
function dropPath(e){
  e.preventDefault(); e.stopPropagation();
  let p = '';
  try{ p = e.dataTransfer.getData('text/uri-list') || e.dataTransfer.getData('text/plain') || ''; }catch(_){}
  if(p.indexOf('file://')===0){
    p = decodeURIComponent(p.slice(7).replace(/^\\/([A-Za-z]:)/,'$1'));
  }
  p = cleanPath(p);
  if(p){ root.value = p; validateRoot(); say('已填入拖入的目录：'+p+'。点「试运行」看看会怎么变。'); }
}
root.addEventListener('blur', ()=>{ if(root.value!==root.value.trim() || /^["']/.test(root.value)) root.value = cleanPath(root.value); validateRoot(); });
root.addEventListener('dragover', e=>{ e.preventDefault(); });
root.addEventListener('drop', dropPath);
root.addEventListener('keydown', e=>{ if(e.key==='Enter'){ e.preventDefault(); makePlan(); } });
output.addEventListener('blur', ()=>{ output.value = cleanPath(output.value); });
const MODE_HINTS = {
  batch: '批量模式：源目录下每个子文件夹视为一个独立资源包，文件最多提到各自资源包根部，不会跨包移动。',
  single: '单个模式：整个源目录当成一个资源包，文件最高提到源目录根部。'
};
const DUP_LEVEL_HINTS = {
  standard: '标准：大小 + 首尾各 1MB 哈希初筛，再自动全文件 SHA-256 复核（推荐）。',
  fast: '快速：大小 + 文件名初筛，同内容异名可能漏检；候选由第二遍全量哈希复核。',
  deep: '深度：直接全文件 SHA-256，最慢但一步到位，不再复核。'
};
const DEL_MODE_HINTS = {
  recycle: '移入回收站：可在回收站里还原；清空回收站才真正释放空间。',
  purge: '彻底删除：立即释放空间，不可撤销，需二次确认。'
};
function updateSelectHints(){
  document.getElementById('modeHint').textContent = MODE_HINTS[mode.value] || '';
  document.getElementById('dupLevelHint').textContent = (DUP_LEVEL_HINTS[dupLevel.value]||'') + ' ' + (DEL_MODE_HINTS[delMode.value]||'');
}
mode.addEventListener('change', updateSelectHints);
dupLevel.addEventListener('change', updateSelectHints);
delMode.addEventListener('change', updateSelectHints);
updateSelectHints();
async function makePlanInner(stay){
  const isCopy = execMode.value === 'copy';
  if(isCopy && !cleanPath(output.value)){ say('复制方式要先填输出目录，再点试运行。',true); return; }
  say('正在扫描并生成试运行计划（只读，不改磁盘）...');
  previewRequest++;
  const prog = trackProgress('planFill','planProg');
  try{
    const data = await api('/api/plan',{selected_root:cleanPath(root.value),mode:mode.value,output_root:isCopy?(cleanPath(output.value)||null):null,view:'summary',progress_key:prog.key});
    currentPlanId = data.plan_id; lastPlan = data; planExecuted = false; planMode = isCopy ? 'copy' : 'move';
    ['execProgress','undoProgress'].forEach(id=>document.getElementById(id).classList.add('idle'));
    renderPlan(data); if(!stay){setTab('preview');}
    document.getElementById('steps').classList.add('hidden');
    say('计划已生成：文件 '+data.mapping_count+' 个，操作 '+data.operation_count+' 个，异常 '+(data.issue_total||0)+' 个。磁盘上还没有任何改动。');
  }catch(e){
    invalidatePlan();
    say('生成计划失败：'+e.message+'（上一份计划已失效，不再可执行）',true);
    prog.fail();
  }finally{
    prog.stop();
  }
}
const makePlan = guard(makePlanInner);
function invalidatePlan(){
  previewRequest++; previewGroups=[]; previewGroup=-1; previewOffset=0;
  currentPlanId = null; lastPlan = null; planExecuted = false; planMode = null;
  ['summarySection','previewSection','foldersSection'].forEach(id=>document.getElementById(id).classList.add('hidden'));
}
function renderPlan(plan){
  filterMode='all';
  const v = plan.display.readable;
  document.getElementById('summarySection').classList.remove('hidden');
  document.getElementById('headline').innerHTML = esc(v.headline);
  document.getElementById('cards').innerHTML = v.cards.map(c=>'<div class="card"><div class="metric-label">'+esc(c.label)+'</div><b>'+esc(c.value)+'</b><span>'+esc(c.hint)+'</span></div>').join('');
  document.getElementById('previewSection').classList.remove('hidden');
  document.getElementById('filters').innerHTML = [
    ['all','全部 '+v.counts.total],
    ['changed','只看会变的 '+v.counts.changed],
    ['renamed','只看改名 '+v.counts.renamed],
    ['same','只看原地不动 '+(v.counts.total-v.counts.changed)]
  ].map(x=>'<button type="button" class="chip'+(x[0]===filterMode?' active':'')+'" aria-pressed="'+(x[0]===filterMode)+'" data-f="'+x[0]+'">'+esc(x[1])+'</button>').join('');
  document.querySelectorAll('#filters .chip').forEach(el=>el.addEventListener('click',()=>setFilter(el.dataset.f)));
  initPreviewGroups();
  document.getElementById('foldersSection').classList.add('hidden');
  document.getElementById('folders').innerHTML='';
  if(v.folders.length){
    document.getElementById('foldersSection').classList.remove('hidden');
    mountPagedRows('folders',v.folders,['资源包','会被清掉的文件夹','层级'],
      f=>'<tr data-open="'+esc(f.full_path||'')+'"><td>'+esc(f.root_name)+'</td><td>'+pathCell(f.path)+'</td><td>第 '+f.depth+' 层</td></tr>',{total:v.folder_total});
  } else if (v.output_root) {
    document.getElementById('foldersSection').classList.remove('hidden');
    document.getElementById('folders').innerHTML = '<p class="muted">这次是复制到输出目录，源目录里的空壳会原样保留，所以没有要清的空文件夹。想连空壳一起清掉，就用 move 模式执行。</p>';
  }
  const d = plan.display;
  document.getElementById('ruleStats').innerHTML = '<p class="muted">口径：R0 = 删空目录条数；R1–R5 = 命中该规则的文件条数（一个文件可命中多条）。</p>'
    + table(d.rule_rows, ['rule','name','count'], ['规则','说明','条数']);
  const b = d.boundary;
  document.getElementById('boundaryNote').innerHTML = '<p>已校验 <b>'+b.checked+'</b> 条文件操作，目标越界 <b>'+b.out_of_bound+'</b> 条；边界口径：'+esc(b.label)+'。</p>';
  mountPagedRows('issues',plan.issues,['文件','为什么跳过','代码','状态'],
    i=>'<tr><td>'+pathCell(i.path)+'</td><td>'+esc(issueText(i.code,i.message))+'</td><td class="muted">'+esc(i.code)+'</td><td>'+esc(i.final_status)+'</td></tr>',{total:plan.issue_total,empty:'没有异常项。'});
  updateExecPanel(v);
}
function rowHtml(g,r){
  const tag = r.renamed ? ' <span class="badge warn">改名</span>' : '';
  const depth = r.depth>=2 ? ' <span class="depth">藏了 '+r.depth+' 层</span>' : '';
  const SEP = String.fromCharCode(92);
  const open = g.root_path ? g.root_path + SEP + String(r.before).split(' › ').join(SEP) : '';
  return '<tr data-row data-open="'+esc(open)+'" data-changed="'+(r.changed?'1':'0')+'" data-renamed="'+(r.renamed?'1':'0')+'">'
    + '<td>'+pathCell(r.before)+depth+'</td><td>'+pathCell(r.after)+tag+'</td><td>'+esc(r.action)+'</td>'
    + '<td>'+esc(r.rules.join('；')||'—')+'</td><td>'+esc(r.why)+'</td><td>'+esc(r.size)+'</td></tr>';
}
// 固定页大小，不累计 DOM 或历史文件行；资源包导航仅保留当前一段选项。
const PREVIEW_PAGE = 80, GROUP_OPTIONS = 100;
let previewGroups=[], previewGroup=-1, previewOffset=0, previewTotal=0, previewRequest=0, previewLoading=false;
const groupSelect=document.getElementById('groupSelect');
const groupPrev=document.getElementById('groupPrev'), groupNext=document.getElementById('groupNext');
const groupJump=document.getElementById('groupJump'), groupGo=document.getElementById('groupGo');
const pagePrev=document.getElementById('pagePrev'), pageNext=document.getElementById('pageNext');
const pageJump=document.getElementById('pageJump'), pageGo=document.getElementById('pageGo');
function groupMatches(g){
  return filterMode==='all'||(filterMode==='changed'&&g.changed>0)
    ||(filterMode==='renamed'&&g.renamed>0)||(filterMode==='same'&&g.row_total>g.changed);
}
function initPreviewGroups(){
  previewRequest++;
  previewGroups=lastPlan.display.readable.groups.filter(groupMatches);
  previewGroup=previewGroups.length?0:-1;
  refreshGroupNav();
  return showPreviewPage(0);
}
function refreshGroupNav(){
  const start=Math.max(0,Math.floor(previewGroup/GROUP_OPTIONS)*GROUP_OPTIONS);
  groupSelect.innerHTML=previewGroups.slice(start,start+GROUP_OPTIONS).map((g,i)=>
    '<option value="'+(start+i)+'">'+esc(g.root_name)+' · '+g.row_total+' 文件</option>').join('');
  if(previewGroup>=0)groupSelect.value=String(previewGroup);
  document.getElementById('groupCount').textContent=previewGroups.length?'资源包 '+(previewGroup+1)+' / '+previewGroups.length:'无匹配资源包';
  groupJump.max=String(Math.max(1,previewGroups.length));groupJump.value=String(Math.max(1,previewGroup+1));
  groupJump.disabled=groupGo.disabled=previewGroup<0;
  groupSelect.disabled=previewGroup<0;
  groupPrev.disabled=previewGroup<=0;
  groupNext.disabled=previewGroup<0||previewGroup>=previewGroups.length-1;
}
function chooseGroup(n){
  if(n<0||n>=previewGroups.length)return;
  previewGroup=n;refreshGroupNav();return showPreviewPage(0);
}
function pageControls(total,offset,loading){
  previewTotal=total;previewOffset=offset;previewLoading=loading;
  const pages=Math.max(1,Math.ceil(total/PREVIEW_PAGE)), page=Math.floor(offset/PREVIEW_PAGE)+1;
  pagePrev.disabled=loading||offset<=0;
  pageNext.disabled=loading||offset+PREVIEW_PAGE>=total;
  pageJump.disabled=pageGo.disabled=loading||total===0;
  pageJump.max=String(pages);pageJump.value=String(page);
  document.getElementById('pageCount').textContent=loading?'正在加载…':total?'第 '+page+' / '+pages+' 页 · '+(offset+1)+'–'+Math.min(offset+PREVIEW_PAGE,total)+' / '+total+' 文件':'没有匹配文件';
}
async function showPreviewPage(offset){
  const request=++previewRequest, planId=currentPlanId, g=previewGroups[previewGroup], kind=filterMode;
  const box=document.getElementById('preview');
  if(!g){box.innerHTML='<p class="muted">当前筛选没有匹配文件。</p>';pageControls(0,0,false);return;}
  pageControls(0,offset,true);
  box.innerHTML='<p class="muted">正在加载文件明细…</p>';
  try{
    const data=await api('/api/plan/rows',{plan_id:planId,root:g.root_path,offset:offset,limit:PREVIEW_PAGE,filter:kind});
    if(request!==previewRequest||planId!==currentPlanId)return;
    box.innerHTML='<section class="gblock"><h3>'+esc(g.root_name)+' <span class="muted">（'+g.row_total+' 个文件）</span></h3>'
      +'<table><thead><tr><th style="width:26%">现在在哪</th><th style="width:26%">整理后在哪</th><th>动作</th><th>规则</th><th>为什么要这样</th><th style="width:8%">大小</th></tr></thead><tbody>'
      +data.rows.map(r=>rowHtml(g,r)).join('')+'</tbody></table></section>';
    box.scrollTop=0;pageControls(data.total,offset,false);
  }catch(e){
    if(request!==previewRequest||planId!==currentPlanId)return;
    box.innerHTML='<p class="error">'+esc(e.message)+'</p>';pageControls(0,0,false);say('取文件明细失败：'+e.message,true);
  }
}
groupSelect.addEventListener('change',()=>chooseGroup(Number(groupSelect.value)));
groupPrev.addEventListener('click',()=>chooseGroup(previewGroup-1));
groupNext.addEventListener('click',()=>chooseGroup(previewGroup+1));
function jumpGroup(){return chooseGroup(Math.min(previewGroups.length,Math.max(1,Math.floor(Number(groupJump.value)||1)))-1);}
groupGo.addEventListener('click',jumpGroup);groupJump.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();jumpGroup();}});
pagePrev.addEventListener('click',()=>showPreviewPage(Math.max(0,previewOffset-PREVIEW_PAGE)));
pageNext.addEventListener('click',()=>showPreviewPage(previewOffset+PREVIEW_PAGE));
function jumpPage(){const n=Math.min(Math.max(1,Number(pageJump.value)||1),Math.max(1,Math.ceil(previewTotal/PREVIEW_PAGE)));return showPreviewPage((Math.floor(n)-1)*PREVIEW_PAGE);}
pageGo.addEventListener('click',jumpPage);
pageJump.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();jumpPage();}});
function updateExecPanel(v){
  const isCopy = execMode.value === 'copy';
  document.getElementById('execModeSummary').textContent = isCopy ? '复制到输出目录' : '原地整理 · 将修改源目录';
  const c = (v && v.counts) || {};
  const hintEl = document.getElementById('execHint'), btn = document.getElementById('execBtn');
  const modeDrifted = !!lastPlan && (planMode !== execMode.value);
  btn.classList.toggle('danger', !isCopy && !planExecuted);
  if(planExecuted){
    hintEl.textContent = '这份计划已经执行过了。要看现在的真实状态，请点「重新试运行」生成新计划（旧计划已失效，不会被再次执行）。';
    btn.textContent = '重新试运行（生成新计划）';
    btn.disabled = false;
  } else if(modeDrifted){
    hintEl.textContent = '执行方式已经切换，而计划是按生成时的方式冻结的（含输出目录）。请先点上方「试运行」重新生成计划，再执行。';
    btn.textContent = isCopy ? '确认复制到输出目录' : '确认原地整理';
    btn.disabled = true;
  } else {
    hintEl.textContent = isCopy
      ? '复制方式：把整理后的完整副本写到输出目录，源目录不动。会多占一份空间，看完满意后可以自己删掉源目录。'
      : '原地整理：'+ (c.moved||0) +' 个文件在资源包内换位置，顺手清掉 '+ (c.folders||0) +' 个空壳文件夹。源目录会变，执行前会再确认一次，执行后可撤销。';
    btn.textContent = isCopy ? '确认复制到输出目录' : '确认原地整理';
    btn.disabled = false;
  }
  document.getElementById('execResult').innerHTML = '';
}
function setFilter(f){filterMode=f;document.querySelectorAll('#filters .chip').forEach(c=>{const on=c.dataset.f===f;c.classList.toggle('active',on);c.setAttribute('aria-pressed',String(on));});return initPreviewGroups();}
const runDuplicate = guard(async function runDuplicate(){
  clearDuplicateState();
  const requestedRoot = cleanPath(root.value);
  document.getElementById('dupResult').innerHTML = '<p class="muted">正在第一遍初筛并自动复核（大文件会比较久）...</p>';
  document.getElementById('dupTools').innerHTML = '';
  const prog = trackProgress('dupFill', 'dupProg');
  try{
    const data = await api('/api/duplicates',{selected_root:requestedRoot,level:dupLevel.value,verify:true,view:'paged',progress_key:prog.key});
    lastDup = data; await renderDup(data, requestedRoot);
  }catch(e){ prog.fail(); document.getElementById('dupResult').innerHTML = '<p class="error">'+esc(e.message)+'</p>'; say(e.message,true); }
  finally{ prog.stop(); }
});
const DUP_PAGE=80, DUP_GROUP_PAGE=100;
let dupState=null, dupGroup=0, dupOffset=0;
function clearDuplicateState(){
  dupState=null;lastDup=null;dupGroup=0;dupOffset=0;updateSelNote();
}
async function renderDup(data, requestedRoot=cleanPath(root.value)){
  dupState={data,requestedRoot,eligible:new Map(),selected:new Map(),request:0,loading:false,current:null};
  dupGroup=0;dupOffset=0;
  document.getElementById('dupTools').innerHTML = '<button class="ghost" onclick="checkAll(true)">全选本页可删副本</button><button class="ghost" onclick="checkAll(false)">取消全部勾选</button><button class="danger" onclick="deleteSelected()">删除所选副本</button>';
  await renderDupPage(0,0);
}
async function renderDupPage(index,offset){
  if(!dupState)return;
  const state=dupState, data=state.data, request=++state.request;
  const s=data.stats;
  let html = '<div class="headline">第一遍「'+esc(data.phase1_label)+'」找到 '+s.groups+' 组；自动跑第二遍全量哈希复核，剔除 '+s.recheck_rejected_groups
    + ' 组误判；可删副本 '+s.deletable_files+' 个，可回收 '+fmtBytes(s.reclaimable_bytes)+'。</div>';
  html += '<p class="muted">共 '+data.row_total+' 条明细、'+data.group_total+' 组，按需分页可查看全部诊断结果。</p>';
  const box=document.getElementById('dupResult');
  state.eligible.clear();state.loading=true;
  box.innerHTML=html+'<p class="muted">正在加载明细…</p>';
  if(!data.group_total){state.loading=false;box.innerHTML=html+'<p class="muted">没有发现重复文件。</p>';return;}
  try{
    const start=Math.floor(index/DUP_GROUP_PAGE)*DUP_GROUP_PAGE;
    const [groupPage,page]=await Promise.all([
      api('/api/duplicates/groups',{diagnosis_id:data.diagnosis_id,offset:start,limit:DUP_GROUP_PAGE}),
      api('/api/duplicates/rows',{diagnosis_id:data.diagnosis_id,group:index,offset,limit:DUP_PAGE})
    ]);
    if(state!==dupState||request!==state.request)return;
    dupGroup=index;dupOffset=offset;state.current=page;state.loading=false;
    const g=page.group,rows=page.rows;
    for(const r of rows)if(r.deletable){
      state.eligible.set(r.path,{path:r.path,keep_path:g.keep_path,expected_sha256:g.verified_hash,bytes:Number(r.size),group:g.duplicate_group_id});
    }
    html += '<div class="preview-nav"><label for="dupGroupSelect">重复组</label><select id="dupGroupSelect" onchange="chooseDupGroup(Number(this.value))">'
      +groupPage.groups.map((item,i)=>'<option value="'+(start+i)+'"'+(start+i===index?' selected':'')+'>'+esc(item.duplicate_group_id)+' · '+item.file_count+' 个文件</option>').join('')
      +'</select><button class="secondary" id="dupGroupPrev" onclick="chooseDupGroup(dupGroup-1)">上一组</button><button class="secondary" id="dupGroupNext" onclick="chooseDupGroup(dupGroup+1)">下一组</button><label>组序号 <input type="number" id="dupGroupJump" min="1" max="'+data.group_total+'" value="'+(index+1)+'"></label><button class="secondary" id="dupGroupGo">跳转</button><span class="muted">'+(index+1)+' / '+data.group_total+' 组</span></div>';
    const badge=g.verified?'<span class="badge ok">已复核</span>':'<span class="badge warn">复核剔除</span>';
    html += '<div class="group"><h4>'+badge+' 组 '+esc(g.duplicate_group_id)+' · '+g.file_count+' 个文件 · 每个 '+fmtBytes(g.size)+'</h4><p class="muted">'+esc(g.note)+'；本组可删 '+g.deletable_total+' 个。跨页勾选保留，保留项不可勾选。</p>';
    html += '<table><thead><tr><th style="width:34px">删</th><th>文件</th><th>相对资源包</th><th>大小</th></tr></thead><tbody>';
    const rootPath=data.selected_root||'';
    rows.forEach(r=>{
      const rel = rootPath && String(r.path).startsWith(rootPath) ? String(r.path).slice(rootPath.length).replace(/^[\\\\/]+/,'') : r.path;
      const isKeep = r.suggested_keep;
      const canDel = dupState.eligible.has(r.path);
      const box = canDel
        ? '<input type="checkbox" class="delbox" aria-label="选择 '+esc(fileName(r.path))+'" data-path="'+esc(r.path)+'"'+(dupState.selected.has(r.path)?' checked':'')+'>'
        : (isKeep ? '<span class="badge ok">保留</span>' : '<span class="muted">—</span>');
      html += '<tr data-pick="'+esc(r.path)+'" title="右键在资源管理器中定位"><td>'+box+'</td><td><code title="'+esc(r.path)+'">'+esc(fileName(r.path))+'</code></td><td>'+pathCell(rel)+'</td><td>'+fmtBytes(r.size)+'</td></tr>';
    });
    const pages=Math.max(1,Math.ceil(page.total/DUP_PAGE));
    html += '</tbody></table><div class="preview-nav"><button class="secondary" id="dupPagePrev" onclick="chooseDupPage(dupOffset-DUP_PAGE)">上一页</button><button class="secondary" id="dupPageNext" onclick="chooseDupPage(dupOffset+DUP_PAGE)">下一页</button><span class="muted">'+(offset+1)+'–'+Math.min(offset+DUP_PAGE,page.total)+' / '+page.total+' 个</span><label>页码 <input type="number" id="dupPageJump" min="1" max="'+pages+'" value="'+(Math.floor(offset/DUP_PAGE)+1)+'"></label><button class="secondary" id="dupPageGo">跳转</button></div>';
    if(g.deletable_total)html += '<p><button class="danger" data-g="'+esc(g.duplicate_group_id)+'" onclick="deleteGroup(this.dataset.g)">删除本组已选副本</button></p>';
    box.innerHTML=html+'</div>';box.scrollTop=0;
    document.getElementById('dupGroupGo').addEventListener('click',()=>chooseDupGroup(Number(document.getElementById('dupGroupJump').value)-1));
    document.getElementById('dupPageGo').addEventListener('click',()=>chooseDupPage((Number(document.getElementById('dupPageJump').value)-1)*DUP_PAGE));
    refreshDupControls();updateSelNote();
  }catch(e){
    if(state!==dupState||request!==state.request)return;
    state.loading=false;state.current=null;box.innerHTML='<p class="error">'+esc(e.message)+'</p>';say(e.message,true);
  }
}
function refreshDupControls(){
  if(!dupState||!dupState.current)return;
  for(const [id,disabled] of [['dupGroupPrev',dupGroup<=0],['dupGroupNext',dupGroup>=dupState.data.group_total-1],['dupPagePrev',dupOffset<=0],['dupPageNext',dupOffset+DUP_PAGE>=dupState.current.total]]){
    const btn=document.getElementById(id);if(btn)btn.disabled=busy||disabled;
  }
}
function chooseDupGroup(n){
  if(busy||!dupState||dupState.loading||!Number.isInteger(n)||n<0||n>=dupState.data.group_total)return;
  return renderDupPage(n,0);
}
function chooseDupPage(n){
  if(busy||!dupState||dupState.loading||!dupState.current||!Number.isInteger(n)||n<0||n>=dupState.current.total)return;
  return renderDupPage(dupGroup,n);
}
function checkAll(on){
  if(!dupState||busy||dupState.loading)return;
  if(on){for(const [path,entry] of dupState.eligible)dupState.selected.set(path,entry);}
  else dupState.selected.clear();
  document.querySelectorAll('#dupResult .delbox').forEach(b=>{b.checked=on;});updateSelNote();
}
function updateSelNote(){
  const n=dupState?dupState.selected.size:0;
  const el=document.getElementById('dupSelNote');
  if(el)el.textContent=n?('跨页已选 '+n+' 个'):'';
}
function collect(groupId=null){
  if(!dupState||dupState.loading)return [];
  return [...dupState.selected.values()].filter(e=>groupId===null||e.group===groupId);
}
function deleteGroup(gid){const entries=collect(gid);if(!entries.length){say('这一组没有勾选任何副本，先勾选再删除。',true);return;}doDelete(entries);}
function deleteSelected(){const entries=collect();if(!entries.length){say('先勾选要删除的副本（每组至少留一个，保留项不能勾）。',true);return;}doDelete(entries);}
const doDelete = guard(async function doDelete(entries){
  if(!dupState||cleanPath(root.value)!==dupState.requestedRoot){say('源目录已改变，请重新诊断重复文件。',true);return;}
  const diagnosisRoot=dupState.data.selected_root;
  const delModeValue = delMode.value;
  const bytes = entries.reduce((a,e)=>a+e.bytes,0);
  const label = delModeValue==='purge' ? '彻底删除（不可撤销）' : '移入回收站（可在回收站还原）';
  const ok = await showModal({
    title:'删除重复副本',
    body:'<p>将<b>'+label+'</b> '+entries.length+' 个重复副本，约 '+fmtBytes(bytes)+'。</p><p>每组都保留一份；程序在删除前还会逐个复核内容一致性和保留副本存在性，任何一项不满足都会跳过并说明原因。</p>',
    confirmText:'删除', danger:true
  });
  if(!ok) return;
  if(delModeValue==='purge'){
    const again = await showModal({
      title:'再确认一次',
      body:'<p><b>彻底删除后无法恢复</b>（回收站里也不会有）。确定继续？</p>',
      confirmText:'仍然彻底删除', danger:true
    });
    if(!again) return;
  }
  const prog = trackProgress('dupFill', 'dupProg');
  try{
    const data = await api('/api/duplicates/delete',{selected_root:diagnosisRoot,diagnosis_id:dupState.data.diagnosis_id,mode:delModeValue,confirm:true,progress_key:prog.key,entries:entries.map(e=>({path:e.path,keep_path:e.keep_path,expected_sha256:e.expected_sha256}))});
    clearDuplicateState();
    const done = data.deleted + data.recycled;
    let html = '<div class="headline">已处理 '+done+' 个（回收站 '+data.recycled+' / 彻底删除 '+data.deleted+'），跳过 '+data.skipped+'，失败 '+data.failed+'。'
      + (data.recycled ? '想反悔就去回收站还原——清空回收站之前它们一直占着空间。' : '')
      + '</div>';
    const suffix=data.transaction_log?'<p class="muted">删除日志：<code>'+esc(data.transaction_log)+'</code></p>':'';
    document.getElementById('dupTools').innerHTML = '';
    mountPagedRows('dupResult',data.rows,['文件','结果','说明'],
      r=>'<tr data-pick="'+esc(r.path)+'"><td><code title="'+esc(r.path)+'">'+esc(fileName(r.path))+'</code></td><td>'+esc(r.status)+'</td><td>'+esc(issueText(r.code,r.message))+'</td></tr>',{prefix:html,suffix});
    say('删除完成：成功 '+done+'，跳过 '+data.skipped+'，失败 '+data.failed+'。');
  }catch(e){ prog.fail(); say(e.message,true); }
  finally{ prog.stop(); }
});
const runRepair = guard(async function runRepair(){
  if(!fixReadonly.checked && !fixHidden.checked && !fixZone.checked){
    say('请先勾选至少一项修复动作（清除只读 / 隐藏 / Zone.Identifier），再执行。',true);
    return;
  }
  const prog = trackProgress('repairFill', 'repairProg');
  say('正在执行权限修复...');
  try{
    const data = await api('/api/repair',{selected_root:cleanPath(root.value),clear_readonly:fixReadonly.checked,clear_hidden:fixHidden.checked,clear_zone_identifier:fixZone.checked,progress_key:prog.key});
    const bad = data.rows || [];
    mountPagedTable('repairResult',bad,['path','action','status','message'],['文件','动作','状态','说明'],
      {prefix:bad.length?'<div class="headline">检查 '+data.checked+' 项，异常 '+bad.length+' 项（正常项略）。</div>':'',
       empty:'检查 '+data.checked+' 项，全部正常，没有需要列出的异常。'});
    say('权限修复完成：检查 '+data.checked+' 项，异常 '+bad.length+' 项。');
  }catch(e){ prog.fail(); say(e.message,true); }
  finally{ prog.stop(); }
});
const executePlan = guard(async function executePlan(){
  if(planExecuted){ await makePlanInner(); return; }
  if(!currentPlanId){ say('请先生成试运行计划（点「试运行」）。',true); return; }
  return execMode.value === 'copy' ? executeCopy() : executeMove();
});
async function executeCopy(){
  if(!cleanPath(output.value)){ say('复制方式要先填输出目录。',true); return; }
  const c = ((lastPlan && lastPlan.display && lastPlan.display.readable) || {}).counts || {};
  const ok = await showModal({
    title:'确认复制到输出目录？',
    body:'<p>会把整理后的副本写入：<br><code>'+esc(cleanPath(output.value))+'</code></p><p>源目录<b>一个字节都不改</b>，也不删任何文件夹；代价是多占一份空间（约 ' + fmtBytes(lastPlan && lastPlan.estimated_space || 0) + '），回退就是删掉输出目录。</p>',
    confirmText:'确认复制'
  });
  if(!ok) return;
  const prog = trackProgress('execFill', 'execProg');
  say('正在复制...');
  try{
    const data = await api('/api/execute-copy',{plan_id:currentPlanId,progress_key:prog.key});
    markPlanExecuted();
    const outRoot = (lastPlan && lastPlan.output_root) || cleanPath(output.value);
    mountPagedTable('execResult',data.issues||[],['path','stage','code','message','final_status'],['文件','阶段','代码','说明','状态'],
      {prefix:'<div class="headline">复制完成：完成 '+data.completed+'，跳过 '+data.skipped+'，失败 '+data.failed+'。源目录没有动。</div>'+(data.history_warning?'<p class="error">'+esc(data.history_warning)+'</p>':'')
      + '<p>输出目录：<code>'+esc(outRoot)+'</code> <button class="secondary" data-open-out="'+esc(outRoot)+'">在资源管理器中打开</button></p>'});
    document.querySelectorAll('#execResult button[data-open-out]').forEach(b=>b.addEventListener('click',()=>reveal(b.dataset.openOut,false)));
    say('复制完成：完成 '+data.completed+'，跳过 '+data.skipped+'，失败 '+data.failed+'。源目录没有动，输出目录见下方。');
  }catch(e){ prog.fail(); say(e.message,true); }
  finally{ prog.stop(); }
}
async function executeMove(){
  const c = ((lastPlan && lastPlan.display && lastPlan.display.readable) || {}).counts || {};
  const ok = await showModal({
    title:'确认原地整理？',
    body:'<p>会换位置的文件：<b>'+ (c.moved||0) +' 个</b><br>会清掉的空文件夹：<b>'+ (c.folders||0) +' 个</b></p><p>源目录会被修改，执行后会生成事务日志，可以用「撤销这次整理」还原（事务记录中可确认的空目录也会尝试重建）。</p>',
    confirmText:'确认原地整理', danger:true
  });
  if(!ok) return;
  const prog = trackProgress('execFill', 'execProg');
  say('正在原地整理...');
  try{
    const data = await api('/api/execute-move',{plan_id:currentPlanId,confirm:true,progress_key:prog.key});
    markPlanExecuted();
    mountPagedTable('execResult',data.issues||[],['path','stage','code','message','final_status'],['文件','阶段','代码','说明','状态'],
      {prefix:'<div class="headline">整理完成：完成 '+data.completed+'，跳过 '+data.skipped+'，失败 '+data.failed+(data.swept?'，另清掉 '+data.swept+' 个变空的文件夹':'')+'。</div>'+(data.history_warning?'<p class="error">'+esc(data.history_warning)+'</p>':''),
       suffix:data.transaction_log?'<p class="muted">事务日志：'+esc(data.transaction_log)+' <button class="secondary" data-tx="'+esc(data.transaction_log)+'">撤销这次整理</button></p>':''});
    document.querySelectorAll('#execResult button[data-tx]').forEach(b=>b.addEventListener('click',()=>undoRun(b.dataset.tx)));
    say('整理完成：完成 '+data.completed+'，跳过 '+data.skipped+'，失败 '+data.failed+(data.swept?'，另清掉 '+data.swept+' 个变空的文件夹':'')+'。');
  }catch(e){ prog.fail(); say(e.message,true); }
  finally{ prog.stop(); }
}
function markPlanExecuted(){
  planExecuted = true;
  const v = (lastPlan && lastPlan.display && lastPlan.display.readable) || {};
  updateExecPanel(v);
  const hl = document.getElementById('headline');
  if(hl && hl.textContent.indexOf('已执行') < 0){
    hl.innerHTML = '<span class="badge ok">已执行</span>' + esc(v.headline||'') + ' <span class="muted">（卡片数字是执行前的计划值；要看现状请重新试运行）</span>';
  }
}
const UNDO_ACTION = {move:'搬回原处', mkdir:'删除新建文件夹', rmdir:'空目录删除', recycle:'回收站', delete:'彻底删除', quarantine:'放回原处'};
const undoRun = guard(async function undoRun(logPath, fromHistory=false){
  if(fromHistory && !await showModal({title:'确认撤销原地整理',body:'将根据事务日志复核并恢复可恢复的操作：<code>'+esc(logPath)+'</code>',confirmText:'确认撤销',danger:true}))return;
  const prog = fromHistory?trackProgress('historyUndoFill','historyUndoProg'):trackProgress('undoFill','undoProg');
  try{
    const data = await api('/api/undo',{transaction_log:logPath,progress_key:prog.key});
    mountPagedRows(fromHistory?'historyUndoResult':'execResult',data.rows||[],['动作','路径','结果','说明'],
      r=>'<tr>'+[UNDO_ACTION[r.action]||r.action,r.source,r.status,r.message].map(v=>'<td>'+esc(v)+'</td>').join('')+'</tr>');
    say('撤销完成，逐条结果见下方。');
    if(fromHistory)await loadHistory();
  }catch(e){ prog.fail(); say(e.message,true); }
  finally{ prog.stop(); }
});
const exportReport = guard(async function exportReportInner(){
  if(!currentPlanId){ say('请先生成计划。',true); return; }
  const prog = trackProgress('planFill','planProg');
  try{ const data = await api('/api/report',{plan_id:currentPlanId,progress_key:prog.key}); say('报告已导出：'+data.report_dir); }
  catch(e){ prog.fail(); say(e.message,true); }
  finally{ prog.stop(); }
});
async function loadHistory(){
  try{ const response=await fetch('/api/history'), data=await response.json();
    if(!response.ok)throw Error(data.error||'读取历史失败');
    const states={started:'已开始（结果待确认）',completed:'已结束',partial:'存在失败项'};
    mountPagedRows('history',data,['时间','方式 / 状态','源目录','操作数 / 异常数','报告 / 日志','撤销'],r=>'<tr><td>'+esc(r.executed_at||r.created_at)+'</td><td>'+esc(r.execution_mode?(r.execution_mode==='move'?'原地整理':'复制')+' / '+(states[r.execution_status]||r.execution_status):'报告记录')+'</td><td>'+pathCell(r.selected_root)+'</td><td>'+r.operation_count+' / '+r.issue_count+'</td><td>'+pathCell(r.transaction_log||r.report_dir||'')+'</td><td>'+(r.undo_available?'<button class="secondary" data-history-tx="'+esc(r.transaction_log)+'">撤销</button>':'—')+'</td></tr>',{empty:'暂无任务或报告记录。'});
  }catch(e){say(e.message,true);}
}
document.getElementById('history').addEventListener('click',e=>{const b=e.target.closest('[data-history-tx]');if(b&&!busy)undoRun(b.dataset.historyTx,true);});
document.getElementById('manualUndoBtn').addEventListener('click',()=>{const path=cleanPath(document.getElementById('manualUndoPath').value);if(!path){say('请填写事务日志路径。',true);return;}undoRun(path,true);});
async function makeManual(){
  try{ const data = await api('/api/manual',{}); say('PDF 手册已生成：'+data.manual_pdf); }catch(e){say(e.message,true);}
}
// Read-only result tables retain the received rows but never accumulate DOM.
const RESULT_PAGE=80;
function mountPagedRows(id,rows,heads,renderRow,opts={}){
  const box=document.getElementById(id), total=opts.total??rows.length;
  const note=total>rows.length?'<p class="muted">仅加载 '+rows.length+' / '+total+' 条；完整记录见报告或日志。</p>':'';
  box.innerHTML=(opts.prefix||'')+note+(rows.length?
    '<div class="result-page"></div><div class="preview-nav"><button class="secondary" data-result-prev>上一页</button><button class="secondary" data-result-next>下一页</button><span class="muted" data-result-count role="status"></span><label>跳至 <input type="number" data-result-jump min="1" value="1"></label><button class="secondary" data-result-go>跳转</button></div>'
    :'<p class="muted">'+esc(opts.empty||'无异常项。')+'</p>')+(opts.suffix||'');
  if(!rows.length)return;
  const page=box.querySelector('.result-page'), prev=box.querySelector('[data-result-prev]'),next=box.querySelector('[data-result-next]'),count=box.querySelector('[data-result-count]'),jump=box.querySelector('[data-result-jump]'),go=box.querySelector('[data-result-go]');
  let offset=0;
  const max=Math.ceil(rows.length/RESULT_PAGE);
  jump.max=String(max);
  function show(start){
    offset=Math.max(0,Math.min((max-1)*RESULT_PAGE,start));
    page.innerHTML='<table><thead><tr>'+heads.map(h=>'<th>'+esc(h)+'</th>').join('')+'</tr></thead><tbody>'+rows.slice(offset,offset+RESULT_PAGE).map(renderRow).join('')+'</tbody></table>';
    for(const [btn,disabled] of [[prev,offset===0],[next,offset+RESULT_PAGE>=rows.length]]){
      btn.dataset.pageDisabled=String(disabled);btn.disabled=busy||disabled;
    }
    const n=offset/RESULT_PAGE+1;jump.value=String(n);
    count.textContent='第 '+n+' / '+max+' 页 · '+(offset+1)+'–'+Math.min(offset+RESULT_PAGE,rows.length)+' / '+rows.length+' 条';
  }
  prev.addEventListener('click',()=>{if(!busy)show(offset-RESULT_PAGE);});
  next.addEventListener('click',()=>{if(!busy)show(offset+RESULT_PAGE);});
  function jumpTo(){if(!busy)show((Math.min(max,Math.max(1,Math.floor(Number(jump.value)||1)))-1)*RESULT_PAGE);}
  go.addEventListener('click',jumpTo);jump.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();jumpTo();}});
  if(busy)go.disabled=true;
  show(0);
}
function mountPagedTable(id,rows,cols,heads,opts={}){
  return mountPagedRows(id,rows,heads||cols,r=>'<tr>'+cols.map(c=>'<td>'+esc(Array.isArray(r[c])?r[c].join(' | '):r[c])+'</td>').join('')+'</tr>',opts);
}
function table(rows, cols, heads){
  if(!rows || !rows.length) return '<p class="muted">无异常项。</p>';
  return '<table><thead><tr>'+cols.map((c,i)=>'<th>'+esc(heads?heads[i]:c)+'</th>').join('')+'</tr></thead><tbody>'
    + rows.map(r=>'<tr>'+cols.map(c=>'<td>'+esc(Array.isArray(r[c])?r[c].join(' | '):r[c])+'</td>').join('')+'</tr>').join('')+'</tbody></table>';
}
document.addEventListener('change', e=>{if(e.target&&e.target.classList.contains('delbox')&&dupState){const p=e.target.dataset.path;if(busy||!dupState.eligible.has(p)){e.target.checked=dupState.selected.has(p);return;}if(e.target.checked)dupState.selected.set(p,dupState.eligible.get(p));else dupState.selected.delete(p);updateSelNote();}});
function reveal(path, select){ api('/api/reveal',{path:path,select:!!select}).catch(e=>say(e.message,true)); }
document.addEventListener('dblclick', e=>{ const el = e.target.closest('[data-open]'); if(el){ e.preventDefault(); reveal(el.dataset.open,false); } });
document.addEventListener('contextmenu', e=>{ const el = e.target.closest('[data-pick]'); if(el){ e.preventDefault(); reveal(el.dataset.pick,true); } });
execMode.addEventListener('change', ()=>{ if(lastPlan && lastPlan.display) updateExecPanel(lastPlan.display.readable); });
fetch('/api/health').then(r=>r.json()).then(rows=>{ document.getElementById('health').innerHTML = table(rows,['name','ok','detail'],['检查项','结果','详情']); });
fetch('/api/config').then(r=>r.json()).then(cfg=>{
  if(!cfg.selected_root) return;
  root.value = cfg.selected_root;
  validateRoot();
  say('已从本地配置载入上次的源目录；试运行只读，不会改动磁盘上的任何东西。');
}).catch(()=>{});
const TAB_KEYS = ['preview','dup','repair','diag'];
document.getElementById('tabs').setAttribute('role','tablist');
function setTab(name){
  document.querySelectorAll('#tabs .tab').forEach(t=>{
    const on = t.dataset.tab===name;
    t.classList.toggle('active',on);
    t.setAttribute('aria-selected', on?'true':'false');
    t.tabIndex = on ? 0 : -1;
  });
  document.querySelectorAll('.panel').forEach(p=>p.classList.toggle('hidden',p.id!=='panel-'+name));
  if(name==='diag')loadHistory();
}
document.querySelectorAll('#tabs .tab').forEach((t,i)=>{
  t.addEventListener('click',()=>setTab(t.dataset.tab));
  t.addEventListener('keydown',e=>{
    if(e.key==='Enter' || e.key===' '){ e.preventDefault(); setTab(t.dataset.tab); }
    else if(e.key==='ArrowRight' || e.key==='ArrowLeft'){
      e.preventDefault();
      const next = (i + (e.key==='ArrowRight'?1:-1) + TAB_KEYS.length) % TAB_KEYS.length;
      const el = document.querySelectorAll('#tabs .tab')[next];
      el.focus(); setTab(el.dataset.tab);
    }
  });
});
</script>
</body>
</html>"""
