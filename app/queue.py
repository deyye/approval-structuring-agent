"""待办总览：把散落在各文档里「需要人工判断」的值收成一份可逐条确认的清单。

为什么需要它：系统原本只给**单个格子**打标记（这个值已确认、那个值要核对），
却没有「整个项目还剩多少没核对完」的总账。用的人永远不知道什么时候能收工，
也不知道还有没有漏网的——这是「流程不直观」里最让人难受的一点。

口径（与业务方确认后写进 docs/FIELD_SPEC.md）：
1. 需要人工判断的项 = 字段状态属待核对类（needs_review / conflict / uncertain）
   + 字段未载明但在原文检索到线索（reason=unextracted）
   + 指标冲突或需复核
2. 「原文未载明（reason=absent）」**不进**队列：系统已按全文线索判定原文确无
   该要素（实测庆元三份的「印发机关」版记里确实没有独立署名行），再要求逐条
   点击只是白耗人力。它仍可在单文档视图里查看并改成其他值。
3. 一个人工已处理过的项不再计入：判断依据是修订历史里出现过该字段/指标，
   而不是只看状态——因为「确认缺失」也会把状态写回 missing，
   只认状态会让确认过的缺失反复回到队列里。
4. 一个项目「审完」= 该项目下所有文档的待办项都为零。
"""

PENDING_FIELD_STATUS = ('needs_review', 'conflict', 'uncertain')
PENDING_METRIC_STATUS = ('needs_review', 'conflict', 'uncertain')

_WHY = {
    'needs_review': '系统给出的是推断值或图形候选，需人工确认',
    'conflict': '同一文档内出现多个值，需人工选定统计口径',
    'uncertain': '页面不可读，无法证明该要素确实缺失',
    'unextracted': '原文有该要素的线索，但自动提取未取到',
}
# 自检循环补回来的值可信度低于主干直取，理由要说清楚，不能让用户以为
# 它和正常抽出来的一样可靠。
_AGENT_WHY = '由自检循环补回（采用了放宽的识别办法），可信度低于主干直取，需人工核对'


def _why(key, method):
    """给出「为什么这一项需要人工判断」。自检循环的补救值优先说明其来源。"""
    if (method or '').startswith('agent-'):
        return _AGENT_WHY
    return _WHY.get(key, '需人工确认')


def _touched(doc):
    """人工动过的字段与指标（名字集合）。修订历史是唯一依据。"""
    names = set()
    for h in doc.get('history', []):
        if h.get('kind') in ('fixed', 'metric') and h.get('name'):
            names.add((h.get('kind'), h.get('name')))
    return names


def _page(cell):
    return next((e['page'] for e in cell.get('evidence', []) if 'page' in e), None)


def pending_items(doc):
    """单份文档里还需要人工处理的项。"""
    touched = _touched(doc)
    items = []
    for name, c in doc['fields'].items():
        if ('fixed', name) in touched or c.get('method') == 'human':
            continue
        status = c.get('status')
        unextracted = status == 'missing' and c.get('reason') == 'unextracted'
        if status in PENDING_FIELD_STATUS or unextracted:
            items.append({
                'kind': 'fixed', 'index': None, 'name': name,
                'value': c.get('value'), 'status': status,
                'method': c.get('method'), 'page': _page(c),
                'why': _why('unextracted' if unextracted else status, c.get('method')),
            })
    for i, m in enumerate(doc['metrics']):
        if ('metric', m['name']) in touched:
            continue
        if m.get('status') in PENDING_METRIC_STATUS:
            items.append({
                'kind': 'metric', 'index': i, 'name': m['name'],
                'value': m.get('value'), 'status': m.get('status'),
                'method': m.get('method'), 'page': _page(m),
                'why': _why(m.get('status'), m.get('method')),
            })
    return items


def absent_items(doc):
    """已核实「原文未载明」的空字段。不进队列，但要让用户看得见系统查过了。"""
    return [{'name': k, 'why': '已检索全文，原文未载明该要素'}
            for k, c in doc['fields'].items()
            if not c.get('value') and c.get('reason') == 'absent']


def alignment_items(rows):
    """跨阶段疑似同一指标的提示，同样需要人工决定是否合并。

    一对疑似指标会在两行上各标一次（比对表两行都要变色提示），但待办清单里
    只应出现一条——否则同一件事数两遍，进度永远清不了零。
    """
    seen = set()
    out = []
    for r in rows:
        if r['kind'] != 'metric' or not r.get('align_with'):
            continue
        for other in r['align_with']:
            pair = tuple(sorted((r['name'], other)))
            if pair in seen:
                continue
            seen.add(pair)
            out.append({'names': list(pair), 'name': pair[0], 'align_with': [pair[1]],
                        'why': '数值相同但各阶段名称不同，疑似同一指标，请确认是否合并'})
    return out


def project_progress(docs, rows=None):
    """项目级完成度：待办数、已处理动作数、三档状态。"""
    per_doc = []
    total = 0
    for d in docs:
        items = pending_items(d)
        total += len(items)
        per_doc.append({
            'id': d['id'], 'filename': d['filename'], 'stage': d['stage'],
            'pending': len(items), 'items': items,
            'absent': absent_items(d),
        })
    touched = sum(1 for d in docs for h in d.get('history', [])
                  if h.get('kind') in ('fixed', 'metric'))
    align = alignment_items(rows or [])
    if total == 0 and not align:
        status = '已审完'
    elif touched == 0:
        status = '未开始'
    else:
        status = '进行中'
    pending = total + len(align)
    return {
        'pending': pending, 'field_pending': total, 'alignment_pending': len(align),
        'touched': touched, 'status': status,
        'label': '已审完' if pending == 0 else '还剩 %d 项待核对' % pending,
        'documents': per_doc, 'alignments': align,
    }
