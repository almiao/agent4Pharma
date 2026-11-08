"""移除独立候选简报后，验证入口、调度、冻结身份及主编直接交接。"""

import hashlib
import json
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from conftest import seed_archived_assessment
from pydantic import ValidationError

from agent4pharma.composition import (
    build_production_workbench,
    build_selection_policy,
    open_application_storage,
)
from agent4pharma.entrypoints.cli.argument_parser import build_parser
from agent4pharma.entrypoints.web.prompt_api import PromptAPI
from agent4pharma.infrastructure.llm.patch import create_patch
from agent4pharma.infrastructure.persistence.sqlite.intelligence_repository import IntelligenceRepository
from agent4pharma.modules.discovery.intelligence_contracts import (
    IntelligenceAssessment,
    IntelligenceEvidence,
    IntelligenceRequest,
)
from agent4pharma.runtime.production_execution import ProductionLimits
from agent4pharma.runtime.scheduling import due_days
from agent4pharma.runtime.settings import ApplicationSettings


@pytest.mark.parametrize(
    "arguments",
    [
        ["brief", "batch"],
        ["mcp", "--batch", "batch"],
        ["run", "--brief"],
        ["schedule", "--brief"],
        ["acquisition", "run", "--brief"],
        ["acquisition", "schedule", "--brief"],
        ["acquisition", "resume", "batch", "--brief"],
    ],
)
def test_removed_briefing_entry_points_are_rejected(arguments):
    with pytest.raises(SystemExit) as error:
        build_parser().parse_args(arguments)
    assert error.value.code == 2


def test_scheduler_does_not_revisit_completed_collection_for_summaries():
    config = ApplicationSettings()
    store = SimpleNamespace(
        daily_jobs=lambda: [
            {"request": {"day": "2026-10-28", "policy": config.snapshot()}, "status": "complete"}
        ]
    )
    assert due_days(config, store, datetime(2026, 10, 29, 8, tzinfo=ZoneInfo("Asia/Shanghai"))) == []


def test_new_config_omits_briefing_but_frozen_snapshot_keeps_original_identity():
    config = ApplicationSettings()
    assert "briefing_prompt" not in config.snapshot()
    assert "briefing_prompt" not in ApplicationSettings.model_fields
    historical = {**config.snapshot(), "briefing_prompt": "Historical saved prompt"}
    frozen = ApplicationSettings.model_validate(historical)
    frozen.freeze_snapshot(historical)
    expected = hashlib.sha256(json.dumps(historical, sort_keys=True).encode()).hexdigest()[:16]
    assert frozen.fingerprint() == expected
    assert frozen.snapshot() == historical
    historical["briefing_prompt"] = "Changed caller object"
    assert frozen.fingerprint() == expected


def test_prompt_catalog_and_api_have_no_briefing_entry(tmp_path):
    open_application_storage(tmp_path).close()
    api = PromptAPI(tmp_path, "offline")
    assert "briefing" not in {node["key"] for node in api.get()["items"]}
    with pytest.raises(ValidationError):
        api.post("/api/prompts/briefing", {"instructions": "x" * 60})


def test_editorial_reads_intelligence_summary_without_another_model_call(tmp_path):
    open_application_storage(tmp_path).close()
    repository = IntelligenceRepository(tmp_path)
    request = IntelligenceRequest(
        material_ref="paper",
        mode="production",
        model="offline",
        policy=build_selection_policy(tmp_path),
        evidence=[IntelligenceEvidence(id="source", text="AI for protein design")],
    )
    assessment = IntelligenceAssessment(
        relevance="core",
        disposition="recommend",
        summary="精筛直接形成的候选摘要",
        reason="实际AI与蛋白设计相关",
        attention_reason="具体实验结果值得解读",
        evidence_ids=["source"],
        ai_quote="AI",
        domain_quote="protein design",
    )
    seed_archived_assessment(tmp_path, request, assessment)
    candidate = repository.candidates()[0]
    workbench = build_production_workbench(tmp_path, model="offline")
    queued = workbench.start([candidate["id"]], ProductionLimits())
    context = json.loads(queued.input_json)["candidates"][0]
    assert context["summary"] == assessment.summary
    assert context["reason"] == assessment.attention_reason
    assert context["evidence"][0]["id"] == "source"
    assert queued.status == "queued"


def test_dsh_patch_requires_explicit_role_tools_and_keeps_capability_guards(tmp_path):
    config = ApplicationSettings(data_dir=tmp_path)
    with pytest.raises(ValueError, match="明确指定"):
        create_patch(tmp_path / "config.toml", config, "task", "Prompt", mcp_args=[])
    arguments = ["-m", "agent4pharma", "production-mcp", "--task", "task", "--lease", "lease"]
    patch = create_patch(
        tmp_path / "config.toml",
        config,
        "task",
        "Prompt",
        mcp_args=arguments,
        mcp_cwd=tmp_path,
        tool_timeout_ms=150000,
    )
    rows = json.loads(patch.read_text(encoding="utf-8"))
    plugins = {row["id"]: row for item in rows for row in item.get("insert", [item])}
    client = plugins["pharma-mcp"]["config"]
    assert client["args"] == arguments
    assert client["cwd"] == str(tmp_path.resolve())
    assert client["toolCallTimeoutMs"] == 150000
    assert plugins["persistent-bash"]["disabled"] is True
    assert plugins["persistent-pwsh"]["disabled"] is True
    assert not any(plugins["tool-fs"]["config"].values())
