"""RSS 语义输入提取：字段出处、正文清洗和信息质量保持可追溯。"""

import re
from html import unescape
from xml.etree.ElementTree import Element

from bs4 import BeautifulSoup

from agent4pharma.modules.materials.collection_records import RssSignal, RssTextSignal
from agent4pharma.modules.sources.collection_plans import RssExtractionProfile


def clean_signal(text: str) -> str:
    """清理实际 HTML 与空白，不执行脚本或根据标题生成摘要。"""
    if "<" not in text:
        return " ".join(unescape(text).split())
    soup = BeautifulSoup(text, "html.parser")
    for node in soup.select("script,style,noscript"):
        node.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())


def bibliographic_only(text: str) -> bool:
    """识别没有研究内容的卷期页码/DOI 信息；不能用长度判定完整摘要。"""
    value = clean_signal(text)
    return (
        not value
        or bool(re.fullmatch(r"Publication date:.*?Source:.*?Author\(s\):.*", value, re.IGNORECASE))
        or bool(re.fullmatch(r".+?,\s*Volume\s+\S+,\s*Issue\s+.+", value, re.IGNORECASE) and len(value) < 180)
    )


def extract_signals(entry: Element, profile: RssExtractionProfile) -> RssSignal:
    """按已登记字段提取带出处的非重复文本，原文仍由原始 Feed 产物保存。"""
    fields: list[RssTextSignal] = []
    categories: list[str] = []
    raw: dict[str, list[str]] = {}
    for node in entry:
        name = node.tag.rsplit("}", 1)[-1]
        value = "".join(node.itertext()).strip() or node.attrib.get("term", "")
        raw.setdefault(name, []).append(value)
        if name in profile.category_fields and value:
            categories.append(clean_signal(value))
        if name not in profile.text_fields:
            continue
        text = clean_signal(value)
        if bibliographic_only(text):
            continue
        # Nature 将题录前缀与导读放在同一 encoded 中，剥离前缀而不造新内容。
        text = re.sub(r"^.*?Published online:.*?doi:\s*10\.\S+\s+", "", text, count=1)
        if text and not any(text == field["text"] for field in fields):
            fields.append({"field": name, "text": text[:12000], "truncated": len(text) > 12000})
    fields = [
        field
        for field in fields
        if not any(field["text"] != other["text"] and field["text"] in other["text"] for other in fields)
    ]
    return {
        "journal": profile.journal_name,
        "profile_version": profile.version,
        "fields": fields,
        "categories": list(dict.fromkeys(categories)),
        "article_types": raw.get("type", []),
        "information_status": "rss_text_available" if fields else "title_only",
    }
