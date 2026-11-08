"""强调层保留正文并拒绝错位、重复和过期输入。"""

import pytest
from bs4 import BeautifulSoup

from agent4pharma.modules.publishing.article_layout import ArticleEmphasis, ArticlePresentation, TextEmphasis
from agent4pharma.modules.publishing.article_renderer import emphasis_ranges, render_article, text_hash


def test_emphasis_preserves_exact_text_and_escapes_markup():
    body = "## 实验边界\n只在指定条件下，A < B & C；仍需人工处理故障。\n😀 结果值得关注。"
    emphasis = ArticleEmphasis(
        source_body_hash=text_hash(body),
        items=[
            TextEmphasis(paragraph=2, quote="只在指定条件下，A < B & C"),
            TextEmphasis(paragraph=3, quote="😀"),
        ],
    )
    web, wechat = render_article("测试", body, [], [], emphasis)
    for markup in (web, wechat):
        soup = BeautifulSoup(markup, "html.parser")
        assert soup.find("h2").get_text() == "实验边界"
        assert [p.get_text() for p in soup.select("p")] == body.splitlines()[1:]
        assert [s.get_text() for s in soup.select("strong")] == ["只在指定条件下，A < B & C", "😀"]
        assert soup.find("script") is None


@pytest.mark.parametrize(
    "items",
    [
        [{"paragraph": 4, "quote": "结果"}],
        [{"paragraph": 1, "quote": "标题"}],
        [{"paragraph": 2, "quote": "相同"}],
        [{"paragraph": 3, "quote": "不存在"}],
        [{"paragraph": 3, "quote": "唯一结果"}, {"paragraph": 3, "quote": "结果"}],
    ],
)
def test_invalid_emphasis_is_rejected(items):
    body = "## 标题\n相同 相同\n唯一结果"
    with pytest.raises(ValueError):
        emphasis_ranges(body, ArticleEmphasis(source_body_hash=text_hash(body), items=items))


def test_stale_emphasis_and_legacy_presentation():
    with pytest.raises(ValueError, match="版本"):
        emphasis_ranges(
            "new",
            ArticleEmphasis(source_body_hash=text_hash("old"), items=[{"paragraph": 1, "quote": "new"}]),
        )
    old = ArticlePresentation(
        wechat_html="old", wechat_hash=text_hash("old"), source_body_hash=text_hash("body")
    )
    assert "emphasis" not in old.model_dump()
    assert (
        ArticlePresentation.model_validate_json(old.model_dump_json()).model_dump_json()
        == old.model_dump_json()
    )
