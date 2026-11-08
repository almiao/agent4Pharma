import base64
import hashlib

import pytest

from agent4pharma.modules.publishing.article_layout import ArticlePresentation, FigureRecord
from agent4pharma.modules.publishing.release_contracts import ChannelPackage
from agent4pharma.modules.publishing.wechat_content import prepare_wechat_content


def content(png=b"\x89PNG\r\n\x1a\nfixture", source=None, expected=None):
    digest = hashlib.sha256(png).hexdigest()
    source = source if source is not None else "data:image/png;base64," + base64.b64encode(png).decode()
    presentation = ArticlePresentation(
        wechat_html='<p>准确正文</p><img src="' + source + '"/>',
        wechat_hash="unused",
        source_body_hash="unused",
        figures=[FigureRecord.model_construct(image_sha256=expected or digest)],
    )
    return ChannelPackage.model_construct(presentation=presentation)


def test_preserves_exact_html_and_png_bytes():
    package = content()
    prepared = prepare_wechat_content(package, "准确标题")
    assert prepared.html == package.presentation.wechat_html
    assert prepared.images[0].png == b"\x89PNG\r\n\x1a\nfixture"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"source": "https://unbound.test/image.png"},
        {"source": "file:///secret.png"},
        {"source": "data:image/png;base64,broken"},
        {"expected": "0" * 64},
        {"png": b"not-a-png"},
        {"png": b"\x89PNG\r\n\x1a\n" + b"x" * 1048576},
    ],
)
def test_invalid_unbound_or_oversize_image_is_rejected(kwargs):
    with pytest.raises(ValueError):
        prepare_wechat_content(content(**kwargs), "标题")


def test_registered_but_missing_image_is_rejected():
    package = content()
    package.presentation.wechat_html = "<p>图片缺失</p>"
    with pytest.raises(ValueError, match="缺少"):
        prepare_wechat_content(package, "标题")
