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
:root{color-scheme:light;font-family:"Segoe UI","Microsoft YaHei",sans-serif;--line:#d8ddda;--ink:#242424;--mute:#687370;--ok:#196f5f;--bad:#9b1c1c;--warn:#8a5a00}
*{box-sizing:border-box} body{margin:0;background:#f7f7f5;color:var(--ink);font-size:14px;line-height:1.55}
header{padding:18px 26px;background:#174b4f;color:#fff} header h1{margin:0;font-size:20px} header p{margin:4px 0 0;opacity:.85;font-size:13px}
main{max-width:1240px;margin:0 auto;padding:18px 22px 60px}
section{background:#fff;border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin:14px 0} h2{font-size:16px;margin:0 0 10px;padding-bottom:8px;border-bottom:1px solid #eef1ef} h3{font-size:14px;margin:14px 0 6px}
label{display:block;font-weight:600;margin:10px 0 5px;font-size:13px} input[type=text],select{width:100%;padding:9px 11px;border:1px solid #b8bfbd;border-radius:6px;background:#fff;font-size:13px}
button{padding:9px 13px;border:0;border-radius:6px;background:var(--ok);color:#fff;cursor:pointer;font-size:13px;margin:4px 6px 4px 0} button:disabled{background:#c3cbc8;cursor:not-allowed;opacity:.6}
button.secondary{background:#495057} button.danger{background:#a4161a} button.ghost{background:#e9efec;color:#174b4f}
table{width:100%;border-collapse:collapse;background:#fff;font-size:12.5px} th,td{padding:7px 8px;border-bottom:1px solid #eef1ef;text-align:left;vertical-align:top;overflow-wrap:anywhere} th{background:#f2f6f4;font-weight:600;white-space:nowrap}
code{font-family:Consolas,"Courier New",monospace;font-size:12px;word-break:break-all}
.row{display:flex;gap:12px;flex-wrap:wrap} .row>div{flex:1 1 240px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;margin-top:12px} .card{border:1px solid var(--line);border-radius:8px;padding:10px 12px;background:#fbfcfb}
.card b{display:block;font-size:20px;line-height:1.2} .card span{color:var(--mute);font-size:12px}
.muted{color:var(--mute)} .error{color:var(--bad)} .ok{color:var(--ok)} .depth{color:var(--mute);font-size:11px}
.headline{background:#eef7f3;border-left:4px solid var(--ok);padding:10px 12px;border-radius:6px;font-size:14px}
.badge{display:inline-block;padding:1px 7px;border-radius:10px;font-size:11px;border:1px solid;margin-right:6px}
.badge.ok{color:#0d5e4d;border-color:#9ecdc0;background:#eaf6f1} .badge.warn{color:#7a5200;border-color:#e2c98a;background:#fdf6e3} .badge.bad{color:#9b1c1c;border-color:#e3a8a8;background:#fdeeee}
.chips{margin:8px 0} .chip{display:inline-block;padding:4px 10px;border:1px solid var(--line);border-radius:14px;margin:0 6px 6px 0;cursor:pointer;font-size:12px;background:#fff} .chip.active{background:var(--ok);color:#fff;border-color:var(--ok)}
.group{border:1px solid var(--line);border-radius:8px;margin:10px 0;padding:10px 12px} .group h4{margin:0 0 6px;font-size:13px} details summary{cursor:pointer;font-weight:600;padding:4px 0}
.filtered{display:none} .hidden{display:none!important}
.pbar{height:8px;background:#e6ecea;border-radius:4px;overflow:hidden;margin:8px 0} .pfill{height:100%;width:0;background:var(--ok);transition:width .2s} .pfill.failed{background:var(--bad)}
.bar{position:sticky;top:0;z-index:6;display:flex;gap:10px;align-items:center;flex-wrap:wrap;background:#fff;padding:8px 0;margin:-4px 0 6px;border-bottom:1px solid #eef1ef} .bar .sp{flex:1 1 40px} .bar label{margin:0;display:inline-flex;align-items:center;gap:8px;font-weight:600;font-size:13px} .bar select{width:auto;max-width:300px} .bar button{margin:0}
.tabs{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:0 0 16px} .tab{padding:12px 14px;border:1px solid var(--line);border-radius:10px;cursor:pointer;background:#fff;text-align:center}
.tab:focus-visible{outline:2px solid var(--ok);outline-offset:2px} .tab b{display:block;font-size:15px} .tab small{color:var(--mute);font-size:12px}
.tab.active{background:var(--ok);border-color:var(--ok);color:#fff} .tab.active small{color:#d8ece5} .panel>section>section{background:none;border:0;padding:0;margin:12px 0} .scrollbox{max-height:58vh;overflow:auto;border:1px solid #eef1ef;border-radius:8px;padding:2px 6px} .scrollbox th{position:sticky;top:0;z-index:2}
pre{white-space:pre-wrap;background:#f5f7f6;border:1px solid var(--line);border-radius:6px;padding:8px;font-size:12px;margin:6px 0}
.msgbar{background:#fff;border:1px solid var(--line);border-left:4px solid var(--mute);border-radius:8px;padding:9px 14px;margin:0 0 14px;font-size:13px}
.msgbar.error{border-left-color:var(--bad);background:#fdf3f3;color:var(--bad)}
.steps{color:var(--mute);font-size:12.5px;margin:8px 0 0} .steps b{color:var(--ink)}
.roothint{font-size:12px;margin:4px 0 0;min-height:18px} .roothint.ok{color:var(--ok)} .roothint.err{color:var(--bad)}
.overlay{position:fixed;inset:0;background:rgba(20,30,28,.45);z-index:50;display:flex;align-items:center;justify-content:center;padding:20px}
.dialog{background:#fff;border-radius:12px;max-width:520px;width:100%;padding:20px 22px;box-shadow:0 12px 40px rgba(0,0,0,.25)}
.dialog h3{margin:0 0 10px;font-size:16px} .dialog .dbody{font-size:13.5px;margin:0 0 16px} .dialog .dbody p{margin:6px 0} .dialog .dactions{text-align:right}
@media (max-width:900px){
  .row>div{flex:1 1 100%}
  .bar select{max-width:100%} .bar label{flex:1 1 100%} .bar{padding:8px 0 2px}
  main{padding:14px 14px 50px} section{padding:14px}
}
@media (max-width:700px){
  .tabs{grid-template-columns:repeat(2,1fr)} .tab{padding:10px 8px} .tab small{display:none}
  .card b{font-size:17px} .scrollbox{max-height:46vh}
}
</style>
</head>
<body>
<header><h1>理巢 NestTidy</h1>
  <p>本地离线资源目录剥壳整理 · 试运行只读：先看清「哪些文件会换位置」，再决定复制、移动，或者删掉多余副本；每一步都要你亲手点按钮</p></header>
<main>
<div class="tabs" id="tabs"><span class="tab active" role="tab" tabindex="0" aria-selected="true" data-tab="preview"><b>整理预览</b><small>看清会怎么变，再执行</small></span><span class="tab" role="tab" tabindex="0" aria-selected="false" data-tab="dup"><b>重复文件</b><small>两阶段比对，点选删除</small></span><span class="tab" role="tab" tabindex="0" aria-selected="false" data-tab="repair"><b>权限修复</b><small>只读 / 隐藏 / 网络标记</small></span><span class="tab" role="tab" tabindex="0" aria-selected="false" data-tab="diag"><b>诊断信息</b><small>异常项、规则、历史</small></span></div>
<section><div class="row"><div><label>源目录（四个页签共用这个路径）</label><input type="text" id="root" placeholder="把文件夹路径粘贴到这里，例如 F:\\私人定制\\【系列作品】" autocomplete="off" spellcheck="false"><p class="roothint" id="rootHint"></p></div><div><label>整理单位（只影响「整理预览」）</label><select id="mode"><option value="batch">批量：子文件夹各自成包</option><option value="single">单个：整个文件夹算一包</option></select></div></div>
  <p class="steps" id="steps">三步上手：<b>①</b> 粘贴或拖入源目录路径 → <b>②</b> 点「试运行」看对照表 → <b>③</b> 确认执行（原地整理可撤销，复制模式不动源目录）。</p>
  <p class="muted" id="modeHint"></p>
  <label>输出目录（只在「复制到输出目录」时用；原地整理留空）</label><input type="text" id="output" placeholder="例如 F:\\私人定制\\理巢输出_20261118" spellcheck="false"></section>
<div class="msgbar" id="msgbar"><span class="muted" id="msg">粘贴源目录路径后点「试运行」——试运行只读，不会改动磁盘上的任何东西。</span></div>
<div class="panel" id="panel-preview"><section><h2>整理预览</h2>
  <div class="bar"><button onclick="makePlan()">试运行：看看会变成什么样</button><button class="secondary" onclick="exportReport()">导出报告文件</button><button class="secondary" onclick="makeManual()">生成 PDF 手册</button><span class="sp"></span></div>
  <section id="summarySection" class="hidden"><div class="headline" id="headline"></div><div class="cards" id="cards"></div></section>
  <section id="previewSection" class="hidden"><div class="bar"><span class="chips" id="filters" style="margin:0"></span><span class="sp"></span><label>执行方式<select id="execMode"><option value="move">原地整理</option><option value="copy">复制到输出目录</option></select></label><button id="execBtn" onclick="executePlan()">确认执行</button></div>
    <p class="muted" id="execHint" style="margin:0 0 4px"></p>
    <div class="pbar"><div class="pfill" id="execFill"></div></div><p class="muted" id="execProg">尚未开始。</p><div id="execResult" class="scrollbox"></div>
    <p class="muted">左列是现在的位置（相对资源包），右列是整理后的位置；「藏了 N 层」表示这个文件现在被 N 层壳文件夹压着。<b>双击任意一行</b>在资源管理器里打开它所在的目录。</p><div id="preview" class="scrollbox"></div></section>
  <section id="foldersSection" class="hidden"><h3>会被清掉的空文件夹</h3><p class="muted">只有文件被提走、文件夹真的变空之后才会删；非空目录一律保留。</p><div id="folders" class="scrollbox"></div></section>
</section></div>
<div class="panel hidden" id="panel-dup"><section><h2>重复文件</h2>
  <div class="bar"><label>初筛口径<select id="dupLevel"><option value="standard">标准（推荐）</option><option value="fast">快速</option><option value="deep">深度</option></select></label><label>删除方式<select id="delMode"><option value="recycle">移入回收站</option><option value="purge">彻底删除</option></select></label><button onclick="runDuplicate()">诊断重复文件</button><span class="sp"></span><span id="dupTools"></span><span class="muted" id="dupSelNote"></span></div>
  <p class="muted" id="dupLevelHint"></p>
  <p class="muted">第一遍按你选的级别初筛，程序自动对每组候选再跑一遍「全文件 SHA-256」复核：首尾相同但中段不同的文件标成「复核剔除」，永不进入可删名单。逐个勾选要删的副本（每组至少留一份），再点删除——程序不会自己删任何东西。<b>右键任意一行</b>可在资源管理器里定位到那个文件。</p>
  <div class="pbar"><div class="pfill" id="dupFill"></div></div><p class="muted" id="dupProg">尚未诊断。</p>
  <div id="dupResult" class="scrollbox"><p class="muted">尚未诊断。</p></div></section></div>
<div class="panel hidden" id="panel-repair"><section><h2>权限修复</h2>
  <div class="bar"><label><input type="checkbox" id="fixReadonly" style="width:auto"> 清除只读属性</label><label><input type="checkbox" id="fixHidden" style="width:auto"> 清除普通隐藏属性</label><label><input type="checkbox" id="fixZone" style="width:auto"> 清除 Zone.Identifier（来自网络的标记）</label><button onclick="runRepair()">执行所选权限修复</button></div>
  <div class="pbar"><div class="pfill" id="repairFill"></div></div><p class="muted" id="repairProg">尚未开始。</p>
  <div id="repairResult" class="scrollbox"></div></section></div>
<div class="panel hidden" id="panel-diag"><section><h2>诊断信息</h2>
  <div class="bar"><button onclick="makePlan(true)">重新扫描异常项</button><button class="secondary" onclick="loadHistory()">刷新历史</button><span class="sp"></span><span class="muted">只读信息；整理前不用看，异常项来自最近一次扫描。</span></div>
  <h3>异常项（这些文件不会被动）</h3><div id="issues" class="scrollbox"></div>
  <h3>规则分类计数与边界检查</h3><div id="ruleStats" class="scrollbox"></div><div id="boundaryNote"></div>
  <h3>历史</h3><div id="history" class="scrollbox"></div>
  <h3>健康检查</h3><div id="health"></div></section></div>
</main>
<div class="overlay hidden" id="overlay"><div class="dialog" role="dialog" aria-modal="true" id="dialogBox">
  <h3 class="dtitle"></h3><div class="dbody"></div>
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
  document.querySelectorAll('button').forEach(b=>{ if(!b.classList.contains('dok') && !b.classList.contains('dno')) b.disabled = on; });
  if(!on){
    const btn = document.getElementById('execBtn');
    if(btn && lastPlan && !planExecuted && planMode !== execMode.value) btn.disabled = true;  // 执行方式漂移后保持禁用
  }
}
function guard(fn){
  return async function(...args){
    if(busy) return;
    setBusy(true);
    try{ await fn.apply(this, args); }
    finally{ setBusy(false); }
  };
}
function trackProgress(fillId, textId){
  const key = 'p' + Math.random().toString(36).slice(2), fill = document.getElementById(fillId), text = document.getElementById(textId);
  const tick = () => fetch('/api/progress?key=' + key).then(r => r.json()).then(d => {
    const pct = d.total ? Math.round(d.done / d.total * 100) : 0;
    fill.style.width = (d.total ? pct : 5) + '%';
    text.textContent = (d.total ? d.done + ' / ' + d.total + ' · ' + pct + '%' : '正在准备') + (d.label ? ' · ' + d.label : '');
  }).catch(() => {});
  const timer = setInterval(tick, 300); tick();
  return {key: key, stop: (ok) => { clearInterval(timer); if(ok){ fill.style.width='100%'; fill.classList.remove('failed'); } else { fill.classList.add('failed'); } }};
}
function showModal(opts){
  return new Promise(resolve=>{
    const ov = document.getElementById('overlay'), box = document.getElementById('dialogBox');
    box.querySelector('.dtitle').textContent = opts.title || '请确认';
    box.querySelector('.dbody').innerHTML = opts.body || '';
    const okBtn = box.querySelector('.dok'), noBtn = box.querySelector('.dno');
    okBtn.textContent = opts.confirmText || '确认';
    okBtn.className = 'dok' + (opts.danger ? ' danger' : '');
    function close(v){ ov.classList.add('hidden'); okBtn.onclick=noBtn.onclick=ov.onclick=null; document.removeEventListener('keydown', onKey); resolve(v); }
    function onKey(e){ if(e.key==='Escape') close(false); }
    okBtn.onclick = ()=>close(true); noBtn.onclick = ()=>close(false);
    ov.onclick = e=>{ if(e.target===ov) close(false); };
    document.addEventListener('keydown', onKey);
    ov.classList.remove('hidden');
    okBtn.focus();
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
  fast: '快速：大小 + 文件名初筛，误判最多，靠第二遍全量哈希兜底。',
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
  say('正在扫描并生成试运行计划（只读，不改磁盘）...');
  try{
    const isCopy = execMode.value === 'copy';
    const data = await api('/api/plan',{selected_root:cleanPath(root.value),mode:mode.value,output_root:isCopy?(cleanPath(output.value)||null):null});
    currentPlanId = data.plan_id; lastPlan = data; planExecuted = false; planMode = isCopy ? 'copy' : 'move'; renderPlan(data); if(!stay){setTab('preview');}
    document.getElementById('steps').classList.add('hidden');
    say('计划已生成：共 '+data.operations.length+' 个操作，'+data.issues.length+' 个异常。磁盘上还没有任何改动。');
  }catch(e){
    invalidatePlan();
    say('生成计划失败：'+e.message+'（上一份计划已失效，不再可执行）',true);
  }
}
const makePlan = guard(makePlanInner);
function invalidatePlan(){
  currentPlanId = null; lastPlan = null; planExecuted = false; planMode = null;
  ['summarySection','previewSection','foldersSection'].forEach(id=>document.getElementById(id).classList.add('hidden'));
}
function renderPlan(plan){
  const v = plan.display.readable;
  document.getElementById('summarySection').classList.remove('hidden');
  document.getElementById('headline').innerHTML = esc(v.headline);
  document.getElementById('cards').innerHTML = v.cards.map(c=>'<div class="card"><b>'+esc(c.value)+'</b>'+esc(c.label)+'<span>'+esc(c.hint)+'</span></div>').join('');
  document.getElementById('previewSection').classList.remove('hidden');
  const allRows = v.groups.reduce((acc,g)=>acc.concat(g.rows),[]);
  const changedCount = allRows.filter(r=>r.changed).length;
  document.getElementById('filters').innerHTML = [
    ['all','全部 '+allRows.length],
    ['changed','只看会变的 '+changedCount],
    ['renamed','只看改名 '+v.counts.renamed],
    ['same','只看原地不动 '+v.counts.in_place]
  ].map(x=>'<span class="chip'+(x[0]===filterMode?' active':'')+'" data-f="'+x[0]+'">'+esc(x[1])+'</span>').join('');
  document.querySelectorAll('#filters .chip').forEach(el=>el.addEventListener('click',()=>setFilter(el.dataset.f)));
  let html = '';
  v.groups.forEach(g=>{
    const moved = g.rows.filter(r=>r.changed).length;
    const renamed = g.rows.filter(r=>r.renamed).length;
    html += '<section class="gblock" data-changed="'+(moved?1:0)+'" data-renamed="'+(renamed?1:0)+'" data-same="'+(g.rows.length-moved?1:0)+'">'
      + '<h3>'+esc(g.root_name)+' <span class="muted">（'+g.rows.length+' 个文件，会换位置 '+g.moved+' 个，改名 '+g.renamed+' 个）</span></h3>'
      + '<table><thead><tr><th style="width:26%">现在在哪</th><th style="width:26%">整理后在哪</th><th>动作</th><th>规则</th><th>为什么要这样</th><th style="width:8%">大小</th></tr></thead><tbody>';
    g.rows.forEach(r=>{
      const tag = r.renamed ? ' <span class="badge warn">改名</span>' : '';
      const depth = r.depth>=2 ? ' <span class="depth">藏了 '+r.depth+' 层</span>' : '';
      const open = g.root_path ? g.root_path + '\\\\' + String(r.before).split(' › ').join('\\\\') : '';
      html += '<tr data-row data-open="'+esc(open)+'" data-changed="'+(r.changed?'1':'0')+'" data-renamed="'+(r.renamed?'1':'0')+'">'
        + '<td>'+pathCell(r.before)+depth+'</td>'
        + '<td>'+pathCell(r.after)+tag+'</td>'
        + '<td>'+esc(r.action)+'</td>'
        + '<td>'+esc(r.rules.join('；')||'—')+'</td>'
        + '<td>'+esc(r.why)+'</td>'
        + '<td>'+esc(r.size)+'</td></tr>';
    });
    html += '</tbody></table></section>';
  });
  html += '<p id="previewEmpty" class="muted" style="display:none">当前筛选下，这些资源包都不受这次整理影响——切回「全部」可以看完整对照。</p>';
  document.getElementById('preview').innerHTML = html || '<p class="muted">没有需要整理的文件。</p>';
  if(v.folders.length){
    document.getElementById('foldersSection').classList.remove('hidden');
    document.getElementById('folders').innerHTML = '<table><thead><tr><th>资源包</th><th>会被清掉的文件夹</th><th>层级</th></tr></thead><tbody>'
      + v.folders.map(f=>'<tr data-open="'+esc(f.full_path||'')+'"><td>'+esc(f.root_name)+'</td><td>'+pathCell(f.path)+'</td><td>第 '+f.depth+' 层</td></tr>').join('')+'</tbody></table>';
  } else if (v.output_root) {
    document.getElementById('foldersSection').classList.remove('hidden');
    document.getElementById('folders').innerHTML = '<p class="muted">这次是复制到输出目录，源目录里的空壳会原样保留，所以没有要清的空文件夹。想连空壳一起清掉，就用 move 模式执行。</p>';
  }
  const d = plan.display;
  document.getElementById('ruleStats').innerHTML = '<p class="muted">口径：R0 = 删空目录条数；R1–R5 = 命中该规则的文件条数（一个文件可命中多条）。</p>'
    + table(d.rule_rows, ['rule','name','count'], ['规则','说明','条数']);
  const b = d.boundary;
  document.getElementById('boundaryNote').innerHTML = '<p>已校验 <b>'+b.checked+'</b> 条文件操作，目标越界 <b>'+b.out_of_bound+'</b> 条；边界口径：'+esc(b.label)+'。</p>';
  document.getElementById('issues').innerHTML = plan.issues.length
    ? '<table><thead><tr><th>文件</th><th>为什么跳过</th><th>代码</th><th>状态</th></tr></thead><tbody>'
      + plan.issues.map(i=>'<tr><td>'+pathCell(i.path)+'</td><td>'+esc(issueText(i.code,i.message))+'</td><td class="muted">'+esc(i.code)+'</td><td>'+esc(i.final_status)+'</td></tr>').join('')+'</tbody></table>'
    : '<p class="muted">没有异常项。</p>';
  applyFilter();
  updateExecPanel(v);
}
function updateExecPanel(v){
  const isCopy = (execMode.value === 'copy') || !!(v && v.output_root);
  const c = (v && v.counts) || {};
  const hintEl = document.getElementById('execHint'), btn = document.getElementById('execBtn');
  const modeDrifted = !!lastPlan && (planMode !== execMode.value);
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
function setFilter(f){filterMode=f;document.querySelectorAll('#filters .chip').forEach(c=>c.classList.toggle('active',c.dataset.f===f));applyFilter();}
function applyFilter(){
  document.querySelectorAll('#preview tr[data-row]').forEach(tr=>{
    const show = filterMode==='all'
      || (filterMode==='changed' && tr.dataset.changed==='1')
      || (filterMode==='renamed' && tr.dataset.renamed==='1')
      || (filterMode==='same' && tr.dataset.changed==='0');
    tr.style.display = show ? '' : 'none';
  });
  let anyVisible = false;
  document.querySelectorAll('#preview .gblock').forEach(block=>{
    const keepBlock = filterMode==='all' || block.dataset[filterMode]==='1';
    block.style.display = keepBlock ? '' : 'none';
    if(keepBlock) anyVisible = true;
  });
  const empty = document.getElementById('previewEmpty');
  if(empty) empty.style.display = (filterMode!=='all' && !anyVisible) ? '' : 'none';
}
const runDuplicate = guard(async function runDuplicate(){
  document.getElementById('dupResult').innerHTML = '<p class="muted">正在第一遍初筛并自动复核（大文件会比较久）...</p>';
  document.getElementById('dupTools').innerHTML = '';
  const prog = trackProgress('dupFill', 'dupProg');
  let failed = false;
  try{
    const data = await api('/api/duplicates',{selected_root:cleanPath(root.value),level:dupLevel.value,verify:true,progress_key:prog.key});
    lastDup = data; renderDup(data);
  }catch(e){ failed = true; document.getElementById('dupResult').innerHTML = '<p class="error">'+esc(e.message)+'</p>'; say(e.message,true); }
  finally{ prog.stop(!failed); }
});
function renderDup(data){
  const s = data.stats;
  let html = '<div class="headline">第一遍「'+esc(data.phase1_label)+'」找到 '+s.groups+' 组；自动跑第二遍全量哈希复核，剔除 '+s.recheck_rejected_groups
    + ' 组误判；可删副本 '+s.deletable_files+' 个，可回收 '+fmtBytes(s.reclaimable_bytes)+'。</div>';
  document.getElementById('dupTools').innerHTML = '<button class="ghost" onclick="checkAll(true)">全选可删副本</button><button class="ghost" onclick="checkAll(false)">取消全选</button><button class="danger" onclick="deleteSelected()">删除所选副本</button>';
  const groups = data.groups || [];
  if(!groups.length) html += '<p class="muted">没有发现重复文件。</p>';
  groups.forEach(g=>{
    const deletable = g.deletable_paths.length;
    const badge = g.verified && deletable ? '<span class="badge ok">已全量复核</span>' : (g.verified ? '<span class="badge ok">已复核</span>' : '<span class="badge warn">复核剔除</span>');
    html += '<div class="group" id="g-'+esc(g.duplicate_group_id)+'"><h4>'+badge+' 组 '+esc(g.duplicate_group_id)+' · '+g.file_count+' 个文件 · 每个 '+fmtBytes(g.size)+'</h4>';
    html += '<p class="muted" style="margin:2px 0 8px">'+esc(g.note)+'</p>';
    html += '<table><thead><tr><th style="width:34px">删</th><th>文件</th><th>相对资源包</th><th>大小</th></tr></thead><tbody>';
    const rootPath = data.selected_root || '';
    const rows = data.rows.filter(r=>r.duplicate_group_id===g.duplicate_group_id);
    rows.forEach(r=>{
      const rel = rootPath && String(r.path).startsWith(rootPath) ? String(r.path).slice(rootPath.length).replace(/^[\\\\/]+/,'') : r.path;
      const isKeep = r.suggested_keep;
      const canDel = (g.deletable_paths||[]).indexOf(r.path) >= 0;
      const box = canDel
        ? '<input type="checkbox" class="delbox" data-path="'+esc(r.path)+'" data-keep="'+esc(g.keep_path)+'" data-hash="'+esc(g.verified_hash)+'" data-bytes="'+r.size+'">'
        : (isKeep ? '<span class="badge ok">保留</span>' : '<span class="muted">—</span>');
      html += '<tr data-pick="'+esc(r.path)+'" title="右键在资源管理器中定位"><td>'+box+'</td><td><code title="'+esc(r.path)+'">'+esc(fileName(r.path))+'</code></td><td>'+pathCell(rel)+'</td><td>'+fmtBytes(r.size)+'</td></tr>';
    });
    html += '</tbody></table>';
    if(deletable) html += '<p><button class="danger" style="margin-top:8px" data-g="'+esc(g.duplicate_group_id)+'" onclick="deleteGroup(this.dataset.g)">删除本组其余 '+deletable+' 个副本</button></p>';
    html += '</div>';
  });
  document.getElementById('dupResult').innerHTML = html;
}
function checkAll(on){document.querySelectorAll('#dupResult .delbox').forEach(b=>{b.checked=on;});updateSelNote();}
function updateSelNote(){
  const n = document.querySelectorAll('#dupResult .delbox:checked').length;
  const el = document.getElementById('dupSelNote');
  if(el) el.textContent = n ? ('已选 '+n+' 个') : '';
}
function collect(scopeSel){
  const scope = scopeSel ? document.querySelector(scopeSel) : document.getElementById('dupResult');
  if(!scope) return [];
  return Array.from(scope.querySelectorAll('.delbox:checked')).map(n=>({path:n.dataset.path,keep_path:n.dataset.keep,expected_sha256:n.dataset.hash,bytes:Number(n.dataset.bytes)}));
}
function deleteGroup(gid){ const entries = collect('#g-'+gid); if(!entries.length){say('这一组没有勾选任何副本，先勾选再删除。',true);return;} doDelete(entries); }
function deleteSelected(){ const entries = collect(''); if(!entries.length){say('先勾选要删除的副本（每组至少留一个，保留项不能勾）。',true);return;} doDelete(entries); }
const doDelete = guard(async function doDelete(entries){
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
  let failed = false;
  try{
    const data = await api('/api/duplicates/delete',{selected_root:cleanPath(root.value),mode:delModeValue,confirm:true,progress_key:prog.key,entries:entries.map(e=>({path:e.path,keep_path:e.keep_path,expected_sha256:e.expected_sha256}))});
    const done = data.deleted + data.recycled;
    let html = '<div class="headline">已处理 '+done+' 个（回收站 '+data.recycled+' / 彻底删除 '+data.deleted+'），跳过 '+data.skipped+'，失败 '+data.failed+'。'
      + (data.recycled ? '想反悔就去回收站还原——清空回收站之前它们一直占着空间。' : '')
      + '</div>';
    html += '<table><thead><tr><th>文件</th><th>结果</th><th>说明</th></tr></thead><tbody>'
      + data.rows.map(r=>'<tr data-pick="'+esc(r.path)+'"><td><code title="'+esc(r.path)+'">'+esc(fileName(r.path))+'</code></td><td>'+esc(r.status)+'</td><td>'+esc(issueText(r.code,r.message))+'</td></tr>').join('')+'</tbody></table>';
    if(data.transaction_log) html += '<p class="muted">删除日志：<code>'+esc(data.transaction_log)+'</code></p>';
    document.getElementById('dupTools').innerHTML = '';
    document.getElementById('dupResult').innerHTML = html;
    say('删除完成：成功 '+done+'，跳过 '+data.skipped+'，失败 '+data.failed+'。');
  }catch(e){ failed = true; say(e.message,true); }
  finally{ prog.stop(!failed); }
});
const runRepair = guard(async function runRepair(){
  if(!fixReadonly.checked && !fixHidden.checked && !fixZone.checked){
    say('请先勾选至少一项修复动作（清除只读 / 隐藏 / Zone.Identifier），再执行。',true);
    return;
  }
  const prog = trackProgress('repairFill', 'repairProg');
  let failed = false;
  say('正在执行权限修复...');
  try{
    const data = await api('/api/repair',{selected_root:cleanPath(root.value),clear_readonly:fixReadonly.checked,clear_hidden:fixHidden.checked,clear_zone_identifier:fixZone.checked,progress_key:prog.key});
    const bad = data.rows || [];
    document.getElementById('repairResult').innerHTML = bad.length
      ? '<div class="headline">检查 '+data.checked+' 项，异常 '+bad.length+' 项（正常项略）。</div>'
        + table(bad,['path','action','status','message'],['文件','动作','状态','说明'])
      : '<p class="muted">检查 '+data.checked+' 项，全部正常，没有需要列出的异常。</p>';
    say('权限修复完成：检查 '+data.checked+' 项，异常 '+bad.length+' 项。');
  }catch(e){ failed = true; say(e.message,true); }
  finally{ prog.stop(!failed); }
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
  let failed = false;
  say('正在复制...');
  try{
    const data = await api('/api/execute-copy',{plan_id:currentPlanId,progress_key:prog.key});
    markPlanExecuted();
    const outRoot = (lastPlan && lastPlan.output_root) || cleanPath(output.value);
    document.getElementById('execResult').innerHTML =
      '<div class="headline">复制完成：完成 '+data.completed+'，跳过 '+data.skipped+'，失败 '+data.failed+'。源目录没有动。</div>'
      + '<p>输出目录：<code>'+esc(outRoot)+'</code> <button class="secondary" data-open-out="'+esc(outRoot)+'">在资源管理器中打开</button></p>'
      + table(data.issues||[],['path','stage','code','message','final_status'],['文件','阶段','代码','说明','状态']);
    document.querySelectorAll('#execResult button[data-open-out]').forEach(b=>b.addEventListener('click',()=>reveal(b.dataset.openOut,false)));
    say('复制完成：完成 '+data.completed+'，跳过 '+data.skipped+'，失败 '+data.failed+'。源目录没有动，输出目录见下方。');
  }catch(e){ failed = true; say(e.message,true); }
  finally{ prog.stop(!failed); }
}
async function executeMove(){
  const c = ((lastPlan && lastPlan.display && lastPlan.display.readable) || {}).counts || {};
  const ok = await showModal({
    title:'确认原地整理？',
    body:'<p>会换位置的文件：<b>'+ (c.moved||0) +' 个</b><br>会清掉的空文件夹：<b>'+ (c.folders||0) +' 个</b></p><p>源目录会被修改，执行后会生成事务日志，可以用「撤销这次整理」还原（已被清掉的空壳文件夹不重建）。</p>',
    confirmText:'确认原地整理'
  });
  if(!ok) return;
  const prog = trackProgress('execFill', 'execProg');
  let failed = false;
  say('正在原地整理...');
  try{
    const data = await api('/api/execute-move',{plan_id:currentPlanId,confirm:true,progress_key:prog.key});
    markPlanExecuted();
    document.getElementById('execResult').innerHTML =
      '<div class="headline">整理完成：完成 '+data.completed+'，跳过 '+data.skipped+'，失败 '+data.failed+'。</div>'
      + table(data.issues||[],['path','stage','code','message','final_status'],['文件','阶段','代码','说明','状态'])
      + (data.transaction_log ? '<p class="muted">事务日志：'+esc(data.transaction_log)+' <button class="secondary" data-tx="'+esc(data.transaction_log)+'">撤销这次整理</button></p>' : '');
    document.querySelectorAll('#execResult button[data-tx]').forEach(b=>b.addEventListener('click',()=>undoRun(b.dataset.tx)));
    say('整理完成：完成 '+data.completed+'，跳过 '+data.skipped+'，失败 '+data.failed+'。');
  }catch(e){ failed = true; say(e.message,true); }
  finally{ prog.stop(!failed); }
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
const undoRun = guard(async function undoRun(logPath){
  let failed = false;
  try{
    const data = await api('/api/undo',{transaction_log:logPath});
    document.getElementById('execResult').innerHTML = table(data.rows||[],['path','status','message'],['路径','结果','说明']);
    say('撤销完成，逐条结果见下方。');
  }catch(e){ failed = true; say(e.message,true); }
});
async function exportReport(){
  if(!currentPlanId){ say('请先生成计划。',true); return; }
  try{ const data = await api('/api/report',{plan_id:currentPlanId}); say('报告已导出：'+data.report_dir); }
  catch(e){say(e.message,true);}
}
async function loadHistory(){
  try{ const data = await fetch('/api/history').then(r=>r.json());
    document.getElementById('history').innerHTML = table(data,['created_at','mode','selected_root','operation_count','issue_count','report_dir'],['时间','模式','源目录','操作数','异常数','报告目录']);
  }catch(e){say(e.message,true);}
}
async function makeManual(){
  try{ const data = await api('/api/manual',{}); say('PDF 手册已生成：'+data.manual_pdf); }catch(e){say(e.message,true);}
}
function table(rows, cols, heads){
  if(!rows || !rows.length) return '<p class="muted">无异常项。</p>';
  return '<table><thead><tr>'+cols.map((c,i)=>'<th>'+esc(heads?heads[i]:c)+'</th>').join('')+'</tr></thead><tbody>'
    + rows.map(r=>'<tr>'+cols.map(c=>'<td>'+esc(Array.isArray(r[c])?r[c].join(' | '):r[c])+'</td>').join('')+'</tr>').join('')+'</tbody></table>';
}
document.addEventListener('change', e=>{ if(e.target && e.target.classList.contains('delbox')) updateSelNote(); });
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
  });
  document.querySelectorAll('.panel').forEach(p=>p.classList.toggle('hidden',p.id!=='panel-'+name));
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
