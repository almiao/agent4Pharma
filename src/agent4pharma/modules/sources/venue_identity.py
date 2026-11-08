"""Conservative venue identity normalization."""

import hashlib
import html
import re
import unicodedata


def normalize_name(name: str) -> str:
    """保守规范化载体显示名的 Unicode、实体和空白，不按相似度合并不同刊物。"""
    value = unicodedata.normalize("NFKC", html.unescape(name)).casefold()
    value = value.replace("’", "'").replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", value).strip().rstrip(".")


def venue_id(name: str) -> str:
    """从规范化载体名称派生稳定 ID；显式别名关系由目录仓储维护。"""
    return "venue-" + hashlib.sha256(normalize_name(name).encode()).hexdigest()[:24]
