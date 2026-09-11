'use strict';
const $ = id => document.getElementById(id);
const state = {groups: [], key: null, kind: 'fixed', docId: '', selection: null, page: 1, stages: [], evidenceIds: new Set()};
const labels = {same:'一致',different:'内容变化',equivalent:'表述差异',missing:'未载明',review:'待核对',align:'疑似同一指标',unextracted:'原文有线索未提取到'};
let toastTimer;
// 说明每个值的来源与可信度，让复核者知道"为什么要确认"，而不是只看到一个红色状态。
// 后端产出的每一种来源都必须在这里有解释：漏掉一种，那个值在用户眼里就是"来历不明"
// ——大模型给的、自检循环补救的，会和规则直接抽出的一模一样，这与本系统
// 「每个值都要有出处」的定位直接冲突。
const methodNotes = {
  human: c => c.evidence_binding === 'reviewed' ? '此值及证据经人工复核。' : '此值经人工修订，以下仍为原提取证据。',
  'macos-vision': () => '本值由版头图片经 OCR 识别得到，可能存在识别误差，请对照原文版头核对。',
  tesseract: () => '本值由版头图片经 OCR 识别得到，可能存在识别误差，请对照原文版头核对。',
  'vision-required': () => '该要素位于图片层且未能识别出内容，请人工查看原文版头后再填。',
  'vision-model': () => '本值由视觉模型复核印章候选得到，不鉴定印章真实性，请对照原文核对。',
  filename: () => '正文无该文本，值取自附件名推断，请核对。',
  'title-inference': () => '该值由标题推断而来，请核对事项口径。',
  addressee: () => '该值由主送机关（收件人）推断为项目单位，请核对角色。',
  'visual-heuristic': () => '红色近圆形区域检测结果，请核对图像后确认；不鉴定真实性。',
  llm: () => '本值由大模型辅助抽取，其引用已通过原文校验，请按需核对。',
  'agent-widen': () => '本值由自检循环放宽章节判据后补回，可信度低于主干直取，请核对。',
  'agent-alt-form': () => '本值由自检循环换用备用识别形态补回，可信度低于主干直取，请核对。',
  'agent-read': () => '本值由自检循环定点回读原文补回，可信度低于主干直取，请核对。'
};
// 自检循环的动作名，翻成业务语言，让用户看得懂它到底做了什么。
const actionLabels = {
  widen_sections: '放宽章节判据后重扫',
  alt_form_metrics: '换用备用识别形态重抽',
  read_section: '定点回读原文补字段',
  escalate: '放弃自动修复并上报'
};
function methodNote(row, c) {
  if (row.name === '印章' && !methodNotes[c.method]) return '';
  const note = methodNotes[c.method];
  return note ? note(c) : '';
}
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
    // 项目级完成度：让用户一眼看到「还剩多少没核对完」，而不是只看单个格子的颜色。
    const rv = item.review;
    if (rv) b.append(node('small', rv.pending === 0 ? '核对已审完' : '核对 ' + rv.label, 'progress' + (rv.pending === 0 ? ' done' : '')));
    b.onclick = () => {state.key=item.key;state.docId='';state.selection=null;render();resetEvidence();};
    $('projects').append(b);
  }
  $('empty').hidden=!!g; $('tableWrap').hidden=!g; $('warnings').hidden=true;
  $('documentSelect').replaceChildren(option('','项目阶段对照'));
  if (!g) {resetEvidence();renderReview(null);renderAgent(null);return;}
  renderReview(g);
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
      // 「原文确实没有」与「系统没抽到」必须分开显示：混在一起时，正确的缺失
      // 看起来像系统故障（实测庆元三份的印发机关连开三个空格子），而真正的漏抽
      // 又淹没在同样的灰格里没人处理。
      const status=['needs_review','conflict','uncertain'].includes(c.status)?'review'
        :(c.value==null&&c.reason==='unextracted')?'unextracted'
        :c.value==null?'missing':state.docId?'same':row.status;
      const td=node('td',undefined,status),b=node('button',undefined,'cell-button');
      const blank=c.status==='uncertain'?'识别不确定':(c.reason==='unextracted'?'未提取到（原文有线索）':'原文未载明');
      b.append(node('span',c.value??blank,'cell-value'));
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
  renderAgent(g);
}
// 待办总览：把「还需要人工判断」的值收成一份可逐条确认的清单，并给出项目级进度。
// 这是本系统最缺的一条主线——原先只有单个格子的颜色，用户永远不知道什么时候能收工。
function renderReview(g) {
  const box=$('reviewPanel');
  if (!g || !g.review) {box.hidden=true;return;}
  const r=g.review;
  box.replaceChildren();box.hidden=false;
  const head=node('div',undefined,'review-head');
  head.append(node('span','核对进度'),
    node('span',r.pending===0?'已审完（'+r.touched+' 处人工确认）':r.status+' · '+r.label,'progress'+(r.pending===0?' done':'')));
  box.append(head);
  if (r.pending===0) {
    box.append(node('p','全部待办项已处理完毕，可以导出归档。','review-empty'));
  } else {
    const ul=node('ul',undefined,'review-list');
    for (const a of (r.alignments||[])) {
      const li=node('li');
      const names=a.names||[];
      li.append(node('span','指标对齐','doc'),
                node('span',names.join(' ↔ ')+'（均为 '+(a.value||'同一数值')+'）','val'));
      // 合并/不合并必须都能落地：只在表里标黄而不给处理入口，这一项会永远
      // 留在待办里，项目也就永远到不了「已审完」。
      for (const target of names) {
        const other=names.find(n=>n!==target);
        const holder=(a.holders||[]).find(h=>h.name===other);
        if (!holder) continue;
        const b=node('button','统一为「'+target+'」');
        b.onclick=()=>resolveAlign(holder,other,target,'merge');
        li.append(b);
      }
      const keepHolder=(a.holders||[])[0];
      if (keepHolder) {
        const n=keepHolder.name, other=names.find(x=>x!==n)||'';
        const k=node('button','确认不合并');
        k.onclick=()=>resolveAlign(keepHolder,n,other,'keep');
        li.append(k);
      }
      li.append(node('span',a.why,'why'));
      ul.append(li);
    }
    for (const d of r.documents) for (const it of d.items) {
      const li=node('li');
      const btn=node('button','去核对');btn.onclick=()=>gotoItem(g,d.id,it);
      li.append(node('span',d.stage,'doc'),node('span',it.name+'：'+(it.value??'（空）'),'val'),btn,
                node('span',it.why,'why'));
      ul.append(li);
    }
    box.append(ul);
  }
  const absent=r.documents.reduce((n,d)=>n+d.absent.length,0);
  if (absent) box.append(node('p','另有 '+absent+' 个空值未检出相关线索，暂不计入待办；这不等于人工确认原文未载明，交付前请对照原文抽查。','review-note'));
}
function gotoItem(g,docId,it) {
  if (state.docId!==docId) {state.docId=docId;render();}
  const doc=g.documents.find(d=>d.id===docId);
  const row=g.rows.find(r=>r.kind===it.kind&&r.name===it.name);
  if (!doc||!row) return;
  selectCell(doc,row,row.cells[g.documents.indexOf(doc)],it.index);
}
// 处理「疑似同一指标」：merge 把本文件里的指标改名为对方名称（两行合并），
// keep 只记录「确实不是同一指标」的判断。两者都会让该项目少一项待办。
async function resolveAlign(holder,name,other,decision) {
  try {
    const doc=await api('/api/documents/'+holder.doc);
    await api('/api/documents/'+holder.doc+'/review',{kind:'align',name,other,decision,value:'',
      reason:decision==='merge'?'跨阶段指标名称对齐：合并为同一指标':'跨阶段指标名称对齐：确认不是同一指标',
      revision:doc.revision??0});
    await refresh();
    toast(decision==='merge'
      ? '已把「'+name+'」统一为「'+other+'」，比对已重新计算。'
      : '已确认两者不是同一指标，该项不再计入待办。');
  } catch(e) {toast(e.message);}
}
// 自检循环的记录：用户能看见系统在想什么，「流程不直观」才有解。
function renderAgent(g) {
  const box=$('agentPanel');
  const doc=g&&state.docId?g.documents.find(d=>d.id===state.docId):null;
  const agent=doc&&doc.agent;
  if (!agent) {box.hidden=true;return;}
  box.replaceChildren();box.hidden=false;
  const h=node('h4','自检循环记录');
  const words={ok:'已收敛',noted:'有提示',escalated:'转人工复核'};
  h.append(node('span',words[agent.status]||agent.status,'agent-status '+(agent.status||'noted')));
  box.append(h);
  if (!(agent.trace||[]).length) {
    box.append(node('p','本次未发现异常，未执行任何补救动作。','muted'));
  } else {
    const ol=node('ol');
    for (const t of agent.trace) {
      const li=node('li');
      li.append(node('span','发现「'+(t.issues||[]).join('、')+'」→ 执行「'+(actionLabels[t.action]||t.action)
        +'」（'+(t.decided_by==='llm'?'模型决策':'规则决策')+'）→ '+t.note));
      if (t.reason) li.append(node('span','；'+t.reason,'why'));
      ol.append(li);
    }
    box.append(ol);
  }
  const left=(agent.issues||[]);
  if (left.length) box.append(node('p','仍需人工处理：'+left.map(i=>i.detail).join('；'),'why'));
}
function resetEvidence() {$('evidenceEmpty').hidden=false;$('evidenceContent').hidden=true;$('evidenceStatus').textContent='选择表格中的字段';}
function selectCell(doc,row,c,index=null) {
  state.selection={doc,row,c,index:index??(row.kind==='metric'?doc.metrics.findIndex(m=>m.name===row.name):null)};
  document.querySelectorAll('.cell-button').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.documentId===doc.id&&b.dataset.field===row.name)));
  $('evidenceEmpty').hidden=true;$('evidenceContent').hidden=false;
  $('selectedField').textContent=row.name+' / '+doc.stage;$('selectedValue').textContent=c.value??(c.status==='uncertain'?'识别不确定':'原文未载明');
  $('evidenceStatus').textContent=['needs_review','conflict','uncertain'].includes(c.status)?'需要人工确认':c.status==='reviewed'?'人工已复核':'原文证据';
  $('humanNote').textContent=methodNote(row,c);
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
async function testModelConnection(vision) {
  const b=$(vision?'testVision':'testModel');b.disabled=true;
  $('modelTestResult').textContent='正在测试连接（仅发送测试内容）…';
  try {const r=await api('/api/model/test',{vision});$('modelTestResult').textContent=r.model+' · 连接正常 · '+r.latency_ms+' ms';}
  catch(e){$('modelTestResult').textContent=e.message;}finally{b.disabled=false;}
}
$('testModel').onclick=()=>testModelConnection(false);$('testVision').onclick=()=>testModelConnection(true);
(async()=>{
  try {
    const config=await api('/api/config');state.stages=config.stages;$('stageSelect').replaceChildren(...config.stages.map(s=>option(s,s)));
    $('useModel').disabled=!config.llm_ready;
    $('testModel').disabled=!config.llm_ready;$('testVision').disabled=!config.llm_ready||!config.vision_model;
    if(config.model_error)$('modelHelp').textContent=config.model_error;
    if(config.llm_ready){$('engineBadge').textContent='大模型辅助已配置';$('modelHelp').textContent='勾选后会将批文发送至已配置的模型服务。';}
    await refresh();const jid=sessionStorage.getItem('activeJob');if(jid){await watchJob(jid);sessionStorage.removeItem('activeJob');}
  }catch(e){toast(e.message);}
})();
