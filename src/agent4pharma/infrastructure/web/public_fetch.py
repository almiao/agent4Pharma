"""公开网页读取：直连固定公网 IP，可信代理路径兼容 Fake-IP，逐跳校验访问边界。"""

import http.client
import ipaddress
import socket
import ssl
import time
from threading import Lock
from urllib.parse import urljoin, urlsplit
from urllib.request import getproxies, proxy_bypass

import httpx

from agent4pharma.modules.maintenance.repository import FetchedDocument


def public_address(url: str, *, proxy_dns: bool = False) -> tuple[str, int, str]:
    """验证目标地址；仅可信代理路径允许域名解析到 198.18.0.0/15 虚拟地址。

    直连返回可固定连接的公网 IP。代理路径返回的地址仅用于预检，代理负责最终解析、
    连接及出口隔离，不能声称该路径仍具有本地 IP 固定保证。字面 IP 不享受虚拟地址例外。
    """
    parts = urlsplit(url)
    if parts.scheme not in {"https", "http"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("只支持无内嵌凭证的公开 HTTP/HTTPS URL")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if port not in {80, 443}:
        raise ValueError("只允许公开 Web 的 80/443 端口")
    host = parts.hostname
    normalized = host.rstrip(".").lower()
    if normalized == "localhost" or normalized.endswith((".localhost", ".local", ".internal")):
        raise ValueError("不允许访问本机或私网域名")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and not literal.is_global:
        raise ValueError("不允许访问本机、私网或保留 IP 地址")
    addresses = {record[4][0] for record in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
    fake_network = ipaddress.ip_network("198.18.0.0/15")
    if not addresses or any(
        not ipaddress.ip_address(value).is_global
        and not (proxy_dns and literal is None and ipaddress.ip_address(value) in fake_network)
        for value in addresses
    ):
        raise ValueError("不允许访问本机、私网、保留地址或混合公网/私网 DNS")
    return host, port, sorted(addresses)[0]


class PublicPageFetcher:
    """读取公开网页，使用环境/系统 HTTP(S) 代理，不继承浏览器登录态。

    构造时快照代理配置（Windows 含系统代理），NO_PROXY/系统绕过规则逐请求生效。
    代理是本机受信任配置，不能由页面或模型指定；连接失败不降级直连。
    每次请求自行关闭连接，调用方无需额外 close。
    """

    def __init__(self, max_bytes: int = 1_500_000, timeout: float = 15):
        self.max_bytes, self.timeout = max_bytes, timeout
        self._last_request = 0.0
        self._lock = Lock()
        self._proxies = getproxies()

    def fetch(self, url: str) -> FetchedDocument:
        """每次跳转重新选择代理并校验目标，限制响应与跳数；网络错误不泄露代理凭证。"""
        for _ in range(5):
            parts = urlsplit(url)
            proxy = self._proxies.get(parts.scheme) or self._proxies.get("all")
            if parts.hostname and proxy_bypass(parts.hostname):
                proxy = None
            host, port, address = public_address(url, proxy_dns=bool(proxy))
            with self._lock:
                time.sleep(max(0, 0.5 - (time.monotonic() - self._last_request)))
                self._last_request = time.monotonic()
            if proxy:
                document, location = self._fetch_proxy(url, proxy)
            else:
                document, location = self._fetch_direct(url, host, port, address)
            if document.http_status in {301, 302, 303, 307, 308}:
                if not location:
                    raise ValueError("重定向缺少地址")
                url = urljoin(url, location)
                continue
            return document
        raise ValueError("页面重定向次数超过上限")

    def _fetch_proxy(self, url: str, proxy: str) -> tuple[FetchedDocument, str | None]:
        # 保留原始域名交给代理解析，不能将 Fake-IP 当作真实目标连接。
        if urlsplit(proxy).scheme not in {"http", "https"}:
            raise ValueError("公开网页读取仅支持 HTTP/HTTPS 代理，请配置本机 HTTP 代理端口")
        try:
            with httpx.Client(
                proxy=proxy, trust_env=False, timeout=self.timeout, follow_redirects=False
            ) as client:
                with client.stream("GET", url, headers=self._headers()) as response:
                    body = bytearray()
                    for chunk in response.iter_bytes(chunk_size=65536):
                        body.extend(chunk)
                        if len(body) > self.max_bytes:
                            raise ValueError("页面超过读取大小限制")
                    return (
                        FetchedDocument(
                            url, bytes(body), response.headers.get("Content-Type", ""), response.status_code
                        ),
                        response.headers.get("Location"),
                    )
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            raise OSError("代理读取失败：" + type(exc).__name__) from None

    @staticmethod
    def _headers() -> dict[str, str]:
        return {
            "User-Agent": "agent4Pharma/0.1 source-review",
            "Accept-Encoding": "identity",
            "Accept": (
                "text/html,application/rss+xml,application/atom+xml,"
                "application/json,application/xml,text/plain;q=0.8"
            ),
        }

    def _fetch_direct(
        self, url: str, host: str, port: int, address: str
    ) -> tuple[FetchedDocument, str | None]:
        parts = urlsplit(url)
        connection = http.client.HTTPConnection(host, port, timeout=self.timeout)
        try:
            sock = socket.create_connection((address, port), self.timeout)
            if parts.scheme == "https":
                try:
                    sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
                except BaseException:
                    sock.close()
                    raise
            connection.sock = sock
            path = parts.path or "/"
            if parts.query:
                path += "?" + parts.query
            connection.request(
                "GET",
                path,
                headers=self._headers(),
            )
            response = connection.getresponse()
            body = response.read(self.max_bytes + 1)
            if len(body) > self.max_bytes:
                raise ValueError("页面超过读取大小限制")
            return (
                FetchedDocument(url, body, response.getheader("Content-Type", ""), response.status),
                response.getheader("Location"),
            )
        finally:
            connection.close()
