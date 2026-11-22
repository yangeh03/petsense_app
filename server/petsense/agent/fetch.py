"""read_web_page 抓取层（W06，agent-w06-spec.md §4.4）。

只读公网网页 + trafilatura 正文提取。安全边界全部为确定性代码：

- 仅 http/https 绝对地址；禁止 userinfo；端口仅 80/443；
- **pin-IP**：DNS 解析后逐 IP 用 ipaddress 校验（非公网地址一律拒绝，覆盖环回/私网/
  保留/链路本地/组播/169.254.169.254 元数据地址），连接固定到校验过的公网地址；
  TLS 仍按原域名做证书与主机名校验（server_hostname=域名），不降级校验；
- 直连目标地址，不走代理（不读代理环境变量，显式拒绝隧道）；
- 重定向 ≤3 跳，每跳对目标 URL 重新执行全部校验（scheme/端口/userinfo/DNS/IP）；
- 下载上限 2MB（Content-Length 超限直接拒绝；流式读取超出即截断）；
  连接 5s / 读 15s；Content-Type 须为 text/html、text/plain、application/xhtml+xml。

失败一律返回结构化 error（明确原因），不抛裸异常给模型；模型据此换来源或如实说明，
不得当作「来源不存在」。已知边界：校验与连接共用同一次 getaddrinfo 结果，
DNS rebinding 窗口收窄到单次解析调用内部。
"""
import http.client
import ipaddress
import socket
import ssl
from urllib.parse import urljoin, urlsplit

import trafilatura

MAX_BYTES = 2 * 1024 * 1024
BODY_CHAR_LIMIT = 4000
CONNECT_TIMEOUT_S = 5.0
READ_TIMEOUT_S = 15.0
MAX_REDIRECTS = 3
CHUNK = 64 * 1024
ALLOWED_CONTENT_TYPES = ('text/html', 'text/plain', 'application/xhtml+xml')
ALLOWED_SCHEMES = ('http', 'https')
ALLOWED_PORTS = {80, 443}
USER_AGENT = 'PetSense-Agent/1.0 (pet companion read_web_page)'


class FetchError(Exception):
    """带明确原因的抓取失败；str(exc) 即模型可读的错误信息。"""


def _reject(message):
    raise FetchError(message)


def validate_url(url, hop):
    """scheme/userinfo/端口白名单校验，返回 (scheme, hostname, port, path_with_query)。"""
    if not isinstance(url, str) or not url.strip():
        _reject('URL 为空或不是字符串')
    try:
        parts = urlsplit(url.strip())
        port = parts.port  # 非法端口字符串在这里抛 ValueError
    except ValueError:
        _reject(f'URL 无法解析（第 {hop} 跳）')
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        _reject(f'仅支持 http/https 地址（第 {hop} 跳）')
    if parts.username or parts.password or '@' in (parts.netloc or ''):
        _reject(f'URL 不允许携带用户名密码（第 {hop} 跳）')
    hostname = (parts.hostname or '').strip().rstrip('.')
    if not hostname:
        _reject(f'URL 缺少主机名（第 {hop} 跳）')
    if port is None:
        port = 443 if parts.scheme.lower() == 'https' else 80
    if port not in ALLOWED_PORTS:
        _reject(f'仅允许 80/443 端口，收到 {port}（第 {hop} 跳）')
    path = (parts.path or '/') + (('?' + parts.query) if parts.query else '')
    return parts.scheme.lower(), hostname, port, path


def validate_addresses(hostname, port):
    """DNS 解析并逐 IP 校验：只放行公网地址（ipaddress.is_global）。

    返回校验通过的地址列表（保持系统解析顺序）；任一解析结果落在环回/私网/保留/
    链路本地/组播等非公网范围即整体拒绝（fail-closed，防混合记录绕过）。
    """
    try:
        infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, OSError, UnicodeError) as exc:
        _reject(f'域名解析失败：{hostname}（{exc.__class__.__name__}）')
    addresses, seen = [], set()
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped  # IPv4 映射地址按其 IPv4 真值判断
        if not ip.is_global:
            _reject(f'{hostname} 解析到不允许访问的地址（{ip}），内网/保留/环回地址一律拒绝')
        if ip not in seen:
            seen.add(ip)
            addresses.append(str(ip))
    if not addresses:
        _reject(f'域名解析无可用地址：{hostname}')
    return addresses


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """连接固定到已校验的 IP；TLS 仍按原域名做证书与主机名校验。"""

    def __init__(self, host, ip_address, port, timeout, context):
        super().__init__(host, port=port, timeout=timeout, context=context)
        self._pinned_ip = ip_address

    def connect(self):
        if self._tunnel_host:
            _reject('不允许通过代理隧道访问')  # 显式拒绝代理，杜绝环境注入的隧道配置
        self.sock = self._context.wrap_socket(
            socket.create_connection((self._pinned_ip, self.port), self.timeout),
            server_hostname=self.host)


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, ip_address, port, timeout):
        super().__init__(host, port=port, timeout=timeout)
        self._pinned_ip = ip_address

    def connect(self):
        if self._tunnel_host:
            _reject('不允许通过代理隧道访问')
        self.sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)


def _send(url, hop):
    """校验 URL → 解析并校验 IP → pin-IP 建连 → 发送 GET。返回已发请求的连接。"""
    scheme, host, port, path = validate_url(url, hop)
    addresses = validate_addresses(host, port)
    last_error = None
    for ip_address in addresses:  # 全部已过白名单，按系统解析顺序尝试连接
        if scheme == 'https':
            conn = _PinnedHTTPSConnection(host, ip_address, port, CONNECT_TIMEOUT_S,
                                          ssl.create_default_context())
        else:
            conn = _PinnedHTTPConnection(host, ip_address, port, CONNECT_TIMEOUT_S)
        try:
            conn.connect()
            conn.timeout = READ_TIMEOUT_S  # 建连后切到读超时（http.client 每次 send 会 settimeout）
            conn.request('GET', path, headers={
                'Host': host, 'User-Agent': USER_AGENT,
                'Accept': 'text/html, text/plain; q=0.9, */*; q=0.1',
                'Accept-Encoding': 'identity', 'Connection': 'close'})
            return conn
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            last_error = exc
            try:
                conn.close()
            except Exception:
                pass
    _reject(f'无法连接到 {host}（{type(last_error).__name__ if last_error else "未知错误"}）')


def _download(url, hop):
    """发送并读取：重定向逐跳重新校验；限制大小与内容类型。
    返回 (payload, truncated, final_url, content_type_header)。"""
    conn = _send(url, hop)
    try:
        try:
            response = conn.getresponse()
        except (OSError, http.client.HTTPException) as exc:
            _reject(f'读取响应失败（{type(exc).__name__}）')
        location = response.headers.get('Location') if response.status in (301, 302, 303, 307, 308) else None
        if location:
            response.close()
            if hop >= MAX_REDIRECTS:
                _reject(f'重定向超过 {MAX_REDIRECTS} 跳，已停止')
            target = urljoin(url, location)
            return _download(target, hop + 1)  # 每跳重新做全部校验（含 DNS/IP）
        status = response.status
        if status == 404:
            _reject('页面不存在（HTTP 404），该地址没有可用内容')
        if 400 <= status < 500:
            _reject(f'页面请求被拒绝（HTTP {status}），请换一个来源')
        if not 200 <= status < 300:
            _reject(f'页面服务端错误（HTTP {status}），稍后重试或换来源')
        content_type_header = response.headers.get('Content-Type') or ''
        content_type = content_type_header.split(';')[0].strip().lower()
        if response.length is not None and response.length > MAX_BYTES:
            _reject(f'页面声明大小 {response.length} 字节，超过 {MAX_BYTES} 字节上限，已拒绝下载')
        if content_type not in ALLOWED_CONTENT_TYPES:
            _reject(f'内容类型 {content_type or "未知"} 不是网页'
                    '（仅支持 text/html、text/plain、application/xhtml+xml）')
        charset_header = content_type_header
        chunks, total, truncated = [], 0, False
        try:
            while True:
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total >= MAX_BYTES:  # 流式截断：超出上限即停，不继续下载
                    truncated = True
                    break
        except (OSError, http.client.HTTPException) as exc:
            _reject(f'下载中断（{type(exc).__name__}）')
        return b''.join(chunks), truncated, url, charset_header
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _decode(payload, content_type_header):
    """按 Content-Type charset 解码，缺省 utf-8（非法字节替换，不中断）。"""
    charset = 'utf-8'
    if content_type_header and 'charset=' in content_type_header:
        candidate = content_type_header.split('charset=', 1)[1].split(';')[0].strip().strip('"\'')
        if candidate:
            charset = candidate
    try:
        return payload.decode(charset, errors='replace')
    except (LookupError, ValueError):
        return payload.decode('utf-8', errors='replace')


def fetch_page(url):
    """抓取并提取正文。成功返回 {'url','title','text','truncated','bytes'}；
    失败抛 FetchError（str 为明确原因），由 registry 层转结构化 error。"""
    payload, truncated, final_url, charset_header = _download(url, 1)
    document = trafilatura.bare_extraction(_decode(payload, charset_header),
                                           with_metadata=True, include_comments=False)
    text = (getattr(document, 'text', '') or '').strip() if document is not None else ''
    title = (getattr(document, 'title', '') or '').strip() if document is not None else ''
    if not text:
        _reject('未能提取到正文（页面可能是脚本渲染或非文章内容），不得当作来源不存在')
    return {'url': final_url, 'title': title, 'text': text[:BODY_CHAR_LIMIT],
            'truncated': truncated, 'bytes': len(payload)}
