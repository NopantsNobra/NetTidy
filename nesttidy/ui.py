"""页面外壳：只放静态 HTML/CSS/JS，所有数据来自 /api/*。

单独成模块的原因：可读化改版后 HTML 内联体积会顶破 web.py 的 400 行上限；
web.py 保留路由与序列化，页面改动不再触碰服务端逻辑。
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
button{padding:9px 13px;border:0;border-radius:6px;background:var(--ok);color:#fff;cursor:pointer;font-size:13px;margin:4px 6px 4px 0} button:disabled{background:#c3cbc8;cursor:not-allowed}
button.secondary{background:#495057} button.danger{background:#a4161a} button.ghost{background:#e9efec;color:#174b4f}
table{width:100%;border-collapse:collapse;background:#fff;font-size:12.5px} th,td{padding:7px 8px;border-bottom:1px solid #eef1ef;text-align:left;vertical-align:top} th{background:#f2f6f4;font-weight:600;white-space:nowrap}
code{font-family:Consolas,"Courier New",monospace;font-size:12px;word-break:break-all}
.row{display:flex;gap:12px;flex-wrap:wrap} .row>div{flex:1 1 240px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin-top:12px} .card{border:1px solid var(--line);border-radius:8px;padding:10px 12px;background:#fbfcfb}
.card b{display:block;font-size:20px;line-height:1.2} .card span{color:var(--mute);font-size:12px}
.muted{color:var(--mute)} .error{color:var(--bad)} .ok{color:var(--ok)} .depth{color:var(--mute);font-size:11px}
.headline{background:#eef7f3;border-left:4px solid var(--ok);padding:10px 12px;border-radius:6px;font-size:14px}
.badge{display:inline-block;padding:1px 7px;border-radius:10px;font-size:11px;border:1px solid;margin-right:6px}
.badge.ok{color:#0d5e4d;border-color:#9ecdc0;background:#eaf6f1} .badge.warn{color:#7a5200;border-color:#e2c98a;background:#fdf6e3} .badge.bad{color:#9b1c1c;border-color:#e3a8a8;background:#fdeeee}
.chips{margin:8px 0} .chip{display:inline-block;padding:4px 10px;border:1px solid var(--line);border-radius:14px;margin:0 6px 6px 0;cursor:pointer;font-size:12px;background:#fff} .chip.active{background:var(--ok);color:#fff;border-color:var(--ok)}
.group{border:1px solid var(--line);border-radius:8px;margin:10px 0;padding:10px 12px} .group h4{margin:0 0 6px;font-size:13px} details summary{cursor:pointer;font-weight:600;padding:4px 0}
.filtered{display:none} .hidden{display:none}
.pbar{height:8px;background:#e6ecea;border-radius:4px;overflow:hidden;margin:8px 0} .pfill{height:100%;width:0;background:var(--ok);transition:width .2s}
pre{white-space:pre-wrap;background:#f5f7f6;border:1px solid var(--line);border-radius:6px;padding:8px;font-size:12px;margin:6px 0}
</style>
</head>
<body>
<header><h1>理巢 NestTidy</h1>
  <p>本地离线资源目录剥壳整理 · 试运行只读：先看清「哪些文件会换位置」，再决定复制、移动，或者删掉多余副本；每一步都要你亲手点按钮</p></header>
<main>
<section><h2>第 1 步 · 选目录并试运行</h2>
  <div class="row">
    <div><label>源目录（要整理哪个文件夹）</label><input type="text" id="root" placeholder="把文件夹路径粘贴到这里，例如 F:\\私人定制\\【系列作品】"></div>
    <div><label>整理单位</label><select id="mode"><option value="batch">批量：这个文件夹里每个子文件夹都是独立资源包，各自整理</option><option value="single">单个：把这个文件夹本身当一个资源包整理</option></select></div>
  </div>
  <label>输出目录（只有「复制到输出目录」需要；原地整理留空）</label>
  <input type="text" id="output" placeholder="例如 F:\\私人定制\\理巢输出_20261118">
  <label>执行方式</label>
  <select id="execMode"><option value="move">原地整理：文件在资源包内换位置，空壳文件夹随之清掉（源目录会变）</option><option value="copy">复制到输出目录：源目录一动不动，另存一份整理好的副本（占双份空间）</option></select>
  <p><button onclick="makePlan()">试运行：看看会变成什么样</button><button class="secondary" onclick="exportReport()">导出报告文件</button><button class="secondary" onclick="makeManual()">生成 PDF 手册</button></p>
  <p id="msg" class="muted">还没有生成计划。</p>
</section>
<section id="summarySection" class="hidden"><h2>第 2 步 · 会发生什么（大白话版）</h2>
  <div class="headline" id="headline"></div><div class="cards" id="cards"></div></section>
<section id="previewSection" class="hidden"><h2>整理前 → 整理后：逐个文件对照</h2>
  <p class="muted">左列是现在的位置（相对资源包），右列是整理后的位置；「藏了 N 层」表示这个文件现在被 N 层壳文件夹压着。点下面的筛选条可以只看会变的文件。</p>
  <div class="chips" id="filters"></div><div id="preview"></div></section>
<section id="foldersSection" class="hidden"><h2>会被清掉的空文件夹</h2>
  <p class="muted">只有文件被提走、文件夹真的变空之后才会删；非空目录一律保留。</p><div id="folders"></div></section>
<section id="execSection" class="hidden"><h2>第 3 步 · 真正执行</h2>
  <p class="muted" id="execHint"></p><p><button id="execBtn" onclick="executePlan()">确认执行</button></p>
  <div class="pbar"><div class="pfill" id="execFill"></div></div><p class="muted" id="execProg">尚未开始。</p><div id="execResult"></div></section>
<section id="dupSection"><h2>第 4 步 · 重复文件诊断（两阶段自动复核）</h2>
  <p class="muted">第一遍按你选的级别快速初筛，程序会自动对每组候选再跑一遍「全文件 SHA-256」复核：首尾相同但中段不同的文件会被标成「复核剔除」，永远不进入可删名单。看到结果后，逐个勾选要删的副本（每组至少留一份），再点删除——程序不会自己删任何东西。</p>
  <div class="row">
    <div><label>第一遍初筛口径</label><select id="dupLevel"><option value="standard">标准：大小 + 首尾各 1MB 哈希（推荐）</option><option value="fast">快速：大小 + 文件名（最快，误判最多，靠第二遍兜底）</option><option value="deep">深度：直接全文件哈希（最慢，不重复计算）</option></select></div>
    <div><label>删除方式</label><select id="delMode"><option value="quarantine">移入隔离区（可撤销，同盘瞬间完成；清空隔离区才真正释放空间）</option><option value="purge">彻底删除（立即释放空间，不可撤销，需二次确认）</option></select></div>
  </div>
  <p><button onclick="runDuplicate()">诊断重复文件</button></p><div id="dupResult"><p class="muted">尚未诊断。</p></div></section>
<section><details><summary>权限修复（可选）</summary>
  <p><label><input type="checkbox" id="fixReadonly" style="width:auto"> 清除只读属性</label> <label><input type="checkbox" id="fixHidden" style="width:auto"> 清除普通隐藏属性</label> <label><input type="checkbox" id="fixZone" style="width:auto"> 清除 Zone.Identifier（来自网络的标记）</label> <button class="secondary" onclick="runRepair()">执行所选权限修复</button></p>
  <div class="pbar"><div class="pfill" id="repairFill"></div></div><p class="muted" id="repairProg">尚未开始。</p>
  <div id="diagnostics"></div></details></section>
<section><details><summary>异常项、规则口径、历史与健康检查</summary>
  <h3>异常项（这些文件不会被动）</h3><div id="issues"></div>
  <h3>规则分类计数与边界检查</h3><div id="ruleStats"></div><div id="boundaryNote"></div>
  <h3>历史</h3><p><button class="secondary" onclick="loadHistory()">查看历史</button></p>
  <h3>健康检查</h3><div id="health"></div></details></section>
</main>
<script>
let currentPlanId = null, lastPlan = null, lastDup = null, filterMode = 'all';
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
function pathCell(p){return '<code title="'+esc(p)+'">'+esc(String(p).split('\\\\').join(' › '))+'</code>';}
function issueText(code,message){return ISSUE_TEXT[code] || message || code || '';}
async function api(path, body){
  const res = await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});
  const data = await res.json();
  if(!res.ok) throw new Error(data.error || res.statusText);
  return data;
}
function say(text, isError){const el=document.getElementById('msg');el.className=isError?'error':'muted';el.textContent=text;}
function trackProgress(fillId, textId){
  const key = 'p' + Math.random().toString(36).slice(2), fill = document.getElementById(fillId), text = document.getElementById(textId);
  const tick = () => fetch('/api/progress?key=' + key).then(r => r.json()).then(d => {
    const pct = d.total ? Math.round(d.done / d.total * 100) : 0;
    fill.style.width = (d.total ? pct : 5) + '%';
    text.textContent = (d.total ? d.done + ' / ' + d.total + ' · ' + pct + '%' : '正在准备') + (d.label ? ' · ' + d.label : '');
  }).catch(() => {});
  const timer = setInterval(tick, 300); tick(); return {key: key, stop: () => { clearInterval(timer); fill.style.width = '100%'; }};
}
async function makePlan(){
  say('正在扫描并生成试运行计划（只读，不改磁盘）...');
  try{
    const isCopy = execMode.value === 'copy';
    const data = await api('/api/plan',{selected_root:root.value.trim(),mode:mode.value,output_root:isCopy?(output.value.trim()||null):null});
    currentPlanId = data.plan_id; lastPlan = data; renderPlan(data);
    say('计划已生成：共 '+data.operations.length+' 个操作，'+data.issues.length+' 个异常。磁盘上还没有任何改动。');
  }catch(e){say(e.message,true);}
}
function renderPlan(plan){
  const v = plan.display.readable;
  document.getElementById('summarySection').classList.remove('hidden');
  document.getElementById('headline').textContent = v.headline;
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
      html += '<tr data-row data-changed="'+(r.changed?'1':'0')+'" data-renamed="'+(r.renamed?'1':'0')+'">'
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
      + v.folders.map(f=>'<tr><td>'+esc(f.root_name)+'</td><td>'+pathCell(f.path)+'</td><td>第 '+f.depth+' 层</td></tr>').join('')+'</tbody></table>';
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
  document.getElementById('execSection').classList.remove('hidden');
  const isCopy = (execMode.value === 'copy') || !!(v.output_root);
  const c = v.counts || {};
  document.getElementById('execHint').textContent = isCopy
    ? '复制方式：把整理后的完整副本写到输出目录，源目录不动。会多占一份空间（'+ (v.headline||'') +' 的体积），看完满意后可以自己删掉源目录。'
    : '原地整理：'+ (c.moved||0) +' 个文件在资源包内换位置，顺手清掉 '+ (c.folders||0) +' 个空壳文件夹。源目录会变，执行前会再确认一次，执行后可撤销。';
  document.getElementById('execBtn').textContent = isCopy ? '确认复制到输出目录' : '确认原地整理';
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
async function runDuplicate(){
  const btn = event && event.target; if(btn) btn.disabled = true;
  document.getElementById('dupResult').innerHTML = '<p class="muted">正在第一遍初筛并自动复核（大文件会比较久）...</p>';
  try{
    const data = await api('/api/duplicates',{selected_root:root.value.trim(),level:dupLevel.value,verify:true});
    lastDup = data; renderDup(data);
  }catch(e){document.getElementById('dupResult').innerHTML = '<p class="error">'+esc(e.message)+'</p>';}
  finally{ if(btn) btn.disabled = false; }
}
function renderDup(data){
  const s = data.stats;
  let html = '<div class="headline">第一遍「'+esc(data.phase1_label)+'」找到 '+s.groups+' 组；自动跑第二遍全量哈希复核，剔除 '+s.recheck_rejected_groups
    + ' 组误判；可删副本 '+s.deletable_files+' 个，可回收 '+fmtBytes(s.reclaimable_bytes)+'。</div>';
  html += '<p><button class="ghost" onclick="checkAll(true)">全选可删副本</button><button class="ghost" onclick="checkAll(false)">取消全选</button>'
    + '<button class="danger" onclick="deleteSelected()">删除所选副本</button>'
    + '<span class="muted" id="dupSelNote"></span></p>';
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
      html += '<tr><td>'+box+'</td><td>'+pathCell(r.path)+'</td><td>'+pathCell(rel)+'</td><td>'+fmtBytes(r.size)+'</td></tr>';
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
function deleteGroup(gid){ const entries = collect('#g-'+gid); if(!entries.length){alert('这一组没有勾选任何副本。');return;} doDelete(entries); }
function deleteSelected(){ const entries = collect(''); if(!entries.length){alert('先勾选要删除的副本（每组至少留一个，保留项不能勾）。');return;} doDelete(entries); }
async function doDelete(entries){
  const delModeValue = delMode.value;
  const bytes = entries.reduce((a,e)=>a+e.bytes,0);
  const label = delModeValue==='purge' ? '彻底删除（不可撤销）' : '移入隔离区（可撤销）';
  if(!confirm('将'+label+' '+entries.length+' 个重复副本，约 '+fmtBytes(bytes)+'。\\n每组都保留一份。确定继续？')) return;
  if(delModeValue==='purge' && !confirm('再确认一次：彻底删除后无法恢复，确定继续？')) return;
  try{
    const data = await api('/api/duplicates/delete',{selected_root:root.value.trim(),mode:delModeValue,confirm:true,entries:entries.map(e=>({path:e.path,keep_path:e.keep_path,expected_sha256:e.expected_sha256}))});
    const ok = data.deleted + data.quarantined;
    let html = '<div class="headline">已处理 '+ok+' 个（隔离 '+data.quarantined+' / 彻底删除 '+data.deleted+'），跳过 '+data.skipped+'，失败 '+data.failed+'。'
      + (data.trash_dir ? '隔离区：'+esc(data.trash_dir)+'（想反悔可用删除日志撤销）' : '')
      + '</div>';
    html += '<table><thead><tr><th>文件</th><th>结果</th><th>说明</th></tr></thead><tbody>'
      + data.rows.map(r=>'<tr><td>'+pathCell(r.path)+'</td><td>'+esc(r.status)+'</td><td>'+esc(issueText(r.code,r.message))+'</td></tr>').join('')+'</tbody></table>';
    if(data.transaction_log) html += '<p class="muted">删除日志：<code>'+esc(data.transaction_log)+'</code></p>';
    document.getElementById('dupResult').innerHTML = html;
    say('删除完成：成功 '+ok+'，跳过 '+data.skipped+'，失败 '+data.failed+'。');
  }catch(e){say(e.message,true);}
}
async function runRepair(){
  const prog = trackProgress('repairFill', 'repairProg');
  say('正在执行权限修复...');
  try{
    const data = await api('/api/repair',{selected_root:root.value.trim(),clear_readonly:fixReadonly.checked,clear_hidden:fixHidden.checked,clear_zone_identifier:fixZone.checked,progress_key:prog.key});
    const bad = data.rows || [];
    document.getElementById('diagnostics').innerHTML = bad.length
      ? '<div class="headline">检查 '+data.checked+' 项，异常 '+bad.length+' 项（正常项略）。</div>'
        + table(bad,['path','action','status','message'],['文件','动作','状态','说明'])
      : '<p class="muted">检查 '+data.checked+' 项，全部正常，没有需要列出的异常。</p>';
    say('权限修复完成：检查 '+data.checked+' 项，异常 '+bad.length+' 项。');
  }catch(e){say(e.message,true);}
  finally{prog.stop();}
}
async function executePlan(){
  if(!currentPlanId){say('请先生成试运行计划。',true);return;}
  return execMode.value === 'copy' ? executeCopy() : executeMove();
}
async function executeCopy(){
  if(!output.value.trim()){say('复制方式要先填输出目录。',true);return;}
  const prog = trackProgress('execFill', 'execProg');
  try{
    say('正在复制...');
    const data = await api('/api/execute-copy',{plan_id:currentPlanId,progress_key:prog.key});
    document.getElementById('execResult').innerHTML = table(data.issues||[],['path','stage','code','message','final_status'],['文件','阶段','代码','说明','状态']);
    say('复制完成：完成 '+data.completed+'，跳过 '+data.skipped+'，失败 '+data.failed+'。源目录没有动。');
  }catch(e){say(e.message,true);}
  finally{prog.stop();}
}
async function executeMove(){
  const c = ((lastPlan && lastPlan.display && lastPlan.display.readable) || {}).counts || {};
  const ok = confirm('确认原地整理？\\n\\n会换位置的文件：'+(c.moved||0)+' 个\\n会清掉的空文件夹：'+(c.folders||0)+' 个\\n\\n源目录会被修改，执行后会生成事务日志，可以用「撤销这次整理」还原。');
  if(!ok) return;
  const prog = trackProgress('execFill', 'execProg');
  try{
    say('正在原地整理...');
    const data = await api('/api/execute-move',{plan_id:currentPlanId,confirm:true,progress_key:prog.key});
    document.getElementById('execResult').innerHTML = table(data.issues||[],['path','stage','code','message','final_status'],['文件','阶段','代码','说明','状态'])
      + (data.transaction_log ? '<p class="muted">事务日志：'+esc(data.transaction_log)+' <button class="secondary" data-tx="'+esc(data.transaction_log)+'">撤销这次整理</button></p>' : '');
    document.querySelectorAll('#execResult button[data-tx]').forEach(b=>b.addEventListener('click',()=>undoRun(b.dataset.tx)));
    say('整理完成：完成 '+data.completed+'，跳过 '+data.skipped+'，失败 '+data.failed+'。');
  }catch(e){say(e.message,true);}
  finally{prog.stop();}
}
async function undoRun(logPath){
  try{
    const data = await api('/api/undo',{transaction_log:logPath});
    document.getElementById('execResult').innerHTML = table(data.rows||[],['path','status','message'],['路径','结果','说明']);
    say('撤销完成，逐条结果见下方。');
  }catch(e){say(e.message,true);}
}
async function exportReport(){
  if(!currentPlanId){say('请先生成计划。',true);return;}
  try{ const data = await api('/api/report',{plan_id:currentPlanId}); say('报告已导出：'+data.report_dir); }
  catch(e){say(e.message,true);}
}
async function loadHistory(){
  try{ const data = await fetch('/api/history').then(r=>r.json());
    document.getElementById('diagnostics').innerHTML = table(data,['created_at','mode','selected_root','operation_count','issue_count','report_dir'],['时间','模式','源目录','操作数','异常数','报告目录']);
  }catch(e){say(e.message,true);}
}
async function makeManual(){
  try{ const data = await api('/api/manual',{}); say('PDF 手册已生成：'+data.manual_pdf); }catch(e){say(e.message,true);}
}
function table(rows, cols, heads){
  if(!rows || !rows.length) return '<p class="muted">无</p>';
  return '<table><thead><tr>'+cols.map((c,i)=>'<th>'+esc(heads?heads[i]:c)+'</th>').join('')+'</tr></thead><tbody>'
    + rows.map(r=>'<tr>'+cols.map(c=>'<td>'+esc(Array.isArray(r[c])?r[c].join(' | '):r[c])+'</td>').join('')+'</tr>').join('')+'</tbody></table>';
}
document.addEventListener('change', e=>{ if(e.target && e.target.classList.contains('delbox')) updateSelNote(); });
execMode.addEventListener('change', ()=>{ if(lastPlan && lastPlan.display) updateExecPanel(lastPlan.display.readable); });
fetch('/api/health').then(r=>r.json()).then(rows=>{ document.getElementById('health').innerHTML = table(rows,['name','ok','detail'],['检查项','结果','详情']); });
</script>
</body>
</html>"""
