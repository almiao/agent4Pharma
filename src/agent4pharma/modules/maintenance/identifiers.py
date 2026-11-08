"""文章调查中的标准身份解析，不进行网络请求或模糊身份合并。"""

import re
from urllib.parse import unquote, urlsplit, urlunsplit


def identifiers_from_text(text: str) -> list[str]:
    """抽取 DOI、PMID 和 arXiv ID；只规范格式，不声称来源已收录。"""
    decoded = unquote(text)
    result = []
    for value in re.findall(r"10\.\d{4,9}/[^\s<>\"'\[\]?#]+", decoded, re.I):
        value = value.rstrip(".,;:")
        while value.endswith(")") and value.count(")") > value.count("("):
            value = value[:-1]
        result.append("doi:" + value.casefold())
    for value in re.findall(r"(?:pubmed\.ncbi\.nlm\.nih\.gov/|pmid\s*:\s*)(\d+)", decoded, re.I):
        result.append("pmid:" + value)
    for value in re.findall(
        r"(?:arxiv\.org/(?:abs|pdf)/|arxiv\s*:\s*)([a-z.-]+/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?", decoded, re.I
    ):
        result.append("arxiv:" + value.casefold())
    return list(dict.fromkeys(result))


def reference_url(reference: str) -> str | None:
    """把 URL 或标准 ID 转成访问入口；纯标题不伪造 URL。"""
    reference = reference.strip()
    if reference.startswith(("https://", "http://")):
        return reference
    identifiers = identifiers_from_text(reference)
    if not identifiers and re.fullmatch(r"\d{4}\.\d{4,5}(v\d+)?", reference):
        identifiers = ["arxiv:" + re.sub(r"v\d+$", "", reference)]
    if not identifiers and reference.isdigit():
        identifiers = ["pmid:" + reference]
    if not identifiers:
        return None
    kind, value = identifiers[0].split(":", 1)
    return {
        "doi": "https://doi.org/",
        "pmid": "https://pubmed.ncbi.nlm.nih.gov/",
        "arxiv": "https://arxiv.org/abs/",
    }[kind] + value


def normalized_url(url: str) -> str:
    """保留查询范围并规范 URL 表面形式，供精确 URL 比较使用。"""
    parts = urlsplit(url)
    return urlunsplit(
        (parts.scheme.casefold(), parts.netloc.casefold(), parts.path.rstrip("/"), parts.query, "")
    )
