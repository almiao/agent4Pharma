"""Seed archived candidates for downstream tests without the retired evaluator."""

import hashlib
import json
import sqlite3

import pytest

from agent4pharma.composition import build_selection_policy
from agent4pharma.infrastructure.persistence.sqlite.batch_intelligence import BatchIntelligenceRepository
from agent4pharma.modules.discovery.intelligence_contracts import IntelligenceAssessment


def seed_archived_assessment(root, request, result):
    """Insert a historical fixture; this does not exercise or emulate model execution."""
    identity = "intel-fixture-" + hashlib.sha256(request.model_dump_json().encode()).hexdigest()[:24]
    payload = result.model_dump_json()
    with sqlite3.connect(root / "discovery.sqlite3") as db:
        db.execute(
            "INSERT OR REPLACE INTO intelligence_tasks(id,status,request,result,updated_at) "
            "VALUES (?,?,?,?,?)",
            (identity, "complete", request.model_dump_json(), payload, "2026-11-01T00:00:00+00:00"),
        )
        if result.disposition == "recommend":
            db.execute(
                "INSERT OR REPLACE INTO editorial_candidates "
                "(id,material_ref,task_id,policy_id,card,created_at) "
                "VALUES (?,?,?,?,?,?)",
                (
                    "candidate-" + identity,
                    request.material_ref,
                    identity,
                    request.policy.id,
                    payload,
                    "2026-11-01T00:00:00+00:00",
                ),
            )
    return identity


@pytest.fixture
def evaluate_offline():
    def seed(config, store, batch_id):
        adapter = BatchIntelligenceRepository(config.data_dir)
        for request in adapter.pending(
            batch_id, build_selection_policy(config.data_dir), config.llm.model, 100
        ):
            corpus = "\n".join(e.title + "\n" + e.text for e in request.evidence)
            ai = next((q for q in ["large language model", "LLM", "智能体"] if q in corpus), "")
            domain = next((q for q in ["drug discovery", "制药", "药物研发"] if q in corpus), "")
            result = IntelligenceAssessment(
                relevance="core" if ai and domain else "insufficient_information",
                disposition="recommend" if ai and domain else "needs_evidence",
                summary="Archived fixture",
                reason="Archived fixture evidence",
                evidence_ids=[e.id for e in request.evidence],
                ai_quote=ai,
                domain_quote=domain,
                attention_reason="Archived fixture attention" if ai and domain else "",
            )
            identity = seed_archived_assessment(config.data_dir, request, result)
            state = "selected" if result.disposition == "recommend" else "needs_evidence"
            with sqlite3.connect(config.data_dir / "discovery.sqlite3") as db:
                for row in db.execute(
                    "SELECT id,assessment FROM discovery_batch_items WHERE batch_id=? AND source_record_id=?",
                    (batch_id, request.evidence[0].id),
                ).fetchall():
                    assessment = json.loads(row[1])
                    assessment.update(
                        intelligence_task=identity,
                        intelligence=result.model_dump(mode="json"),
                        selected=state == "selected",
                        reason=result.reason,
                    )
                    db.execute(
                        "UPDATE discovery_batch_items SET state=?,assessment=? WHERE id=?",
                        (state, json.dumps(assessment), row[0]),
                    )
        return store.write_report(batch_id)

    return seed


def seed_archived_task(root, request, *, status="pending", error="", result=None):
    """Write a stored task fixture directly, with no retired execution behavior."""
    from agent4pharma.infrastructure.persistence.sqlite.intelligence_repository import IntelligenceRepository

    identity = "intel-fixture-" + hashlib.sha256(request.model_dump_json().encode()).hexdigest()[:24]
    with sqlite3.connect(root / "discovery.sqlite3") as db:
        db.execute(
            "INSERT OR REPLACE INTO intelligence_tasks(id,status,request,result,error,updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                identity,
                status,
                request.model_dump_json(),
                result.model_dump_json() if result else None,
                error,
                "2026-10-31T00:00:00+00:00",
            ),
        )
    return IntelligenceRepository(root).get(identity)
