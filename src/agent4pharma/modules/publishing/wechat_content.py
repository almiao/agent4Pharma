"""在批准内容内核对内嵌图片；不访问任意路径、不改写正文。"""

import base64
import binascii
import hashlib
from html.parser import HTMLParser

from agent4pharma.modules.publishing.release_contracts import ChannelPackage
from agent4pharma.modules.publishing.wechat_delivery import WechatDraftContent, WechatImage


class _ImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.sources: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "img":
            self.sources.append(dict(attrs).get("src") or "")


def prepare_wechat_content(package: ChannelPackage, title: str) -> WechatDraftContent:
    """要求所有图源均为批准包中登记的 PNG，缺图/多图/篡改直接拒绝。"""
    presentation = package.presentation
    if presentation is None:
        raise ValueError("此包尚无公众号正文，请先生成图文包")
    expected = {figure.image_sha256 for figure in presentation.figures}
    if presentation.paper_header and presentation.paper_header.status == "ready":
        expected.add(presentation.paper_header.image_sha256)
    parser = _ImageParser()
    parser.feed(presentation.wechat_html)
    images: dict[str, WechatImage] = {}
    for source in parser.sources:
        if not source.startswith("data:image/png;base64,"):
            raise ValueError("公众号交付只接受准确包内的 PNG 图片")
        try:
            png = base64.b64decode(source.split(",", 1)[1], validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("图文包图片编码损坏") from exc
        digest = hashlib.sha256(png).hexdigest()
        if digest not in expected or not png.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("图片与批准包记录不一致")
        if len(png) > 1024 * 1024:
            raise ValueError("公众号交付图片须不超过 1MB；请调整图文包后重新核验批准")
        images[digest] = WechatImage(digest, png)
    if images.keys() != expected:
        raise ValueError("图文包缺少登记的图片")
    return WechatDraftContent(title, presentation.wechat_html, tuple(images.values()))
