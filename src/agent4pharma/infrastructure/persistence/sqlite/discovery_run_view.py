"""发现批次只读投影；资料通过资料仓储取快照，来源任务通过任务仓储读取。"""

import json
from dataclasses import dataclass
from pathlib import Path

from agent4pharma.infrastructure.persistence.sqlite.job_repository import JobRepository
from agent4pharma.infrastructure.persistence.sqlite.material_repository import MaterialRepository
from agent4pharma.infrastructure.persistence.sqlite.workbench_connection import transaction
from agent4pharma.modules.collection.acquisition_interfaces import CollectionDiagnostic
from agent4pharma.modules.discovery.run_duplicates import annotate_run_duplicates
from agent4pharma.modules.discovery.run_records import DiscoveryRunSnapshot, RunArticle, RunSource
from agent4pharma.modules.materials.collection_records import SourceRecordReading
from agent4pharma.modules.sources.collection_plans import CollectionPlan
from agent4pharma.modules.sources.source_categories import source_category


@dataclass
class DiscoveryRunReader:
    """读取批次目录和完整来源成员，包括没有任何成功预选结果的批次。"""

    root: Path

    def article_reading(self, batch_id: str, record_id: str, offset: int = 0) -> SourceRecordReading:
        """只读取本批次实际成员的归档原文；越界或不存在的来源版本抛出 KeyError。"""
        with transaction(self.root) as db:
            if (
                db.execute(
                    "SELECT 1 FROM discovery_batch_items WHERE batch_id=? AND source_record_id=?",
                    (batch_id, record_id),
                ).fetchone()
                is None
            ):
                raise KeyError("来源记录不属于当前批次")
            return MaterialRepository(self.root, db).source_record_reading(record_id, offset)

    def snapshot(self, batch_id: str = "") -> DiscoveryRunSnapshot:
        """最新目录限 50 项；明确指定的历史批次可读取，不截断该批次成员。"""
        with transaction(self.root) as db:
            batches = [
                r[0] for r in db.execute("SELECT id FROM discovery_batches ORDER BY rowid DESC LIMIT 50")
            ]
            imports = dict(
                db.execute(
                    "SELECT json_extract(j.request,'$.origin_batch'),b.id FROM discovery_batches b "
                    "JOIN jobs owner ON owner.id=b.run_id JOIN jobs j ON j.parent_id=owner.id "
                    "WHERE owner.kind='archive_import' AND j.kind='collect' "
                    "AND json_extract(j.request,'$.origin_batch') IS NOT NULL ORDER BY b.rowid"
                ).fetchall()
            )
            selected = batch_id or next((identity for identity in batches if identity not in imports), "")
            if not selected:
                return DiscoveryRunSnapshot()
            batch = db.execute("SELECT run_id FROM discovery_batches WHERE id=?", (selected,)).fetchone()
            if batch is None:
                raise KeyError("发现批次不存在")
            if selected not in batches:
                batches.append(selected)
            jobs = JobRepository(self.root, db)
            policy = jobs.job(batch[0])["request"].get("policy", {})
            batch_kind = jobs.job(batch[0])["kind"]
            materials = MaterialRepository(self.root, db)
            articles = []
            members = db.execute(
                "SELECT i.*,l.decision_id,e.error AS triage_error FROM discovery_batch_items i "
                "LEFT JOIN rss_triage_links l ON l.batch_id=i.batch_id "
                "AND l.source_record_id=i.source_record_id "
                "LEFT JOIN rss_triage_errors e ON e.batch_id=i.batch_id "
                "AND e.source_record_id=i.source_record_id "
                "WHERE i.batch_id=? ORDER BY i.id",
                (selected,),
            ).fetchall()
            records = materials.source_record_snapshots([row["source_record_id"] for row in members])
            for row in members:
                record = records[row["source_record_id"]]
                assessment = json.loads(row["assessment"] or "{}")
                articles.append(
                    RunArticle(
                        id=row["source_record_id"],
                        material_id=row["material_id"],
                        source_id=record.source_id,
                        title=record.title,
                        url=record.url,
                        state=row["state"],
                        member_kind=row["kind"],
                        category=source_category(
                            record.source_id,
                            url=record.url,
                            material_type=str(record.metadata.get("material_type", "")),
                            journal=str((record.metadata.get("rss_signal") or {}).get("journal", "")),
                        ),
                        triage_id=row["decision_id"] or "",
                        route=assessment.get("local_route", ""),
                        triage_error=row["triage_error"] or "",
                        triage_method=(
                            "local_model"
                            if row["decision_id"]
                            else "semantic_passthrough"
                            if assessment.get("method") == "semantic_passthrough"
                            or (
                                assessment.get("coarse_mode") == "semantic_only"
                                and assessment.get("method") == "keyword_gate"
                            )
                            else "keyword_gate"
                            if assessment.get("method") == "keyword_gate"
                            else "unknown"
                        ),
                        intelligence_task_id=assessment.get("intelligence_task", ""),
                    )
                )
            annotate_run_duplicates(articles)
            articles_by_source: dict[str, list[RunArticle]] = {}
            for article in articles:
                articles_by_source.setdefault(article.source_id, []).append(article)
            sources = []
            source_jobs = jobs.collection_jobs(batch[0])
            by_source = {job["request"]["source"]: job for job in source_jobs}
            for source in sorted(set(policy.get("sources", {})) | set(by_source)):
                job = by_source.get(source)
                settings = policy.get("sources", {}).get(source, {})
                frozen_url = settings.get("feed_url", "")
                if settings.get("plan_json"):
                    frozen_url = CollectionPlan.model_validate_json(settings["plan_json"]).entries[0].url
                request_url = job["request"].get("url", "") if job else ""
                members = articles_by_source.get(source, [])
                count_known = bool(
                    job and (members or "total" in (job["result"] or {}) or job["status"] == "complete")
                )
                sources.append(
                    RunSource(
                        source_id=source,
                        job_id=job["id"] if job else "",
                        status=job["status"] if job else "not_started",
                        records=len(members) if count_known else None,
                        unique_materials=len({a.material_id for a in members}) if count_known else None,
                        error=(job["error"] or "") if job else "",
                        attempt=job["attempt"] if job else 0,
                        request_url=request_url or frozen_url,
                        request_url_basis="recorded"
                        if request_url
                        else "frozen_plan"
                        if frozen_url
                        else "unknown",
                        updated_at=job["updated_at"] if job else "",
                        collection_mode=job["request"].get("mode", "") if job else "",
                        diagnostic=CollectionDiagnostic.model_validate(job["result"]["diagnostic"])
                        if job and job["result"] and job["result"].get("diagnostic")
                        else None,
                        start=job["request"].get("start", "") if job else "",
                        end=job["request"].get("end", "") if job else "",
                    )
                )
            return DiscoveryRunSnapshot(
                selected=selected,
                aggregate_batch=imports.get(selected, ""),
                batch_kind=batch_kind,
                batches=batches,
                sources=sources,
                articles=articles,
            )
