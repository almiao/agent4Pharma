import base64
import hashlib
import json
from uuid import uuid4

import httpx
import pytest

from agent4pharma.infrastructure.web.wechat_drafts import WechatDraftClient
from agent4pharma.modules.publishing.wechat_delivery import (
    WechatDeliveryError,
    WechatDeliveryUnknown,
    WechatDraftContent,
    WechatDraftRequest,
    WechatImage,
    WechatPreparedDraft,
)


def test_official_endpoints_upload_exact_images_and_unicode_without_publishing(caplog):
    caplog.set_level("DEBUG")
    calls = []
    png = b"\x89PNG\r\n\x1a\nfixture"
    digest = hashlib.sha256(png).hexdigest()

    def handler(request):
        calls.append(request)
        path = request.url.path
        if path.endswith("stable_token"):
            assert json.loads(request.content)["force_refresh"] is False
            return httpx.Response(200, json={"access_token": "sensitive-token", "expires_in": 7200})
        assert request.url.params["access_token"] == "sensitive-token"
        if path.endswith("uploadimg"):
            assert png in request.content
            return httpx.Response(200, json={"url": "https://mmbiz.qpic.cn/test"})
        if path.endswith("add_material"):
            assert request.url.params["type"] == "image"
            return httpx.Response(200, json={"media_id": "cover"})
        assert path.endswith("draft/add")
        article = json.loads(request.content)["articles"][0]
        assert article["title"] == "研究报道"
        assert article["thumb_media_id"] == "cover"
        assert "data:" not in article["content"]
        assert "https://mmbiz.qpic.cn/test" in article["content"]
        return httpx.Response(200, json={"media_id": "draft"})

    client = WechatDraftClient("appid", "secret", transport=httpx.MockTransport(handler))
    body = '<p>原文</p><img src="data:image/png;base64,' + base64.b64encode(png).decode() + '"/>'
    content = WechatDraftContent("研究报道", body, (WechatImage(digest, png),))
    request = WechatDraftRequest(
        request_id=uuid4(),
        package_id="p",
        account_id="appid",
        expected_fingerprint="0" * 64,
        cover_sha256=digest,
    )
    assert client.create(client.prepare(content, request)) == "draft"
    assert len(calls) == 4
    assert "sensitive-token" not in caplog.text


@pytest.mark.parametrize("response", ["timeout", "invalid", "missing", "server"])
def test_creation_ambiguous_responses_are_unknown_and_never_retried(response):
    calls = []

    def handler(request):
        if request.url.path.endswith("stable_token"):
            return httpx.Response(200, json={"access_token": "secret-token", "expires_in": 7200})
        calls.append(request)
        if response == "timeout":
            raise httpx.ReadTimeout("secret-token", request=request)
        if response == "invalid":
            return httpx.Response(200, text="secret-token")
        if response == "server":
            return httpx.Response(502, text="secret-token")
        return httpx.Response(200, json={})

    client = WechatDraftClient("appid", "secret", transport=httpx.MockTransport(handler))
    with pytest.raises(WechatDeliveryUnknown) as error:
        client.create(WechatPreparedDraft("标题", "<p>正文</p>", "cover"))
    assert "secret-token" not in str(error.value)
    assert len(calls) == 1


def test_explicit_rejection_is_safe_failure_not_unknown():
    def handler(request):
        if request.url.path.endswith("stable_token"):
            return httpx.Response(200, json={"access_token": "secret-token", "expires_in": 7200})
        return httpx.Response(200, json={"errcode": 40007, "errmsg": "contains-secret"})

    client = WechatDraftClient("appid", "secret", transport=httpx.MockTransport(handler))
    with pytest.raises(WechatDeliveryError) as error:
        client.create(WechatPreparedDraft("标题", "<p>正文</p>", "cover"))
    assert not isinstance(error.value, WechatDeliveryUnknown)
    assert "40007" in str(error.value) and "contains-secret" not in str(error.value)
