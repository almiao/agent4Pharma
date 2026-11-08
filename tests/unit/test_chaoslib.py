"""混沌书苑接入的离线契约测试：通道派生、路由选择与凭证加载，不联网。"""

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from agent4pharma.infrastructure.web.chaoslib import (
    BLOCKED_PREFIXES,
    ROUTES,
    AccountSession,
    ChaosLibAuthError,
    ChaosLibConnector,
    ChaosLibSettings,
    compile_aad,
    doi_suffix,
    load_settings,
    route_for,
    runtime_tag,
)


def test_runtime_tag_matches_documented_mix_sum():
    # 站点 WASM 的 mix(a,b) 是 32 位加法；标签必须与前端逐字节一致，否则通道解包失败。
    payload = {"target": "uid:7917", "trace": "abc", "version": "v1"}
    digest = hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    expected = str((int(digest[:8], 16) + int(digest[8:16], 16)) & 0xFFFFFFFF)
    assert runtime_tag(payload) == expected


def test_route_selection_uses_doi_prefix_not_title_guess():
    assert route_for("10.1038/s41586-026-10438-2").name == "nature"
    assert route_for("10.1007/s10822-026-00970-8").name == "springer"
    assert route_for("10.1016/j.cell.2026.09.002") is None
    assert route_for("11.1038/lookalike") is None


def test_blocked_prefixes_are_known_publishers_not_nature():
    assert "10.1016" in BLOCKED_PREFIXES
    assert "10.1038" not in BLOCKED_PREFIXES


def test_doi_suffix_strips_registrant_prefix():
    assert doi_suffix("10.1038/s41587-026-03343-6") == "s41587-026-03343-6"


def test_aad_is_compact_json_with_fixed_key_order():
    payload = compile_aad("uid:7917", "trace", "version")
    decoded = json.loads(payload)
    assert list(decoded) == ["runtimeTag", "target", "trace", "version"]
    assert payload.decode() == json.dumps(decoded, separators=(",", ":"), ensure_ascii=False)


def test_settings_prefers_process_environment_over_dotenv(tmp_path):
    (tmp_path / ".env").write_text(
        "PAPER_D_ACCOUNT=file-account\nPAPER_D_PASSWORD=file-secret\n", encoding="utf-8"
    )
    settings = load_settings(
        tmp_path,
        {"PAPER_D_ACCOUNT": "env-account", "PAPER_D_PASSWORD": "env-secret"},
    )
    assert settings.account == "env-account"
    assert settings.password == "env-secret"
    assert settings.state_dir == tmp_path / "data/private/chaoslib"


def test_settings_reject_missing_credentials():
    with pytest.raises(ChaosLibAuthError):
        ChaosLibSettings(account="", password="")


def test_session_freshness_keeps_margin():
    now = datetime.now(UTC)
    session = AccountSession(jwt="token", cookies={}, uid=1, expires_at=now + timedelta(minutes=10))
    assert session.is_fresh(now) is True
    assert session.is_fresh(now + timedelta(minutes=6)) is False


def test_routes_have_unique_prefixes():
    prefixes = [route.doi_prefix for route in ROUTES]
    assert len(prefixes) == len(set(prefixes))


def test_site_throttle_maps_to_rate_limited_instead_of_crashing():
    result = ChaosLibConnector._failure("10.1038/x", ROUTES[0], "该账号存在频繁操作异常，账号被临时限制", [])
    assert result.status == "failed"
    assert result.reason == "rate_limited:site_throttle"


def test_automation_block_is_labelled_before_generic_channel_error():
    result = ChaosLibConnector._failure(
        "10.1038/x", ROUTES[0], "通道未加密回包: 检测到自动化访问，已被暂时封禁", []
    )
    assert result.status == "failed"
    assert result.reason == "blocked_automation"


@pytest.mark.parametrize("expired", [True, False])
def test_browser_login_does_not_treat_homepage_with_stale_or_rejected_token_as_authenticated(
    tmp_path, monkeypatch, expired
):
    import base64
    from types import SimpleNamespace

    import playwright.sync_api

    from agent4pharma.infrastructure.web import chaoslib as adapter

    expiry = datetime.now(UTC) + timedelta(hours=-1 if expired else 12)
    payload = (
        base64.urlsafe_b64encode(json.dumps({"exp": int(expiry.timestamp()), "sub": "1"}).encode())
        .decode()
        .rstrip("=")
    )
    token = "header." + payload + ".signature"
    events, closed = [], []

    class Page:
        url = "https://www.chaoslib.com/login"
        callback = None

        def on(self, name, callback):
            self.callback = callback

        def goto(self, url, **kwargs):
            self.url = url
            self.callback(SimpleNamespace(headers={"authorization": "Bearer " + token}))

        def click(self, *args, **kwargs):
            self.url = "https://www.chaoslib.com/"

        def evaluate(self, script):
            events.append(script)

        def wait_for_timeout(self, *args):
            pass

        def fill(self, *args):
            pass

        def check(self, *args, **kwargs):
            pass

    context = SimpleNamespace(
        pages=[Page()],
        clear_cookies=lambda **kw: events.append(kw),
        close=lambda: closed.append(True),
        cookies=lambda: [],
        request=SimpleNamespace(get=lambda *a, **kw: SimpleNamespace(status=403)),
    )

    class Playwright:
        chromium = SimpleNamespace(launch_persistent_context=lambda *a, **kw: context)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(playwright.sync_api, "sync_playwright", Playwright)
    monkeypatch.setattr(adapter, "_resolve_chromium", lambda _: "fake-browser")
    settings = ChaosLibSettings(account="fixture-account", password="fixture-password", state_dir=tmp_path)
    with pytest.raises(ChaosLibAuthError, match="登录态"):
        adapter.login_with_browser(settings)
    assert closed and events
    assert not (tmp_path / "session.json").exists()
