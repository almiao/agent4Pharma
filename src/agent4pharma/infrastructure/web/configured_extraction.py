"""无站点分支的 CSS 字段、公开正文、日期精度及稳定 URL 提取。"""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import soupsieve
from bs4 import BeautifulSoup, Tag

from agent4pharma.modules.materials.collection_records import CollectedLink
from agent4pharma.modules.sources.collection_plans import CollectionPlan, ExtractField
from agent4pharma.shared.url_validation import public_reference_url


def normalize_url(url: str) -> str:
    """仅移除常见跟踪参数和片段，不删除可能决定文章身份的业务参数。"""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_")]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", urlencode(query), ""))


def extract_field(document: BeautifulSoup | Tag, rule: ExtractField) -> str:
    """按配置顺序返回第一个非空字段；不执行脚本或 eval。"""
    for selector in rule.selectors:
        for node in document.select(selector):
            value = node.get(rule.attribute, "") if rule.attribute else node.get_text(" ", strip=True)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def timestamp(raw: str) -> str | None:
    """只将带明确时区的时间点转换为 UTC；日期和无时区值保持未知。"""
    if not raw:
        return None
    try:
        point = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            point = parsedate_to_datetime(raw)
        except (ValueError, TypeError):
            return None
    return point.astimezone(timezone.utc).isoformat() if point.tzinfo else None


def date_precision(raw: str) -> str:
    """区分带时区时间点、仅日期、无时区时间和未知输入，原文另存。"""
    if timestamp(raw):
        return "instant"
    try:
        datetime.strptime(raw, "%Y-%m-%d")
        return "day"
    except ValueError:
        return "unknown"


def validate_selectors(plan: CollectionPlan) -> None:
    """导入前编译全部选择器，配置拼写错误不能等到定时执行才发现。"""
    rules = plan.article
    if not rules.body.selectors:
        raise ValueError("正文必须配置至少一个 CSS 提取规则")
    for entry in plan.entries:
        allowed_url(entry.url, entry.url, plan)
    selectors = [s for entry in plan.entries for s in [entry.link_selector, entry.next_selector] if s]
    for name in ("title", "body", "canonical", "external_id", "author", "published", "modified"):
        selectors.extend(getattr(rules, name).selectors)
    selectors.extend(rules.remove_selectors + rules.paywall_selectors)
    for selector in selectors:
        try:
            soupsieve.compile(selector)
        except soupsieve.SelectorSyntaxError:
            raise ValueError("无效的 CSS 提取规则") from None


def allowed_url(url: str, base: str, plan: CollectionPlan) -> str:
    """解析相对链接并限制为配置内的文章域名。"""
    resolved = normalize_url(urljoin(base, url))
    public_reference_url(resolved)
    parts = urlsplit(resolved)
    if parts.scheme not in {"https", "http"} or parts.hostname not in plan.allowed_hosts:
        raise ValueError("链接不在采集配置允许的公开站点范围内")
    return resolved


def body_links(document: BeautifulSoup, rule: ExtractField, base: str) -> list[CollectedLink]:
    """只保留正文区域内无凭证的公开外链，不访问链接也不合并论文身份。"""
    links = {}
    for selector in rule.selectors:
        node = document.select_one(selector)
        if node is None:
            continue
        for anchor in node.select("a[href]"):
            url = normalize_url(urljoin(base, str(anchor["href"])))
            try:
                public_reference_url(url)
            except ValueError:
                continue
            links.setdefault(url, CollectedLink(url, anchor.get_text(" ", strip=True)[:300]))
        break
    return list(links.values())[:150]
