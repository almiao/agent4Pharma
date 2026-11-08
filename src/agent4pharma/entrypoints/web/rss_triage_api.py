"""本地预选只读管理接口，共用现有工作台同源和令牌保护。"""

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from pydantic import JsonValue

from agent4pharma.composition import rss_triage_view


def get_rss_triage(root: Path, url: str) -> dict[str, JsonValue]:
    """读取固定查询参数，不允许任意路径、SQL 或模型请求。"""
    parts = urlsplit(url)
    if parts.path != "/api/rss-triage":
        raise KeyError("本地预选接口不存在")
    query = parse_qs(parts.query)
    return rss_triage_view(
        root,
        query.get("batch", [""])[0],
        view=query.get("view", [""])[0],
        run_id=query.get("run", [""])[0],
    )
