'use strict';
const $ = id => document.getElementById(id);
const state = {groups: [], key: null, kind: 'fixed', docId: '', selection: null, page: 1, stages: [], evidenceIds: new Set()};
const labels = {same:'一致',different:'内容变化',equivalent:'表述差异',missing:'未载明',review:'待核对'};
let toastTimer;
function toast(msg) {
  $('toast').textContent = msg; $('toast').hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, 10000);
}
async function api(path, body) {
  const response = await fetch(path, body ? {method:'POST',headers:{'Content-Type':'application/json','X-Requested-With':'ApprovalAgent'},body:JSON.stringify(body)} : {});
  const result = await response.json();
  if (!response.ok) throw Error(result.error || '请求失败');
  return result;
}
function node(tag, text, cls) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (cls) element.className = cls;
  return element;
}
function group() { return state.groups.find(g => g.key === state.key); }
function option(value, text) { const o = node('option', text); o.value = value; return o; }
async function refresh() {
  state.groups = (await api('/api/documents')).groups;
  if (!group()) state.key = state.groups[0]?.key || null;
  if (state.docId && !group()?.documents.some(d => d.id === state.docId)) state.docId = '';
  render();
}
function render() {
  const g = group();
  $('docCount').textContent = state.groups.reduce((a,b) => a+b.documents.length,0) + ' 份';
  $('projects').replaceChildren();
  for (const item of state.groups) {
    const b = node('button',undefined,'project-item'+(item.key === state.key ? ' active':''));
    b.append(node('strong',item.name),node('small',item.documents.length+' 份批文 · '+new Set(item.documents.map(d=>d.stage)).size+' 个阶段'));
    b.onclick = () => {state.key=item.key;state.docId='';state.selection=null;render();resetEvidence();};
    $('projects').append(b);
  }
  $('empty').hidden=!!g; $('tableWrap').hidden=!g; $('warnings').hidden=true;
  $('documentSelect').replaceChildren(option('','项目阶段对照'));
  if (!g) {resetEvidence();return;}
  for (const d of g.documents) $('documentSelect').append(option(d.id,'单文档 · '+d.stage+' · '+d.filename));
  $('documentSelect').value=state.docId; $('singleTools').hidden=!state.docId;
  if (state.docId) $('stageSelect').value=g.documents.find(d=>d.id===state.docId).stage;
  $('projectTitle').textContent=g.name;
  $('projectCode').textContent=g.key.startsWith('unassigned:')?'项目代码未载明，暂不自动合并':'项目代码 '+g.key;
  $('metricCount').textContent=g.rows.filter(r=>r.kind==='metric').length;
  const indices=g.documents.map((d,i)=>i).filter(i=>!state.docId||g.documents[i].id===state.docId);
  const table=node('table'), thead=node('thead'), header=node('tr');
  header.append(node('th','提取字段'));
  for (const i of indices) {
    const d=g.documents[i],th=node('th',d.stage);
    th.append(node('small',d.fields['发文字号'].value||'文号待确认'),node('small',d.filename)); header.append(th);
  }
  thead.append(header); table.append(thead);
  const tbody=node('tbody');
  for (const row of g.rows.filter(r=>r.kind===state.kind&&(!$('diffOnly').checked||r.status!=='same'))) {
    const tr=node('tr'),name=node('td',row.name);
    name.append(node('span',state.docId?'单文档提取':labels[row.status],'row-note'));
    tr.append(name);
    for (const i of indices) {
      const c=row.cells[i];
      const status=['needs_review','conflict','uncertain'].includes(c.status)?'review':c.value==null?'missing':state.docId?'same':row.status;
      const td=node('td',undefined,status),b=node('button',undefined,'cell-button');
      b.append(node('span',c.value??(c.status==='uncertain'?'识别不确定':'未载明'),'cell-value'));
      b.append(node('small',c.evidence.length?'查看原文 · 第 '+[...new Set(c.evidence.map(e=>e.page))].join('、')+' 页':'无可定位证据'));
      b.dataset.documentId=g.documents[i].id;b.dataset.field=row.name;
      b.setAttribute('aria-pressed',String(state.selection?.doc.id===g.documents[i].id&&state.selection?.row.name===row.name));
      b.onclick=()=>selectCell(g.documents[i],row,c);td.append(b);tr.append(td);
    }
    tbody.append(tr);
    if(row.note&&!state.docId){const noteRow=node('tr'),noteCell=node('td',row.note,'comparison-note');noteCell.colSpan=indices.length+1;noteRow.append(noteCell);tbody.append(noteRow);}
  }
  table.append(tbody);$('tableWrap').replaceChildren(table);
  const warnings=g.documents.flatMap(d=>d.warnings.map(w=>d.filename+'：'+w)),counts={};
  g.documents.forEach(d=>counts[d.stage]=(counts[d.stage]||0)+1);
  Object.entries(counts).forEach(([k,n])=>{if(n>1)warnings.push(k+'有多份批复，已保留独立列，请在单文档视图确认阶段。');});
  if (warnings.length) {
    const details=node('details'),ul=node('ul');details.append(node('summary','处理提示 · '+warnings.length+' 项'));
    warnings.forEach(w=>ul.append(node('li',w)));details.append(ul);$('warnings').replaceChildren(details);$('warnings').hidden=false;
  }
}
function resetEvidence() {$('evidenceEmpty').hidden=false;$('evidenceContent').hidden=true;$('evidenceStatus').textContent='选择表格中的字段';}
function selectCell(doc,row,c,index=null) {
  state.selection={doc,row,c,index:index??(row.kind==='metric'?doc.metrics.findIndex(m=>m.name===row.name):null)};
  document.querySelectorAll('.cell-button').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.documentId===doc.id&&b.dataset.field===row.name)));
  $('evidenceEmpty').hidden=true;$('evidenceContent').hidden=false;
  $('selectedField').textContent=row.name+' / '+doc.stage;$('selectedValue').textContent=c.value??(c.status==='uncertain'?'识别不确定':'原文未载明');
  $('evidenceStatus').textContent=['needs_review','conflict','uncertain'].includes(c.status)?'需要人工确认':c.status==='reviewed'?'人工已复核':'原文证据';
  $('humanNote').textContent=c.method==='human'?(c.evidence_binding==='reviewed'?'此值及证据经人工复核。':'此值经人工修订，以下仍为原提取证据。'):row.name==='印章'&&c.method==='visual-heuristic'?'红色近圆形区域检测结果，请核对图像后确认；不鉴定真实性。':'';
  $('variants').replaceChildren();$('editButton').disabled=false;
  if (row.kind==='metric') {
    const matches=doc.metrics.map((m,i)=>({m,i})).filter(({m})=>m.name===row.name);
    if (matches.length>1) {
      $('editButton').disabled=index===null;
      for (const {m,i} of matches) {const b=node('button','选择值：'+m.value,'source-link');b.onclick=()=>selectCell(doc,row,m,i);$('variants').append(b);}
    }
  }
  $('historyList').replaceChildren();
  const history=(doc.history||[]).filter(h=>h.name===row.name);
  if (!history.length) $('historyList').append(node('p','暂无修订记录','muted'));
  for(const h of history.slice().reverse()) {
    const value=typeof h.before==='object'?h.before.value:h.before;
    $('historyList').append(node('p',new Date(h.time*1000).toLocaleString()+'：'+(value??'未载明')+' → '+h.after+(h.reason?'；'+h.reason:''),'muted'));
  }
  $('sourceLinks').replaceChildren();
  const pages=[...new Set(c.evidence.map(e=>e.page))];
  pages.forEach(p=>{const b=node('button','第 '+p+' 页','source-link');b.onclick=()=>showPage(p);$('sourceLinks').append(b);});
  showPage(pages[0]||1);
}
function showPage(n) {
  const sel=state.selection;if(!sel)return;
  state.page=Math.max(1,Math.min(n,sel.doc.pages.length));
  const p=sel.doc.pages[state.page-1],ev=sel.c.evidence.filter(e=>e.page===state.page);
  $('pageLabel').textContent='第 '+state.page+' / '+sel.doc.pages.length+' 页';
  $('prevPage').disabled=state.page===1;$('nextPage').disabled=state.page===sel.doc.pages.length;
  $('pageImage').src='/api/documents/'+sel.doc.id+'/pages/'+state.page+'.png';
  $('pageImage').onerror=()=>toast('页面加载失败，请重试。');$('highlights').replaceChildren();
  for (const e of ev) {
    const [x0,y0,x1,y1]=e.bbox,h=node('div',undefined,'highlight');
    Object.assign(h.style,{left:(x0/p.width*100)+'%',top:(y0/p.height*100)+'%',width:((x1-x0)/p.width*100)+'%',height:((y1-y0)/p.height*100)+'%'});
    $('highlights').append(h);
  }
  $('sourceQuote').textContent=ev.length?ev.map(e=>e.quote).join(''):'本页无该字段证据。缺失信息不会从其他批文补填。';
}
function toBase64(file) {return new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result.split(',')[1]);r.onerror=reject;r.readAsDataURL(file);});}
async function watchJob(jid) {
  $('progress').hidden=false;let job;
  do {job=await api('/api/jobs/'+jid);$('progress').textContent='处理中 '+job.done+' / '+job.total+' 份';if(job.status==='running')await new Promise(r=>setTimeout(r,1000));} while(job.status==='running');
  await refresh();$('progress').textContent='完成 '+job.results.length+' 份'+(job.errors.length?'，失败 '+job.errors.length+' 份':'');
  if(job.errors.length)toast(job.errors.map(e=>e.filename+'：'+e.message).join('；'));
  else toast('处理完成，点击结果可定位原文。');
}
async function upload() {
  const files=[...$('fileInput').files];if(!files.length)return;
  if(files.length>10||files.some(f=>f.size>20*1024*1024)||files.reduce((n,f)=>n+f.size,0)>23*1024*1024){toast('每批最多10份，单份20MB，批次总计23MB以内。');return;}
  $('uploadButton').disabled=true;$('emptyUpload').disabled=true;$('progress').hidden=false;$('progress').textContent='正在读取文件…';
  try {
    const payload=[];for(const f of files)payload.push({name:f.name,data:await toBase64(f)});
    const r=await api('/api/upload',{files:payload,use_llm:$('useModel').checked});
    sessionStorage.setItem('activeJob',r.job_id);await watchJob(r.job_id);sessionStorage.removeItem('activeJob');
  } catch(e) {toast(e.message);$('progress').textContent='上传未完成，请重试。';}
  finally {$('uploadButton').disabled=false;$('emptyUpload').disabled=false;$('fileInput').value='';}
}
function renderEvidencePicker() {
  const doc=state.selection.doc,page=Number($('evidencePageSelect').value);$('evidencePicker').replaceChildren();
  const lines=doc.lines.filter(l=>l.page===page).map(l=>({id:l.id,text:l.text}));
  for(const e of doc.fields['印章'].evidence) if(e.page===page)lines.push({id:e.id,text:e.quote});
  for(const l of lines) {
    const label=node('label'),check=node('input');check.type='checkbox';check.checked=state.evidenceIds.has(l.id);
    check.onchange=()=>check.checked?state.evidenceIds.add(l.id):state.evidenceIds.delete(l.id);
    label.append(check,node('span',l.text));$('evidencePicker').append(label);
  }
}
$('uploadButton').onclick=$('emptyUpload').onclick=()=>$('fileInput').click();$('fileInput').onchange=upload;
$('documentSelect').onchange=()=>{state.docId=$('documentSelect').value;render();resetEvidence();};
$('saveStage').onclick=async()=>{try{const doc=group().documents.find(d=>d.id===state.docId);await api('/api/documents/'+doc.id+'/review',{kind:'stage',name:'审批阶段',value:$('stageSelect').value,revision:doc.revision??0});await refresh();toast('审批阶段已确认。');}catch(e){toast(e.message);}};
$('reprocess').onclick=async()=>{const b=$('reprocess');b.disabled=true;try{const r=await api('/api/documents/'+state.docId+'/reprocess',{use_llm:$('useModel').checked});await watchJob(r.job_id);resetEvidence();}catch(e){toast(e.message);}finally{b.disabled=false;}};
$('fixedTab').onclick=()=>{state.kind='fixed';$('fixedTab').setAttribute('aria-selected','true');$('metricsTab').setAttribute('aria-selected','false');render();};
$('metricsTab').onclick=()=>{state.kind='metric';$('fixedTab').setAttribute('aria-selected','false');$('metricsTab').setAttribute('aria-selected','true');render();};
$('diffOnly').onchange=render;$('prevPage').onclick=()=>showPage(state.page-1);$('nextPage').onclick=()=>showPage(state.page+1);
$('editButton').onclick=()=>{
  if(!state.selection)return;
  $('editLabel').textContent=state.selection.row.name;$('editValue').value=state.selection.c.value??'';$('editReason').value='';
  state.evidenceIds=new Set(state.selection.c.evidence.map(e=>e.id));$('evidencePageSelect').replaceChildren();
  state.selection.doc.pages.forEach(p=>$('evidencePageSelect').append(option(p.number,'第 '+p.number+' 页')));
  $('evidencePageSelect').value=state.page;renderEvidencePicker();$('editDialog').showModal();
};
$('evidencePageSelect').onchange=renderEvidencePicker;$('cancelEdit').onclick=()=>$('editDialog').close();
$('editForm').onsubmit=async e=>{
  e.preventDefault();const sel=state.selection;
  try {
    await api('/api/documents/'+sel.doc.id+'/review',{kind:sel.row.kind,name:sel.row.name,index:sel.index,value:$('editValue').value,reason:$('editReason').value,evidence_ids:[...state.evidenceIds],revision:sel.doc.revision??0});
    $('editDialog').close();await refresh();const g=state.groups.find(g=>g.documents.some(d=>d.id===sel.doc.id));
    state.key=g.key;render();const doc=g.documents.find(d=>d.id===sel.doc.id),row=g.rows.find(r=>r.name===sel.row.name&&r.kind===sel.row.kind);
    selectCell(doc,row,row.cells[g.documents.indexOf(doc)]);toast('修订与证据已保存，差异已重新计算。');
  } catch(err) {toast(err.message);}
};
(async()=>{
  try {
    const config=await api('/api/config');state.stages=config.stages;$('stageSelect').replaceChildren(...config.stages.map(s=>option(s,s)));
    $('useModel').disabled=!config.llm_ready;
    if(config.llm_ready){$('engineBadge').textContent='大模型辅助已配置';$('modelHelp').textContent='勾选后会将批文发送至已配置的模型服务。';}
    await refresh();const jid=sessionStorage.getItem('activeJob');if(jid){await watchJob(jid);sessionStorage.removeItem('activeJob');}
  }catch(e){toast(e.message);}
})();
