"""从固定批次来源构造精选输入，不执行或写回旧情报评估。"""

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import JsonValue

from agent4pharma.infrastructure.persistence.sqlite.workbench_connection import transaction
from agent4pharma.modules.discovery.intelligence_contracts import (
    IntelligenceEvidence,
    IntelligencePolicyVersion,
    IntelligenceRequest,
)
from agent4pharma.modules.discovery.paper_selection import PaperSelectionDetails


@dataclass
class BatchIntelligenceRepository:
    """读取批次来源与执行快照，为当前精选提供冻结输入。"""

    root: Path

    def selection_details(self, batch_id: str) -> dict[str, PaperSelectionDetails]:
        """离线读取已存载体；历史 PubMed 缺期刊时从归档 XML 恢复，不改原始记录。"""
        import xml.etree.ElementTree as ET

        with transaction(self.root) as db:
            rows = db.execute(
                "SELECT i.source_record_id,r.source_id,r.metadata FROM discovery_batch_items i "
                "JOIN source_records r ON r.id=i.source_record_id WHERE i.batch_id=?",
                (batch_id,),
            ).fetchall()
            raw = db.execute(
                "SELECT storage_uri,provenance FROM artifacts WHERE kind='raw_response'"
            ).fetchall()
        journals = {}
        affiliations: dict[str, list[str]] = {}
        for row in raw:
            if "efetch.fcgi" not in json.loads(row["provenance"]).get("url", ""):
                continue
            path = (self.root / row["storage_uri"]).resolve()
            if not path.is_relative_to(self.root.resolve()) or not path.is_file():
                continue
            try:
                document = ET.fromstring(path.read_bytes())
            except ET.ParseError:
                continue
            for article in document.findall(".//PubmedArticle"):
                pmid = article.findtext("./MedlineCitation/PMID")
                journals[pmid] = article.findtext("./MedlineCitation/Article/Journal/Title", "")
                affiliations[pmid] = list(
                    dict.fromkeys(
                        "".join(a.itertext()).strip()
                        for a in article.findall(
                            "./MedlineCitation/Article/AuthorList/Author/AffiliationInfo/Affiliation"
                        )
                        if "".join(a.itertext()).strip()
                    )
                )
        result = {}
        for row in rows:
            record = json.loads(row["metadata"])
            metadata = record.get("metadata") or {}
            journal = (
                metadata.get("journal")
                or (metadata.get("rss_signal") or {}).get("journal")
                or journals.get((record.get("identifiers") or {}).get("pmid"), "")
            )
            result[row["source_record_id"]] = PaperSelectionDetails(
                source_id=row["source_id"],
                journal=journal,
                affiliations=metadata.get("affiliations")
                or affiliations.get((record.get("identifiers") or {}).get("pmid"), []),
            )
        return result

    def remaining(self, batch_id: str) -> int:
        """没有待精筛材料时无需凭证或模型请求。"""
        with transaction(self.root) as db:
            return db.execute(
                "SELECT count(*) FROM discovery_batch_items WHERE batch_id=? "
                "AND state='awaiting_intelligence'",
                (batch_id,),
            ).fetchone()[0]

    def pending(
        self, batch_id: str, policy: IntelligencePolicyVersion, model: str, limit: int
    ) -> list[IntelligenceRequest]:
        """选每份实际来源版本的待判断资料，有限预算内返回。"""
        with transaction(self.root) as db:
            rows = db.execute(
                "SELECT DISTINCT i.material_id,i.source_record_id,i.assessment,r.metadata "
                "FROM discovery_batch_items i "
                "JOIN source_records r ON r.id=i.source_record_id "
                "WHERE i.batch_id=? AND i.state='awaiting_intelligence' ORDER BY i.id LIMIT ?",
                (batch_id, limit),
            ).fetchall()
            history = [
                r[0]
                for r in db.execute("SELECT card FROM editorial_candidates ORDER BY created_at DESC LIMIT 20")
            ]
        frozen = self.execution_snapshot(batch_id)
        requests = []
        for row in rows:
            data = json.loads(row["metadata"])
            metadata = data.get("metadata") or {}
            native = data.get("abstract", "") if metadata.get("abstract_status") == "found" else ""
            requests.append(
                IntelligenceRequest(
                    material_ref=row["material_id"],
                    mode="production",
                    evidence=[
                        IntelligenceEvidence(
                            id=row["source_record_id"],
                            title=data["title"],
                            text=(data.get("content") or {}).get("text", data.get("abstract", ""))[:60000],
                            url=data.get("url", ""),
                            doi=(data.get("identifiers") or {}).get("doi", ""),
                            source_abstract=native[:60000],
                            abstract_status="found" if native else None,
                            abstract_source=metadata.get("abstract_source", "") if native else "",
                            origin="source_" + data["content"]["access_state"]
                            if data.get("content")
                            else "source_metadata",
                        )
                    ],
                    policy=policy,
                    model=frozen.get("intelligence_model") or model,
                    model_endpoint=frozen.get("intelligence_endpoint") or "https://api.deepseek.com",
                    model_max_tokens=frozen.get("intelligence_max_tokens", 4096),
                    parent_ref=batch_id,
                    history=history,
                )
            )
        return requests

    def execution_snapshot(self, batch_id: str) -> dict[str, JsonValue]:
        """读取采集时冻结的执行配置，重跑不静默换模型或供应地址。"""
        with transaction(self.root) as db:
            row = db.execute(
                "SELECT j.request FROM jobs j JOIN discovery_batches b ON b.run_id=j.id WHERE b.id=?",
                (batch_id,),
            ).fetchone()
        if row is None:
            raise KeyError("批次不存在")
        return json.loads(row[0])["policy"]
