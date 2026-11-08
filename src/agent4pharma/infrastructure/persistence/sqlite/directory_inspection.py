"""目录事实核对：资料、期刊登记与媒体关注状态分开，绝不把 demo 算正式关注。"""

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from agent4pharma.infrastructure.persistence.sqlite.workbench_connection import transaction
from agent4pharma.modules.sources.management import DirectoryInspection


def normalized_host(value: str) -> str:
    """仅去除常见 www 前缀，不能以主域相似自动合并机构。"""
    return (urlsplit(value).hostname or "").lower().removeprefix("www.")


@dataclass
class DirectoryInspector:
    """只读目录核对，不修改媒体与期刊身份。"""

    root: Path

    def inspect(self, reference: str) -> DirectoryInspection:
        """返回精确同域来源和同名载体，目录关注不等于已采集。"""
        host = normalized_host(reference)
        with transaction(self.root) as db:
            sources = []
            for row in db.execute("SELECT * FROM sources"):
                endpoints = [
                    dict(r)
                    for r in db.execute(
                        "SELECT id,url,access_method,connector_key,enabled FROM "
                        "source_endpoints WHERE source_id=?",
                        (row["id"],),
                    )
                ]
                if (
                    host
                    and any(
                        normalized_host(u) == host
                        for u in [row["homepage_url"] or "", *[e["url"] for e in endpoints]]
                    )
                ) or (not host and reference.strip().casefold() == (row["name"] or "").casefold()):
                    descriptor = json.loads(row["descriptor"])
                    sources.append(
                        {
                            "id": row["id"],
                            "name": row["name"],
                            "status": row["status"],
                            "revision": row["management_revision"],
                            "dataset": descriptor.get("dataset", "main"),
                            "followed": row["status"] == "watch" and descriptor.get("dataset") != "demo",
                            "endpoints": endpoints,
                        }
                    )
            venues = [
                dict(r)
                for r in db.execute(
                    "SELECT id,name,kind,watch_status,homepage_url FROM venues WHERE lower(name)=lower(?) "
                    "OR (homepage_url IS NOT NULL AND homepage_url=?)",
                    (reference.strip(), reference.strip()),
                )
            ]
        return DirectoryInspection(reference=reference, sources=sources, venues=venues)
