"""抓取层：统一 UA、重试、限速、编码探测、gzip。

设计取舍
--------
- 只用标准库：政府网站多为静态 HTML/JSON，requests 不是必需品；少一层依赖，
  换台机器就能跑。
- **编码必须探测**：省级站点里 GBK/GB18030 仍很常见，直接按 UTF-8 解会整页乱码。
- **限速是硬要求**：这些站点不是给我们压测的。默认同一站点 1.2 秒/次，
  并在遇到 403/412/429 时退避重试，绝不并发轰炸。
"""
import gzip
import io
import json
import random
import ssl
import time
import urllib.error
import urllib.request
import zlib

UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

DEFAULT_HEADERS = {
    'User-Agent': UA,
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Accept-Encoding': 'gzip, deflate',
    'Connection': 'keep-alive',
}

# 部分省级站点的 TLS 配置较旧：握手时要求"旧式服务器连接"（不安全重协商）。
# OpenSSL 3.x 默认关闭该项，Python 于是抛
#   UNSAFE_LEGACY_RENEGOTIATION_DISABLED
# 而 curl 默认允许、能正常访问——所以这**不是站点不可用**，是本机 TLS 策略过严。
# 实测：开启 SSL_OP_LEGACY_SERVER_CONNECT 后，江西省发改委从"不可达"变为
# HTTP 200 / 235KB 正常返回。对现代站点无副作用（该选项只放宽这一类兼容）。
# 注意 verify_mode=CERT_NONE 只影响证书校验；这里抓的是公开政策文件，不涉及凭据传输。
SSL_OP_LEGACY_SERVER_CONNECT = 0x4
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE
_CTX.options |= SSL_OP_LEGACY_SERVER_CONNECT


class FetchError(Exception):
    pass


class Fetcher:
    """按站点限速抓取。每个站点一份节流状态，互不牵连。"""

    def __init__(self, delay=1.2, timeout=25, retries=3, jitter=0.4, use_proxy=True):
        self.delay = delay
        self.timeout = timeout
        self.retries = retries
        self.jitter = jitter
        self.use_proxy = use_proxy
        handlers = [urllib.request.HTTPSHandler(context=_CTX)]
        if not use_proxy:
            # 排查用：有些环境（CI、容器、公司代理）会给 HTTP_PROXY 注入一个中间代理，
            # 报出来的错误却是"站点不可达 / 502 Bad Gateway"，很容易把代理问题
            # 误判成站点问题而放弃一个本来能抓的源。带上 --no-proxy 可直连对照。
            handlers.append(urllib.request.ProxyHandler({}))
        self._opener = urllib.request.build_opener(*handlers)
        self._last = {}
        self.stats = {'ok': 0, 'fail': 0, 'retry': 0}

    def _throttle(self, host):
        gap = time.time() - self._last.get(host, 0.0)
        wait = self.delay + random.random() * self.jitter - gap
        if wait > 0:
            time.sleep(wait)
        self._last[host] = time.time()

    def raw(self, url, referer=None, timeout=None):
        """取字节流。返回 (bytes, http_status)。失败抛 FetchError。"""
        from urllib.parse import urlsplit
        host = urlsplit(url).netloc
        headers = dict(DEFAULT_HEADERS)
        if referer:
            headers['Referer'] = referer
        last = None
        for attempt in range(self.retries):
            self._throttle(host)
            req = urllib.request.Request(url, headers=headers)
            try:
                with self._opener.open(req, timeout=timeout or self.timeout) as resp:
                    body = resp.read()
                    enc = (resp.headers.get('Content-Encoding') or '').lower()
                    if enc == 'gzip':
                        body = gzip.GzipFile(fileobj=io.BytesIO(body)).read()
                    elif enc == 'deflate':
                        try:
                            body = zlib.decompress(body)
                        except zlib.error:
                            body = zlib.decompress(body, -zlib.MAX_WBITS)
                    self.stats['ok'] += 1
                    return body, resp.status
            except urllib.error.HTTPError as exc:
                last = 'HTTP %s' % exc.code
                # 反爬/限流类状态码退避后重试；404 等直接放弃。
                if exc.code in (403, 412, 429, 500, 502, 503, 504):
                    self.stats['retry'] += 1
                    time.sleep((attempt + 1) * 2.0)
                    continue
                break
            except Exception as exc:  # 超时、DNS、TLS
                last = '%s: %s' % (type(exc).__name__, exc)
                self.stats['retry'] += 1
                time.sleep((attempt + 1) * 1.5)
        self.stats['fail'] += 1
        raise FetchError('%s -> %s' % (url, last))

    def text(self, url, referer=None, timeout=None, encodings=('utf-8', 'gb18030', 'gbk')):
        """取文本。先按 HTTP 头/声明探测编码，再按候选表试解。"""
        body, status = self.raw(url, referer=referer, timeout=timeout)
        return decode(body, encodings), status

    def json(self, url, referer=None, timeout=None):
        text, status = self.text(url, referer=referer, timeout=timeout)
        try:
            return json.loads(text), status
        except ValueError as exc:
            raise FetchError('返回不是合法 JSON: %s' % exc)


def decode(body, encodings=('utf-8', 'gb18030', 'gbk')):
    """尽力把字节解成中文文本。先看 meta 声明，再依次试候选编码。"""
    head = body[:4096].decode('ascii', 'ignore').lower()
    declared = None
    for key in ('charset=',):
        idx = head.find(key)
        if idx >= 0:
            declared = head[idx + len(key):].split('"')[0].split("'")[0].split(';')[0].split('>')[0].strip()
            break
    order = []
    if declared:
        order.append(declared)
    order += [e for e in encodings if e not in order]
    for enc in order:
        try:
            return body.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return body.decode('utf-8', 'ignore')
