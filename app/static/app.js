'use strict';
const $=id=>document.getElementById(id);
const state={groups:[],key:null,kind:'fixed',selection:null,page:1};
const labels={same:'一致',different:'内容变化',equivalent:'表述差异',missing:'未载明',review:'待核对'};
let toastTimer;
function toast(msg){$('toast').textContent=msg;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,9000)}
async function api(path,body){const r=await fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json','X-Requested-With':'ApprovalAgent'},body:JSON.stringify(body)}:{});const d=await r.json();if(!r.ok)throw Error(d.error||'请求失败');return d}
function node(tag,text,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e}
function group(){return state.groups.find(g=>g.key===state.key)}
async function refresh(){const d=await api('/api/documents');state.groups=d.groups;if(!group())state.key=d.groups[0]?.key||null;render()}
function render(){
 const g=group();$('docCount').textContent=state.groups.reduce((a,b)=>a+b.documents.length,0)+' 份';$('projects').replaceChildren();
 for(const item of state.groups){const b=node('button',undefined,'project-item'+(item.key===state.key?' active':''));b.append(node('strong',item.name),node('small',item.documents.length+' 份批文 · '+new Set(item.documents.map(d=>d.stage)).size+' 个阶段'));b.onclick=()=>{state.key=item.key;state.selection=null;render();resetEvidence()};$('projects').append(b)}
 $('empty').hidden=!!g;$('tableWrap').hidden=!g;$('warnings').hidden=true;
 if(!g){resetEvidence();return}
 $('projectTitle').textContent=g.name;$('projectCode').textContent=g.key.startsWith('unassigned:')?'项目代码未载明，暂不与其他文件自动合并':'项目代码 '+g.key;
 $('metricCount').textContent=g.rows.filter(r=>r.kind==='metric').length;
 const table=node('table');const thead=node('thead');const tr=node('tr');tr.append(node('th','提取字段'));
 for(const d of g.documents){const th=node('th',d.stage);th.append(node('small',d.fields['发文字号'].value||'文号待确认'),node('small',d.filename));tr.append(th)}thead.append(tr);table.append(thead);
 const tbody=node('tbody');
 for(const row of g.rows.filter(r=>r.kind===state.kind&&(!$('diffOnly').checked||r.status!=='same'))){const tr=node('tr');const name=node('td',row.name);name.append(node('span',labels[row.status],'row-note'));if(row.note)name.append(node('span',row.note,'row-note'));tr.append(name);
 row.cells.forEach((c,i)=>{const td=node('td',undefined,c.value==null?'missing':row.status);const b=node('button',undefined,'cell-button');b.append(node('span',c.value??'未载明','cell-value'));b.append(node('small',c.evidence.length?'查看原文 · 第 '+[...new Set(c.evidence.map(e=>e.page))].join('、')+' 页':'无可定位证据'));b.onclick=()=>selectCell(g.documents[i],row,c);td.append(b);tr.append(td)});tbody.append(tr)}
 table.append(tbody);$('tableWrap').replaceChildren(table);
 const warnings=g.documents.flatMap(d=>d.warnings.map(w=>d.filename+'：'+w));const counts={};g.documents.forEach(d=>counts[d.stage]=(counts[d.stage]||0)+1);Object.entries(counts).forEach(([k,n])=>{if(n>1)warnings.push(k+'有多份批复，已全部保留为独立列，请确认版本关系。')});
 if(warnings.length){const details=node('details');details.append(node('summary','处理提示 · '+warnings.length+' 项'));const ul=node('ul');warnings.forEach(w=>ul.append(node('li',w)));details.append(ul);$('warnings').replaceChildren(details);$('warnings').hidden=false}
}
function resetEvidence(){$('evidenceEmpty').hidden=false;$('evidenceContent').hidden=true;$('evidenceStatus').textContent='选择表格中的字段'}
function selectCell(doc,row,c){
 state.selection={doc,row,c,index:row.kind==='metric'?doc.metrics.findIndex(m=>m.name===row.name):null};
 $('evidenceEmpty').hidden=true;$('evidenceContent').hidden=false;$('selectedField').textContent=row.name+' / '+doc.stage;$('selectedValue').textContent=c.value??'原文未载明';$('evidenceStatus').textContent=c.status==='needs_review'?'需要人工确认':c.status==='reviewed'?'人工已复核':'原文证据';
 $('humanNote').textContent=c.method==='human'?'此值经人工修订，以下保留原提取证据。':row.name==='印章'&&c.method==='visual-heuristic'?'红色近圆形区域检测结果，请核对图像后确认；不鉴定印章真实性。':'';
 $('variants').replaceChildren();
 if(row.kind==='metric'){
 const matches=doc.metrics.map((m,i)=>({m,i})).filter(({m})=>m.name===row.name);
 if(matches.length>1){$('editButton').disabled=true;for(const {m,i} of matches){const b=node('button','选择值：'+m.value,'source-link');b.onclick=()=>{selectCell(doc,row,m);state.selection.index=i;$('editButton').disabled=false};$('variants').append(b)}}else $('editButton').disabled=false;
 }else $('editButton').disabled=false;
 $('sourceLinks').replaceChildren();
 const pages=[...new Set(c.evidence.map(e=>e.page))];pages.forEach(p=>{const b=node('button','第 '+p+' 页','source-link');b.onclick=()=>showPage(p);$('sourceLinks').append(b)});
 state.page=pages[0]||1;showPage(state.page);
}
function showPage(n){const sel=state.selection;if(!sel)return;state.page=Math.max(1,Math.min(n,sel.doc.pages.length));const p=sel.doc.pages[state.page-1];const ev=sel.c.evidence.filter(e=>e.page===state.page);
 $('pageLabel').textContent='第 '+state.page+' / '+sel.doc.pages.length+' 页';$('prevPage').disabled=state.page===1;$('nextPage').disabled=state.page===sel.doc.pages.length;
 $('pageImage').src='/api/documents/'+sel.doc.id+'/pages/'+state.page+'.png';$('pageImage').onerror=()=>toast('页面加载失败，请重试。');$('highlights').replaceChildren();
 for(const e of ev){const [x0,y0,x1,y1]=e.bbox;const h=node('div',undefined,'highlight');Object.assign(h.style,{left:(x0/p.width*100)+'%',top:(y0/p.height*100)+'%',width:((x1-x0)/p.width*100)+'%',height:((y1-y0)/p.height*100)+'%'});$('highlights').append(h)}
 $('sourceQuote').textContent=ev.length?ev.map(e=>e.quote).join(''):'本页无该字段证据。缺失信息不会从其他批文补填。';
}
function toBase64(file){return new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result.split(',')[1]);r.onerror=reject;r.readAsDataURL(file)})}
async function upload(){const files=[...$('fileInput').files];if(!files.length)return;
 if(files.length>10||files.some(f=>f.size>20*1024*1024)||files.reduce((n,f)=>n+f.size,0)>23*1024*1024){toast('每批最多10份，单份20MB，批次总计23MB以内。');return}
 $('uploadButton').disabled=true;$('emptyUpload').disabled=true;$('progress').hidden=false;$('progress').textContent='正在读取文件…';
 try{const payload=[];for(const f of files)payload.push({name:f.name,data:await toBase64(f)});const r=await api('/api/upload',{files:payload,use_llm:$('useModel').checked});let job;
 do{await new Promise(r=>setTimeout(r,1000));job=await api('/api/jobs/'+r.job_id);$('progress').textContent='处理中 '+job.done+' / '+job.total+' 份，请保持页面打开。'}while(job.status==='running');
 await refresh();$('progress').textContent='完成 '+job.results.length+' 份'+(job.errors.length?'，失败 '+job.errors.length+' 份':'');if(job.errors.length)toast(job.errors.map(e=>e.filename+'：'+e.message).join('；'));else toast('提取完成，点击表格中的值可定位原文。');
 }catch(e){toast(e.message);$('progress').textContent='上传未完成，请重试。'}finally{$('uploadButton').disabled=false;$('emptyUpload').disabled=false;$('fileInput').value=''}
}
$('uploadButton').onclick=$('emptyUpload').onclick=()=>$('fileInput').click();$('fileInput').onchange=upload;
$('fixedTab').onclick=()=>{state.kind='fixed';$('fixedTab').setAttribute('aria-selected','true');$('metricsTab').setAttribute('aria-selected','false');render()};
$('metricsTab').onclick=()=>{state.kind='metric';$('fixedTab').setAttribute('aria-selected','false');$('metricsTab').setAttribute('aria-selected','true');render()};$('diffOnly').onchange=render;
$('prevPage').onclick=()=>showPage(state.page-1);$('nextPage').onclick=()=>showPage(state.page+1);
$('editButton').onclick=()=>{if(!state.selection)return;$('editLabel').textContent=state.selection.row.name;$('editValue').value=state.selection.c.value??'';$('editDialog').showModal()};$('cancelEdit').onclick=()=>$('editDialog').close();
$('editForm').onsubmit=async e=>{e.preventDefault();const sel=state.selection;try{await api('/api/documents/'+sel.doc.id+'/review',{kind:sel.row.kind,name:sel.row.name,index:sel.index,value:$('editValue').value});$('editDialog').close();await refresh();const doc=state.groups.flatMap(g=>g.documents).find(d=>d.id===sel.doc.id);const g=state.groups.find(g=>g.documents.some(d=>d.id===sel.doc.id));state.key=g.key;render();const row=g.rows.find(r=>r.name===sel.row.name&&r.kind===sel.row.kind);selectCell(doc,row,row.cells[g.documents.indexOf(doc)]);toast('修订已保存，差异已重新计算。')}catch(err){toast(err.message)}};
(async()=>{try{const c=await api('/api/config');$('useModel').disabled=!c.llm_ready;if(c.llm_ready){$('useModel').checked=true;$('engineBadge').textContent='大模型辅助已配置';$('modelHelp').textContent='启用后，批文文本会发送至你配置的模型服务。';}await refresh()}catch(e){toast(e.message)}})();
