"""保守确认研究 HTML 的正文范围；付费预览和验证页不能视为全文。"""

from bs4 import BeautifulSoup


def primary_scope(body: bytes, content_type: str) -> str:
    """只接受有独立正文和研究章节的 HTML；无法确认的格式保持页面级证据。"""
    if "html" not in content_type:
        return "primary_page"
    soup = BeautifulSoup(body, "html.parser")
    if soup.select_one(".paywall, .c-article-buy-box, #challenge-running, #cf-challenge-running"):
        return "primary_page"
    article = soup.select_one("article")
    if article is None:
        return "primary_page"
    text = article.get_text(" ", strip=True)
    headings = {node.get_text(" ", strip=True).casefold() for node in article.select("h2, h3")}
    methods = any(
        value.startswith(("methods", "materials and methods", "experimental")) for value in headings
    )
    findings = any(value.startswith(("results", "discussion", "main")) for value in headings)
    if methods and findings and 3000 <= len(text) < 60000:
        return "primary_fulltext"
    return "primary_page"
