"""Validation and auditable human review, shared by UI and HTTP tests."""
import copy,re,time
from .extract import FIELDS,STAGES,numeric

def validate_evidence(d,ids):
    if not isinstance(ids,list) or len(ids)>100 or any(not isinstance(i,str) for i in ids):raise ValueError('证据编号无效')
    byid={l['id']:l for l in d['lines']}
    for c in d['fields'].values():
        for e in c['evidence']:
            if '-seal-' in e['id']:byid[e['id']]=dict(e,text=e['quote'])
    if any(i not in byid for i in ids):raise ValueError('引用证据不在本文件中')
    return [{'id':i,'page':byid[i]['page'],'bbox':byid[i]['bbox'],'quote':byid[i]['text']} for i in dict.fromkeys(ids)]

def update(d,data):
    if 'revision' in data and data['revision']!=d.get('revision',0):raise ValueError('文件已被修改，请刷新后再试')
    kind,name,value=data.get('kind'),data.get('name'),data.get('value')
    if not isinstance(value,str) or len(value)>15000:raise ValueError('字段内容无效')
    value=value.strip()
    reason=data.get('reason','')
    if not isinstance(reason,str) or len(reason)>1000:raise ValueError('说明过长')
    if kind=='stage':
        if value not in STAGES:raise ValueError('审批阶段无效')
        before=d['stage'];d['stage']=value
    else:
        if kind=='fixed':
            if name not in FIELDS:raise ValueError('未知字段')
            if name=='项目代码' and value and not re.fullmatch(r'\d{4}-\d{6}-\d{2}-\d{2}-\d{6}',value):raise ValueError('项目代码须为四位-六位-两位-两位-六位数字')
            if name=='印章' and value not in ['有','无','无法判断','']:raise ValueError('印章值须为有、无或无法判断')
            c=d['fields'][name]
        elif kind=='metric':
            i=data.get('index')
            if type(i) is not int or not 0<=i<len(d['metrics']):raise ValueError('指标索引无效')
            c=d['metrics'][i]
            if c['name']!=name:raise ValueError('指标名称与索引不匹配')
        else:raise ValueError('字段类型无效')
        before=copy.deepcopy(c)
        if 'evidence_ids' in data:c['evidence']=validate_evidence(d,data['evidence_ids'])
        c.update(value=value or None,status='reviewed' if value else 'missing',method='human')
        c['evidence_binding']='reviewed' if 'evidence_ids' in data else 'original'
        if kind=='metric':c['normalized']=numeric(value)
        if kind=='fixed' and name=='项目代码':d['project_key']=value or 'unassigned:'+d['id']
        if kind=='fixed' and name=='项目名称':d['project_name']=value or d['filename']
    d.setdefault('history',[]).append({'time':time.time(),'kind':kind,'name':name,'before':before,'after':value,'reason':reason})
    d['revision']=d.get('revision',0)+1
    return d
