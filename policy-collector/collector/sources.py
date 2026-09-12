"""站点清单与列表解析。

两类源：
  · gov_cn_api —— 中央人民政府政策文件库，官方 JSON 接口，最稳最全（6000+ 条）
  · html_list  —— 部委/省级发改委的政策专栏，静态 HTML

省级站点的坑：不少站点列表页是 JS 渲染（如浙江省发改委用聚宝 CMS，
`/module/jpage/dataproxy.jsp` 已下线，列表由 jubascript 注入），
直接抓首页只拿到空壳。这类站点在 STATUS 里会被显式标成 js_required，
不会被静默跳过——**沉默的漏采比报错更危险**。
"""
import re
from urllib.parse import urljoin, urlsplit, urlunsplit

from .extract import strip_tags

# 征集目标：国家层面 + 省级发改委（按任务书口径）
PROVINCES = [
    ('北京市', 'beijing', 'https://fgw.beijing.gov.cn/'),
    ('天津市', 'tianjin', 'https://fzgg.tj.gov.cn/'),
    ('河北省', 'hebei', 'https://hbdrc.hebei.gov.cn/'),
    ('山西省', 'shanxi', 'https://fgw.shanxi.gov.cn/'),
    ('内蒙古自治区', 'neimenggu', 'https://fgw.nmg.gov.cn/'),
    ('辽宁省', 'liaoning', 'https://fgw.ln.gov.cn/'),
    ('吉林省', 'jilin', 'https://jldrc.jl.gov.cn/'),
    ('黑龙江省', 'heilongjiang', 'https://hljfgw.hlj.gov.cn/'),
    ('上海市', 'shanghai', 'https://fgw.sh.gov.cn/'),
    ('江苏省', 'jiangsu', 'https://fzggw.jiangsu.gov.cn/'),
    ('浙江省', 'zhejiang', 'https://fzggw.zj.gov.cn/'),
    ('安徽省', 'anhui', 'https://fzggw.ah.gov.cn/'),
    ('福建省', 'fujian', 'https://fgw.fujian.gov.cn/'),
    ('江西省', 'jiangxi', 'https://drc.jiangxi.gov.cn/'),
    ('山东省', 'shandong', 'http://fgw.shandong.gov.cn/'),
    ('河南省', 'henan', 'https://fgw.henan.gov.cn/'),
    ('湖北省', 'hubei', 'https://fgw.hubei.gov.cn/'),
    ('湖南省', 'hunan', 'https://fgw.hunan.gov.cn/'),
    ('广东省', 'guangdong', 'https://drc.gd.gov.cn/'),
    ('广西壮族自治区', 'guangxi', 'http://fgw.gxzf.gov.cn/'),
    ('海南省', 'hainan', 'https://plan.hainan.gov.cn/'),
    ('重庆市', 'chongqing', 'https://fzggw.cq.gov.cn/'),
    ('四川省', 'sichuan', 'https://fgw.sc.gov.cn/'),
    ('贵州省', 'guizhou', 'https://fgw.guizhou.gov.cn/'),
    ('云南省', 'yunnan', 'https://ynfgw.yn.gov.cn/'),
    ('陕西省', 'shaanxi', 'https://sndrc.shaanxi.gov.cn/'),
    ('甘肃省', 'gansu', 'https://fzgg.gansu.gov.cn/'),
    ('青海省', 'qinghai', 'https://fgw.qinghai.gov.cn/'),
    ('宁夏回族自治区', 'ningxia', 'https://fzggw.nx.gov.cn/'),
    ('新疆维吾尔自治区', 'xinjiang', 'https://xjdrc.xinjiang.gov.cn/'),
]

# 政策专栏的识别词（用于从首页自动发现栏目）
POLICY_WORDS = ['政策文件', '政策法规', '规范性文件', '政策发布', '政策解读',
                '规划文件', '政策专栏', '法规公文', '行政规范性文件', '政策库']
# 栏目链接必须排除的词
COLUMN_DENY = ['通知公告', '人事', '招聘', '党建', '互动', '留言', '信访', '下载中心',
               '办事指南', '政务服务', '数据', '专题专栏', '网站地图', '联系我们']

SOURCES = [
    {
        'id': 'gov_cn',
        'name': '中央人民政府·政策文件库',
        'level': '国家',
        'region': '全国',
        'type': 'gov_cn_api',
        'api': 'https://sousuo.www.gov.cn/search-gov/data',
        'referer': 'https://www.gov.cn/zhengce/zhengceku/',
        'page_size': 20,
        'max_pages': 40,
    },
    {
        'id': 'ndrc',
        'name': '国家发展改革委·政策发布',
        'level': '国家',
        'region': '全国',
        'type': 'html_list',
        'columns': [
            ('通知', 'https://www.ndrc.gov.cn/xxgk/zcfb/tz/'),
            ('规范性文件', 'https://www.ndrc.gov.cn/xxgk/zcfb/ghxwj/'),
            ('规划文本', 'https://www.ndrc.gov.cn/xxgk/zcfb/ghwb/'),
            ('公告', 'https://www.ndrc.gov.cn/xxgk/zcfb/gg/'),
            ('发展改革委令', 'https://www.ndrc.gov.cn/xxgk/zcfb/fzggwl/'),
        ],
        'max_pages': 6,
    },
    {
        'id': 'ndrc_gzdt',
        'name': '国家发展改革委·委内动态',
        'level': '国家',
        'region': '全国',
        'type': 'html_list',
        'columns': [('新闻发布', 'https://www.ndrc.gov.cn/xwdt/xwfb/')],
        'max_pages': 3,
    },
    {
        'id': 'zj_fgw',
        'name': '浙江省发展和改革委员会·政策文件',
        'level': '省',
        'region': '浙江省',
        'type': 'html_discover',
        'home': 'https://fzggw.zj.gov.cn/',
        'max_pages': 5,
        'hint': '聚宝 CMS，列表由 JS 注入，需浏览器渲染层',
    },
]

# 省份码来自 PROVINCES 表，不再从域名首段推断。
# 原写法把 fgw.beijing.gov.cn / fgw.shanxi.gov.cn / fgw.ln.gov.cn… 全算成 'fgw'，
# 于是十几个省共用同一个 id 'prov_fgw'——后果是 --sources 选不中单个省、
# 覆盖报告按 id 归并后互相覆盖、库内也无法按省统计。实测踩过。
# 按 region 去重：浙江已单独配置（zj_fgw），不再重复登记一份 prov_zhejiang。
_seen_regions = {s['region'] for s in SOURCES if s['level'] == '省'}
for region, code, home in PROVINCES:
    if region in _seen_regions:
        continue
    SOURCES.append({
        'id': 'prov_' + code,
        'name': '%s发展和改革委员会' % region,
        'level': '省',
        'region': region,
        'type': 'html_discover',
        'home': home,
        'max_pages': 3,
    })

# 栏目翻页模板候选：(URL 模板, 第 2 页在该模板里的页码)
#
# ⚠️ 为什么需要一张候选表而不是"读页面上的下一页链接"：
# 政务 CMS 的翻页链接大量是 `document.write(...)` 在**浏览器运行时**拼出来的，
# 静态 HTML 里没有 `<a href>`，读链接这一招用不上。
# 实测北京市发改委某栏目 JS 里写着 `countPage = 134`（共 134 页），
# 规则是 index.htm → index_1.htm → index_2.htm（**`.htm` 而非 `.html`**），
# 按文件名猜的 index_1.html 一律 404 —— 结果是每个栏目只抓到第 1 页的 15 条，
# 数据"看着有条目"，实际少了两个数量级。
#
# 所以改成**探测**：逐个试候选模板，取第一个「能抓到 + 解析出条目 + 与第 1 页不同」的。
PAGE_TEMPLATES = [
    ('index_%d.htm', 1),        # index.htm → index_1.htm（北京等）
    ('index_%d.html', 1),
    ('index_%d.shtml', 1),
    ('index%d.htm', 2),         # 少数站：index.htm → index2.htm
    ('index%d.html', 2),
    ('index%d.shtml', 2),
    ('?page=%d', 2),
    ('index.jsp?pageNo=%d', 2),
]


def apply_page_template(base, tmpl, pageno):
    """按模板拼出某页 URL。pageno 是**模板里的页码**（不是"第几页"）。

    base 可能是目录（.../zcwj/）也可能是带文件名的入口（.../zcwj/index.htm），
    两种都先归一成目录再拼，避免出现 .../zcwj/index.htm/index_1.htm。

    ⚠️ query 型模板（`?page=%d`）**必须保留目录末尾的斜杠**：
    实测陕西省政府网栏目 base 是 `https://www.shaanxi.gov.cn/zfxxgk/`，
    若 rstrip('/') 后拼成 `.../zfxxgk?page=2`，`urljoin` 会把 `zfxxgk` 当成文件名、
    父目录当成 `/` —— 该页所有相对链接被解析成 `https://www.shaanxi.gov.cn/fdzdgknr/...`
    （丢了 `/zfxxgk`），于是整页详情全部 404、25 条抓取全失败。
    """
    if tmpl.startswith('?'):
        directory = base if base.endswith('/') else base + '/'
        return directory + tmpl % pageno
    directory = base if base.endswith('/') else base.rsplit('/', 1)[0] + '/'
    return directory + tmpl % pageno


def parse_list_ndrc(html, base_url):
    """国家发改委专用列表解析。

    通用解析器在这里会把「相关解读」的嵌套条目也当成政策原文——列表里
    每条通知的 li 内嵌了一个 div，div 里又有一组解读 li，而 HTML 解析在
    遇到第一个 </li> 就截断，于是嵌套条目被误当成独立政策。
    实测后果：一批「答记者问」混进政策库。
    这里只认「政策发布」正文路径（./YYYYMM/tYYYYMMDD_id.html），
    解读路径（../../jd/...）自然被排除。
    """
    pat = re.compile(
        r'<li[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*title="([^"]*)"[^>]*>.*?</a>'
        r'.*?<span>\s*((?:19|20)\d{2})/(\d{1,2})/(\d{1,2})\s*</span>', re.S)
    items, seen = [], set()
    for m in pat.finditer(html):
        href, title, y, mo, d = m.groups()
        href = href.strip()
        if not re.match(r'^\.{0,2}/?(?:[\w\-]+/)?\d{6}/t\d{8}_\d+\.html$', href.replace('../', '').lstrip('/')):
            continue
        title = re.sub(r'\s+', ' ', strip_tags(title)).strip()
        if len(title) < 6:
            continue
        url = urljoin(base_url, href)
        if url in seen:
            continue
        seen.add(url)
        items.append({'title': title, 'url': url,
                      'pub_date': '%04d-%02d-%02d' % (int(y), int(mo), int(d))})
    return items


ARTICLE_HINTS = ('.html', '.htm', '.shtml', '/art/', 'content_', 't20', '/info/')
LIST_DENY = ('javascript', 'mailto:', '#', '.css', '.js', '.jpg', '.png', '.gif',
             '.pdf', '.doc', '.xls', '.zip', '.rar', '.mp4')
DATE_RE = re.compile(r'((?:19|20)\d{2})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})')

# 路径里出现这些片段的绝不是政策文件：机构概况 / 领导分工 / 领导成员 / 搜索 / 打印
# 实测吉林省发改委的「领导分工」页（/jggk/ldfg/）被当成政策收了 9 条人名职务。
URL_SEGMENT_DENY = ('/ldfg/', '/ldcy/', '/lingdao/', '/leader/', '/rss/', '/search/',
                    '/login/', '/print/', '/jggk/')
# 以 index / indexN 结尾的地址，**不能一律**当栏目落地页——判据要看父目录。
# 辽宁等站的文章地址形如 /fgw/zc/zcjd/2026082815040164859/index.shtml：
# 文章 ID 是目录名，文件名恰好叫 index.shtml。若只匹配 "以 index 结尾"，
# 这批真政策会被整批误删（实测踩过：清噪声脚本一次误删 59 条，其中约 20 条是真文章）。
# 正确判据：父目录里没有长数字串的，才是栏目/目录落地页。
_INDEX_TAIL_RE = re.compile(r'/([^/]+)/index\d*\.(?:html?|shtml|jsp)$', re.I)


def _is_section_landing(url):
    """判断是否为「栏目落地页」而非文章页。"""
    m = _INDEX_TAIL_RE.search(url or '')
    if not m:
        return False
    parent = m.group(1)
    return not re.search(r'\d{6,}', parent)
# 「姓名 + 职务」型标题：人事页面的典型形态，不是文件名
PERSON_TITLE_RE = re.compile(
    r'^[\u4e00-\u9fa5\s·]{1,20}'
    r'(党组书记|党组成员|副主任|主任|局长|副局长|巡视员|组长|处长|副处长|部长|副部长|纪检)'
    r'([,，、].{0,40})?$')
# 导航/工具条标签：整页扫描时必然出现的噪声
NAV_TITLE = {
    '首页', '政务公开', '政务服务', '政民互动', '专题专栏', '网站地图', '联系我们', '联系方式',
    '无障碍', '无障碍浏览', '长者模式', '繁體', '简体', 'English', 'EN', '机构职能', '内设机构',
    '直属单位', '领导班子', '领导介绍', '领导信息', '处室', '部门职责', '网站声明', '版权声明',
    '隐私声明', '使用帮助', '关闭', '打印', '分享', '返回顶部', '上一页', '下一页', '更多', '详情',
}


def _registrable(netloc):
    """取「可注册域名」用于判断是否属同一站点体系。

    为什么不用精确 netloc 相等：同省的发改委站与省政府站常互为子域
    （sndrc.shaanxi.gov.cn 与 www.shaanxi.gov.cn），政策栏目往往直接放在省政府站上。
    为什么也不能只看末两段：`www.gov.cn` 的末两段是 `gov.cn`，
    会让**所有** gov.cn 站点被当成同一个站——所以 gov.cn 这类二级后缀要取三段。
    """
    host = (netloc or '').split(':')[0].lower()
    parts = [x for x in host.split('.') if x]
    if len(parts) < 2:
        return host
    if '.'.join(parts[-2:]) in ('gov.cn', 'com.cn', 'net.cn', 'org.cn', 'edu.cn', 'ac.cn'):
        return '.'.join(parts[-3:]) if len(parts) >= 3 else host
    return '.'.join(parts[-2:])


def _same_site(a, b):
    return _registrable(a) == _registrable(b)


def _looks_article(href):
    h = href.lower()
    if any(d in h for d in LIST_DENY):
        return False
    if any(d in h for d in URL_SEGMENT_DENY):
        return False
    if _is_section_landing(h):
        return False
    return any(k in h for k in ARTICLE_HINTS) or re.search(r'/\d{6}/', h) is not None


def _noise_title(title):
    """导航标签、人事页标题——整页扫 <a> 时必然混进来的东西。"""
    t = re.sub(r'\s+', '', title)
    return t in NAV_TITLE or bool(PERSON_TITLE_RE.match(title.strip()))


def _depth_events(html, tag):
    """返回 (位置列表, 该位置的嵌套深度列表)，用于判断锚点是否落在 <li>/<tr> 里。"""
    ev = [(m.start(), 1) for m in re.finditer(r'<%s\b' % tag, html, re.I)]
    ev += [(m.start(), -1) for m in re.finditer(r'</%s>' % tag, html, re.I)]
    ev.sort()
    pos, dep, d = [], [], 0
    for p, delta in ev:
        pos.append(p)
        d += delta
        dep.append(d)
    return pos, dep


def _depth_at(index, p):
    import bisect
    pos, dep = index
    i = bisect.bisect_right(pos, p) - 1
    return dep[i] if i >= 0 else 0


def parse_list_generic(html, base_url, same_host=True):
    """通用列表解析：以「链接」为中心抓条目，日期在就近窗口里取。

    不依赖站点 class 名——政府栏目改版频繁，绑死选择器的话换一个省就得重写。

    ⚠️ 这里刻意不用「先切块、再在块里找链接」的写法。原先用的是
        re.findall(r'<(li|tr|dd|div)\\b[^>]*>(.*?)</\\1>', html, re.S)
    但 HTML 的 <div> 是**嵌套**的，非贪婪正则在**第一个** </div> 就截断：外层包装
    div 被当成一个"条目"，里面几十个真实条目全被丢掉。实测后果——
      河北某政策栏目页有 16 个详情链接 → 解析出 0 条
      河南某政策发布页有 20 个详情链接 → 解析出 1 条
    这两个省因此在上一轮被记为"低产"，看着像站点反爬，其实是解析规则把数据切碎了。
    （同一类坑在正文抽取上踩过一次：<div> 嵌套不能交给非贪婪正则。）

    改成以 <a> 为中心，三步：
      1. 扫出所有「像正文」的链接（_looks_article）；
      2. 日期只在「本条链接结束 → 下一条链接开始」的窗口里找，天然不会误取邻条日期；
      3. 窗口内没有日期时，退回「链接前 120 字符」的小窗口——少数站点把日期放在
         标题之前（如 [2026-08-31] 标题）。

    另外两个实测出来的写法差异：
    - **地址可能不在 href 里**。天津某个 CMS 把地址写在
      `onclick="isDownLoad(this,'https://…/t20260910_7371699.html')"`，页面上根本没有
      href 属性——只认 href 的匹配方式会整页解析出 0 条（该源曾被记为"解析规则缺口"）。
      所以取地址时先找 href，找不到再看标签属性里有没有 http(s) 的文章地址。
    - **标题可能不仅写在 <a> 文本里**：有的站把 `<span>标题</span><span>日期</span>` 一起
      放进 <a>，直接取文本会把日期拼进标题。故优先取 `title` 属性，并对文本做日期剥离。
    - **整页扫 <a> 会把侧栏和导航也收进来**：改成锚点中心后范围变宽，吉林省发改委的
      「领导分工」侧栏（9 条"李 平 党组书记、主任"）一度被当成政策文件入库。
      所以**优先只收落在 `<li>` / `<tr>` 里的锚点**（列表项的典型容器），
      只有这种锚点少于 3 个时才放弃容器约束——否则整个源会被判成 0 条。
      再加两道保险：路径黑名单（/ldfg/、/lingdao/…）与标题黑名单（导航标签、人事页）。
    """
    # base 归一化：剥掉 query / fragment 再当 urljoin 的基准。
    # ⚠️ 翻页地址常带 `?page=2`，直接拿去 urljoin 会让相对链接解析错位——
    # 实测 base = .../zfxxgk/?page=2 时，`zfxxgk` 会被当成文件名、父目录当成 `/`，
    # 整页链接变成 https://host/fdzdgknr/...（丢了 /zfxxgk）→ 全部 404。
    # 这里做第二层防御（第一层在 apply_page_template 保留目录斜杠）。
    _p = urlsplit(base_url)
    if _p.query or _p.fragment:
        base_url = urlunsplit((_p.scheme, _p.netloc, _p.path, '', ''))
    host = urlsplit(base_url).netloc
    anchors = list(re.finditer(r'<a\b([^>]*)>(.*?)</a>', html, re.S | re.I))
    li_idx = _depth_events(html, 'li')
    tr_idx = _depth_events(html, 'tr')

    def collect(only_list_items):
        out, seen = [], set()
        for i, m in enumerate(anchors):
            if only_list_items and not (_depth_at(li_idx, m.start()) > 0
                                        or _depth_at(tr_idx, m.start()) > 0):
                continue
            attrs, body = m.group(1), m.group(2)
            href = _href_from(attrs)
            if not href or not _looks_article(href):
                continue
            title = ''
            attr = re.search(r'\btitle\s*=\s*["\']([^"\']{8,200})["\']', attrs, re.I)
            if attr:
                title = attr.group(1)
            if len(title) < 8:
                title = strip_tags(body)
            title = re.sub(r'\s+', ' ', DATE_RE.sub(' ', title)).strip()
            if len(title) < 8 or len(title) > 200 or _noise_title(title):
                continue
            url = urljoin(base_url, href)
            netloc = urlsplit(url).netloc
            if same_host and netloc and netloc != host:
                # 跨站链接不收：省级栏目的「国家政策法规」栏会转载中央文件，
                # 收进来归属会错记成该省，而中央文件已由国家级源覆盖。
                continue
            if url in seen:
                continue
            # 日期三处找，按"离本条最近"排序：
            #   1) <a> 自己的文本里——有的站把 `<span>标题</span><span>日期</span>` 都放进 <a>，
            #      此时日期在链接**内部**，只看链接之后会取空（天津实测）；
            #   2) 本条结束 → 下一条开始之间——最外层的常见写法；
            #   3) 本条之前 120 字符——少数站日期在标题前。
            nxt = anchors[i + 1].start() if i + 1 < len(anchors) else m.end() + 1500
            date = (_date_in(body)
                    or _date_in(html[m.end():min(nxt, m.end() + 1500)])
                    or _date_in(html[max(0, m.start() - 120):m.start()]))
            seen.add(url)
            out.append({'title': title, 'url': url, 'pub_date': date})
        return out

    items = collect(only_list_items=True)
    if len(items) < 3:
        # 有的站列表项不在 <li>/<tr> 里（裸 <div> 或 <p>），收紧会全丢，故放宽重试
        items = collect(only_list_items=False)
    return items


def _href_from(attrs):
    """从 <a> 的属性串里取目标地址。

    优先 href；没有 href 时，回退到属性里的 http(s) 文章地址——
    部分 CMS 用 `onclick="isDownLoad(this,'https://…/t20260820_7355799.html')"` 承载跳转，
    页面上没有任何 href。只认 href 会把这整页判成"0 条"，进而误判为 JS 渲染。
    """
    m = re.search(r'\bhref\s*=\s*["\']([^"\']+)["\']', attrs, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r'https?://[^\s\'"<>)]+\.(?:html?|shtml)', attrs, re.I)
    return m.group(0).strip() if m else ''


def _date_in(text):
    """在给定片段里取第一个合法日期，返回 YYYY-MM-DD；取不到返回空串。"""
    if not text:
        return ''
    for dm in DATE_RE.finditer(text):
        y, mo, d = int(dm.group(1)), int(dm.group(2)), int(dm.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return '%04d-%02d-%02d' % (y, mo, d)
    return ''


def discover_policy_columns(html, home_url):
    """从首页自动发现「政策专栏」入口。"""
    out, seen = [], set()
    for m in re.finditer(r'<a\b[^>]*href\s*=\s*["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.S | re.I):
        href, label = m.group(1).strip(), re.sub(r'\s+', ' ', strip_tags(m.group(2))).strip()
        if not label or len(label) > 20:
            continue
        if href.lower().startswith(('javascript:', '#', 'mailto:')):
            continue
        if any(d in label for d in COLUMN_DENY):
            continue
        if not any(w in label for w in POLICY_WORDS):
            continue
        url = urljoin(home_url, href)
        if url in seen or not url.lower().startswith('http'):
            continue
        # ⚠️ 栏目链接必须落在**本站域名体系内**，否则会沿着它跑到别的站去。
        # 实测：陕西省发改委首页的「国务院政策文件」栏目直接指向 www.gov.cn、
        # 「省政府政策文件」指向 www.shaanxi.gov.cn——收了就会把中央文件和省政府的
        # 文件记在"陕西省发改委"名下（region 与来源都失真），还会顺手把
        # gov.cn 页脚的"国务院客户端小程序"当政策抓进来。
        # 同省不同子域（政府站 ↔ 发改委站）放行，因为它们本属同一省级体系。
        if not _same_site(urlsplit(url).netloc, urlsplit(home_url).netloc):
            continue
        seen.add(url)
        out.append({'name': label, 'url': url})
    return out


def page_url(base, page):
    """栏目翻页（**兜底方案**）：兼容 index_1.html / index_1.shtml 等常见写法。

    ⚠️ 不要只靠这个函数翻页。各站翻页写法差异极大：
        辽宁    → /fgw/zc/zcjd/8375b354-2.shtml（UUID + 序号，该栏目共 7 页）
        TRS CMS → index_2.shtml
        部分站  → ?page=2 / index.jsp?pageNo=2
    按文件名猜模式必然覆盖不全——实测辽宁因此每个栏目只拿到第 1 页的 15 条，
    而该栏目其实有 7 页、约 100 条。正确做法是**从页面自身读「下一页」链接**
    （见 next_page_url），本函数只作为读不到链接时的兜底。
    """
    if page <= 1:
        return base
    if base.endswith('index.html'):
        return base[:-len('index.html')] + 'index_%d.html' % page
    if base.endswith('index.shtml'):
        return base[:-len('index.shtml')] + 'index_%d.shtml' % page
    if base.endswith('/'):
        return base + 'index_%d.html' % page
    return base


NEXT_PAGE_HINTS = ('下一页', '下页', 'nextpage', 'next')


def next_page_url(html, current):
    """从列表页自身找「下一页」地址；找不到返回空串。

    比按文件名猜模式可靠得多，且与 CMS 无关。取地址的顺序：
      1. tagname 属性——部分 CMS（辽宁等）把目标地址放这里，href 为空、靠 onclick 调 JS；
      2. href；
      3. 整段属性里的相对路径（形如 /xxx/yyy-2.shtml）。
    命中条件：锚点文本或属性里出现「下一页 / 下页 / next」。
    """
    for m in re.finditer(r'<a\b([^>]*)>(.*?)</a>', html, re.S | re.I):
        attrs, body = m.group(1), m.group(2)
        label = re.sub(r'\s+', '', strip_tags(body)).lower()
        if not any(h in label for h in NEXT_PAGE_HINTS) and 'nextpage' not in attrs.lower():
            continue
        mm = re.search(r'\btagname\s*=\s*["\']([^"\']+)["\']', attrs, re.I)
        cand = mm.group(1).strip() if mm else _href_from(attrs)
        if not cand or cand.lower().startswith(('javascript', '#')):
            mm = re.search(r'["\'](/[^"\']*?\.(?:s?html?|jsp))["\']', attrs, re.I)
            cand = mm.group(1) if mm else ''
        if not cand:
            continue
        url = urljoin(current, cand)
        if url != current:
            return url
    return ''
