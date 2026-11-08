"""只提取条目内明确标记的作者单位，保留字段出处，不推断作者或出版商身份。"""

from typing import Literal
from xml.etree.ElementTree import Element

from bs4 import BeautifulSoup

from agent4pharma.infrastructure.connectors.rss_signals import clean_signal
from agent4pharma.modules.materials.collection_records import AuthorAffiliationEvidence

AFFILIATION_EXTRACTION_VERSION = "rss-affiliations-v1"
_AFFILIATION_TAGS = {
    "aff",
    "affiliation",
    "affiliations",
    "authoraffiliation",
    "authorinstitution",
    "authororganization",
    "authororganisation",
}
_AUTHOR_ORGANIZATION_TAGS = {"institution", "organization", "organisation", "orgname"}
_HTML_FIELDS = {"description", "summary", "content", "encoded"}


def _name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower().replace("-", "").replace("_", "")


def extract_author_affiliations(entry: Element) -> list[AuthorAffiliationEvidence]:
    """读取显式 XML、author 属性和 HTML 单位标签；无字段时返回空，不补抓网页。

    仅遍历单个 item/entry；publisher/source/channel 的单位不属于作者证据。
    返回条目级单位，不声称已经完成作者与单位的逐人对应。
    """
    found: list[AuthorAffiliationEvidence] = []

    def append(field: str, value: str, origin: Literal["rss_xml", "rss_html"]) -> None:
        text = clean_signal(value)
        if text and not any(e.text == text for e in found):
            found.append(AuthorAffiliationEvidence(field=field, text=text, origin=origin))

    def visit(node: Element, path: str, in_author: bool = False) -> None:
        name = _name(node.tag)
        if name in {"publisher", "source", "channel", "rights", "copyright"}:
            return
        in_author = in_author or name in {"author", "creator", "contrib"}
        if name in _AFFILIATION_TAGS or in_author and name in _AUTHOR_ORGANIZATION_TAGS:
            append(path, " ".join(node.itertext()), "rss_xml")
            return  # 父单位已包含院系/机构，避免再把子节点重复当另一家单位。
        if in_author:
            for attribute, value in node.attrib.items():
                if _name(attribute) in _AFFILIATION_TAGS:
                    append(path + "/@" + attribute, value, "rss_xml")
        if name in _HTML_FIELDS:
            soup = BeautifulSoup("".join(node.itertext()), "html.parser")
            for unsafe in soup.select("script, style, noscript"):
                unsafe.decompose()
            for element in soup.select(
                ".affiliation, .affiliations, .author-affiliation, .author_affiliation, "
                '[itemprop="affiliation"], [data-affiliation]'
            ):
                append(
                    path + "/html:affiliation",
                    element.get("data-affiliation") or element.get_text(" ", strip=True),
                    "rss_html",
                )
        for child in node:
            visit(child, path + "/" + child.tag, in_author)

    for child in entry:
        visit(child, child.tag)
    return found
