"""落地与去重：SQLite 存储 + 多键去重 + Excel/CSV 导出。

去重策略（逐级收紧，命中即判重，并记录与谁重复，便于审计）
------------------------------------------------------------------
1. url          —— 同一网址重复抓取（分页重叠、重跑）
2. doc_number   —— 同一份文件被多站转载（最常见：部委原文 + 省政府转发）
3. content_hash —— 正文与标题归一化后的指纹，抓「换了网址、改了栏目」的同一文件
4. title_key    —— 标题归一化后同名（补住正文抓取失败的漏网）

为什么用多个键而不是单一指纹：政府文件转载时 url、栏目、附件往往都变了，
但**文号是文件的身份证**，所以 doc_number 优先；没有文号的（规划、目录类）
再退到正文指纹。
"""
import hashlib
import json
import os
import re
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS policies (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id         TEXT NOT NULL,
    source_name       TEXT,
    level             TEXT,
    region            TEXT,
    title             TEXT NOT NULL,
    doc_number        TEXT DEFAULT '',
    pub_date          TEXT DEFAULT '',
    issuer            TEXT DEFAULT '',
    category          TEXT DEFAULT '待定',
    category_confidence REAL DEFAULT 0,
    category_reason   TEXT DEFAULT '',
    doc_type          TEXT DEFAULT '政策原文',
    is_investment     INTEGER DEFAULT 0,
    relevance         REAL DEFAULT 0,
    url               TEXT NOT NULL,
    content           TEXT DEFAULT '',
    attachments       TEXT DEFAULT '[]',
    content_hash      TEXT DEFAULT '',
    title_key         TEXT DEFAULT '',
    dup_of            INTEGER,
    collected_at      TEXT,
    first_seen_run    TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_url ON policies(url);
CREATE INDEX IF NOT EXISTS ix_docnum ON policies(doc_number);
CREATE INDEX IF NOT EXISTS ix_hash ON policies(content_hash);
CREATE INDEX IF NOT EXISTS ix_titlekey ON policies(title_key);
CREATE INDEX IF NOT EXISTS ix_date ON policies(pub_date);
CREATE INDEX IF NOT EXISTS ix_cat ON policies(category);

CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    started_at  TEXT, finished_at TEXT,
    fetched     INTEGER DEFAULT 0,
    new_records INTEGER DEFAULT 0,
    duplicates  INTEGER DEFAULT 0,
    filtered    INTEGER DEFAULT 0,
    errors      INTEGER DEFAULT 0,
    detail      TEXT DEFAULT '[]'
);
"""

_TITLE_NOISE = re.compile(
    r'[（(【\[]?\s*[\u4e00-\u9fa5]{1,12}[〔\[\(]\s*(?:19|20)\d{2}\s*[〕\]\)]\s*\d{1,5}\s*号\s*[)）】\]]?'
    r'|[（(【\[][^）)】\]]{0,40}[)）】\]]|[\s\u3000·、，,。：:；;—\-–_]+')


def norm_title(title):
    """标题归一化：剥掉文号括号、标点与空白，只留实义字。"""
    t = title or ''
    t = re.sub(r'\s+', '', t)
    t = _TITLE_NOISE.sub('', t)
    return t.lower()


def norm_docnum(doc_number):
    """文号归一化：统一括号与空格（〔〕[]（）混用极常见）。"""
    if not doc_number:
        return ''
    s = re.sub(r'\s+', '', doc_number)
    s = s.replace('[', '〔').replace(']', '〕')
    s = s.replace('(', '〔').replace(')', '〕')
    s = s.replace('（', '〔').replace('）', '〕').replace('【', '〔').replace('】', '〕')
    return s


def content_hash(title, content):
    """正文指纹：取正文首 3000 字 + 归一化标题。

    只取前 3000 字是刻意的：转载版本常在尾部附加「扫一扫」「相关链接」，
    全量哈希反而认不出同一份文件。
    """
    body = re.sub(r'\s+', '', (content or '')[:3000])
    return hashlib.sha1((norm_title(title) + '|' + body).encode('utf-8')).hexdigest()


class Store:
    def __init__(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self.path = path

    # ---------------- 去重 ----------------
    def find_duplicate(self, rec):
        """按优先级找已存在的同一条记录，返回 (row, reason) 或 (None, None)。"""
        c = self.conn
        row = c.execute('SELECT * FROM policies WHERE url=?', (rec['url'],)).fetchone()
        if row:
            return row, 'url'
        dn = norm_docnum(rec.get('doc_number'))
        if dn:
            row = c.execute('SELECT * FROM policies WHERE doc_number=?', (dn,)).fetchone()
            if row:
                return row, 'doc_number'
        ch = rec.get('content_hash')
        if ch:
            row = c.execute('SELECT * FROM policies WHERE content_hash=?', (ch,)).fetchone()
            if row:
                return row, 'content_hash'
        tk = rec.get('title_key')
        if tk and len(tk) >= 8:
            row = c.execute('SELECT * FROM policies WHERE title_key=?', (tk,)).fetchone()
            if row:
                return row, 'title_key'
        return None, None

    def insert(self, rec):
        cols = ['source_id', 'source_name', 'level', 'region', 'title', 'doc_number',
                'pub_date', 'issuer', 'category', 'doc_type', 'category_confidence', 'category_reason',
                'is_investment', 'relevance', 'url', 'content', 'attachments',
                'content_hash', 'title_key', 'dup_of', 'collected_at', 'first_seen_run']
        vals = [rec.get(k) for k in cols]
        q = 'INSERT OR IGNORE INTO policies (%s) VALUES (%s)' % (
            ','.join(cols), ','.join('?' * len(cols)))
        cur = self.conn.execute(q, vals)
        self.conn.commit()
        return cur.lastrowid

    # ---------------- 运行记录（支撑「定期采集」的增量） ----------------
    def enrich(self, row, rec):
        """用新抓到的详情补齐一条已有记录（主要是补正文与由此衍生的字段）。

        场景：`--list-only` 先入库了只有标题的记录，后续全量运行时补详情。
        只填"原来为空"的字段，不覆盖已有值——尤其不能覆盖人工修订痕迹。
        返回 True 表示确实补上了内容。
        """
        if (row['content'] or '') or not (rec.get('content') or ''):
            return False
        sets, args = [], []
        for col in ('content', 'attachments', 'content_hash'):
            if rec.get(col) is not None:
                sets.append('%s=?' % col)
                args.append(rec[col])
        for col in ('doc_number', 'pub_date', 'issuer'):
            if rec.get(col) and not (row[col] or ''):
                sets.append('%s=?' % col)
                args.append(rec[col])
        if not sets:
            return False
        args.append(row['id'])
        self.conn.execute('UPDATE policies SET %s WHERE id=?' % ', '.join(sets), args)
        self.conn.commit()
        return True

    def start_run(self, run_id):
        self.conn.execute('INSERT OR REPLACE INTO runs (run_id, started_at) VALUES (?,?)',
                          (run_id, time.strftime('%Y-%m-%d %H:%M:%S')))
        self.conn.commit()

    def finish_run(self, run_id, **kw):
        self.conn.execute(
            'UPDATE runs SET finished_at=?, fetched=?, new_records=?, duplicates=?, '
            'filtered=?, errors=?, detail=? WHERE run_id=?',
            (time.strftime('%Y-%m-%d %H:%M:%S'), kw.get('fetched', 0), kw.get('new_records', 0),
             kw.get('duplicates', 0), kw.get('filtered', 0), kw.get('errors', 0),
             json.dumps(kw.get('detail', []), ensure_ascii=False), run_id))
        self.conn.commit()

    def known_urls(self, source_id=None, complete_only=False):
        """已入库的 URL 集合，供抓取前跳过（省带宽、也是礼貌）。

        complete_only=True 时**只返回已有正文的记录**。原因是 `--list-only`
        只抓列表、不抓正文，会先入库一批"没有正文"的记录；若这些 URL 也进跳过集合，
        之后跑全量时会因为"URL 已存在"而跳过，正文将永远补不上——
        库看着有几千条，实际没有内容可读。把它们排除在跳过集合外，
        全量运行时会重新抓详情并用 enrich() 补齐。
        """
        q = 'SELECT url FROM policies'
        args = ()
        if complete_only:
            q += ' WHERE length(COALESCE(content, "")) > 200'
        elif source_id:
            q += ' WHERE source_id=?'
            args = (source_id,)
        return {r['url'] for r in self.conn.execute(q, args)}

    def latest_pub_date(self, source_id=None):
        q = 'SELECT MAX(pub_date) d FROM policies'
        args = ()
        if source_id:
            q += ' WHERE source_id=?'
            args = (source_id,)
        return (self.conn.execute(q, args).fetchone()['d'] or '')

    # ---------------- 导出 ----------------
    def stats(self):
        c = self.conn
        out = {'total': c.execute('SELECT COUNT(*) n FROM policies').fetchone()['n']}
        out['by_category'] = {r['category']: r['n'] for r in c.execute(
            'SELECT category, COUNT(*) n FROM policies GROUP BY category ORDER BY n DESC')}
        out['by_source'] = {r['source_name']: r['n'] for r in c.execute(
            'SELECT source_name, COUNT(*) n FROM policies GROUP BY source_name ORDER BY n DESC')}
        out['by_level'] = {r['level']: r['n'] for r in c.execute(
            'SELECT level, COUNT(*) n FROM policies GROUP BY level ORDER BY n DESC')}
        out['investment'] = c.execute(
            'SELECT COUNT(*) n FROM policies WHERE is_investment=1').fetchone()['n']
        out['duplicates'] = c.execute(
            'SELECT COUNT(*) n FROM policies WHERE dup_of IS NOT NULL').fetchone()['n']
        dr = c.execute(
            'SELECT MIN(pub_date) a, MAX(pub_date) b FROM policies WHERE pub_date<>""').fetchone()
        # 转成普通 dict：sqlite3.Row 不能直接 json 序列化
        out['date_range'] = {'from': dr['a'] or '', 'to': dr['b'] or ''}
        out['by_doc_type'] = {r['doc_type']: r['n'] for r in c.execute(
            'SELECT doc_type, COUNT(*) n FROM policies GROUP BY doc_type ORDER BY n DESC')}
        return out

    def all_rows(self, only_investment=True):
        q = 'SELECT * FROM policies'
        if only_investment:
            q += ' WHERE is_investment=1'
        q += ' ORDER BY pub_date DESC, id DESC'
        return [dict(r) for r in self.conn.execute(q)]

    def export_xlsx(self, path, only_investment=True):
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from .sources import SOURCES
        # source_name 存的是「栏目名」（通知 / 规范性文件 / 新闻发布…），
        # 光看它分不清来自哪个站点。导出时用 source_id 映射回站点全名。
        site_of = {s['id']: s['name'] for s in SOURCES}
        rows = self.all_rows(only_investment)
        wb = Workbook()
        ws = wb.active
        ws.title = '政策文件'
        head = ['序号', '政策分类', '体裁', '层级', '地区', '标题', '文号', '发布日期',
                '发文机关', '是否投资项目类', '相关度', '分类依据', '来源站点', '来源栏目',
                '附件数', '附件', '正文', '原文链接', '采集时间']
        ws.append(head)
        for i, r in enumerate(rows, 1):
            atts = json.loads(r['attachments'] or '[]')
            ws.append([i, r['category'], r.get('doc_type', ''), r['level'], r['region'], r['title'], r['doc_number'],
                       r['pub_date'], r['issuer'], '是' if r['is_investment'] else '否',
                       r['relevance'], r['category_reason'],
                       site_of.get(r['source_id'], r['source_id']), r['source_name'],
                       len(atts), '；'.join(a['name'] for a in atts),
                       (r['content'] or '')[:30000], r['url'], r['collected_at']])
        fills = {'引导类': 'DDEBF7', '准入类': 'FFF2CC', '保障类': 'E2EFDA',
                 '激励约束类': 'FCE4D6', '待定': 'EDEDED'}
        for c in ws[1]:
            c.fill = PatternFill('solid', fgColor='173A60')
            c.font = Font(color='FFFFFF', bold=True)
        for row in ws.iter_rows(min_row=2):
            cat = row[1].value
            if cat in fills:
                row[1].fill = PatternFill('solid', fgColor=fills[cat])
        widths = [6, 12, 12, 8, 10, 52, 20, 12, 20, 14, 8, 40, 26, 18, 8, 26, 60, 42, 18]
        for idx, w in enumerate(widths, 1):
            ws.column_dimensions[ws.cell(1, idx).column_letter].width = w
        ws.freeze_panes = 'A2'
        ws.auto_filter.ref = 'A1:%s%d' % (ws.cell(1, len(head)).column_letter, ws.max_row)
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(vertical='top', wrap_text=True)

        # 第二张表：分类统计
        st = self.stats()
        ws2 = wb.create_sheet('统计')
        ws2.append(['指标', '值'])
        ws2.append(['入库总数', st['total']])
        ws2.append(['其中投资项目类', st['investment']])
        ws2.append(['标记为重复', st['duplicates']])
        ws2.append(['发布日期区间', '%s ~ %s' % (st['date_range']['from'], st['date_range']['to'])])
        ws2.append([])
        ws2.append(['政策分类', '数量'])
        for k, v in st['by_category'].items():
            ws2.append([k, v])
        ws2.append([])
        ws2.append(['来源', '数量'])
        for k, v in st['by_source'].items():
            ws2.append([k, v])
        for c in ws2[1]:
            c.font = Font(bold=True)
        ws2.column_dimensions['A'].width = 24
        ws2.column_dimensions['B'].width = 30
        wb.save(path)
        return len(rows)
