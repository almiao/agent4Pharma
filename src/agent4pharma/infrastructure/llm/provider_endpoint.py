"""DSH DeepSeek Messages 端点适配，与 OpenAI 兼容 API 根地址分别处理。"""

from urllib.parse import urlsplit, urlunsplit


def dsh_base_url(base_url: str) -> str:
    """官方 OpenAI 根地址映射到 DSH 所需 Messages 根；自定义网关保持原样。

    固定的 DSH 0.2.1rc2 追加 /v1/messages。把官方 / 或 /v1 根直接传入会 404。
    不改变模型、凭证、供应商或自定义路径，不尝试其他网络目的地。
    """
    parts = urlsplit(base_url)
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("模型端点不允许内嵌凭证、查询或片段")
    if (
        parts.scheme == "https"
        and parts.netloc == "api.deepseek.com"
        and parts.path.rstrip("/") in {"", "/v1"}
    ):
        return urlunsplit((parts.scheme, parts.netloc, "/anthropic", "", ""))
    return base_url
