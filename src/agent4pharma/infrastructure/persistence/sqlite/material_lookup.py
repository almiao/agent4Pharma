"""只读查询主库和既有 smoke 库的精确资料身份，不升级历史 smoke 数据库。"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from agent4pharma.modules.maintenance.identifiers import identifiers_from_text, normalized_url, reference_url
from agent4pharma.modules.maintenance.models import LocalMaterialMatch


@dataclass
class LocalMaterialLookup:
    """匹配标准 ID/URL；明确 demo 和历史试跑，不把近似标题当成相同研究。"""

    root: Path

    def find_material(self, reference: str) -> list[LocalMaterialMatch]:
        """在固定允许的两个库中精确匹配，不接受调用方提供任意数据库路径。"""
        url = reference_url(reference)
        identifiers = identifiers_from_text(reference + " " + (url or ""))
        matches = []
        for label, path in (
            ("main", self.root / "discovery.sqlite3"),
            ("smoke", self.root / "smoke" / "discovery.sqlite3"),
        ):
            if path.is_file():
                matches.extend(self._find(path, label, url, identifiers))
        return matches

    def _find(
        self, path: Path, label: str, url: str | None, identifiers: list[str]
    ) -> list[LocalMaterialMatch]:
        db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA temp_store=MEMORY")
        try:
            columns = {r[1] for r in db.execute("PRAGMA table_info(materials)")}
            dataset = "m.dataset" if "dataset" in columns else "'legacy_smoke'"
            clauses, values = [], []
            for identity in identifiers:
                kind, external = identity.split(":", 1)
                clauses.append("(lower(m.identity)=? OR r.external_id=?)")
                values += [identity.casefold(), external]
                if kind in {"doi", "pmid", "arxiv"}:
                    clauses.append(f"lower(json_extract(m.identifiers,'$.{kind}'))=?")
                    values.append(external.casefold())
            if url:
                variants = list(dict.fromkeys([url, normalized_url(url), normalized_url(url) + "/"]))
                for value in variants:
                    clauses.append("json_extract(r.metadata,'$.url')=?")
                    values.append(value)
                    clauses.append("m.identity=?")
                    values.append("demo:url:" + value)
                    if "canonical_url" in columns:
                        clauses.append("m.canonical_url=?")
                        values.append(value)
            if not clauses:
                return []
            rows = db.execute(
                f"SELECT DISTINCT m.id,m.title,m.identity,{dataset} AS dataset FROM materials m "
                "LEFT JOIN source_records r ON r.material_id=m.id WHERE "
                + " OR ".join(clauses)
                + " LIMIT 30",
                values,
            ).fetchall()
            return [
                LocalMaterialMatch(
                    database=label,
                    dataset=r["dataset"],
                    material_id=r["id"],
                    title=r["title"],
                    identity=r["identity"],
                    matched_by=reference_label(url, identifiers),
                )
                for r in rows
            ]
        finally:
            db.close()


def reference_label(url: str | None, identifiers: list[str]) -> str:
    """记录匹配请求类型，精确匹配仍需结合返回身份查看。"""
    return ", ".join(identifiers) or url or "unknown"
