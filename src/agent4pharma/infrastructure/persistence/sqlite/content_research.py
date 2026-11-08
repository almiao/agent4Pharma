"""内容调查的有限本地资料检索，不把元数据冒充全文。"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from agent4pharma.infrastructure.persistence.sqlite.workbench_connection import transaction
from agent4pharma.modules.content.draft_contracts import ResearchEvidence


@dataclass
class ContentResearchRepository:
    """只读本地主库元数据，搜索结果按实际快照提供引用 ID。"""

    root: Path

    def search(self, query: str) -> list[ResearchEvidence]:
        """标题/元数据文本匹配，最多十条；不是全文语义检索或全网召回。"""
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = "%" + escaped + "%"
        with transaction(self.root) as connection:
            rows = connection.execute(
                "SELECT m.title,r.metadata,r.id FROM materials m JOIN source_records r ON r.material_id=m.id "
                "WHERE m.title LIKE ? ESCAPE '\\' OR r.metadata LIKE ? ESCAPE '\\' "
                "ORDER BY r.rowid DESC LIMIT 10",
                (pattern, pattern),
            ).fetchall()
        results = []
        for row in rows:
            metadata = json.loads(row["metadata"])
            content = metadata.get("content") or {}
            text = str(content.get("text") or metadata.get("abstract") or metadata.get("summary") or "")[
                :60000
            ]
            if not text:
                continue
            identity = "research-" + hashlib.sha256((str(row["id"]) + text).encode()).hexdigest()[:24]
            results.append(
                ResearchEvidence(
                    id=identity,
                    title=row["title"],
                    text=text,
                    url=str(metadata.get("url") or ""),
                    origin="local_fulltext"
                    if content.get("access_state") == "fulltext"
                    else "local_metadata",
                    fulltext=content.get("access_state") == "fulltext",
                )
            )
        return results
