import socket

import httpx
import pytest

from agent4pharma.infrastructure.web import public_fetch


@pytest.fixture
def proxy_network(monkeypatch):
    monkeypatch.setattr(public_fetch, "getproxies", lambda: {"https": "http://127.0.0.1:7897"})
    monkeypatch.setattr(public_fetch, "proxy_bypass", lambda host: False)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("198.18.0.96", 443))])


def mock_client(monkeypatch, handler):
    original = httpx.Client
    options = []

    def client(**kwargs):
        options.append(kwargs)
        return original(transport=httpx.MockTransport(handler), trust_env=False)

    monkeypatch.setattr(public_fetch.httpx, "Client", client)
    return options


def test_fake_dns_uses_system_proxy_and_keeps_target_hostname(monkeypatch, proxy_network):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, content=b"rss")

    options = mock_client(monkeypatch, handler)
    result = public_fetch.PublicPageFetcher().fetch("https://www.biopharmatrend.com/rss/")
    assert result.body == b"rss"
    assert result.url == "https://www.biopharmatrend.com/rss/"
    assert options[0]["proxy"] == "http://127.0.0.1:7897"
    assert options[0]["trust_env"] is False
    assert options[0]["follow_redirects"] is False
    assert requests[0].url.host == "www.biopharmatrend.com"


@pytest.mark.parametrize("proxies,bypass", [({}, False), ({"https": "http://127.0.0.1:7897"}, True)])
def test_fake_dns_without_effective_proxy_is_rejected(monkeypatch, proxy_network, proxies, bypass):
    monkeypatch.setattr(public_fetch, "getproxies", lambda: proxies)
    monkeypatch.setattr(public_fetch, "proxy_bypass", lambda host: bypass)
    with pytest.raises(ValueError, match="私网"):
        public_fetch.PublicPageFetcher().fetch("https://example.org/")


@pytest.mark.parametrize("target", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "198.18.0.96", "localhost"])
def test_proxy_still_rejects_internal_and_literal_fake_targets(proxy_network, target):
    with pytest.raises(ValueError):
        public_fetch.PublicPageFetcher().fetch(f"https://{target}/")


def test_proxy_revalidates_redirect_before_request(monkeypatch, proxy_network):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})

    mock_client(monkeypatch, handler)
    with pytest.raises(ValueError):
        public_fetch.PublicPageFetcher().fetch("https://example.org/")
    assert len(requests) == 1


def test_proxy_size_limit(monkeypatch, proxy_network):
    mock_client(monkeypatch, lambda request: httpx.Response(200, content=b"too long"))
    with pytest.raises(ValueError, match="大小"):
        public_fetch.PublicPageFetcher(max_bytes=3).fetch("https://example.org/")


def test_proxy_error_redaction(monkeypatch, proxy_network):
    def failed(request):
        raise httpx.ProxyError("http://user:secret@proxy.invalid")

    mock_client(monkeypatch, failed)
    with pytest.raises(OSError) as error:
        public_fetch.PublicPageFetcher().fetch("https://example.org/")
    assert "ProxyError" in str(error.value)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("addresses", [["10.0.0.1"], ["198.18.0.96", "192.168.1.1"], ["::1"]])
def test_proxy_rejects_private_and_mixed_dns(monkeypatch, proxy_network, addresses):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", (ip, 443)) for ip in addresses])
    with pytest.raises(ValueError, match="私网"):
        public_fetch.PublicPageFetcher().fetch("https://example.org/")


def test_direct_public_request_still_uses_validated_ip(monkeypatch):
    monkeypatch.setattr(public_fetch, "getproxies", lambda: {})
    monkeypatch.setattr(public_fetch, "proxy_bypass", lambda host: False)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("8.8.8.8", 443))])
    connections = []

    class Connection:
        sock = None

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            return self

        status = 200

        def read(self, size):
            return b"ok"

        def getheader(self, name, default=None):
            return default

        def close(self):
            connections.append("closed")

    monkeypatch.setattr(public_fetch.http.client, "HTTPConnection", lambda *a, **k: Connection())
    monkeypatch.setattr(socket, "create_connection", lambda address, timeout: connections.append(address))
    result = public_fetch.PublicPageFetcher().fetch("http://example.org/")
    assert result.body == b"ok"
    assert connections == [("8.8.8.8", 80), "closed"]
