import asyncio
import json
import sqlite3
import sys
import threading
import time

import httpx
import pytest
from conftest import seed_archived_assessment, seed_archived_task
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from agent4pharma.composition import (
    build_investigation_tools,
    build_maintenance_workbench,
    build_selection_policy,
)
from agent4pharma.entrypoints.web.server import WorkbenchHTTPServer
from agent4pharma.modules.discovery.screening_policy import ScreeningPolicy, screen_text
from agent4pharma.modules.maintenance.models import (
    AgentFinding,
    ArticleCaseInput,
    InvestigationRequest,
    InvestigationStart,
    OriginalCandidate,
)
from agent4pharma.modules.maintenance.repository import FetchedDocument
from agent4pharma.modules.sources.management import SourceEdit
from agent4pharma.runtime.settings import ApplicationSettings
from agent4pharma.workflows.investigate_article import investigate_article


@pytest.fixture
def app(tmp_path):
    config = ApplicationSettings(data_dir=tmp_path, sources={"biorxiv": {"query": ""}})
    application = build_maintenance_workbench(tmp_path / "config.toml", config)
    yield application
    application.close()


def create_job(app, reference="https://news.example/story", **options):
    case = app.cases.save_case(ArticleCaseInput(reference=reference, text=options.pop("text", "")))
    return app.cases.create_run(
        InvestigationRequest(
            case=case,
            options=InvestigationStart(**options),
            policy=app.policies.get_policy(),
        )
    )


def test_compact_selection_queries_require_token_and_do_not_read_the_legacy_full_pool(app, monkeypatch):
    from agent4pharma.infrastructure.persistence.paper_selection_repository import PaperSelectionRepository

    def forbidden(*args, **kwargs):
        pytest.fail("compact selection must not read the legacy full pool")

    monkeypatch.setattr(PaperSelectionRepository, "latest", forbidden)
    server = WorkbenchHTTPServer(app, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False) as client:
            assert client.get("/api/paper-selections").status_code == 403
            assert client.get("/api/paper-selections/downloads?batch=unknown").status_code == 403
            client.headers["X-Workbench-Token"] = server.token
            empty = client.get("/api/paper-selections")
            assert empty.status_code == 200
            assert empty.json()["paper_selections"] == []
            assert empty.json()["runs"] == []
            assert "decisions" not in empty.json()
            assert client.get("/api/paper-selections?batch=unknown").status_code == 404
            assert client.get("/api/paper-selections?run=unknown").status_code == 404
            assert client.get("/api/paper-selections/downloads?batch=unknown").status_code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


class Pages:
    def __init__(self):
        self.calls = []

    def fetch(self, url):
        self.calls.append(url)
        content = {
            "https://news.example/story": (
                "<title>LLM agents for drug discovery</title>"
                "<p>A large language model supports drug discovery.</p>"
                '<a href="https://company.example/announcement">Original press release</a>'
            ),
            "https://company.example/announcement": (
                '<p>Our research</p><a href="https://doi.org/10.1234/example">Original paper</a>'
            ),
            "https://doi.org/10.1234/example": (
                '<title>Research</title><meta name="citation_doi" content="10.1234/example">'
                "<p>A large language model supports drug discovery.</p>"
            ),
        }[url]
        return FetchedDocument(url, content.encode(), "text/html", 200)


def test_live_console_serves_same_origin_assets_and_protects_api(app):
    import re

    server = WorkbenchHTTPServer(app, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with httpx.Client(base_url=base) as client:
            page = client.get("/console")
            assert page.status_code == 200
            assert "__WORKBENCH_TOKEN__" not in page.text
            assert "'unsafe-inline'" not in page.headers["Content-Security-Policy"]
            assert "'nonce-" in page.headers["Content-Security-Policy"]
            script = re.search(r'src="(/console-assets/assets/[^\"]+\.js)"', page.text)
            assert script
            assert client.get(script.group(1)).status_code == 200
            assert client.get("/console-assets/assets/missing.js").status_code == 404
            settings = client.get("/settings")
            assert settings.status_code == 200
            assert "/console-assets/assets/" in settings.text
            assert "'nonce-" in settings.headers["Content-Security-Policy"]
            assert client.get("/assets/settings_page.js").status_code == 404
            assert client.get("/api/prompts").status_code == 403
            assert client.post("/api/prompts/content", json={}).status_code == 403
            assert client.get("/api/discovery-run").status_code == 403
            assert client.get("/", headers={"Origin": "https://outside.example"}).status_code == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_rules_trace_evidence_budget_local_lookup_and_snapshot(app, tmp_path):
    job = create_job(app, max_pages=3, request_id="same")
    assert app.cases.create_run(job.request).id == job.id
    with sqlite3.connect(tmp_path / "discovery.sqlite3") as db:
        db.execute(
            "INSERT INTO materials(id,identity,title,identifiers,first_seen_at) VALUES (?,?,?,?,?)",
            ("original", "doi:10.1234/example", "Research", "{}", "2026-10-29"),
        )
    tools = build_investigation_tools(tmp_path, job.id)
    tools.fetcher = pages = Pages()
    report = investigate_article(tools)
    assert len(pages.calls) == 3
    assert report.screening.route == "llm_candidate"
    assert report.local_matches[0].material_id == "original"
    assert all(p.content_hash for p in report.pages)
    assert len(list((tmp_path / "workbench" / "evidence").glob("*/*"))) == 3
    assert any(s.name == "上游检索覆盖" and s.status == "unverified" for s in report.stages)
    assert all(c.status == "page_read_candidate" for c in report.original_candidates)
    assert app.cases.get_job(job.id).status == "complete"
    with pytest.raises(ValueError, match="运行状态"):
        tools.fetch_page("https://other.example")
    case = job.request.case
    app.cases.save_case(ArticleCaseInput(reference="Edited title", revision=case.revision), case.id)
    assert app.cases.get_job(job.id).request.case.reference == "https://news.example/story"
    with pytest.raises(ValueError, match="已被修改"):
        app.cases.save_case(ArticleCaseInput(reference="Stale", revision=case.revision), case.id)


def test_page_budget_reserved_before_fetch_and_failures_not_success(app, tmp_path):
    job = create_job(app, max_pages=1)
    app.cases.update_job(job.id, "running")
    tools = build_investigation_tools(tmp_path, job.id)
    tools.fetcher = pages = Pages()
    tools.fetch_page(job.request.case.reference)
    tools.fetch_page(job.request.case.reference)
    with pytest.raises(ValueError, match="页面预算"):
        tools.fetch_page("https://company.example/announcement")
    assert len(pages.calls) == 1

    class Forbidden:
        def fetch(self, url):
            return FetchedDocument(url, b"Forbidden", "text/html", 403)

    failed = create_job(app)
    failing_tools = build_investigation_tools(tmp_path, failed.id)
    failing_tools.fetcher = Forbidden()
    report = investigate_article(failing_tools)
    assert report.pages[0].status == "http_error"
    assert app.cases.get_job(failed.id).status == "partial"
    assert report.screening.route == "insufficient_information"


def test_policies_sources_and_recovery(app, tmp_path):
    first = app.policies.get_policy()
    second = app.policies.save_policy(ScreeningPolicy(name="Version two", agent_terms=["agentic AI"]))
    app.policies.publish_policy(first.id)
    app.policies.publish_policy(second.id)
    assert app.policies.get_policy().id == second.id
    assert app.policies.get_policy(first.id).definition.name == first.definition.name
    assert sum(p.status == "published" for p in app.policies.list_versions()) == 1
    source = app.sources.save_source(
        SourceEdit(
            name="Company", homepage_url="https://company.example", endpoint_url="https://company.example/rss"
        )
    )
    change = SourceEdit.model_validate(source.model_dump())
    change.notes = "Human review"
    app.sources.save_source(change, source.id)
    with pytest.raises(ValueError, match="已被修改"):
        app.sources.save_source(change, source.id)
    with sqlite3.connect(tmp_path / "discovery.sqlite3") as db:
        assert (
            db.execute("SELECT enabled FROM source_endpoints WHERE id=?", (source.endpoint_id,)).fetchone()[0]
            == 0
        )
    job = create_job(app)
    app.cases.update_job(job.id, "running")
    app.cases.recover_interrupted()
    assert app.cases.get_job(job.id).status == "interrupted"


def test_agent_cannot_invent_evidence_urls_or_quotes(app, tmp_path):
    job = create_job(app, mode="agent", text="A large language model supports drug discovery.")
    app.cases.update_job(job.id, "running")
    tools = build_investigation_tools(tmp_path, job.id)
    tools.seed_manual_evidence()
    page = tools.evidence.list_evidence(job.id)[0]
    finding = AgentFinding(
        relevance="core",
        reason="Explicit method and task",
        evidence_ids=[page.id],
        ai_evidence="large language model",
        drug_evidence="drug discovery",
    )
    assert tools.save_findings(finding).agent_finding.relevance == "core"
    bad = finding.model_copy(update={"ai_evidence": "Invented quote"})
    with pytest.raises(ValueError, match="原文"):
        tools.save_findings(bad)
    bad = finding.model_copy(update={"evidence_ids": ["other-task"]})
    with pytest.raises(ValueError, match="不存在"):
        tools.save_findings(bad)
    finding.original_candidates = [
        OriginalCandidate(url="https://invented.example", evidence_id=page.id, reason="guess")
    ]
    with pytest.raises(ValueError, match="未实际出现"):
        tools.save_findings(finding)
    finding.original_candidates[0].url = ""
    with pytest.raises(ValueError, match="完整"):
        tools.save_findings(finding)


def test_agent_workflow_requires_structured_result_and_redacts_failures(app, tmp_path):
    job = create_job(app, reference="Offline example", mode="agent", text="LLM for drug discovery")
    tools = build_investigation_tools(tmp_path, job.id)

    class Silent:
        def run(self, job_id):
            pass

    report = investigate_article(tools, Silent())
    assert app.cases.get_job(job.id).status == "partial"
    assert report.agent_finding is None

    class Broken:
        def run(self, job_id):
            raise ValueError("test-only-provider-secret")

    other = create_job(app, reference="Offline other", mode="agent", text="LLM for drug discovery")
    investigate_article(build_investigation_tools(tmp_path, other.id), Broken())
    saved = app.cases.get_job(other.id)
    assert saved.status == "partial"
    assert "test-only-provider-secret" not in saved.model_dump_json()


def test_http_roundtrip_and_origin_guards(app):
    server = WorkbenchHTTPServer(app, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False) as client:
            assert client.get("/").status_code == 200
            for name in ("flow_projection", "node_metrics", "run_scope", "console_routes"):
                assert client.get(f"/assets/{name}.js").status_code == 200
            assert client.get("/api/cases").status_code == 403
            client.headers["X-Workbench-Token"] = server.token
            assert client.get("/api/cases", headers={"Host": "evil.example"}).status_code == 403
            assert (
                client.post("/api/cases", json={}, headers={"Origin": "https://evil.example"}).status_code
                == 403
            )
            assert client.post("/api/cases", json={}).status_code == 400
            case = client.post(
                "/api/cases", json={"reference": "Manual example", "text": "LLM agents for drug discovery"}
            ).json()
            job = client.post(f"/api/cases/{case['id']}/run", json={"request_id": "http"}).json()
            for _ in range(100):
                result = client.get(f"/api/jobs/{job['id']}").json()
                if result["status"] not in {"queued", "running"}:
                    break
                time.sleep(0.01)
            assert result["status"] == "complete"
            assert result["result"]["screening"]["route"] == "llm_candidate"
            preview = client.post(
                "/api/policies/preview", json={"definition": ScreeningPolicy().model_dump()}
            ).json()
            assert "pubmed" in preview["queries"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_flat_console_pages_and_removed_legacy_routes(app):
    import re
    from importlib.resources import files

    from bs4 import BeautifulSoup

    from agent4pharma.entrypoints.web.console_assets import ASSETS

    static = files("agent4pharma.entrypoints.web").joinpath("static")
    for asset in ASSETS:
        assert static.joinpath(asset).is_file(), asset
        if asset.endswith(".js"):
            script = static.joinpath(asset).read_text(encoding="utf-8")
            for dependency in re.findall(r'(?:from\s+|import\s*)["\']\./([^"\']+)["\']', script):
                assert dependency in ASSETS, (asset, dependency)

    server = WorkbenchHTTPServer(app, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False) as client:
            for path in ("/sources", "/settings"):
                response = client.get(path)
                assert response.status_code == 200
                page = BeautifulSoup(response.text, "html.parser")
                assert page.select_one("#root")
                assert not page.select(".sidebar, .console-topbar, #source-form, #new-source")
                assert "nonce-" in response.headers["content-security-policy"]
                for script in page.select("script[src]"):
                    assert client.get(script["src"]).status_code == 200
            for old in (
                "/maintenance",
                "/maintenance?reference=https://example.org",
                "/assets/maintenance_page.js",
                "/assets/review_views.js",
                "/production",
                "/app.js",
                "/production.js",
                "/style.css",
                "/production.css",
                "/assets/source_form.js",
                "/assets/overview.js",
                "/assets/stage_page.js",
                "/assets/intelligence_page.js",
                "/assets/flow_canvas.js",
                "/assets/rss_triage.js",
                "/assets/sources_page.js",
                "/assets/retrieval_settings.js",
                "/assets/policy_form.js",
                "/assets/core.js",
                "/assets/shell.js",
                "/assets/console.css",
                "/assets/workspace.css",
            ):
                assert client.get(old).status_code == 404
            for route in (
                "/",
                "/console",
                "/console?stage=content",
                "/intelligence",
                "/editorial",
                "/content",
                "/review",
                "/publishing",
            ):
                response = client.get(route)
                assert response.status_code == 200
                assert 'id="root"' in response.text
                assert "__WORKBENCH_TOKEN__" not in response.text
                assert "nonce-" in response.headers["content-security-policy"]
            assert client.get("/assets/shell.html").status_code == 404
            assert client.get("/api/intelligence-tasks").status_code == 403
            assert client.get("/api/collection-plans").status_code == 403
            settings = client.get("/settings")
            assert settings.status_code == 200
            assert "/console-assets/assets/" in settings.text
            assert "'nonce-" in settings.headers["Content-Security-Policy"]
            assert client.get("/assets/settings_page.js").status_code == 404
            assert client.get("/api/prompts").status_code == 403
            assert client.post("/api/prompts/content", json={}).status_code == 403
            assert client.get("/api/discovery-run").status_code == 403
            headers = {"X-Workbench-Token": server.token}
            assert client.get("/api/discovery-run", headers=headers).json() == {
                "selected": "",
                "aggregate_batch": "",
                "batch_kind": "",
                "batches": [],
                "sources": [],
                "articles": [],
            }
            assert client.post("/api/sources", headers=headers, json={}).status_code == 404
            assert client.post("/api/sources/old", headers=headers, json={}).status_code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_intelligence_list_projects_metadata_and_detail_keeps_evidence(app):
    from agent4pharma.entrypoints.web.api import WorkbenchAPI
    from agent4pharma.modules.discovery.intelligence_contracts import (
        IntelligenceEvidence,
        IntelligenceRequest,
    )

    store = app.intelligence_store
    task = seed_archived_task(
        store.root,
        IntelligenceRequest(
            material_ref="console-paper",
            mode="production",
            evidence=[IntelligenceEvidence(id="paper-v1", title="Bound paper", text="Private evidence text")],
            policy=build_selection_policy(store.root),
            model="offline",
        ),
    )
    api = WorkbenchAPI(app)
    summaries = api.get("/api/intelligence-tasks")["items"]
    assert summaries[0]["id"] == task.id and summaries[0]["mode"] == "production"
    assert "Private evidence text" not in json.dumps(summaries)
    detail = api.get("/api/intelligence-tasks/" + task.id)
    assert detail["request"]["evidence"][0]["text"] == "Private evidence text"
    assert api.get("/api/collection-plans") == {"items": []}
    with pytest.raises(ValueError, match="200"):
        store.recent_summaries(201)


def test_batch_intelligence_summary_keeps_parent_currentness_and_evidence_states(app, monkeypatch):
    from agent4pharma.entrypoints.web.api import WorkbenchAPI
    from agent4pharma.modules.discovery.intelligence_contracts import (
        IntelligenceAssessment,
        IntelligenceEvidence,
        IntelligenceRequest,
    )

    store = app.intelligence_store
    request = IntelligenceRequest(
        material_ref="batch-material",
        mode="production",
        parent_ref="batch-a",
        policy=build_selection_policy(store.root),
        model="offline-v1",
        evidence=[
            IntelligenceEvidence(
                id="source-version",
                title="Bound paper",
                text="Private fulltext",
                origin="primary_fulltext",
                abstract_status="found",
                abstract_source="native",
            )
        ],
    )
    first = seed_archived_task(store.root, request, status="failed", error="safe failure")
    second_request = request.model_copy(update={"model": "offline-v2"})
    result = IntelligenceAssessment(
        relevance="core",
        disposition="recommend",
        summary="推荐摘要",
        reason="合法推荐",
        attention_reason="关注理由",
        evidence_ids=["source-version"],
    )
    second = store.get(seed_archived_assessment(store.root, second_request, result))
    unrelated = seed_archived_task(
        store.root, request.model_copy(update={"parent_ref": "batch-b", "material_ref": "other"})
    )
    api = WorkbenchAPI(app)
    with monkeypatch.context() as patch:
        patch.setattr(type(store), "get", lambda *_: pytest.fail("list must not load full task per row"))
        rows = api.get("/api/intelligence-tasks?batch=batch-a")["items"]
    assert {r["id"] for r in rows} == {first.id, second.id}
    assert next(r for r in rows if r["id"] == second.id)["current"]
    assert not next(r for r in rows if r["id"] == first.id)["current"]
    assert all(r["fulltext_state"] == "available" and r["abstract_state"] == "found" for r in rows)
    assert "Private fulltext" not in json.dumps(rows)
    assert unrelated.id not in {r["id"] for r in rows}
    candidates = api.get("/api/candidates?batch=batch-a")["items"]
    assert len(candidates) == 1 and candidates[0]["task_id"] == second.id
    assert api.get("/api/candidates?batch=batch-b")["items"] == []
    exact = api.get("/api/intelligence-tasks?task_ids=" + first.id)["items"]
    assert [r["id"] for r in exact] == [first.id]
    assert "Private fulltext" not in json.dumps(exact)


def test_investigation_real_stdio_tools_and_structured_save(app, tmp_path):
    job = create_job(app, mode="agent", text="A large language model supports drug discovery.")
    app.cases.update_job(job.id, "running")
    tools = build_investigation_tools(tmp_path, job.id)
    tools.seed_manual_evidence()
    page = tools.evidence.list_evidence(job.id)[0]

    async def check():
        server = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "agent4pharma",
                "investigation-mcp",
                "--root",
                str(tmp_path),
                "--job",
                job.id,
            ],
        )
        async with stdio_client(server) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                assert len((await session.list_tools()).tools) == 9
                result = await session.call_tool("get_investigation", {})
                assert not result.is_error
                result = await session.call_tool(
                    "save_findings",
                    {
                        "finding": {
                            "relevance": "core",
                            "reason": "Explicit evidence",
                            "evidence_ids": [page.id],
                            "ai_evidence": "large language model",
                            "drug_evidence": "drug discovery",
                        }
                    },
                )
                assert not result.is_error
                payload = result.structured_content or json.loads(result.content[0].text)
                assert payload["agent_finding"]["relevance"] == "core"

    asyncio.run(check())


@pytest.mark.parametrize(
    "title,route",
    [
        ("LLM agents for drug discovery", "llm_candidate"),
        ("Machine learning for drug discovery", "no_keyword_match"),
        ("Therapeutic agent for drug discovery", "no_keyword_match"),
        ("Protein foundation model for drug discovery", "related_review"),
    ],
)
def test_theme_is_agent_llm_not_traditional_ml(title, route):
    assert screen_text(ScreeningPolicy(), title, "Background. " * 20).route == route


def test_primary_article_rules_mode_needs_no_model_and_cannot_be_reused_as_agent(app):
    from agent4pharma.entrypoints.web.api import WorkbenchAPI

    app.model_available = lambda: False

    class NoModelCalls:
        def run(self, job_id):
            pytest.fail("规则模式不允许调用模型")

    app.agent = NoModelCalls()
    api = WorkbenchAPI(app)
    payload = {"content": "LLM agents for drug discovery", "request_id": "rules-main", "mode": "rules"}
    job = api.post("/api/analyze", payload)
    for _ in range(100):
        current = app.cases.get_job(job["id"])
        if current.status not in {"queued", "running"}:
            break
        time.sleep(0.01)
    assert current.status == "complete"
    assert current.request.options.mode == "rules"
    assert current.result.intelligence_results == []
    assert api.post("/api/analyze", payload)["id"] == job["id"]
    with pytest.raises(ValueError, match="request_id"):
        api.post("/api/analyze", {**payload, "mode": "agent"})
    with pytest.raises(ValueError, match="凭证"):
        api.post("/api/analyze", {"content": payload["content"], "mode": "agent"})
