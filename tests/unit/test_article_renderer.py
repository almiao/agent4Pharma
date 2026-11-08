from html.parser import HTMLParser

from agent4pharma.modules.publishing.article_renderer import render_article


def test_article_renderer_preserves_text_and_escapes_injection():
    title = "<script>alert(1)</script>"
    body = "正文 <img src=x onerror=alert(1)> & 原值\n\n第二段，17%。"
    web, wechat = render_article(title, body, [], [("来源", "javascript:alert(1)")])
    assert "<script>" not in web and "<img src=x" not in web
    assert 'href="javascript:' not in web
    assert "<style>" in web and "<style>" not in wechat

    class Text(HTMLParser):
        def __init__(self):
            super().__init__()
            self.text = []

        def handle_data(self, data):
            self.text.append(data)

    parsed = Text()
    parsed.feed(wechat)
    for paragraph in body.split("\n\n"):
        assert paragraph in parsed.text


def test_headings_are_safe_in_both_channels_and_keep_line_positions():
    from agent4pharma.modules.publishing.article_renderer import article_paragraphs

    body = """开场

## 结果 <script>alert(1)</script>
正文
### 普通文本"""
    web, wechat = render_article("标题", body, [], [])
    assert article_paragraphs(body)[2] == "正文"
    assert "<h2>结果 &lt;script&gt;alert(1)&lt;/script&gt;</h2>" in web
    assert "<h2 style=" in wechat and "<script>" not in wechat
    assert "<p>### 普通文本</p>" in web
