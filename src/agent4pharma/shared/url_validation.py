"""持久化公开入口前的纯 URL 校验；不做 DNS 或网络请求。"""

from urllib.parse import parse_qsl, urlsplit


def public_reference_url(value: str) -> str:
    """只接受不带内嵌凭证或常见密钥查询字段的 HTTP(S) 地址，允许空值。"""
    if not value:
        return value
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("请提供无内嵌凭证的 HTTP/HTTPS URL")
    sensitive = {"key", "api_key", "apikey", "token", "access_token", "password", "secret", "signature"}
    if any(key.casefold().replace("-", "_") in sensitive for key, _ in parse_qsl(parts.query)):
        raise ValueError("请移除 URL 中的凭证/签名参数，使用公开文章永久链接")
    return value
