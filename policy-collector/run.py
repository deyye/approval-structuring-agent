#!/usr/bin/env python3
"""投资项目政策文件采集器 —— 命令行入口。

用法
----
  python run.py --sources gov_cn ndrc              # 只跑指定源
  python run.py --max-pages 5 --detail-limit 80    # 限速试跑
  python run.py --since 2026-01-01                 # 增量（定期采集用这个）
  python run.py --list-only                        # 只抓列表不抓正文，很快
  python run.py --report                           # 只看覆盖情况，不抓取

产物
----
  out/policies.db     SQLite 库（主交付物，可被其他系统直接查询）
  out/policies.xlsx   人工查阅用（含分类着色与统计页）
  out/coverage.json   各源采集覆盖情况（含失败原因，直连不了的源不会被静默跳过）
"""
import argparse
import json
import os
import re
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from collector.classify import classify
from collector.extract import extract_detail, strip_tags
from collector.fetch import Fetcher, FetchError
from collector.sources import (PAGE_TEMPLATES, SOURCES, apply_page_template,
                               discover_policy_columns, next_page_url,
                               parse_list_generic, parse_list_ndrc)
from collector.store import Store, content_hash, norm_docnum, norm_title

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, 'out')


def log(msg):
    print(msg, flush=True)


def norm_date(s):
    if not s:
        return ''
    s = re.sub(r'[^\d]', '-', str(s)).strip('-')
    part = [p for p in s.split('-') if p]
    if len(part) >= 3:
        return '%04d-%02d-%02d' % (int(part[0]), int(part[1]), int(part[2]))
    return ''


# ---------------------------------------------------------------- 列表获取
def list_gov_cn(src, fetcher, max_pages):
    """中央人民政府政策文件库：官方 JSON 接口分页。"""
    items = []
    n = src.get('page_size', 20)
    for page in range(1, max_pages + 1):
        q = ('%s?t=zhengcelibrary_gw&q=&timetype=timeqb&mintime=&maxtime=&sort=pubtime'
             '&sortType=1&searchfield=title&p=%d&n=%d' % (src['api'], page, n))
        try:
            data, _ = fetcher.json(q, referer=src.get('referer'))
        except FetchError as exc:
            log('    ! 第 %d 页失败：%s' % (page, exc))
            break
        vo = (data or {}).get('searchVO') or {}
        batch = vo.get('listVO') or []
        if not batch:
            break
        for it in batch:
            items.append({
                'title': (it.get('title') or '').strip(),
                'url': (it.get('url') or '').strip(),
                'pub_date': norm_date(it.get('pubtimeStr') or it.get('pubtime')),
                'doc_number': (it.get('pcode') or it.get('wenhao') or '').strip(),
                'issuer': (it.get('puborg') or '').strip(),
                'summary': (it.get('summary') or '').strip(),
                'column': (it.get('childtype') or '').strip(),
                'index_no': (it.get('index') or '').strip(),
            })
        log('    · 第 %d 页 %d 条（累计 %d）' % (page, len(batch), len(items)))
        if len(batch) < n:
            break
    return items


def _classify_error(exc):
    """把抓取异常归成三档，因为处理方式完全不同。

    js_required  —— 列表由 JS 注入，静态抓不到，需要浏览器渲染层（已知的架构缺口）
    parser_gap   —— 页面有内容但解析规则没覆盖，是**我们自己的代码缺陷**，必须修
    failed       —— 网络/证书/反爬，属环境或对方站点问题，可能要人工介入

    必须把 parser_gap 单独拎出来：它和 js_required 长得像（都是 0 条），
    但一个是"要加渲染层"，一个是"改几行选择器就行"。混在一起会让人误判工期。
    """
    msg = str(exc)
    if '解析规则缺口' in msg:
        return 'parser_gap'
    if 'JS 注入' in msg or 'JS 渲染' in msg:
        return 'js_required'
    return 'failed'


def _guard_empty(items, probe_html, where):
    """列表抓取返回 0 条时，必须抛错并说清成因——不能默默返回空列表。

    ⚠️ 改之前这里直接 return []，调用方记 status=ok / new=0，浙江省就是这么被标成
    「✓ 新增 0」的：看起来跑完了，实际一条没抓到，日志里也没有任何异常。
    这类"沉默的 0 条"比抛错危险得多——它会被当成"该站最近没发政策"，
    于是漏洞进了交付物还查不出来。

    还要区分两种成因，因为处理方式完全不同：
      页面里有发布日期 → 是列表页，解析器没认出来 → 解析规则缺口，补选择器即可
      页面里没有日期  → JS 空壳或跳转页 → 需要浏览器渲染层
    两种都带上正文长度，便于判断页面到底是空壳还是没解析对。
    """
    if items:
        return items
    probe = probe_html or ''
    size = len(strip_tags(probe))
    if re.search(r'(20\d{2})[-/年.]\d{1,2}[-/月.]\d{1,2}', probe):
        raise RuntimeError(
            '栏目页可访问且含发布日期，但未识别出任何条目（正文 %d 字符）'
            '——解析规则缺口，需补选择器' % size)
    raise RuntimeError(
        '栏目页无列表内容（正文仅 %d 字符，无发布日期）——列表由 JS 注入，'
        '需浏览器渲染层' % size)


def detect_page_template(col_url, first_batch, fetcher, referer):
    """探测栏目的翻页 URL 模板，返回 (模板, 第2页页码) 或 (None, None)。

    ⚠️ 必须探测的原因：政务 CMS 的翻页链接很多是 `document.write` 在**运行时**
    拼出来的，静态 HTML 里没有 `<a href>`，"读下一页链接"这一招用不上。
    实测北京市发改委某栏目 JS 写的是 `countPage = 134`（共 134 页），
    翻页规则 index.htm → index_1.htm → index_2.htm（`.htm` 不是 `.html`），
    而按文件名猜的 index_1.html 一律 404 —— 每个栏目只抓到第 1 页，
    数据"看着有"，实际少了两个数量级。

    判据：候选模板要满足「能取到 + 解析出条目 + 至少一条与第 1 页不同」，
    避免把"其实还是第 1 页"（服务器对任何路径都返回同一页）当成翻页成功。
    """
    seen = {it['url'] for it in first_batch}
    for tmpl, start in PAGE_TEMPLATES:
        probe = apply_page_template(col_url, tmpl, start)
        if probe.rstrip('/') == col_url.rstrip('/'):
            continue
        try:
            html, _ = fetcher.text(probe, referer=referer)
        except Exception:
            continue
        try:
            got = parse_list_generic(html, probe)
        except Exception:
            continue
        if got and any(it['url'] not in seen for it in got):
            return tmpl, start
    return None, None


def crawl_column(fetcher, col_url, col_name, max_pages, referer, parser=parse_list_generic):
    """抓一个栏目的多页列表：第 1 页 → 决定翻页方式 → 依次翻页。

    翻页方式的优先级：
      1. 页面里的「下一页」链接（最可靠，但很多站没有）
      2. 探测到的模板（见 detect_page_template）
      3. 都拿不到 → 只取第 1 页，并**明确记一行日志**，不假装抓到全部
    返回 (items, last_html)。
    """
    items, visited, last_html = [], set(), ''
    url, page = col_url, 1
    tmpl = start = None
    while url and url not in visited and page <= max_pages:
        visited.add(url)
        try:
            html, _ = fetcher.text(url, referer=referer)
        except FetchError as exc:
            log('    ! [%s] p%d 失败：%s' % (col_name, page, str(exc)[:80]))
            break
        last_html = html
        batch = parser(html, url)
        for b in batch:
            b['column'] = col_name
        items.extend(batch)
        log('    · [%s] p%d → %d 条（累计 %d）' % (col_name, page, len(batch), len(items)))
        if not batch:
            break

        nxt = next_page_url(html, url)
        if nxt and nxt != url:
            url, page = nxt, page + 1
            continue

        if tmpl is None:
            tmpl, start = detect_page_template(col_url, batch, fetcher, referer)
            if not tmpl:
                log('    · [%s] 页面无下一页链接、模板探测也未命中，仅取第 1 页' % col_name[:14])
                break
            log('    · [%s] 探测到翻页模板 %s' % (col_name[:14], tmpl))

        # 第 n 页 → 模板页码 = start + (n - 2)；当前页是 page，下一页就是 page+1
        nxt = apply_page_template(col_url, tmpl, start + page - 1)
        if not nxt or nxt == url:
            break
        url, page = nxt, page + 1
    return items, last_html


def list_html_columns(src, fetcher, max_pages):
    """部委/省级栏目：静态列表 + 翻页。"""
    parser = parse_list_ndrc if src['id'].startswith('ndrc') else parse_list_generic
    items, last_html = [], ''
    for col_name, col_url in src.get('columns', []):
        got, h = crawl_column(fetcher, col_url, col_name, max_pages,
                              src.get('home') or col_url, parser)
        items.extend(got)
        last_html = h or last_html
    return _guard_empty(items, last_html, src['id'])


def list_html_discover(src, fetcher, max_pages):
    """省级站：先从首页发现政策专栏，再逐栏抓列表。

    两处实测出来的必要设计：
    1. **栏目内再发现一层子栏目**。相当多省站的「政策文件」入口只是一个落地页，
       真正的列表在它下面（规范性文件 / 政策解读 / 规划计划…）。只抓第一层会得到
       0 条，看起来像采集失败，其实是没往下走——这类"沉默的漏采"最容易被忽略。
    2. **栏目数与总量都要封顶**，否则一个省能裂出几十个栏目，时间全耗在翻页上。
    """
    home = src['home']
    try:
        html, _ = fetcher.text(home)
    except FetchError as exc:
        raise RuntimeError('首页不可达：%s' % str(exc)[:90])
    cols = discover_policy_columns(html, home)
    if not cols:
        raise RuntimeError('首页可达但未发现政策栏目（正文长度仅 %d 字符，疑似 JS 渲染）'
                           % len(strip_tags(html)))
    log('    首页发现政策栏目 %d 个：%s' % (len(cols), '、'.join(c['name'] for c in cols[:6])))

    items, tried, last_html = [], set(), ''
    queue = [(c['name'], c['url'], 1) for c in cols[:4]]
    while queue and len(items) < 400:
        name, url, depth = queue.pop(0)
        if url in tried:
            continue
        tried.add(url)
        got, h = crawl_column(fetcher, url, name, max_pages, home)
        last_html = h or last_html
        got_here = len(got)
        items.extend(got)
        if got_here:
            log('    · [%s] 小计 %d 条' % (name[:14], got_here))
        elif depth < 2 and last_html:
            sub = discover_policy_columns(last_html, url)
            for c in sub[:4]:
                queue.append((c['name'], c['url'], depth + 1))
            if sub:
                log('    · [%s] 本层无条目，下探子栏目 %d 个' % (name[:14], len(sub)))

    # 见 _guard_empty：这里以前直接 return []，是「浙江被标成 ✓ 新增 0」的根因。
    return _guard_empty(items, last_html or html, src['id'])


# ---------------------------------------------------------------- 主流程
def collect(args):
    os.makedirs(OUT, exist_ok=True)
    db_path = os.path.join(OUT, 'policies.db')
    store = Store(db_path)
    fetcher = Fetcher(delay=args.sleep, use_proxy=not getattr(args, 'no_proxy', False))

    run_id = time.strftime('run-%Y%m%d-%H%M%S')
    store.start_run(run_id)

    targets = [s for s in SOURCES if not args.sources or s['id'] in args.sources]
    if not targets:
        log('!! 没有匹配的源。可用：%s' % ', '.join(s['id'] for s in SOURCES))
        return 1

    # 只把「已有正文」的记录算作已知：list-only 留下的空壳记录要能被重新抓详情
    known = store.known_urls(complete_only=True)
    log('库内已有 %d 条；本次采集 %d 个源' % (len(known), len(targets)))
    log('=' * 78)

    coverage, totals = [], {'fetched': 0, 'new': 0, 'dup': 0, 'filtered': 0, 'errors': 0,
                            'enriched': 0}
    detail_left = args.detail_limit

    for src in targets:
        t0 = time.time()
        log('\n■ %s（%s）' % (src['name'], src['id']))
        entry = {'id': src['id'], 'name': src['name'], 'status': 'ok',
                 'level': src.get('level', ''), 'region': src.get('region', ''),
                 'listed': 0, 'new': 0, 'duplicates': 0, 'filtered': 0,
                 'errors': 0, 'note': '',
                 'checked_at': time.strftime('%Y-%m-%d %H:%M')}
        try:
            if src['type'] == 'gov_cn_api':
                items = list_gov_cn(src, fetcher, min(args.max_pages, src.get('max_pages', 40)))
            elif src['type'] == 'html_list':
                items = list_html_columns(src, fetcher, min(args.max_pages, src.get('max_pages', 6)))
            elif src['type'] == 'html_discover':
                items = list_html_discover(src, fetcher, min(args.max_pages, src.get('max_pages', 3)))
            else:
                raise RuntimeError('未知源类型 %s' % src['type'])
        except Exception as exc:
            entry['status'] = _classify_error(exc)
            entry['note'] = str(exc)[:200]
            log('  ✗ %s：%s' % (entry['status'], entry['note']))
            coverage.append(entry)
            totals['errors'] += 1
            continue

        entry['listed'] = len(items)
        log('  列表合计 %d 条' % len(items))
        fresh = [it for it in items if it['url'] and it['url'] not in known]
        if args.since:
            fresh = [it for it in fresh if not it['pub_date'] or it['pub_date'] >= args.since]
        # 最新优先：详情抓取是有限额的，先抓最近的——政策时效性远高于陈年旧文。
        fresh.sort(key=lambda x: (x.get('pub_date') or ''), reverse=True)
        # 每源单独限额：全局共享会让排在前面的源（如国家发改委有 700 条列表）
        # 把额度吃光，省级站一条正文都抓不到——实测踩过。
        quota = min(getattr(args, 'detail_per_source', 60), detail_left)
        if not args.list_only and len(fresh) > quota:
            log('  未入库 %d 条，本限抓最新 %d 条正文' % (len(fresh), quota))
            fresh = fresh[:quota]
        else:
            log('  其中未入库 %d 条%s' % (len(fresh), '（已按 --since 过滤）' if args.since else ''))

        if args.list_only:
            for it in fresh:
                rec = _record(src, it, {}, run_id)
                if store.find_duplicate(rec)[0]:
                    totals['dup'] += 1
                    entry['duplicates'] += 1
                    continue
                store.insert(rec)
                known.add(rec['url'])
                totals['new'] += 1
                entry['new'] += 1
            totals['fetched'] += len(items)
            coverage.append(entry)
            log('  ✓ 仅列表模式：新增 %d' % entry['new'])
            continue

        for it in fresh:
            if detail_left <= 0:
                entry['note'] = (entry['note'] + ' 已达 --detail-limit，未抓正文').strip()
                break
            detail_left -= 1
            totals['fetched'] += 1
            try:
                html, _ = fetcher.text(it['url'], referer=src.get('home') or it['url'])
                det = extract_detail(html, it['url'])
            except Exception as exc:
                entry['errors'] += 1
                totals['errors'] += 1
                log('    ! %s :: %s' % (it['title'][:32], str(exc)[:70]))
                continue
            rec = _record(src, it, det, run_id)
            dup, why = store.find_duplicate(rec)
            if dup is not None:
                # 已有记录但没有正文（list-only 留下的空壳），这次把详情补上而不是跳过。
                # 不这么做的话，这批 URL 会被当成"已采集"，正文永远补不回来。
                if store.enrich(dup, rec):
                    totals['enriched'] += 1
                    entry['new'] += 1
                    log('    ~ 补全正文：%s' % rec['title'][:50])
                    continue
                totals['dup'] += 1
                entry['duplicates'] += 1
                continue
            if not rec['is_investment']:
                # 不丢弃：研判结果本身要留痕，且「是否投资项目类」的门槛后续可能要调，
                # 一旦在入库时删掉就再也补不回来。导出时再按需过滤。
                totals['filtered'] += 1
                entry['filtered'] += 1
            store.insert(rec)
            known.add(rec['url'])
            totals['new'] += 1
            entry['new'] += 1
            if not rec['is_investment']:
                log('    · [非投资类] %s' % rec['title'][:52])
            else:
                log('    + [%s] %s' % (rec['category'], rec['title'][:56]))

        entry['seconds'] = round(time.time() - t0, 1)
        log('  ✓ 新增 %d / 重复 %d / 非投资类过滤 %d / 失败 %d（%ss）'
            % (entry['new'], entry['duplicates'], entry['filtered'], entry['errors'], entry['seconds']))
        coverage.append(entry)

    store.finish_run(run_id, fetched=totals['fetched'], new_records=totals['new'],
                     duplicates=totals['dup'], filtered=totals['filtered'],
                     errors=totals['errors'], detail=coverage)
    _merge_coverage(coverage)

    log('\n' + '=' * 78)
    log('本次：抓取 %d / 新增 %d / 重复 %d / 过滤 %d / 失败 %d'
        % (totals['fetched'], totals['new'], totals['dup'], totals['filtered'], totals['errors']))
    log('库内合计：%s' % json.dumps(store.stats(), ensure_ascii=False))
    if args.export:
        n = store.export_xlsx(os.path.join(OUT, 'policies.xlsx'), only_investment=args.only_investment)
        log('导出 Excel：out/policies.xlsx（%d 条）' % n)
    return 0


def _record(src, item, detail, run_id):
    from collector.extract import _is_meta_label
    dtitle = detail.get('title') or ''
    # 详情页标题若抽成了版式标签（政府网用 h2 放"索引号"），回退用列表标题
    title = dtitle if (dtitle and not _is_meta_label(dtitle) and len(dtitle) >= 6) else (item.get('title') or dtitle)
    content = detail.get('content') or item.get('summary') or ''
    pub_date = detail.get('pub_date') or item.get('pub_date') or ''
    doc_number = norm_docnum(detail.get('doc_number') or item.get('doc_number') or '')
    issuer = detail.get('issuer') or item.get('issuer') or ''
    cls = classify(title, content, doc_number, profile=getattr(_record, 'profile', 'strict'))
    return {
        'source_id': src['id'], 'source_name': (item.get('column') or src['name']),
        'level': src.get('level', ''), 'region': src.get('region', ''),
        'title': title, 'doc_number': doc_number, 'pub_date': pub_date, 'issuer': issuer,
        'category': cls['category'], 'doc_type': cls['doc_type'],
        'category_confidence': cls['category_confidence'],
        'category_reason': cls['category_reason'],
        'is_investment': 1 if cls['is_investment'] else 0,
        'relevance': cls['relevance'],
        'url': item['url'], 'content': content,
        'attachments': json.dumps(detail.get('attachments') or [], ensure_ascii=False),
        'content_hash': content_hash(title, content),
        'title_key': norm_title(title),
        'dup_of': None,
        'collected_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'first_seen_run': run_id,
    }


def reclassify(profile='strict'):
    """按当前规则重判库内全部记录（不联网）。

    必要性：分类门槛是业务口径，会随业务方反馈调整。若每次调门槛都要重新
    抓一遍网页，既慢又给对方站点添麻烦——所以入库时保留正文，重判只读本地。
    """
    db_path = os.path.join(OUT, 'policies.db')
    if not os.path.exists(db_path):
        log('还没有数据库。')
        return 1
    store = Store(db_path)
    rows = store.conn.execute('SELECT id,title,content,doc_number FROM policies').fetchall()
    inv = 0
    for r in rows:
        k = classify(r['title'], r['content'] or '', r['doc_number'] or '', profile=profile)
        inv += 1 if k['is_investment'] else 0
        store.conn.execute(
            'UPDATE policies SET category=?, doc_type=?, category_confidence=?, '
            'category_reason=?, is_investment=?, relevance=? WHERE id=?',
            (k['category'], k['doc_type'], k['category_confidence'], k['category_reason'],
             1 if k['is_investment'] else 0, k['relevance'], r['id']))
    store.conn.commit()
    log('重判完成（档位 %s）：共 %d 条，其中投资项目类 %d 条（%.0f%%）'
        % (profile, len(rows), inv, 100.0 * inv / max(len(rows), 1)))
    # 重判后必须重新导出：否则 out/policies.xlsx 还是旧口径的结果，
    # 而人看的是 Excel——调完门槛发现 Excel 没变，会以为是重判没生效。
    n = store.export_xlsx(os.path.join(OUT, 'policies.xlsx'), only_investment=True)
    log('已按新口径重新导出 Excel：out/policies.xlsx（%d 条）' % n)
    return 0


def _merge_coverage(coverage):
    """把本次结果并入 coverage.json，按源 id 保留最新一条。

    为什么必须合并而不是直接覆盖：`--sources` 允许只跑一个源（排查问题时最常用）。
    如果每次运行都把 coverage.json 重写成"本次跑到的源"，那么跑一次单源排查，
    整份覆盖报告就只剩那一个源——之后 `--report` 看到的"哪些源没抓到"是残缺的，
    会让人误以为其他源都正常。合并后，报告始终反映**每个源最近一次的真实状态**。
    """
    path = os.path.join(OUT, 'coverage.json')
    old = {}
    if os.path.exists(path):
        try:
            for e in json.load(open(path, encoding='utf-8')):
                old[e.get('id')] = e
        except Exception:
            pass
    for e in coverage:
        old[e['id']] = e
    # 未在本轮跑到的源也保留——只跑几个源排查时，不该把整份覆盖报告清空。
    # 每条的 checked_at 记录了它最近一次被真正检查的时间，用它可以判断
    # 「这个失败是刚测出来的，还是几周前的旧结论」。比一个二值 stale 标记有用得多：
    # 刚跑完单源排查时，其他 32 个源会被标成 stale，但其实都是几分钟前才验证过的。
    ordered = sorted(old.values(), key=lambda x: (x.get('level') != '国家', x.get('name', '')))
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(ordered, f, ensure_ascii=False, indent=1)


def report():
    db_path = os.path.join(OUT, 'policies.db')
    if not os.path.exists(db_path):
        log('还没有数据库，先跑一次采集。')
        return 1
    store = Store(db_path)
    st = store.stats()
    log('=== 库内构成 ===')
    log('  总数 %s 条（其中投资项目类 %s 条）' % (st['total'], st['investment']))
    log('  按分类: %s' % '  '.join('%s %d' % (k, v) for k, v in st['by_category'].items()))
    log('  按层级: %s' % '  '.join('%s %d' % (k, v) for k, v in st['by_level'].items()))
    log('  按体裁: %s' % '  '.join('%s %d' % (k, v) for k, v in st.get('by_doc_type', {}).items() if k))
    dr = st.get('date_range') or {}
    log('  发布日期区间: %s ~ %s' % (dr.get('from', ''), dr.get('to', '')))
    log('  去重命中: %s 条' % st.get('duplicates', 0))

    # 按省份/站点看覆盖，而不是按栏目名。
    # 栏目名（通知 / 政策解读）跨省重名，按它统计会把 30 个省混成一坨，
    # 看不出"哪个省没抓到"——而那恰恰是交付时要交代的事。
    region_rows = store.conn.execute(
        "SELECT COALESCE(NULLIF(region,''),'全国') AS r, level, COUNT(*) n, "
        "SUM(is_investment) inv FROM policies GROUP BY r, level ORDER BY n DESC").fetchall()
    log('\n=== 按来源地区 ===')
    for r in region_rows:
        log('  %-14s %-4s 共 %-4d 条，其中投资项目类 %d 条' % (r['r'], r['level'], r['n'], r['inv'] or 0))

    cov_path = os.path.join(OUT, 'coverage.json')
    if os.path.exists(cov_path):
        cov = json.load(open(cov_path, encoding='utf-8'))
        bad = [e for e in cov if e['status'] != 'ok']
        log('\n=== 未能正常采集的源（%d/%d）===' % (len(bad), len(cov)))
        if not bad:
            log('  无')
        for e in sorted(bad, key=lambda x: x['status']):
            log('  [%-11s] %-30s 检查于 %s' % (e['status'], e['name'][:30],
                                             e.get('checked_at', '—')))
            log('      %s' % (e.get('note') or '')[:88])
        log('\n=== 全部源 ===')
        for e in cov:
            log('  %-32s %-11s 列表%-5s 新增%-5s %s'
                % (e['name'][:32], e['status'], e.get('listed', 0), e.get('new', 0),
                   (e.get('note') or '')[:50]))
    return 0


def main():
    ap = argparse.ArgumentParser(description='投资项目政策文件采集器')
    ap.add_argument('--sources', nargs='*', help='只采集指定源 id')
    ap.add_argument('--max-pages', type=int, default=3, help='每个栏目最多翻页数')
    ap.add_argument('--detail-limit', type=int, default=60, help='本次最多抓多少篇正文（全局上限）')
    ap.add_argument('--detail-per-source', type=int, default=70,
                    help='每个源最多抓多少篇正文（防止大源吃光全局额度）')
    ap.add_argument('--since', help='只收该日期之后的文件（增量采集）')
    ap.add_argument('--sleep', type=float, default=1.2, help='同站点请求间隔秒数')
    ap.add_argument('--no-proxy', action='store_true',
                    help='绕过环境变量里的 HTTP(S)_PROXY 直连（排查"是代理还是站点"时用）')
    ap.add_argument('--list-only', action='store_true',
                    help='只抓列表、不抓正文（用于快速探路）。注意：记录会照常入库但'
                         '没有正文，下次全量运行会自动补全，无需手工处理')
    ap.add_argument('--no-export', dest='export', action='store_false', help='不导出 Excel')
    ap.add_argument('--all', dest='only_investment', action='store_false',
                    help='导出包含非投资项目类（默认只导投资项目类）')
    ap.add_argument('--report', action='store_true', help='只打印库内统计与覆盖情况')
    ap.add_argument('--profile', choices=['strict', 'broad'], default='strict',
                    help='研判档位：strict=高精度(默认)，broad=含发展规划类(高召回)')
    ap.add_argument('--reclassify', action='store_true',
                    help='按当前规则重判库内全部记录（不联网）')
    args = ap.parse_args()
    # 允许 `--sources a,b` 与 `--sources a b` 两种写法。
    # argparse 的 nargs='*' 只认空格分隔：写逗号时整串会被当成一个（不存在的）源名，
    # 于是「没有匹配的源」直接退出——看着像跑过了，实际一个源都没采，很容易误判。
    if args.sources:
        args.sources = [x for tok in args.sources
                        for x in re.split(r'[,，]', tok) if x.strip()]
    if args.report:
        return report()
    if args.reclassify:
        return reclassify(args.profile)
    try:
        return collect(args)
    except KeyboardInterrupt:
        log('\n已中断。已入库的数据保留在 out/policies.db')
        return 130
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
