"""从来源明确提供的 URL 和 DOI 提取文献标识符，不按标题猜测。"""

import re
from urllib.parse import unquote, urlparse


def paper_identifier(url: str, doi: str = "") -> str:
    """只提取来源明确提供的 DOI/arXiv ID，不从标题猜测标识符。"""
    if doi:
        return doi.removeprefix("https://doi.org/")
    decoded = unquote(url).split("?")[0].split("#")[0]
    if urlparse(decoded).hostname in {"nature.com", "www.nature.com"} and urlparse(decoded).path.startswith(
        "/articles/"
    ):
        return "10.1038/" + decoded.split("/articles/", 1)[1].removesuffix(".pdf")
    match = re.search(r"10\.\d{4,9}/[^\s]+", decoded)
    if match:
        return match[0]
    match = re.search(r"arxiv.org/(?:abs|pdf)/(\d{4}\.\d{4,5}(?:v\d+)?)", decoded)
    return match[1] if match else ""
