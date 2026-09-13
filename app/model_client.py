"""Bounded OpenAI-compatible transport; no document or credential logging."""
import json
import os
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


class ModelError(ValueError):
    """A user-safe error; never include provider response bodies or URLs."""


def settings():
    base = os.getenv('LLM_BASE_URL', '').strip().rstrip('/')
    parsed = urlsplit(base)
    allowed_http = {x.strip() for x in os.getenv('LLM_HTTP_HOSTS', '').split(',') if x.strip()}
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ModelError('模型地址无效，请填写 API 基础地址')
    if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in {'localhost', '127.0.0.1', '::1'} | allowed_http):
        raise ModelError('模型地址需 HTTPS；内网 HTTP 主机需明确配置 LLM_HTTP_HOSTS')
    key = os.getenv('LLM_API_KEY', '').strip()
    key_file = os.getenv('LLM_API_KEY_FILE', '').strip()
    if key_file:
        try:
            key = Path(key_file).read_text(encoding='utf-8').strip()
        except OSError:
            raise ModelError('无法读取模型密钥文件') from None
    if not key and os.getenv('LLM_ALLOW_NO_KEY', 'false').lower() != 'true':
        raise ModelError('请配置 LLM_API_KEY 或 LLM_API_KEY_FILE')
    try:
        timeout = float(os.getenv('LLM_TIMEOUT_SECONDS', '90'))
        retries = int(os.getenv('LLM_MAX_RETRIES', '2'))
        tokens = int(os.getenv('LLM_MAX_TOKENS', '8192'))
        if not 1 <= timeout <= 300 or not 0 <= retries <= 3 or not 128 <= tokens <= 32768:
            raise ValueError()
        extra = json.loads((os.getenv('LLM_EXTRA_BODY') or '{}'))
        if not isinstance(extra, dict) or set(extra) - {'enable_thinking', 'reasoning_effort'}:
            raise ValueError()
    except (ValueError, TypeError):
        raise ModelError('模型参数无效，请检查超时、重试、输出长度或附加参数') from None
    mode = os.getenv('LLM_JSON_MODE', 'true').lower()
    if mode not in {'true', 'false'}:
        raise ModelError('LLM_JSON_MODE 只能为 true 或 false')
    endpoint = base if parsed.path.endswith('/chat/completions') else base + '/chat/completions'
    return endpoint, key, timeout, retries, tokens, extra, mode == 'true'


def public_config():
    """回传就绪状态与模型名。缺哪一项就说哪一项，但不回显密钥与地址。"""
    try:
        settings()
        if not os.getenv('LLM_MODEL', '').strip():
            raise ModelError('请配置 LLM_MODEL')
        return {'llm_ready': True, 'model': os.getenv('LLM_MODEL'), 'vision_model': os.getenv('VISION_MODEL', ''), 'model_error': ''}
    except (ModelError, ValueError) as exc:
        reason = str(exc).strip() or '请检查服务地址、密钥和模型名'
        return {'llm_ready': False, 'model': os.getenv('LLM_MODEL', ''), 'vision_model': os.getenv('VISION_MODEL', ''),
                'model_error': '模型配置未完成：' + reason + '（在仓库根目录 .env 中修改后重启服务）'}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def chat(messages, model):
    endpoint, key, timeout, retries, tokens, extra, json_mode = settings()
    if not model or not model.strip():
        raise ModelError('未配置模型名称')
    payload = {'model': model, 'temperature': 0, 'messages': messages, 'max_tokens': tokens, **extra}
    if json_mode:
        payload['response_format'] = {'type': 'json_object'}
    headers = {'Content-Type': 'application/json'}
    if key:
        headers['Authorization'] = 'Bearer ' + key
    request = urllib.request.Request(endpoint, data=json.dumps(payload).encode(), headers=headers)
    opener = urllib.request.build_opener(NoRedirect)
    for attempt in range(retries + 1):
        try:
            with opener.open(request, timeout=timeout) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise ModelError('模型响应超过大小限制')
            body = json.loads(raw)
            choice = body['choices'][0]
            if choice.get('finish_reason') == 'length':
                raise ModelError('模型输出被截断，请增加输出长度或拆分文档')
            content = choice['message']['content']
            if not isinstance(content, str):
                raise ModelError('模型未返回文本结果')
            content = content.strip()
            if content.startswith('```') and content.endswith('```'):
                content = content.split('\n', 1)[1].rsplit('```', 1)[0].strip()
            result = json.loads(content)
            if not isinstance(result, dict):
                raise ModelError('模型结果必须为 JSON 对象')
            return result
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.close()
            transient = status in {429, 500, 502, 503, 504}
            message = {401: '模型鉴权失败，请核对密钥', 403: '模型访问被拒绝，请检查权限', 404: '模型或接口不存在，请核对地址和模型名', 400: '模型请求参数不兼容，请检查 JSON 模式和附加参数', 429: '模型限流或额度不足'}.get(status, '模型服务返回 HTTP ' + str(status))
            if not transient or attempt == retries:
                raise ModelError(message) from None
        except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError):
            if attempt == retries:
                raise ModelError('模型连接失败或超时，请检查网络与服务地址') from None
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            if isinstance(exc, ModelError):
                raise
            raise ModelError('模型响应结构或 JSON 无效') from None
        time.sleep(min(2 ** attempt, 4))


def probe(vision=False):
    """Use only synthetic content; never upload existing documents in diagnostics."""
    started = time.monotonic()
    model = os.getenv('VISION_MODEL' if vision else 'LLM_MODEL', '')
    prompt = 'Return a JSON object with exactly this key and value: {"ok":true}.'
    content = prompt
    if vision:
        import base64
        import io
        from PIL import Image
        buffer = io.BytesIO()
        Image.new('RGB', (64, 64), 'white').save(buffer, format='PNG')
        content = [{'type': 'text', 'text': prompt}, {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()}}]
    out = chat([{'role': 'user', 'content': content}], model)
    if out.get('ok') is not True:
        raise ModelError('服务已响应，但未通过 JSON 指令验证')
    return {'ok': True, 'model': model, 'vision': vision, 'latency_ms': round((time.monotonic() - started) * 1000)}
