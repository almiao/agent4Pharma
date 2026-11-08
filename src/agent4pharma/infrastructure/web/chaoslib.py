"""混沌书苑（chaoslib.com）文献代理接入：卡密登录、加密通道与按 DOI 下载。

站点前端用一次性 RSA/AES-GCM 通道加密业务请求，登录还需要 Cloudflare Turnstile；
本模块把登录交给真实浏览器完成，把登录后的接口调用收敛为纯 Python 客户端，
再按出版商选择站内入口下载 PDF。

边界：

- 只使用账号自身的订阅权益，不破解验证码、不伪造机构身份。
- 每次下载都校验文件头、页数和 DOI 线索，失败原因如实返回，不重试到“成功”。
- 站内入口和出版商反爬策略会变化，未覆盖的出版商返回 unsupported。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)
DEFAULT_BASE_URL = "https://www.chaoslib.com"
DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$")


class ChaosLibError(RuntimeError):
    """混沌书苑接入的基类错误，调用方据此区分配置、认证与下载失败。"""


class ChaosLibAuthError(ChaosLibError):
    """登录或会话保持失败；多为需要人工完成验证码或账号失效。"""


class ChaosLibUnsupported(ChaosLibError):
    """当前账号或站点入口未覆盖该出版商，不能靠重试解决。"""


@dataclass(frozen=True)
class ChaosLibSettings:
    """账号、站点与本地状态目录；凭证只来自环境或 .env，不写入仓库。"""

    account: str
    password: str
    base_url: str = DEFAULT_BASE_URL
    state_dir: Path = Path("data/private/chaoslib")
    chromium: str = ""
    request_interval: float = 30.0
    timeout: float = 120.0

    def __post_init__(self) -> None:
        if not self.account or not self.password:
            raise ChaosLibAuthError("缺少 PAPER_D_ACCOUNT / PAPER_D_PASSWORD")


@dataclass(frozen=True)
class AccountSession:
    """一次浏览器登录得到的访问令牌与 Cookie；令牌约 24 小时有效。"""

    jwt: str
    cookies: dict[str, str]
    uid: int
    expires_at: datetime

    def is_fresh(self, now: datetime | None = None, margin_seconds: int = 300) -> bool:
        """距离过期仍有余量时才视为可复用，避免请求中途失效。"""
        current = now or datetime.now(UTC)
        return current + timedelta(seconds=margin_seconds) < self.expires_at


@dataclass(frozen=True)
class PublisherRoute:
    """一个出版商的入口编号与文章/PDF 地址模板。"""

    name: str
    doi_prefix: str
    menu_id: int
    article_template: str
    pdf_template: str


@dataclass(frozen=True)
class ChaosLibDownload:
    """单次下载观测；status 只表达本次实际结果。"""

    doi: str
    publisher: str
    status: str
    channel: str = "chaoslib"
    reason: str = ""
    article_url: str = ""
    file: str = ""
    sha256: str = ""
    bytes: int = 0
    pages: int = 0
    identity_match: bool = False
    attempts: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """返回可直接序列化的结果，供报告和入库使用。"""
        return {
            "doi": self.doi,
            "publisher": self.publisher,
            "status": self.status,
            "channel": self.channel,
            "reason": self.reason,
            "article_url": self.article_url,
            "file": self.file,
            "sha256": self.sha256,
            "bytes": self.bytes,
            "pages": self.pages,
            "identity_match": self.identity_match,
            "attempts": self.attempts,
        }


# 2026-10-30 实测可用的入口。Nature Portfolio 共用 nature.com，SpringerLink 走 /content/pdf。
ROUTES: tuple[PublisherRoute, ...] = (
    PublisherRoute(
        name="nature",
        doi_prefix="10.1038",
        menu_id=88,
        article_template="{base}/articles/{suffix}",
        pdf_template="{article}.pdf",
    ),
    PublisherRoute(
        name="springer",
        doi_prefix="10.1007",
        menu_id=84,
        article_template="{base}/article/{doi}",
        pdf_template="{base}/content/pdf/{doi}.pdf",
    ),
)

# 同一天实测被出版商反爬或账号权益挡住的入口；记录原因，默认不再空转重试。
BLOCKED_PREFIXES: dict[str, str] = {
    "10.1016": "publisher_challenge:turnstile (ezproxy.sjlib.cn, ScienceDirect/Elsevier)",
    "10.1021": "publisher_challenge:turnstile (ezproxy.sjlib.cn, ACS)",
    "10.1002": "publisher_challenge:cloudflare (onlinelibrary.wiley.com)",
    "10.1056": "publisher_challenge:cloudflare (www.nejm.org)",
    "10.1073": "publisher_challenge:cloudflare (www.pnas.org)",
    "10.1126": "publisher_challenge:cloudflare (www.science.org)",
    "10.1093": "account_entitlement_missing (OUP 入口 has_access=false)",
}


def load_settings(project_root: Path, env: Mapping[str, str] | None = None) -> ChaosLibSettings:
    """从 .env 与环境读取卡密和可选覆盖项；进程环境优先于 .env。"""
    values: dict[str, str] = {}
    dotenv = project_root / ".env"
    if dotenv.is_file():
        for line in dotenv.read_text(encoding="utf-8-sig").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    for key, value in (env or os.environ).items():
        if key in {"PAPER_D_ACCOUNT", "PAPER_D_PASSWORD"} or key.startswith("CHAOSLIB_"):
            values[key] = value
    data_dir = Path(values.get("CHAOSLIB_STATE_DIR") or project_root / "data/private/chaoslib")
    return ChaosLibSettings(
        account=values.get("PAPER_D_ACCOUNT", ""),
        password=values.get("PAPER_D_PASSWORD", ""),
        base_url=values.get("CHAOSLIB_BASE_URL", DEFAULT_BASE_URL),
        state_dir=data_dir,
        chromium=values.get("CHAOSLIB_CHROMIUM", ""),
        request_interval=max(30.0, float(values.get("CHAOSLIB_REQUEST_INTERVAL", "30"))),
        timeout=float(values.get("CHAOSLIB_TIMEOUT", "120")),
    )


def _dumps(payload: Any) -> str:
    # 站点用 JSON.stringify 计算附加认证数据，键顺序和紧凑格式必须一致。
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def runtime_tag(payload: Any) -> str:
    """复现站点 WASM runtime 的 mix(a, b) = a + b，作为通道附加认证数据的一部分。"""
    digest = hashlib.sha256(_dumps(payload).encode()).hexdigest()
    return str((int(digest[0:8], 16) + int(digest[8:16], 16)) & 0xFFFFFFFF)


def compile_aad(target: str, trace: str, version: str) -> bytes:
    """构造通道附加认证数据；键顺序必须与前端 JSON.stringify 完全一致。"""
    tag = runtime_tag({"target": target, "trace": trace, "version": version})
    return _dumps({"runtimeTag": tag, "target": target, "trace": trace, "version": version}).encode()


def doi_suffix(doi: str) -> str:
    """取 DOI 在 nature.com 路径中的后缀，例如 s41587-026-03343-6。"""
    return doi.split("/", 1)[1] if "/" in doi else doi


def route_for(doi: str) -> PublisherRoute | None:
    """按 DOI 前缀选择入口；没有配置则返回 None。"""
    for route in ROUTES:
        if doi.startswith(route.doi_prefix + "/"):
            return route
    return None


class _Throttle:
    """串行请求的最小间隔，避免批量下载触发账号风控。"""

    def __init__(self, interval: float) -> None:
        self.interval = max(interval, 30.0)
        self._last = 0.0

    def wait(self) -> None:
        """距离上次请求不足间隔时休眠，保持稳定的低频访问。"""
        remaining = self.interval - (time.monotonic() - self._last)
        if remaining > 0:
            time.sleep(remaining)
        self._last = time.monotonic()


class SealedChannel:
    """把登录后的业务请求封装进一次 RSA 握手建立的 AES-GCM 通道。"""

    def __init__(self, http: httpx.Client, target: str, timeout: float = 120.0) -> None:
        self.http = http
        self.target = target
        self.timeout = timeout
        self._negotiate()

    def _negotiate(self) -> None:
        bootstrap = self.http.get("/client/auth/prepare").json()["data"]
        self.version = bootstrap["version"]
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        spki = private_key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        server_key = serialization.load_pem_public_key(bootstrap["ticket"].encode())
        seed = server_key.encrypt(
            spki, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None)
        )
        granted = self.http.post(
            "/client/auth/grant",
            json={"version": self.version, "target": self.target, "seed": base64.b64encode(seed).decode()},
        ).json()["data"]
        self.aes_key = private_key.decrypt(
            base64.b64decode(granted["secret"]),
            padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
        )
        self.trace = granted["trace"]
        self.proof = granted.get("proof") or ""

    def _aad(self) -> bytes:
        return compile_aad(self.target, self.trace, self.version)

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """发送一次加密封包并解出回包；HTTP 或解包失败抛 ChaosLibError。"""
        aad = self._aad()
        nonce = os.urandom(12)
        packet = AESGCM(self.aes_key).encrypt(nonce, _dumps(payload).encode(), aad)
        response = self.http.post(
            path,
            json={
                "trace": self.trace,
                "nonce": base64.b64encode(nonce).decode(),
                "packet": base64.b64encode(packet).decode(),
            },
            headers={"X-Client-Login-Challenge": self.proof},
            timeout=self.timeout,
        )
        if response.status_code in {401, 403}:
            raise ChaosLibAuthError("session_rejected_http_" + str(response.status_code))
        if response.status_code == 429:
            raise ChaosLibError("rate_limited")
        body = response.json()
        if not isinstance(body, dict):
            raise ChaosLibError("通道返回非 JSON 对象")
        envelope = body.get("data")
        if not (isinstance(envelope, dict) and envelope.get("sealed")):
            raise ChaosLibError(f"通道未加密回包: {body.get('message') or response.status_code}")
        plain = AESGCM(self.aes_key).decrypt(
            base64.b64decode(envelope["nonce"]), base64.b64decode(envelope["packet"]), aad
        )
        result = json.loads(plain)
        if not isinstance(result, dict):
            raise ChaosLibError("通道回包结构异常")
        return result


def _session_path(settings: ChaosLibSettings) -> Path:
    return settings.state_dir / "session.json"


def load_session(settings: ChaosLibSettings) -> AccountSession | None:
    """读取缓存的登录态；文件缺失或损坏时返回 None。"""
    path = _session_path(settings)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return AccountSession(
            jwt=raw["jwt"],
            cookies=raw["cookies"],
            uid=int(raw["uid"]),
            expires_at=datetime.fromisoformat(raw["expires_at"]),
        )
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        return None


def save_session(settings: ChaosLibSettings, session: AccountSession) -> None:
    """保存登录态到本地状态目录，文件权限尽量收紧；不打印令牌。"""
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    path = _session_path(settings)
    path.write_text(
        json.dumps(
            {
                "jwt": session.jwt,
                "cookies": session.cookies,
                "uid": session.uid,
                "expires_at": session.expires_at.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _jwt_expiry(token: str) -> datetime:
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    claims = json.loads(base64.urlsafe_b64decode(payload))
    return datetime.fromtimestamp(int(claims["exp"]), UTC)


def _resolve_chromium(settings: ChaosLibSettings) -> str:
    if settings.chromium and Path(settings.chromium).is_file():
        return settings.chromium
    for candidate in (
        Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
        Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
    ):
        if candidate.is_file():
            return str(candidate)
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        executable = playwright.chromium.executable_path
    if not executable or not Path(executable).is_file():
        raise ChaosLibAuthError("找不到可用 Chromium：请设置 CHAOSLIB_CHROMIUM 或安装 playwright chromium")
    return executable


def login_with_browser(settings: ChaosLibSettings, *, headless: bool = True) -> AccountSession:
    """用真实浏览器完成卡密登录并捕获访问令牌；需要人工验证时抛 ChaosLibAuthError。

    站点登录依赖 Cloudflare Turnstile，本函数不尝试绕过：验证未通过就停止，
    由人工在有界面浏览器里完成一次登录，或稍后重试。
    """
    from playwright.sync_api import sync_playwright

    settings.state_dir.mkdir(parents=True, exist_ok=True)
    temporary = settings.state_dir / "tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    os.environ.update(TEMP=str(temporary), TMP=str(temporary))
    profile = settings.state_dir / "profile"
    tokens: list[str] = []
    chromium = _resolve_chromium(settings)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(profile),
            executable_path=chromium,
            headless=headless,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1366, "height": 900},
            env={**os.environ},
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.on(
                "request",
                lambda request: (
                    tokens.append(request.headers["authorization"])
                    if request.headers.get("authorization", "").startswith("Bearer ")
                    else None
                ),
            )
            page.goto(f"{settings.base_url}/login", wait_until="domcontentloaded", timeout=60_000)
            # 旧 profile 可能带着过期令牌自动跳回首页；显式登录只清理该站状态后重新提交。
            if urlparse(page.url).hostname != urlparse(settings.base_url).hostname:
                raise ChaosLibAuthError("登录页面离开预期站点，交人工核对")
            page.evaluate("() => { localStorage.clear(); sessionStorage.clear(); }")
            context.clear_cookies(domain=re.compile(r"(^|\.)chaoslib\.com$"))
            tokens.clear()
            page.goto(f"{settings.base_url}/login", wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(2_000)
            if "/login" in page.url:
                page.fill("input.auth-floating-field__input", settings.account)
                page.fill("input[type=password]", settings.password)
                try:
                    page.check("input[type=checkbox]", timeout=3_000)
                except Exception:  # noqa: BLE001 - 复选框是可选的同意项
                    pass
                page.click("button.auth-submit")
                page.wait_for_timeout(6_000)
            if not headless:
                deadline = time.monotonic() + 180
                while ("/login" in page.url or not tokens) and time.monotonic() < deadline:
                    page.wait_for_timeout(1000)
            if "/login" in page.url or not tokens:
                raise ChaosLibAuthError("登录未完成：可能需要人工完成 Turnstile 验证或账号失效")
            page.goto(settings.base_url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(2_500)
            if not tokens:
                raise ChaosLibAuthError("登录后未捕获访问令牌")
            jwt = tokens[-1].split(" ", 1)[1]
            if _jwt_expiry(jwt) <= datetime.now(UTC) + timedelta(minutes=5):
                raise ChaosLibAuthError("站点未返回有效的新登录态，需要人工重新登录")
            checked = context.request.get(
                f"{settings.base_url}/api/client/auth/menus",
                headers={"Authorization": "Bearer " + jwt},
                timeout=30_000,
            )
            if checked.status != 200 or not isinstance(checked.json().get("data"), list):
                raise ChaosLibAuthError("登录态未通过服务端验证，需要人工处理")
            cookies = {item["name"]: item["value"] for item in context.cookies()}
        finally:
            context.close()
    uid = _jwt_uid(jwt)
    return AccountSession(jwt=jwt, cookies=cookies, uid=uid, expires_at=_jwt_expiry(jwt))


def _jwt_uid(token: str) -> int:
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return int(json.loads(base64.urlsafe_b64decode(payload))["sub"])


class ChaosLibClient:
    """登录态 + 加密通道的 HTTP 客户端，负责菜单执行等站内接口。"""

    def __init__(self, settings: ChaosLibSettings, session: AccountSession | None = None) -> None:
        self.settings = settings
        self.session = session or load_session(settings)
        if self.session is None or not self.session.is_fresh():
            self.session = login_with_browser(settings)
            save_session(settings, self.session)
        self.http = httpx.Client(
            base_url=f"{settings.base_url}/api",
            timeout=settings.timeout,
            cookies=self.session.cookies,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/plain, */*",
                "Origin": settings.base_url,
                "Referer": f"{settings.base_url}/",
            },
        )
        self.http.headers["Authorization"] = f"Bearer {self.session.jwt}"
        self.channel = SealedChannel(self.http, f"uid:{self.session.uid}", settings.timeout)

    def close(self) -> None:
        """释放底层 HTTP 连接；浏览器会话由连接器单独管理。"""
        self.http.close()

    def menus(self) -> list[dict[str, Any]]:
        """读取当前账号可见的数据库菜单树。"""
        response = self.http.get("/client/auth/menus")
        response.raise_for_status()
        data = response.json()["data"]
        return data if isinstance(data, list) else []

    def execute(self, menu_id: int) -> dict[str, Any]:
        """执行一个入口，返回站点给出的跳转或表单动作。"""
        return self.channel.post("/client/auth/menus/execute", {"menu_id": menu_id})


class ChaosLibConnector:
    """在同一个浏览器上下文中打开出版商入口并按 DOI 取回 PDF。

    入口令牌是短时的，按出版商缓存 base_url；遇到 401/403/404 时重新执行一次入口。
    """

    def __init__(self, settings: ChaosLibSettings, client: ChaosLibClient | None = None) -> None:
        self.settings = settings
        self.client = client or ChaosLibClient(settings)
        self.throttle = _Throttle(settings.request_interval)
        self._playwright = None
        self._context = None
        self._page = None
        self._entries: dict[int, str] = {}

    def __enter__(self) -> ChaosLibConnector:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        """关闭浏览器与 HTTP 客户端，不吞异常。"""
        if self._context is not None:
            self._context.close()
            self._context = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
        self.client.close()

    def _ensure_browser(self):
        if self._context is not None:
            return self._page
        from playwright.sync_api import sync_playwright

        settings = self.settings
        settings.state_dir.mkdir(parents=True, exist_ok=True)
        temporary = settings.state_dir / "tmp"
        temporary.mkdir(parents=True, exist_ok=True)
        os.environ.update(TEMP=str(temporary), TMP=str(temporary))
        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            str(settings.state_dir / "profile"),
            executable_path=_resolve_chromium(settings),
            headless=True,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1366, "height": 900},
            accept_downloads=True,
            env={**os.environ},
        )
        self._context.add_cookies(
            [
                {"name": name, "value": value, "url": settings.base_url}
                for name, value in self.client.session.cookies.items()
            ]
        )
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        self._page.goto(f"{settings.base_url}/", wait_until="domcontentloaded", timeout=60_000)
        return self._page

    def _open_entry(self, route: PublisherRoute) -> str:
        page = self._ensure_browser()
        if route.menu_id in self._entries:
            return self._entries[route.menu_id]
        executed = self.client.execute(route.menu_id)
        payload = executed.get("payload") or {}
        target = payload.get("url") or ""
        if not target and payload.get("action"):
            self._submit_form(payload)
            target = page.url
        if not target:
            raise ChaosLibError(f"入口 {route.menu_id} 未返回地址: {executed.get('message')}")
        if target.startswith("http"):
            self.throttle.wait()
            page.goto(target, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(2_000)
        parsed = urlparse(page.url if page.url.startswith("http") else target)
        base = f"{parsed.scheme}://{parsed.netloc}"
        self._entries[route.menu_id] = base
        return base

    def _submit_form(self, payload: Mapping[str, Any]) -> None:
        page = self._ensure_browser()
        script = (
            "(args) => {const form=document.createElement('form');"
            "form.method=args.method||'POST';form.action=args.action;"
            "for (const item of args.fields||[]) {const input=document.createElement('input');"
            "input.type='hidden';input.name=item.name;input.value=item.value;form.appendChild(input);}"
            "document.body.appendChild(form);form.submit();}"
        )
        with page.expect_navigation(timeout=60_000):
            page.evaluate(
                script,
                {
                    "action": payload["action"],
                    "method": payload.get("method"),
                    "fields": payload.get("fields"),
                },
            )

    def download(self, doi: str, output_dir: Path, *, title: str = "") -> ChaosLibDownload:
        """按 DOI 下载一篇 PDF；只返回本次真实结果，不掩盖失败。"""
        doi = doi.removeprefix("https://doi.org/").strip()
        if not DOI_PATTERN.match(doi):
            return ChaosLibDownload(doi=doi, publisher="", status="invalid_doi", reason="doi_format")
        route = route_for(doi)
        if route is None:
            prefix = doi.split("/", 1)[0]
            reason = BLOCKED_PREFIXES.get(prefix, "")
            if reason:
                return ChaosLibDownload(doi=doi, publisher=prefix, status="unsupported", reason=reason)
            return ChaosLibDownload(doi=doi, publisher=prefix, status="unsupported", reason="no_route")

        attempts: list[dict[str, Any]] = []
        try:
            return self._download_route(doi, route, output_dir, attempts)
        except ChaosLibError as exc:
            return self._failure(doi, route, str(exc), attempts)
        except Exception as exc:  # noqa: BLE001 - 浏览器异常在适配器边界收敛为失败状态
            from playwright.sync_api import Error as PlaywrightError

            if not isinstance(exc, PlaywrightError):
                raise
            return self._failure(doi, route, f"browser:{type(exc).__name__}", attempts)

    def _download_route(
        self, doi: str, route: PublisherRoute, output_dir: Path, attempts: list[dict[str, Any]]
    ) -> ChaosLibDownload:
        page = self._ensure_browser()
        base = self._open_entry(route)
        article = route.article_template.format(base=base, doi=doi, suffix=doi_suffix(doi))
        for _attempt in range(1):
            self.throttle.wait()
            response = page.goto(article, wait_until="domcontentloaded", referer=f"{base}/", timeout=60_000)
            attempts.append(
                {"kind": "article", "status": response.status if response else None, "url": article}
            )
            if response and response.status == 200:
                break
            reason = f"article_http_{response.status if response else 'error'}"
            return self._result(doi, route, "failed", reason, article, attempts)

        body = page.inner_text("body")
        if "安全验证" in body or "Just a moment" in body:
            return self._result(doi, route, "failed", "publisher_challenge", article, attempts)

        pdf_url = route.pdf_template.format(base=base, doi=doi, article=article, suffix=doi_suffix(doi))
        self.throttle.wait()
        fetched = self._context.request.get(
            pdf_url, headers={"Referer": article}, timeout=self.settings.timeout * 1000
        )
        attempts.append({"kind": "pdf", "status": fetched.status, "url": pdf_url})
        content = fetched.body() if fetched.status == 200 else b""
        if fetched.status != 200:
            return self._result(doi, route, "failed", f"pdf_http_{fetched.status}", article, attempts)
        if not content.startswith(b"%PDF"):
            return self._result(doi, route, "invalid_pdf", "not_pdf", article, attempts)

        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"{route.name}-{re.sub(r'[^A-Za-z0-9._-]+', '_', doi)}.pdf"
        path.write_bytes(content)
        pages = _pdf_pages(content)
        identity = doi.encode() in content
        status = "downloaded" if pages > 0 else "invalid_pdf"
        reason = "" if pages > 0 else "unreadable_pdf"
        result = self._result(doi, route, status, reason, article, attempts)
        return replace(
            result,
            file=str(path),
            sha256=hashlib.sha256(content).hexdigest(),
            bytes=len(content),
            pages=pages,
            identity_match=identity,
        )

    @staticmethod
    def _result(
        doi: str, route: PublisherRoute, status: str, reason: str, article: str, attempts: list[dict]
    ) -> ChaosLibDownload:
        return ChaosLibDownload(
            doi=doi,
            publisher=route.name,
            status=status,
            reason=reason,
            article_url=article,
            attempts=attempts,
        )

    @staticmethod
    def _failure(
        doi: str, route: PublisherRoute, message: str, attempts: list[dict[str, Any]]
    ) -> ChaosLibDownload:
        """把异常收敛为可记录的失败原因；限频/封禁单独标注，避免继续无谓重试。"""
        if "session_rejected_http_" in message or "authentication_required" in message:
            reason = "authentication_required"
        elif "自动化访问" in message or "封禁" in message:
            reason = "blocked_automation"
        elif "频繁" in message or "临时限制" in message:
            reason = "rate_limited:site_throttle"
        elif "通道" in message:
            reason = "channel_error"
        elif "Timeout" in message:
            reason = "timeout"
        else:
            reason = f"error:{message[:120]}"
        return ChaosLibConnector._result(doi, route, "failed", reason, "", attempts)


def _pdf_pages(content: bytes) -> int:
    """用 pymupdf 计数；解析失败返回 0，交由调用方按不可读处理。"""
    try:
        import pymupdf
    except ImportError:  # pragma: no cover - 运行环境缺少 PDF 库时退回粗计数
        return 1 if b"/Type /Page" in content else 0
    try:
        with pymupdf.open(stream=content, filetype="pdf") as document:
            return document.page_count
    except Exception:  # noqa: BLE001 - 第三方解析错误统一视为不可读
        return 0
