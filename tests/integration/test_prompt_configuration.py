"""节点提示词的发布、并发冲突、旧快照和升级回滚验证；完全离线。"""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from agent4pharma.composition import (
    build_daily_literature_discovery,
    build_production_workbench,
    build_prompt_repository,
    open_application_storage,
)
from agent4pharma.entrypoints.web.prompt_api import PromptAPI
from agent4pharma.infrastructure.persistence.sqlite import migrations
from agent4pharma.infrastructure.persistence.sqlite.production_unit import production_unit
from agent4pharma.modules.model_configuration.prompt_contracts import PromptDraft, PromptPublication
from agent4pharma.runtime.production_execution import ProductionLimits
from agent4pharma.runtime.settings import ApplicationSettings, LocalTriageSettings
from agent4pharma.workflows.production_dependencies import ProductionContext
from agent4pharma.workflows.production_queue import queue_role


@pytest.fixture
def catalog(tmp_path):
    open_application_storage(tmp_path).close()
    return build_prompt_repository(tmp_path)


def publish(catalog, key, instructions):
    before = catalog.current(key)
    draft = catalog.draft(key, PromptDraft(instructions=instructions))
    return catalog.publish(
        key,
        PromptPublication(
            version_id=draft.id,
            expected_current=before.id,
            expected_related={k: catalog.current(k).id for k in ("triage",)}
            if key == "screening_scope"
            else {},
        ),
    )


def test_all_nodes_validate_edit_publish_and_recover_history(catalog, tmp_path):
    api = PromptAPI(tmp_path, "offline-model")
    nodes = api.get()["items"]
    assert [n["key"] for n in nodes] == [
        "audience",
        "scientific_evidence",
        "chinese_news_style",
        "screening_scope",
        "triage",
        "news_ranking",
        "news_comparison",
        "editor",
        "writer",
        "reviewer",
        "publisher",
        "maintenance",
    ]
    assert "Decider" in next(node for node in nodes if node["key"] == "news_ranking")["model"]
    assert next(node for node in nodes if node["key"] == "news_comparison")["model"] == "offline-model"
    for node in nodes:
        old = catalog.current(node["key"])
        new = publish(catalog, node["key"], old.instructions + "\n新增具体判断要求。")
        assert new.status == "published"
        assert catalog.current(node["key"]).id == new.id
        assert (
            next(v for v in catalog.versions(node["key"]) if v.id == old.id).instructions == old.instructions
        )
        restored = publish(catalog, node["key"], old.instructions)
        assert restored.instructions == old.instructions
    with pytest.raises(ValidationError):
        api.post("/api/prompts/writer", {"instructions": " " * 80})
    with pytest.raises(ValidationError):
        api.post("/api/prompts/writer", {"instructions": "x" * 80, "model": "unapproved"})
    with pytest.raises(ValidationError):
        api.post("/api/prompts/unknown", {"instructions": "x" * 80})


def test_editorial_standards_publish_into_new_chains_and_inherit_within_chain(catalog, tmp_path):
    from agent4pharma.workflows.newsroom_standards import EDITORIAL_STANDARDS

    app = build_production_workbench(tmp_path, model="offline")
    with production_unit(tmp_path) as work:
        parent = queue_role(
            work,
            app.definitions,
            "writer",
            ProductionContext(phase="draft-writing"),
            ProductionLimits(),
        )
    assert "standards/audience.md" in parent.required_resources
    baseline = {s.resource: parent.newsroom_skills.read(s.resource).text for s in EDITORIAL_STANDARDS}
    for standard in EDITORIAL_STANDARDS:
        release = publish(
            catalog, standard.key, catalog.current(standard.key).instructions + "\n测试新发布要求。"
        )
        assert release.id in app.definitions["writer"].newsroom_skills.read(standard.resource).text
    with production_unit(tmp_path) as work:
        child = queue_role(
            work,
            app.definitions,
            "editor",
            ProductionContext(phase="topic-selection", parent_task_id=parent.id),
            ProductionLimits(),
        )
        fresh = queue_role(
            work,
            app.definitions,
            "writer",
            ProductionContext(phase="draft-writing"),
            ProductionLimits(),
        )
    assert fresh.id != parent.id
    for resource, text in baseline.items():
        assert child.newsroom_skills.read(resource).text == text
        assert fresh.newsroom_skills.read(resource).text != text
    for role, phase in (
        ("writer", "draft-writing"),
        ("reviewer", "fact-check"),
        ("publisher", "layout-production"),
    ):
        with production_unit(tmp_path) as work:
            task = queue_role(work, app.definitions, role, ProductionContext(phase=phase), ProductionLimits())
        assert "standards/audience.md" in task.required_resources
    audience = next(n for n in PromptAPI(tmp_path, "offline").get()["items"] if n["key"] == "audience")
    assert audience["kind"] == "standard"
    assert audience["model"] == "不单独调用模型"
    assert any(item["stage"] == "主编立项" for item in audience["exposure"])


def test_standard_publication_conflict_and_resource_size_limit(catalog):
    current = catalog.current("audience")
    pending = catalog.draft("audience", PromptDraft(instructions=current.instructions + "\n待发布版本。"))
    publish(catalog, "audience", current.instructions + "\n其他人发布。")
    with pytest.raises(ValueError, match="当前版本已变更"):
        catalog.publish("audience", PromptPublication(version_id=pending.id, expected_current=current.id))
    with pytest.raises(ValueError, match="23000"):
        catalog.draft("audience", PromptDraft(instructions="x" * 23000, examples="overflow"))


def test_publish_conflict_and_cross_node_never_replace_current(catalog):
    old = catalog.current("writer")
    a = catalog.draft("writer", PromptDraft(instructions=old.instructions + "\nA"))
    b = catalog.draft("writer", PromptDraft(instructions=old.instructions + "\nB"))

    def request(draft):
        return PromptPublication(version_id=draft.id, expected_current=old.id)

    catalog.publish("writer", request(a))
    with pytest.raises(ValueError, match="当前版本已变更"):
        catalog.publish("writer", request(b))
    with pytest.raises(KeyError):
        catalog.publish("reviewer", request(b))
    assert catalog.current("writer").id == a.id
    with pytest.raises(ValueError, match="只能发布草稿"):
        catalog.publish("writer", PromptPublication(version_id=a.id, expected_current=a.id))
    assert next(v for v in catalog.versions("writer") if v.id == b.id).status == "draft"


def test_concurrent_publish_has_one_winner(catalog):
    before = catalog.current("reviewer")
    drafts = [
        catalog.draft("reviewer", PromptDraft(instructions=before.instructions + str(i))) for i in range(2)
    ]

    def attempt(draft):
        try:
            catalog.publish("reviewer", PromptPublication(version_id=draft.id, expected_current=before.id))
            return "published"
        except ValueError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, drafts)) == ["conflict", "published"]
    assert sum(v.status == "published" for v in catalog.versions("reviewer")) == 1


def test_production_queue_reads_release_without_restart_and_freezes_old_task(catalog, tmp_path):
    from agent4pharma.workflows.newsroom_skills import skill_loading_instructions

    app = build_production_workbench(tmp_path, model="offline")
    for role in ("editor", "writer", "reviewer", "publisher"):
        with production_unit(tmp_path) as work:
            old = queue_role(work, app.definitions, role, ProductionContext(), ProductionLimits())
        new = publish(catalog, role, catalog.current(role).text() + "\n下一版本要求。")
        with production_unit(tmp_path) as work:
            queued = queue_role(work, app.definitions, role, ProductionContext(), ProductionLimits())
            suffix = skill_loading_instructions(queued.newsroom_skills) if queued.newsroom_skills else ""
            expected = new.text()
            if role == "editor":
                expected += (
                    "\n以当前提交工具的任务书字段为准。立项交接唯一报道角度，必要时注明关键事实边界；"
                    "主编在本任务内主动核实影响决定的事实，再提交报道（commission）或不报道（decline）。"
                    "以当前证据和时机作决定；只有未来事件才能支撑的角度，当前判不报道。"
                    "按本任务的提交契约作决定，选题理由通过 reason 提交。"
                    "作者和事实核验按具体缺口补查已立项报道的细节。"
                )
            assert queued.prompt == expected + suffix
            assert queued.id != old.id
            assert work.tasks.get(old.id).prompt == old.prompt
            assert work.tasks.get(old.id).newsroom_skills == old.newsroom_skills


def test_new_discovery_freezes_triage_in_fingerprint(catalog, tmp_path):
    triage = publish(catalog, "triage", catalog.current("triage").instructions + "\n新预选要求。")
    config = ApplicationSettings(data_dir=tmp_path, triage=LocalTriageSettings(enabled=True))
    store = open_application_storage(tmp_path)
    try:
        build_daily_literature_discovery(config, store, providers={})
        snapshot = config.snapshot()
        assert triage.text() == json.loads(config.triage.policy_json)["instructions"]
        publish(catalog, "triage", triage.instructions + "\n再次修改。")
        assert config.snapshot() == snapshot
    finally:
        store.close()


def test_v9_upgrade_backup_and_failed_migration_rollback(tmp_path, monkeypatch):
    with sqlite3.connect(tmp_path / "discovery.sqlite3") as db:
        db.executescript(migrations.SCHEMA)
        db.execute("INSERT INTO schema_version(version) VALUES (1)")
        db.commit()
        for number in range(2, 10):
            db.executescript(getattr(migrations, f"MIGRATION_{number}"))
    original = migrations.MIGRATION_10
    monkeypatch.setattr(
        migrations, "MIGRATION_10", "BEGIN IMMEDIATE; CREATE TABLE transient(id); INVALID SQL;"
    )
    with pytest.raises(sqlite3.OperationalError):
        open_application_storage(tmp_path)
    with sqlite3.connect(tmp_path / "discovery.sqlite3") as db:
        assert db.execute("SELECT max(version) FROM schema_version").fetchone()[0] == 9
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='transient'").fetchone()
    monkeypatch.setattr(migrations, "MIGRATION_10", original)
    open_application_storage(tmp_path).close()
    backup = next((tmp_path / "backups").glob("discovery-pre-v10-*.sqlite3"))
    with sqlite3.connect(backup) as db:
        assert db.execute("SELECT max(version) FROM schema_version").fetchone()[0] == 9


def test_legacy_investigation_request_without_prompt_remains_idempotent(catalog, tmp_path):
    from agent4pharma.infrastructure.persistence.sqlite.investigations import InvestigationRepository
    from agent4pharma.infrastructure.persistence.sqlite.management_policies import PolicyRepository
    from agent4pharma.modules.discovery.screening_policy import ScreeningPolicy
    from agent4pharma.modules.maintenance.models import (
        ArticleCaseInput,
        InvestigationRequest,
        InvestigationStart,
    )

    policies = PolicyRepository(tmp_path)
    policies.ensure_default(ScreeningPolicy())
    cases = InvestigationRepository(tmp_path)
    case = cases.save_case(ArticleCaseInput(reference="Historical article"))
    request = InvestigationRequest(
        case=case, options=InvestigationStart(request_id="legacy"), policy=policies.get_policy()
    )
    job = cases.create_run(request)
    with sqlite3.connect(tmp_path / "discovery.sqlite3") as db:
        row = db.execute("SELECT request FROM jobs WHERE id=?", (job.id,)).fetchone()
        historical = json.loads(row[0])
        historical.pop("prompt")
        old_payload = json.dumps(historical)
        db.execute("UPDATE jobs SET request=? WHERE id=?", (old_payload, job.id))
    assert cases.create_run(cases.get_job(job.id).request).id == job.id
    with sqlite3.connect(tmp_path / "discovery.sqlite3") as db:
        assert db.execute("SELECT request FROM jobs WHERE id=?", (job.id,)).fetchone()[0] == old_payload


def test_shared_scope_release_freezes_triage_and_keeps_stage_edits(catalog, tmp_path):
    from agent4pharma.modules.model_configuration.screening_prompts import split_screening_prompt

    old_triage = catalog.current("triage")
    old_scope = catalog.current("screening_scope")
    scope = publish(catalog, "screening_scope", old_scope.instructions)
    triage = catalog.current("triage")
    assert {split_screening_prompt(v.instructions).scope_ref for v in (triage,)} == {scope.id}
    assert "AI × Biology/Pharma" in triage.instructions
    assert "仅通过固定 choice 协议选择 analyze 或 exclude，不输出解释。" in triage.instructions
    assert "不要求算法原创" in triage.instructions
    assert (
        next(v for v in catalog.versions("triage") if v.id == old_triage.id).instructions
        == old_triage.instructions
    )
    edited = publish(
        catalog, "triage", split_screening_prompt(triage.instructions).stage + "\n保留自定义阶段要求。"
    )
    newer = publish(catalog, "screening_scope", scope.instructions + "\n新增明确的领域说明。")
    current = catalog.current("triage")
    assert split_screening_prompt(current.instructions).scope_ref == newer.id
    assert "保留自定义阶段要求。" in current.instructions
    assert (
        next(v for v in catalog.versions("triage") if v.id == edited.id).instructions == edited.instructions
    )
    payload = next(n for n in PromptAPI(tmp_path, "offline").get()["items"] if n["key"] == "triage")
    version = next(v for v in payload["versions"] if v["id"] == payload["current_id"])
    assert version["instructions"].startswith("# 资料预选")
    assert version["shared_scope_ref"] == newer.id
    assert version["effective_instructions"] == current.text()


def test_shared_scope_publication_conflicts_and_stale_stage_draft(catalog):
    from agent4pharma.modules.model_configuration.screening_prompts import split_screening_prompt

    scope = publish(catalog, "screening_scope", catalog.current("screening_scope").instructions)
    stage = catalog.current("triage")
    pending = catalog.draft(
        "triage", PromptDraft(instructions=split_screening_prompt(stage.instructions).stage)
    )
    common = catalog.draft("screening_scope", PromptDraft(instructions=scope.instructions + "\n标准新说明。"))
    expected = {k: catalog.current(k).id for k in ("triage",)}
    publish(
        catalog,
        "triage",
        split_screening_prompt(catalog.current("triage").instructions).stage + "\n新要求。",
    )
    with pytest.raises(ValueError, match="预选版本已变更"):
        catalog.publish(
            "screening_scope",
            PromptPublication(version_id=common.id, expected_current=scope.id, expected_related=expected),
        )
    assert catalog.current("screening_scope").id == scope.id
    publish(catalog, "screening_scope", common.instructions)
    with pytest.raises(ValueError, match="共用主题标准已过期"):
        catalog.publish(
            "triage", PromptPublication(version_id=pending.id, expected_current=catalog.current("triage").id)
        )


def test_shared_triage_failure_rolls_back_all_versions(catalog, monkeypatch):
    from agent4pharma.infrastructure.persistence.sqlite.prompt_repository import PromptRepository

    before = {k: catalog.current(k).id for k in ("screening_scope", "triage")}
    scope = catalog.draft(
        "screening_scope", PromptDraft(instructions=catalog.current("screening_scope").instructions)
    )
    original = PromptRepository._activate

    def fail_on_triage(self, target):
        if target.key == "triage":
            raise ValueError("Injected publication failure")
        return original(self, target)

    monkeypatch.setattr(PromptRepository, "_activate", fail_on_triage)
    with pytest.raises(ValueError, match="Injected"):
        catalog.publish(
            "screening_scope",
            PromptPublication(
                version_id=scope.id,
                expected_current=before["screening_scope"],
                expected_related={k: before[k] for k in ("triage",)},
            ),
        )
    assert {k: catalog.current(k).id for k in before} == before
    assert len(catalog.versions("triage")) == 1


def test_shared_release_changes_new_batch_snapshot_not_existing_config(catalog, tmp_path):
    first = publish(catalog, "screening_scope", catalog.current("screening_scope").instructions)
    frozen = ApplicationSettings(data_dir=tmp_path, triage=LocalTriageSettings(enabled=True))
    store = open_application_storage(tmp_path)
    try:
        build_daily_literature_discovery(frozen, store, providers={})
        old_snapshot = frozen.snapshot()
        second = publish(catalog, "screening_scope", first.instructions + "\n新标准说明。")
        fresh = ApplicationSettings(data_dir=tmp_path, triage=LocalTriageSettings(enabled=True))
        build_daily_literature_discovery(fresh, store, providers={})
        assert first.id in frozen.triage.policy_json
        assert second.id in fresh.triage.policy_json
        assert fresh.intelligence_policy_ref != frozen.intelligence_policy_ref
        assert fresh.fingerprint() != frozen.fingerprint()
        assert frozen.snapshot() == old_snapshot
    finally:
        store.close()


def test_retired_intelligence_configuration_and_commands_are_rejected(catalog, tmp_path):
    from agent4pharma.composition import build_maintenance_workbench
    from agent4pharma.entrypoints.cli.argument_parser import build_parser
    from agent4pharma.entrypoints.web.api import WorkbenchAPI

    api = PromptAPI(tmp_path, "offline")
    assert "intelligence" not in {item["key"] for item in api.get()["items"]}
    with pytest.raises(ValidationError):
        api.post("/api/prompts/intelligence", {"instructions": "x" * 80})
    with pytest.raises(ValidationError):
        api.post("/api/prompts/intelligence/publish", {"version_id": "old", "expected_current": "old"})
    for arguments in [
        ["intelligence-mcp", "--root", str(tmp_path), "--task", "old"],
        ["production", "regress"],
    ]:
        with pytest.raises(SystemExit):
            build_parser().parse_args(arguments)
    app = build_maintenance_workbench(tmp_path / "config.toml", ApplicationSettings(data_dir=tmp_path))
    try:
        for route in ["/api/article-regressions/start", "/api/intelligence-tasks/old/recommend"]:
            with pytest.raises(KeyError):
                WorkbenchAPI(app).post(route, {})
    finally:
        app.close()
    publish(catalog, "screening_scope", catalog.current("screening_scope").instructions)
    with sqlite3.connect(tmp_path / "discovery.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM intelligence_policies").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM intelligence_tasks").fetchone()[0] == 0
