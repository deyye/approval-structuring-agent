"""详情页抽取：标题 / 正文 / 发布日期 / 文号 / 附件。

政府网站的正文容器命名高度不统一（TRS_Editor、UCAP-CONTENT、zoom、article…），
因此不写死单个选择器，而是**按候选容器打分**：命中越多、文本越长，越可能是正文。
这样一套规则能同时吃下多数省部级站点，新增站点往往不用改代码。
"""
import html as html_mod
import re
from urllib.parse import urljoin

# 正文容器的候选特征（id / class 名）。顺序即优先级。
CONTENT_HINTS = [
    ('TRS_Editor', 100), ('UCAP-CONTENT', 100), ('zoom', 90), ('article', 85),
    ('content', 70), ('conTxt', 80), ('con_txt', 80), ('detail', 70),
    ('bt_content', 80), ('xl_con', 80), ('zwnr', 90), ('mainText', 85),
    ('view', 55), ('news_content', 80), ('text', 40),
]
# 绝不当作正文的容器
CONTENT_DENY = ['nav', 'menu', 'footer', 'header', 'crumb', 'share', 'search',
                'sidebar', 'related', 'banner', 'comment', 'qrcode', 'links']

NOISE_TAGS = ['script', 'style', 'noscript', 'iframe', 'form', 'button', 'svg']
ATTACH_EXT = ('.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
              '.zip', '.rar', '.wps', '.et', '.ofd', '.txt')

DOC_NUMBER_RE = re.compile(
    r'([\u4e00-\u9fa5]{1,12}[〔\[\(]\s*(?:19|20)\d{2}\s*[〕\]\)]\s*\d{1,5}\s*号)')
# 带标签的文号（"发文字号：国办发〔2026〕24号"）——最可靠，优先取
DOC_NUMBER_LABELED = re.compile(
    r'(?:发文字号|文号|公文文号)\s*[：:\s]\s*'
    r'([\u4e00-\u9fa5]{1,12}[〔\[\(]\s*(?:19|20)\d{2}\s*[〕\]\)]\s*\d{1,5}\s*号)')
# 第二种文号形态：**令号 / 公告号**。
# 发改委令、部门公告不用机关代字，编号写成「2026年第44号令」「2025年第3号公告」，
# 而且通常出现在**标题末尾**而不是正文里。只认「XX〔2026〕NN号」会漏掉整类文件——
# 实测 19 条发改委令因此没有文号，而文号是去重的第二键，漏抽会削弱去重能力。
DOC_NUMBER_DECREE = re.compile(
    r'((?:19|20)\d{2}\s*年\s*第\s*\d{1,4}\s*号\s*(?:令|公告))')
DATE_RES = [
    re.compile(r'((?:19|20)\d{2})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})'),
]
# 虚词：出现在文号机关代字里必属误粘
_FUNC_WORDS = '的了和为及等并请现将关于与或'


def strip_tags(fragment):
    """去标签留文本，保留段落边界。"""
    if not fragment:
        return ''
    s = fragment
    for tag in NOISE_TAGS:
        s = re.sub(r'<%s\b.*?</%s>' % (tag, tag), ' ', s, flags=re.S | re.I)
    s = re.sub(r'<br\s*/?>', '\n', s, flags=re.I)
    s = re.sub(r'</(p|div|li|tr|h[1-6])>', '\n', s, flags=re.I)
    s = re.sub(r'<[^>]+>', '', s)
    s = html_mod.unescape(s)
    s = s.replace('\u3000', ' ').replace('\xa0', ' ')
    s = re.sub(r'[ \t\f\v]+', ' ', s)
    s = re.sub(r'\n\s*\n+', '\n', s)
    return s.strip()


def _blocks(html):
    """候选容器打分：(score, text_len, text, ident)，按「命中特征优先级 + 文本长度」排序。

    ⚠️ 不能用正则匹配 `<div>...</div>`：HTML 的 div 是嵌套的，非贪婪正则会
    在**第一个** `</div>` 处截断，于是外层包装层被当成正文，抽出来的正文以
    站点头部导航开头（实测踩过）。这里用标准库 HTMLParser 维护标签栈。
    """
    from html.parser import HTMLParser

    class Collector(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.stack = []   # 未闭合的候选容器
            self.done = []    # 已闭合的候选容器

        def handle_starttag(self, tag, attrs):
            ident = ' '.join(v or '' for k, v in attrs if k in ('id', 'class')).lower()
            score = 0
            if ident and not any(b in ident for b in CONTENT_DENY):
                for hint, w in CONTENT_HINTS:
                    if hint.lower() in ident:
                        score = max(score, w)
            if score:
                self.stack.append({'tag': tag, 'ident': ident, 'score': score, 'text': []})

        def handle_startendtag(self, tag, attrs):
            pass

        def handle_endtag(self, tag):
            for i in range(len(self.stack) - 1, -1, -1):
                if self.stack[i]['tag'] == tag:
                    node = self.stack.pop(i)
                    self.done.append(node)
                    break

        def handle_data(self, data):
            for node in self.stack:
                node['text'].append(data)

    c = Collector()
    try:
        c.feed(html)
    except Exception:
        pass
    out = []
    for node in c.done:
        text = re.sub(r'[ \t]+', ' ', ''.join(node['text']))
        text = re.sub(r'\n\s*\n+', '\n', text).strip()
        if len(text) < 120:
            continue
        out.append((node['score'], len(text), text, node['ident']))
    out.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return out


def find_content(html):
    """定位正文文本（直接返回文本，不再返回 HTML 片段）。

    优先「命中正文特征名 + 文本最长」的容器；都命中不到时退回 body。
    正文下游只用于阅读与判类，保留 HTML 标签反而会把噪声带进去。
    """
    blocks = _blocks(html)
    if blocks:
        return blocks[0][2]
    m = re.search(r'<body\b[^>]*>(.*?)</body>', html, re.S | re.I)
    return strip_tags(m.group(1) if m else html)


# 站点头部导航行：出现在正文开头时一律剥掉
NAV_LINE = re.compile(
    r'^(首页|简|繁|EN|登录|退出|个人中心|邮箱|无障碍|网站无障碍开关|分享|打印|'
    r'字号|大|中|小|扫一扫|返回顶部|当前位置|您的位置|导航|菜单|搜索|高级搜索|'
    r'政务微信|政务微博|移动版|繁體|English)[\s|｜·、>]*$', re.I)


def strip_nav_head(text):
    """剥掉正文开头的导航/工具条行与面包屑。"""
    if not text:
        return text
    lines = [l for l in text.split('\n')]
    i = 0
    while i < len(lines) and i < 30:
        line = lines[i].strip()
        if (not line) or NAV_LINE.match(line) or line.startswith(('首页 >', '首页>', '您的位置',
                                                                  '当前位置', '网站首页 >')):
            i += 1
            continue
        break
    return '\n'.join(lines[i:]).strip()


# 元数据标签词：这些绝不能被当成标题或正文
META_LABELS = ['索 引 号', '索引号', '发文机关', '发文字号', '成文日期', '发布日期',
               '主题分类', '公文种类', '公文类型', '有效性', '来源', '打印', '字号',
               '分享到', '扫一扫', '上一篇', '下一篇', '相关链接', '政策解读']


def _is_meta_label(text):
    """判断一段文本是不是版式标签而不是内容。"""
    t = re.sub(r'[\s：:、，,。]+', '', text or '')
    if not t:
        return True
    if re.sub(r'[：:]', '', text or '').strip() in META_LABELS:
        return True
    return any(t == re.sub(r'[\s：:]+', '', m) for m in META_LABELS)


def find_title(html, fallback=''):
    """标题提取。

    ⚠️ 血泪教训：中国政府网详情页用 `<h2>` 放「索 引 号：」这类**版式标签**，
    而真正的标题只在 `<title>` 里。若把 h2 排在 title 前面，全库标题会变成
    「索 引 号：」——实测踩过。所以顺序是 og:title → h1 → title，h2 最后且必须过校验。
    """
    for pat in [r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
                r'<meta[^>]+name=["\']ArticleTitle["\'][^>]+content=["\']([^"\']+)',
                r'<h1[^>]*>(.*?)</h1>']:
        m = re.search(pat, html, re.S | re.I)
        if m:
            t = re.sub(r'\s+', ' ', strip_tags(m.group(1))).strip()
            if 6 <= len(t) <= 200 and not _is_meta_label(t):
                return t
    m = re.search(r'<title[^>]*>(.*?)</title>', html, re.S | re.I)
    if m:
        t = strip_tags(m.group(1)).strip()
        # 站点后缀用 _ - | — 连接，取第一段
        t = re.split(r'[_\-|—]{1,2}', t)[0].strip()
        if 6 <= len(t) <= 200 and not _is_meta_label(t):
            return t
    m = re.search(r'<h2[^>]*>(.*?)</h2>', html, re.S | re.I)
    if m:
        t = re.sub(r'\s+', ' ', strip_tags(m.group(1))).strip()
        if 6 <= len(t) <= 200 and not t.endswith(('：', ':')) and not _is_meta_label(t):
            return t
    return fallback


def strip_meta_head(text):
    """剥掉正文开头的元数据块（索引号/发文机关/成文日期…）。

    部委详情页常把元数据表排在正文容器内部，不剥掉会让每篇正文都以
    「索 引 号：…发文机关：…」开头，既污染正文也让关键词判类跑偏。
    """
    if not text:
        return text
    lines = text.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        compact = re.sub(r'[\s：:]+', '', line)
        # 空行、纯标签行、行数很少的标签+值行，都算元数据区
        is_meta = (not line) or any(compact == re.sub(r'[\s：:]+', '', m) for m in META_LABELS)
        if not is_meta and i < 12:
            # 「发文机关：xxx」这类标签带值的行也算元数据
            is_meta = bool(re.match(r'^(索\s*引\s*号|发文机关|发文字号|成文日期|发布日期|'
                                    r'主题分类|公文种类|公文类型|有效性)\s*[：:]', line))
        if is_meta:
            i += 1
            continue
        break
    return '\n'.join(lines[i:]).strip()


def find_doc_number(*texts):
    """文号提取，三级降级。

    ⚠️ 踩过的坑：正文是连续文本，贪婪的 `[\\u4e00-\\u9fa5]{1,12}` 会把前一句的
    尾巴一起吞进来，抽出「治理有关工作的通知国办发〔2026〕24号」这种脏值。
    所以顺序是：带标签的 → 严格机关代字（≤8 字且不含虚词）→ 宽松兜底。
    """
    for t in texts:
        if not t:
            continue
        m = DOC_NUMBER_LABELED.search(t)
        if m:
            return re.sub(r'\s+', '', m.group(1))
    for t in texts:
        if not t:
            continue
        for m in DOC_NUMBER_RE.finditer(t):
            whole = m.group(1)
            braces = [i for i in (whole.find(c) for c in '〔[(') if i > 0]
            prefix = whole[:min(braces)] if braces else whole
            if 2 <= len(prefix) <= 8 and not any(w in prefix for w in _FUNC_WORDS):
                return re.sub(r'\s+', '', whole)
    for t in texts:
        if not t:
            continue
        m = DOC_NUMBER_RE.search(t)
        if m:
            return re.sub(r'\s+', '', m.group(1))
    # 令号/公告号放最后兜底：它是**弱形态**——「2026年第3号公告」这种字符串
    # 在正文里也可能是某个具体事项的编号，不如机关代字可靠，故只在其他形态都取不到时用。
    # 但标题（第一个入参）里出现时几乎必然是文号，所以标题优先。
    for t in texts:
        if not t:
            continue
        m = DOC_NUMBER_DECREE.search(t)
        if m:
            return re.sub(r'\s+', '', m.group(1))
    return ''


def find_pub_date(*texts):
    """发布日期。先找带标签的（成文日期/发布日期/印发日期），再退到首个合法日期。

    多篇实测：正文里散落着大量业务日期，直接取「全文首个日期」经常取错，
    带标签的元数据才是可靠来源；所以第一个入参理应传**整页文本**。
    """
    for t in texts:
        if not t:
            continue
        m = re.search(r'(?:成文日期|发布日期|印发日期|发布时间|发文日期)[：:\s]*'
                      r'((?:19|20)\d{2})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})', t)
        if m:
            return _fmt(m.group(1), m.group(2), m.group(3))
    for t in texts:
        if not t:
            continue
        for rx in DATE_RES:
            m = rx.search(t)
            if m:
                return _fmt(m.group(1), m.group(2), m.group(3))
    return ''


def _fmt(y, m, d):
    y, m, d = int(y), int(m), int(d)
    if not (1 <= m <= 12 and 1 <= d <= 31):
        return ''
    return '%04d-%02d-%02d' % (y, m, d)


def find_issuer(html, text=''):
    """发文机关：优先 meta，其次正文尾部/开头出现的机关署名。"""
    m = re.search(r'<meta[^>]+name=["\'](?:author|Author|PubOrg|source)["\'][^>]+content=["\']([^"\']+)', html, re.I)
    if m and m.group(1).strip():
        return m.group(1).strip()[:80]
    if text:
        m = re.search(r'([\u4e00-\u9fa5]{2,20}(?:发展和改革委员会|发展改革委|人民政府|办公厅|办公室|财政厅|工业和信息化厅))', text)
        if m:
            return m.group(1)
    return ''


def find_attachments(html, base_url):
    """附件：正文区内指向文档/压缩包的链接，去重保序。"""
    out, seen = [], set()
    for m in re.finditer(r'<a\b[^>]*href\s*=\s*["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.S | re.I):
        href, label = m.group(1).strip(), strip_tags(m.group(2)).strip()
        if not href or href.lower().startswith(('javascript:', 'mailto:', '#')):
            continue
        path = href.split('?')[0].lower()
        if not path.endswith(ATTACH_EXT):
            continue
        url = urljoin(base_url, href)
        if url in seen:
            continue
        seen.add(url)
        out.append({'name': label[:120] or url.rsplit('/', 1)[-1], 'url': url})
    return out


def strip_title_head(body, title):
    """剥掉正文开头重复的标题。

    部委详情页的正文容器里往往先重复一遍标题（有时还带文号），若不剥掉：
    ① 正文首句变成标题重复；
    ② 文号提取会把标题尾巴当成机关代字——实测抽出过
       「治理有关工作的通知国办发〔2026〕24号」这种脏值。
    """
    if not body or not title:
        return body
    def norm(x):
        return re.sub(r'[\s\u3000：:·、，,。；;（）()《》〈〉\-—_]+', '', x or '')
    b, t = body, title.strip()
    if norm(b).startswith(norm(t)):
        # 按规范化比例估算标题在原文里的实际长度
        ratio = len(norm(t))
        acc, cut = 0, 0
        for i, ch in enumerate(b):
            if not re.match(r'[\s\u3000：:·、，,。；;（）()《》〈〉\-—_]', ch):
                acc += 1
            if acc >= ratio:
                cut = i + 1
                break
        if cut:
            rest = body[cut:].lstrip(' \u3000：:·、，,。；;')
            return rest or body
    return body


def extract_detail(html, url):
    """详情页 → 结构化记录（不含分类与去重，那是下游的事）。"""
    body = strip_meta_head(strip_nav_head(find_content(html)))
    title = find_title(html)
    body = strip_title_head(body, title)
    head = strip_tags(html[:6000])
    return {
        'title': title,
        'content': body,
        'pub_date': find_pub_date(head, body),
        'doc_number': find_doc_number(title, head, body[:1200]),
        'issuer': find_issuer(html, body),
        'attachments': find_attachments(html, url),
        'content_chars': len(body),
    }
