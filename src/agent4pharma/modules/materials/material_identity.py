"""资料身份规则：规范化 DOI，缺少 DOI 时保留来源与外部 ID 的组合身份。"""


def identity(source: str, external_id: str, ids: dict) -> str:
    """优先使用规范化 DOI 作为资料身份，否则使用来源键与外部 ID 的组合。"""
    doi = ids.get("doi", "").strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix) :].strip()
    if doi:
        return f"doi:{doi}"
    return f"{source}:{external_id}"
