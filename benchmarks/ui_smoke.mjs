// Developer-only Chromium smoke: Node 22 native WebSocket + CDP, no npm packages.
import {spawn} from 'node:child_process';
import fs from 'node:fs/promises';
import {existsSync} from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function waitFor(fn, timeout=30000) {
  const end = Date.now()+timeout;
  let last;
  while(Date.now()<end) {
    try { const value=await fn(); if(value) return value; } catch(e) { last=e; }
    await sleep(100);
  }
  throw new Error('Timed out waiting for browser/server: '+String(last||'condition'));
}
const temp=await fs.mkdtemp(path.join(os.tmpdir(),'nesttidy-ui-'));
const out=path.resolve(process.argv[2]||'ui-screenshots');
const browserPath=[process.env.CHROME_PATH,
  ...[process.env.ProgramFiles,process.env['ProgramFiles(x86)'],process.env.LOCALAPPDATA].filter(Boolean)
    .flatMap(p=>[path.join(p,'Google','Chrome','Application','chrome.exe'),path.join(p,'Microsoft','Edge','Application','msedge.exe')]),
  '/usr/bin/chromium','/usr/bin/google-chrome'].find(p=>p&&existsSync(p));
if(!browserPath) throw new Error('Set CHROME_PATH to a Chromium browser executable');
let backend,browser,ws;
try {
  await fs.mkdir(out,{recursive:true});
  const info=path.join(temp,'server.json');
  const fixture=String.raw`
import json, os, sys
from pathlib import Path
from nesttidy.web import create_server
base = Path(sys.argv[1]).resolve()
os.chdir(base)
root = base / "资源测试"
for n in range(125):
    p = root / "示例资源包" / "下载" / "内容" / f"文件-{n:03}.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"file {n}", encoding="utf-8")
p = root / "另一资源包" / "保留.txt"
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text("keep", encoding="utf-8")
server = create_server("127.0.0.1", 0)
Path(sys.argv[2]).write_text(json.dumps({"url": f"http://127.0.0.1:{server.server_address[1]}", "root": str(root)}), encoding="utf-8")
server.serve_forever()
`;
  backend=spawn(process.env.PYTHON||'python',['-u','-c',fixture,temp,info],{stdio:['ignore','ignore','inherit']});
  const data=await waitFor(async()=>JSON.parse(await fs.readFile(info,'utf8')));
  const profile=path.join(temp,'chrome-profile');
  browser=spawn(browserPath,['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check',
    '--remote-debugging-port=0','--user-data-dir='+profile,'--window-size=1440,1000','about:blank'],
    {stdio:'ignore'});
  const port=await waitFor(async()=>Number((await fs.readFile(path.join(profile,'DevToolsActivePort'),'utf8')).split('\n')[0]));
  const page=await waitFor(async()=>(await (await fetch('http://127.0.0.1:'+port+'/json/list')).json()).find(t=>t.type==='page'));
  ws=new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((resolve,reject)=>{ws.addEventListener('open',resolve,{once:true});ws.addEventListener('error',reject,{once:true});});
  let next=0;
  const pending=new Map(), errors=[];
  ws.addEventListener('message',event=>{
    const msg=JSON.parse(event.data);
    if(msg.id&&pending.has(msg.id)){
      const {resolve,reject,timer}=pending.get(msg.id);pending.delete(msg.id);clearTimeout(timer);
      if(msg.error)reject(new Error(JSON.stringify(msg.error)));else resolve(msg.result);
    }
    if(msg.method==='Runtime.exceptionThrown')errors.push(msg.params.exceptionDetails.text);
  });
  function send(method,params={}){
    return new Promise((resolve,reject)=>{
      const id=++next,timer=setTimeout(()=>{pending.delete(id);reject(new Error('CDP timeout: '+method));},30000);
      pending.set(id,{resolve,reject,timer});ws.send(JSON.stringify({id,method,params}));
    });
  }
  async function evaluate(expression){
    const res=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});
    if(res.exceptionDetails)throw new Error(JSON.stringify(res.exceptionDetails));
    return res.result.value;
  }
  async function size(width,height){await send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:false});}
  async function screenshot(name){
    const image=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
    await fs.writeFile(path.join(out,name+'.png'),Buffer.from(image.data,'base64'));
  }
  await send('Runtime.enable');await send('Page.enable');await size(1440,1000);
  await send('Page.navigate',{url:data.url});
  await waitFor(()=>evaluate("document.readyState==='complete' && typeof makePlan==='function'"));
  assert.equal(await evaluate("document.querySelector('[aria-selected=true]').id"),'tab-preview');
  await screenshot('desktop-start');
  assert.equal(await evaluate("document.getElementById('execMode').getBoundingClientRect().height > 0"),true);
  await evaluate("root.value="+JSON.stringify(data.root)+"; output.value="+JSON.stringify(path.join(temp,'output'))+"; execMode.value='copy'; makePlan()");
  assert.equal(await evaluate("!!lastPlan && !!lastPlan.output_root"),true);
  assert.equal(await evaluate("document.getElementById('execBtn').classList.contains('danger')"),false);
  await evaluate("execMode.value='move';execMode.dispatchEvent(new Event('change'));true");
  assert.equal(await evaluate("document.getElementById('execBtn').disabled"),true);
  await evaluate("makePlan()");
  await waitFor(()=>evaluate("!!lastPlan && !busy && !document.getElementById('previewSection').classList.contains('hidden')"));
  assert.equal(await evaluate("lastPlan.mapping_count"),126);
  assert.equal(await evaluate("document.querySelectorAll('#cards .card').length"),7);
  assert.equal(await evaluate("document.getElementById('execBtn').classList.contains('danger')"),true);
  assert.equal(await evaluate("document.getElementById('execProgress').offsetHeight"),0);
  assert.equal(await evaluate("document.getElementById('undoProgress').offsetHeight"),0);
  await evaluate("window.smokeProgress=trackProgress('execFill','execProg');true");
  assert.equal(await evaluate("document.getElementById('execProgress').offsetHeight > 0"),true);
  await evaluate("window.smokeProgress.stop();document.getElementById('execProgress').classList.add('idle');true");
  await waitFor(()=>evaluate("document.querySelectorAll('#preview [data-row]').length>0"));
  await evaluate("chooseGroup(previewGroups.findIndex(g=>g.row_total===125))");
  assert.equal(await evaluate("document.querySelectorAll('#preview [data-row]').length"),80);
  await evaluate("showPreviewPage(80)");
  assert.equal(await evaluate("document.querySelectorAll('#preview [data-row]').length"),45);
  await evaluate("showPreviewPage(0)");
  assert.equal(await evaluate("document.querySelectorAll('#preview [data-row]').length"),80);
  await evaluate("setFilter('changed')");
  assert.equal(await evaluate("document.querySelector('[data-f=changed]').getAttribute('aria-pressed')"),'true');
  await evaluate("setFilter('all')");
  await evaluate("document.getElementById('summarySection').scrollIntoView()");
  await screenshot('desktop-preview');
  const perf = await evaluate(`(async()=>{
    const savedPlan=lastPlan, savedId=currentPlanId, realApi=api;
    const original=JSON.parse(JSON.stringify(lastPlan));
    const realRows=await realApi('/api/plan/rows',{plan_id:currentPlanId,root:previewGroups[previewGroup].root_path,offset:0,limit:80});
    const template=realRows.rows[0];
    original.mapping_count=200000;
    original.display.readable.groups=Array.from({length:1000},(_,i)=>({root_path:'root-'+i,root_name:'资源包 '+i,row_total:200,changed:1,renamed:0,moved:1,rows:[]}));
    original.display.readable.counts={total:200000,changed:1000,renamed:0,in_place:199000,moved:1000,folders:0};
    api=async (url,body)=>({root_path:body.root,offset:body.offset,total:body.filter==='changed'?1:200,
      rows:Array.from({length:Math.min(body.limit,Math.max(0,(body.filter==='changed'?1:200)-body.offset))},(_,i)=>({...template,before:'文件 '+(body.offset+i),after:'文件 '+(body.offset+i)}))});
    try{
      lastPlan=original;
      const start=performance.now();
      renderPlan(original);
      await showPreviewPage(0);
      document.getElementById('preview').getBoundingClientRect();
      const first=performance.now()-start;
      let maxRows=0,maxNodes=0,latencies=[];
      for(let i=0;i<25;i++){
        const t=performance.now();
        await chooseGroup(i*39);
        await showPreviewPage(80);
        document.getElementById('preview').getBoundingClientRect();
        latencies.push(performance.now()-t);
        maxRows=Math.max(maxRows,document.querySelectorAll('#preview [data-row]').length);
        maxNodes=Math.max(maxNodes,document.querySelectorAll('#preview *').length);
      }
      await setFilter('changed');
      const filtered=document.querySelectorAll('#preview [data-row]').length;
      return {first_ms:first,max_rows:maxRows,max_nodes:maxNodes,group_options:groupSelect.options.length,filtered,transition_ms:latencies};
    }finally{api=realApi;lastPlan=savedPlan;currentPlanId=savedId;renderPlan(savedPlan);await showPreviewPage(0);}
  })()`);
  assert.equal(perf.max_rows,80);
  assert.equal(perf.group_options,100);
  assert.equal(perf.filtered,1);
  assert.ok(perf.max_nodes<1000);
  console.log('200k simulated browser display benchmark (no disk/network): '+JSON.stringify(perf));
  await evaluate("window.uiModal=showModal({title:'确认原地整理',body:'仅验证取消操作',danger:true}); true");
  assert.equal(await evaluate("document.activeElement.classList.contains('dno')"),true);
  await screenshot('desktop-confirm');
  await evaluate("document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true})); true");
  assert.equal(await evaluate("window.uiModal"),false);
  await evaluate("document.getElementById('tab-preview').focus();document.getElementById('tab-preview').dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));true");
  assert.equal(await evaluate("document.querySelector('[aria-selected=true]').id"),'tab-dup');
  assert.equal(await evaluate("document.getElementById('tab-dup').tabIndex"),0);
  const dupPerf=await evaluate(`(()=>{
    const data={selected_root:root.value,phase1_label:'standard',stats:{groups:2,recheck_rejected_groups:0,deletable_files:398,reclaimable_bytes:398},row_total:200000,group_total:100000,groups:[],rows:[]};
    for(let g=0;g<2;g++){
      const id='group'+g, keep='file'+g+'-0', paths=[];
      for(let n=0;n<200;n++){
        const path='file'+g+'-'+n;
        data.rows.push({path,duplicate_group_id:id,size:1,suggested_keep:n===0});
        if(n)paths.push(path);
      }
      data.groups.push({duplicate_group_id:id,verified:true,verified_hash:'hash',keep_path:keep,deletable_paths:paths,deletable_total:199,file_count:200,size:1,note:''});
    }
    renderDup(data);
    const choose=()=>{const box=document.querySelector('#dupResult .delbox');box.checked=true;box.dispatchEvent(new Event('change',{bubbles:true}));};
    choose();chooseDupPage(80);choose();
    const pageRows=document.querySelectorAll('#dupResult tbody tr').length;
    chooseDupGroup(1);choose();
    const across=collect().length, group0=collect('group0').length;
    chooseDupGroup(0);
    const preserved=document.querySelector('#dupResult .delbox').checked;
    checkAll(true);const all=collect().length;
    checkAll(false);const none=collect().length;
    clearDuplicateState();document.getElementById('dupTools').innerHTML='';document.getElementById('dupResult').innerHTML='';
    return {pageRows,across,group0,preserved,all,none};
  })()`);
  assert.deepEqual(dupPerf,{pageRows:80,across:3,group0:2,preserved:true,all:398,none:0});
  console.log('Bounded duplicate preview and cross-page selection: '+JSON.stringify(dupPerf));
  await size(420,900);
  await evaluate("setTab('preview');window.scrollTo(0,0)");
  await sleep(250);
  assert.equal(await evaluate("document.documentElement.scrollWidth <= innerWidth + 1"),true,'narrow layout must not overflow document');
  assert.equal(await evaluate("document.getElementById('preview').scrollWidth > document.getElementById('preview').clientWidth"),true,'preview should scroll inside its own region');
  await screenshot('narrow-start');
  await evaluate("document.getElementById('previewSection').scrollIntoView()");
  await screenshot('narrow-preview');
  assert.deepEqual(errors,[],'page must not emit uncaught runtime errors');
  console.log('UI smoke passed: actual plan, pagination, filter, risk confirmation, keyboard tabs and 420px layout.');
} finally {
  if(ws)ws.close();
  if(browser)browser.kill();
  if(backend)backend.kill();
  await sleep(500);
  await fs.rm(temp,{recursive:true,force:true,maxRetries:5,retryDelay:300}).catch(e=>console.warn('Temporary browser profile cleanup:',e.message));
}
