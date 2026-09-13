// DOM interaction regression against the real Python HTTP API (not visual browser QA).
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {spawn,execFileSync}=require('node:child_process');
const {JSDOM}=require('jsdom');
const root=path.resolve(__dirname,'..');
const temp=fs.mkdtempSync(path.join(os.tmpdir(),'approval-ui-'));
// 依赖装在项目自己的 venv 里，落到系统 python 会缺包；而失败提示又要靠 PYTHON=
// 环境变量才修得了——不该让人先读文档才知道怎么跑测试。先探测 venv，找不到再退回系统 python。
const python=process.env.PYTHON||[path.join(root,'.venv/bin/python'),path.join(root,'.venv/Scripts/python.exe')].find(p=>fs.existsSync(p))||'python';
let server,dom;
async function until(fn){for(let n=0;n<100;n++){if(fn())return;await new Promise(r=>setTimeout(r,50));}throw Error('UI state timeout');}
(async()=>{
 execFileSync(python,['scripts/demo.py','--review-demo','--data',temp],{cwd:root});
 server=spawn(python,['-u','-c',"import sys;from http.server import ThreadingHTTPServer;from app.server import Handler,Store;s=ThreadingHTTPServer(('127.0.0.1',0),Handler);s.store=Store(sys.argv[1]);print(s.server_port,flush=True);s.serve_forever()",temp],{cwd:root,stdio:['ignore','pipe','pipe']});
 const port=await new Promise((resolve,reject)=>{let buffer='';server.stdout.on('data',b=>{buffer+=b.toString();const match=buffer.match(/(?:^|\n)(\d+)\r?\n/);if(match)resolve(Number(match[1]));});server.once('error',reject);server.once('exit',c=>reject(Error('server exited '+c)));});
 const url='http://127.0.0.1:'+port;
 dom=new JSDOM(fs.readFileSync(path.join(root,'app/static/index.html'),'utf8'),{url,runScripts:'outside-only'});
 const w=dom.window,errors=[],downloads=[];
 w.addEventListener('error',e=>errors.push(e.message));
 w.fetch=(p,options)=>fetch(new URL(p,url),options);
 w.HTMLElement.prototype.scrollIntoView=function(){};
 w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
 w.HTMLDialogElement.prototype.close=function(){this.open=false;};
 w.HTMLAnchorElement.prototype.click=function(){downloads.push(this.href);};
 w.eval(fs.readFileSync(path.join(root,'app/static/app.js'),'utf8'));
 const $=id=>w.document.getElementById(id);
 await until(()=>$('docCount').textContent==='7 份');
 assert.match($('workSummary').textContent,/3 个项目/);
 assert.equal(w.document.querySelector('.settings').open,false);
 assert.equal($('agentDetails').open,false);
 const projects=[...$('projects').querySelectorAll('button')];
 projects.find(b=>b.textContent.includes('演示文体中心')).click();
 $('exportCurrent').click();assert.match(downloads.at(-1),/export.xlsx\?project=/);
 $('exportAll').click();assert.equal(new URL(downloads.at(-1)).search,'');
 $('reviewPanel').querySelector('button.primary').click();
 assert.equal($('evidenceContent').hidden,false);
 assert.equal($('confirmNext').disabled,false);
 const selected=$('selectedField').textContent;
 $('confirmNext').click();
 await until(()=>$('selectedField').textContent!==selected);
 assert.match($('reviewPanel').textContent,/还剩 5 项/);
 $('metricsTab').click();$('rowFilter').value='different';$('rowFilter').dispatchEvent(new w.Event('change'));
 assert.ok($('tableWrap').querySelectorAll('tbody tr').length>0);
 // Stage is actionable; the unknown stage does not appear completed.
 [...$('projects').querySelectorAll('button')].find(b=>b.textContent.includes('演示待核对')).click();
 $('reviewPanel').querySelector('button.primary').click();
 assert.equal($('stageSelect').value,'待确认');
 $('stageSelect').value='初步设计';$('saveStage').click();
 await until(()=>!$('reviewPanel').textContent.includes('审批阶段：')&&$('evidenceContent').hidden===false);
 // Reach a conflict through the queue. It must use the specific metric occurrence.
 const conflict=[...$('reviewPanel').querySelectorAll('li')].find(li=>li.textContent.includes('总建筑面积'));
 conflict.querySelector('button').click();
 assert.equal($('confirmNext').disabled,true);
 $('editButton').click();assert.equal($('editDialog').open,true);
 $('metricName').value='一期建筑面积';$('editReason').value='虚构材料交互测试';
 $('editForm').dispatchEvent(new w.Event('submit',{cancelable:true}));
 await until(()=>$('editDialog').open===false);
 await until(()=>$('tableWrap').textContent.includes('一期建筑面积')||$('selectedField').textContent.includes('印章'));
 const groups=await (await fetch(url+'/api/documents')).json();
 const special=groups.groups.find(g=>g.documents.some(d=>d.filename==='演示待核对项目.pdf'));
 assert.ok(special.review.pending>0,'Unreviewed sibling must remain pending');
 assert.ok(special.documents[0].metrics.some(m=>m.name==='一期建筑面积'));
 // Single-document pending filter must use this document's cells, not another stage.
 $('fixedTab').click();$('rowFilter').value='review';$('rowFilter').dispatchEvent(new w.Event('change'));
 const visibleButtons=[...$('tableWrap').querySelectorAll('.cell-button')];
 assert.ok(visibleButtons.every(b=>b.closest('td').classList.contains('review')||b.closest('td').classList.contains('unextracted')));
 // A fresh batch keeps per-file outcomes and provides result navigation.
 const pdfPath=fs.readdirSync(temp).find(n=>n.endsWith('.pdf'));
 const uploaded=await (await fetch(url+'/api/upload',{method:'POST',headers:{'Content-Type':'application/json','X-Requested-With':'ApprovalAgent'},body:JSON.stringify({files:[{name:'duplicate.pdf',data:fs.readFileSync(path.join(temp,pdfPath)).toString('base64')}]})})).json();
 w.sessionStorage.setItem('activeJob',uploaded.job_id);$('resumeJob').click();
 await until(()=>$('jobPanel').textContent.includes('重复 1'));
 assert.match($('jobPanel').textContent,/重复 1/);
 assert.match($('jobPanel').textContent,/未重复解析/);
 $('jobPanel').querySelector('button').click();
 assert.notEqual($('documentSelect').value,'');
 assert.equal($('evidenceContent').hidden,true,'Changing scope must clear stale evidence');
 assert.equal($('workSummary').querySelectorAll('.workflow li').length,4);
 // Losing the polling connection is not reported as a failed upload.
 await until(()=>!w.sessionStorage.getItem('activeJob'));
 const normalFetch=w.fetch;
 w.fetch=(p,opts)=>String(p).startsWith('/api/jobs/')?Promise.reject(Error('temporary connection loss')):normalFetch(p,opts);
 w.sessionStorage.setItem('activeJob',uploaded.job_id);$('resumeJob').click();
 await until(()=>$('resumeJob').hidden===false);
 assert.match($('progress').textContent,/后台任务可能仍在继续/);
 assert.equal(w.sessionStorage.getItem('activeJob'),uploaded.job_id);
 w.fetch=normalFetch;$('resumeJob').click();
 await until(()=>!w.sessionStorage.getItem('activeJob'));
 assert.equal($('resumeJob').hidden,true);
 assert.deepEqual(errors,[]);
 console.log('UI DOM/API checks passed: summary, settings, export scope, confirm-next, filters, stage, conflict editing.');
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(()=>{dom?.window.close();server?.kill();fs.rmSync(temp,{recursive:true,force:true});});
