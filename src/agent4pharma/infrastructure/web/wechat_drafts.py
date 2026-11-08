"""固定微信公众号 API 的草稿适配器；不调用发布或群发接口。"""

import base64
import html
import json
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from threading import get_ident
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ValidationError

from agent4pharma.modules.publishing.wechat_delivery import (
    WechatDeliveryError,
    WechatDeliveryUnknown,
    WechatDraftContent,
    WechatDraftRequest,
    WechatPreparedDraft,
)


class _Response(BaseModel):
    errcode: int = 0
    access_token: str = ""
    expires_in: int = 0
    url: str = ""
    media_id: str = ""


@contextmanager
def _private_http_logs() -> Iterator[None]:
    # httpx INFO 默认包含带 access_token 的 URL；只屏蔽当前交付线程的传输日志。
    identity = get_ident()

    def allow(record: logging.LogRecord) -> bool:
        return record.thread != identity

    loggers = [
        logging.getLogger(name)
        for name in ("httpx", "httpcore.connection", "httpcore.http11", "httpcore.http2", "httpcore.proxy")
    ]
    for logger in loggers:
        logger.addFilter(allow)
    try:
        yield
    finally:
        for logger in loggers:
            logger.removeFilter(allow)


class WechatDraftClient:
    """调用者串行使用；凭证和短期 token 只存内存，每次 HTTP 显式关闭。"""

    def __init__(self, appid: str, secret: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self._appid, self._secret = appid, secret
        self._transport = transport
        self._token = ""
        self._expires_at = 0.0

    def _post(
        self,
        endpoint: str,
        *,
        body: bytes | None = None,
        image: bytes | None = None,
        token: str = "",
        creating: bool = False,
    ) -> _Response:
        params = {"access_token": token} if token else {}
        if endpoint == "material/add_material":
            params["type"] = "image"
        error_type = WechatDeliveryUnknown if creating else WechatDeliveryError
        try:
            with (
                _private_http_logs(),
                httpx.Client(timeout=30, transport=self._transport, follow_redirects=False) as client,
            ):
                response = client.post(
                    "https://api.weixin.qq.com/cgi-bin/" + endpoint,
                    params=params,
                    content=body,
                    headers={"Content-Type": "application/json; charset=utf-8"} if body else None,
                    files={"media": ("image.png", image, "image/png")} if image else None,
                )
            response.raise_for_status()
            result = _Response.model_validate_json(response.content)
        except (httpx.HTTPError, ValidationError):
            raise error_type("微信请求未获得可确认结果，请查看交付状态") from None
        if result.errcode:
            # 不公开原始 errmsg；可能含 IP、token 或其他请求内容。
            if result.errcode in {40001, 40014, 42001}:
                self._token = ""
                self._expires_at = 0
            raise WechatDeliveryError(f"微信拒绝请求，错误码 {result.errcode}；请核对账号权限、白名单或素材")
        return result

    def _access_token(self) -> str:
        if self._token and time.monotonic() < self._expires_at:
            return self._token
        body = json.dumps(
            {
                "grant_type": "client_credential",
                "appid": self._appid,
                "secret": self._secret,
                "force_refresh": False,
            }
        ).encode()
        result = self._post("stable_token", body=body)
        if not result.access_token or result.expires_in <= 0:
            raise WechatDeliveryError("微信未返回有效调用凭据")
        self._token = result.access_token
        self._expires_at = time.monotonic() + max(0, result.expires_in - 120)
        return self._token

    def prepare(self, content: WechatDraftContent, request: WechatDraftRequest) -> WechatPreparedDraft:
        """逐图上传正文素材，封面独立上传永久素材；不自动删图或压缩原图。"""
        token = self._access_token()
        body = content.html
        thumb = request.thumb_media_id
        for image in content.images:
            result = self._post("media/uploadimg", image=image.png, token=token)
            address = urlsplit(result.url)
            if address.scheme not in {"https", "http"} or not (
                address.hostname and address.hostname.endswith(".qpic.cn")
            ):
                raise WechatDeliveryError("微信未返回有效正文图片地址")
            original = "data:image/png;base64," + base64.b64encode(image.png).decode("ascii")
            body = body.replace(original, html.escape(result.url, quote=True))
            if image.sha256 == request.cover_sha256:
                cover = self._post("material/add_material", image=image.png, token=token)
                thumb = cover.media_id
        if not thumb:
            raise WechatDeliveryError("微信未返回永久封面素材 ID")
        return WechatPreparedDraft(content.title, body, thumb)

    def create(self, prepared: WechatPreparedDraft) -> str:
        """建草稿只请求一次；超时、非 JSON 和缺 ID 均保留未知，绝不重发。"""
        token = self._access_token()
        body = json.dumps(
            {
                "articles": [
                    {
                        "article_type": "news",
                        "title": prepared.title,
                        "content": prepared.html,
                        "thumb_media_id": prepared.thumb_media_id,
                        "need_open_comment": 0,
                        "only_fans_can_comment": 0,
                    }
                ]
            },
            ensure_ascii=False,
        ).encode("utf-8")
        result = self._post("draft/add", token=token, body=body, creating=True)
        if not result.media_id:
            raise WechatDeliveryUnknown("微信未返回草稿 ID；请在公众号后台核对，不要重复发送")
        return result.media_id
