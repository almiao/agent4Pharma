import test from "node:test";
import assert from "node:assert/strict";
import { projectFlow, stageResults, stagePreview } from "../../src/agent4pharma/entrypoints/web/static/flow_projection.js";
import { scopeRun, bindRunLinks } from "../../src/agent4pharma/entrypoints/web/static/run_scope.js";
import { completeNodeMetrics } from "../../src/agent4pharma/entrypoints/web/static/node_metrics.js";

function fixture() {
    return {
        rss: { selected: "batch-a", batches: [{ batch_id: "batch-a", states: { awaiting_intelligence: 1 } }], decisions: [{ id: "decision-a", input: { title: "Protein", journal: "Journal", material_ref: "paper-a" }, route: "analyze" }] },
        sources: { items: [] },
        intelligence: { items: [
            { id: "production-a", title: "Protein", mode: "production", status: "complete", disposition: "needs_evidence" },
            { id: "replay-a", title: "Replay", mode: "replay", status: "failed" },
        ] },
        candidates: { items: [] }, proposals: { items: [] },
        production: { stories: [], tasks: [{ id: "task-a", role: "writer", story_id: "", status: "failed", attempt: 1, limits: { max_attempts: 2 }, result_ref: "", error: "Timeout" }] },
        artifacts: { items: [
            { id: "draft-a", kind: "draft", title: "Protein", story_id: "story-a", version: "v1", status: "saved" },
            { id: "review-a", kind: "review", title: "Protein", story_id: "story-a", version: "v1", status: "changes_requested", reference_id: "draft-a" },
        ] },
    };
}

test("unknown queries remain unknown; successful empty queries remain zero", () => {
    const model = projectFlow({});
    assert.equal(model.metrics.get("sources").count, null);
    assert.equal(model.metrics.get("content-tasks").count, null);
    assert.equal(model.attention.count, null);
    const loaded = projectFlow(fixture());
    assert.equal(loaded.metrics.get("sources").count, 0);
    assert.equal(loaded.metrics.get("packages").count, 0);
});

test("production scopes, unique attention, and report references are preserved", () => {
    const data = fixture();
    data.intelligence.items[0].status = "failed";
    const model = projectFlow(data);
    assert.equal(model.metrics.get("intel-tasks").count, 1);
    assert.equal(model.metrics.get("intel-failed").count, 1);
    assert.equal(model.attention.count, 2); // 同一任务待补证且失败，只计一次；历史修改报告不计待办。
    assert.equal(model.metrics.get("draft-handoff").count, 1);
    assert.equal(model.metrics.get("reviews-pass").count, 0);
    assert.equal(model.metrics.get("review-changes").count, 1);
    assert.equal(model.stages.find((s) => s.id === "intel").status.id, "intel-failed");
});

test("every known metric has the counted objects and a concrete workspace filter", () => {
    const model = projectFlow(fixture());
    for (const metric of model.metrics.values()) {
        assert.equal(metric.count, metric.rows.length, metric.id);
        assert.ok(metric.url.startsWith("/"));
    }
    const triage = new URL(model.metrics.get("analyze").url, "http://localhost");
    assert.equal(triage.searchParams.get("route"), "analyze");
    assert.equal(triage.searchParams.get("batch"), "batch-a");
    const tasks = new URL(model.metrics.get("content-failed").url, "http://localhost");
    assert.equal(tasks.searchParams.get("status"), "failed");
    assert.equal(tasks.searchParams.get("view"), "tasks");
    assert.ok(model.metrics.get("review-changes").url.includes("decision=changes_requested"));
});

test("batch run excludes unrelated records and follows explicit candidate to story links", () => {
    const data = fixture();
    data.intelligence.items[0].material_ref = "paper-a";
    data.candidates.items = [{ id: "candidate-a", material_ref: "paper-a", task_id: "production-a", card: { summary: "Protein" } }];
    data.production.stories = [{ id: "story-a", candidate_ids: ["candidate-a"], brief: { title: "Protein" } }];
    data.production.tasks[0].story_id = "story-a";
    data.production.tasks.push({ ...data.production.tasks[0], id: "unrelated", story_id: "other-story" });
    const scoped = scopeRun(data);
    assert.equal(scoped.production.tasks.length, 1);
    assert.equal(scoped.intelligence.items.length, 1);
    assert.equal(scoped.sources, null);
    assert.equal(scoped.proposals, null);
    const model = bindRunLinks(projectFlow(scoped));
    assert.equal(model.metrics.has("sources"), false);
    assert.equal(model.metrics.get("content-failed").count, 1);
    assert.equal(new URL(model.metrics.get("content-failed").url, "http://localhost").searchParams.get("ids"), "task-a");
    assert.equal(scopeRun(data, "other-story").production, null);
    assert.equal(scopeRun(data, "story-a").rss, null);
    assert.equal(scopeRun({ ...data, rss: null }).production, null);
});

test("source records reconcile across cache reuse, waiting and failure; current versions drive production", () => {
    const data = fixture();
    data.runScope = "batch test";
    data.discovery = { selected: "batch-a", sources: [
        { source_id: "s", status: "complete", records: 4, unique_materials: 3 },
        { source_id: "failed", status: "failed", records: null, unique_materials: null },
        { source_id: "empty", status: "complete", records: 0, unique_materials: 0 },
    ], articles: [
        { id: "r1", material_id: "a", source_id: "s", title: "A", route: "analyze", state: "awaiting_intelligence", triage_id: "cached" },
        { id: "r2", material_id: "a", source_id: "s", title: "A", route: "analyze", state: "awaiting_intelligence", triage_id: "cached" },
        { id: "r3", material_id: "b", source_id: "s", title: "B", route: "", state: "awaiting_triage", triage_error: "Timeout" },
        { id: "r4", material_id: "c", source_id: "s", title: "C", route: "", state: "awaiting_triage", triage_error: "" },
    ] };
    data.artifacts.task_links = [{ task_id: "task-a", current: false, rework: false }];
    data.artifacts.decisions = [];
    data.artifacts.receipts = [];
    data.artifacts.items[0].current = true;
    data.artifacts.items[1].current = false;
    const model = completeNodeMetrics(projectFlow(data), data);
    assert.equal(model.metrics.get("collected-records").count, 4);
    assert.equal(model.metrics.get("unique-materials").count, 3);
    assert.equal(model.metrics.get("analyze").count, 2); // 同一次缓存判别仍有两个批次来源项。
    assert.equal(model.metrics.get("triage-pending").count, 1);
    assert.equal(model.metrics.get("triage-failed").count, 1);
    assert.equal(model.metrics.get("sources-empty").count, 1);
    assert.equal(model.metrics.get("sources-failed").count, 1);
    assert.equal(model.metrics.get("content-failed").count, 0);
    assert.equal(model.metrics.get("content-historical-failed").count, 1);
    assert.equal(model.metrics.get("reviews-history").count, 1);
    assert.equal(model.metrics.get("review-changes").count, 0);
    for (const m of model.metrics.values()) if (m.rows !== null) assert.equal(m.count, m.rows.length, m.id);
});

test("frozen assessment references preserve a historical story after candidate-pool changes", () => {
    const data = fixture();
    data.discovery = { selected: "batch-a", articles: [{ material_id: "paper-a" }] };
    data.intelligence.items[0].material_ref = "paper-a";
    data.intelligence.items[0].parent_ref = "batch-a";
    data.production.stories = [{ id: "story-a", candidate_ids: ["former-candidate"], brief: { title: "Original story" } }];
    data.production.tasks[0].story_id = "story-a";
    data.artifacts.task_links = [{ task_id: "task-a", candidate_ids: ["former-candidate"], assessment_ids: ["production-a"] }];
    const batch = scopeRun(data);
    assert.equal(batch.production.stories.length, 1);
    assert.equal(batch.production.tasks.length, 1);
    const story = scopeRun(data, "story-a");
    assert.equal(story.intelligence.items.length, 1);
    assert.equal(story.intelligence.items[0].id, "production-a");
});


test("failed sources retain diagnostics and duplicate metrics use explicit flags", () => {
    const data = fixture();
    data.discovery = { selected: "batch-a", sources: [
        { source_id: "source-a", name: "Collected", status: "complete", records: 3, unique_materials: 2 },
        { source_id: "source-b", name: "Blocked", status: "failed", records: null, unique_materials: null, error: "Original failure", diagnostic: { http_status: 403, reason: "Denied", url: "https://example.com/feed" } },
    ], articles: [
        { id: "first", material_id: "paper", source_id: "source-a", title: "First", state: "pending" },
        { id: "other", material_id: "paper", source_id: "source-a", title: "Other", state: "pending", duplicate_kind: "same_material", ignore_recommended: false },
        { id: "reused", material_id: "old", source_id: "source-a", title: "Reused", state: "already_seen", duplicate_kind: "already_assessed", ignore_recommended: true },
    ] };
    const model = completeNodeMetrics(projectFlow(data), data);
    assert.equal(model.metrics.get("duplicate-records").count, 2);
    assert.equal(model.metrics.get("ignored-duplicates").count, 1);
    const source = model.sourceRows.find((s) => s.id === "source-b");
    assert.equal(source.detail.diagnostic.http_status, 403);
    assert.match(source.subtitle, /HTTP 403/);
    assert.equal(source.detail.records, null);
    assert.equal(new URL(source.href, "http://localhost").searchParams.get("collection"), "source-b");
});


test("direct semantic routing is distinct from screened results and pending is not output", () => {
    const data = fixture();
    data.intelligence = { items: [] };
    data.discovery = { selected: "retry", sources: [], articles: Array.from({length: 492}, (_, i) => ({
        id: `r${i}`, material_id: `m${i}`, title: `Paper ${i}`, source_id: "journal",
        route: "analyze", state: "awaiting_intelligence", triage_method: "semantic_passthrough",
    })) };
    const model = completeNodeMetrics(projectFlow(data), data);
    assert.equal(model.metrics.get("collected-records").count, 492);
    assert.equal(model.metrics.get("triage-processed").count, 492);
    assert.equal(model.metrics.get("triage-passthrough").count, 492);
    assert.equal(model.metrics.get("triage-screened").count, 0);
    assert.equal(model.stages.find((s) => s.id === "intel").primary.id, "intel-input");
    assert.equal(model.metrics.get("intel-input").count, 492);
    assert.equal(model.metrics.get("intel-unstarted").count, 492);
    assert.equal(model.metrics.get("intel-completed-records").count, 0);
    assert.equal(model.metrics.get("candidates").count, 0);
    data.intelligence = null;
    const unknown = completeNodeMetrics(projectFlow(data), data);
    assert.equal(unknown.metrics.get("intel-unstarted").count, null);
});

test("aggregate follows frozen imported assessments and counts tasks by source version", () => {
    const data = fixture();
    data.discovery = { selected: "aggregate", sources: [], articles: [
        { id: "r1", material_id: "same", source_id: "s", route: "analyze", state: "awaiting_intelligence", intelligence_task_id: "imported" },
        { id: "r2", material_id: "same", source_id: "s", route: "analyze", state: "awaiting_intelligence" },
        { id: "r3", material_id: "other", source_id: "s", route: "analyze", state: "awaiting_intelligence" },
    ] };
    data.intelligence.items = [
        { id: "imported", parent_ref: "old", material_ref: "same", source_record_id: "r1", mode: "production", status: "complete", updated_at: "2026-10-31" },
        { id: "other-version", parent_ref: "old", material_ref: "same", source_record_id: "old-version", mode: "production", status: "complete" },
        { id: "running", parent_ref: "aggregate", material_ref: "other", source_record_id: "r3", mode: "production", status: "running" },
    ];
    const scoped = scopeRun(data);
    assert.deepEqual(scoped.intelligence.items.map((t) => t.id), ["imported", "running"]);
    const model = completeNodeMetrics(projectFlow(scoped), scoped);
    assert.equal(model.metrics.get("intel-input").count, 3);
    assert.equal(model.metrics.get("intel-completed-records").count, 1);
    assert.equal(model.metrics.get("intel-running-records").count, 1);
    assert.equal(model.metrics.get("intel-unstarted").count, 1);
    assert.equal(model.metrics.get("intel-unstarted").rows[0].id, "r2");
});


test("node results expose own branches with detail identities; publication input uses approved review", () => {
    const data = fixture();
    data.discovery = {selected: "batch-a", sources: [], articles: [
        {id: "direct", material_id: "a", source_id: "s", route: "analyze", state: "awaiting_intelligence", triage_method: "semantic_passthrough"},
        {id: "screened", material_id: "b", source_id: "s", route: "analyze", state: "awaiting_intelligence", triage_method: "keyword_gate"},
    ]};
    data.artifacts.items[1].status = "pass";
    data.artifacts.items[1].current = true;
    data.artifacts.task_links = [];
    data.artifacts.decisions = [];
    data.artifacts.receipts = [];
    const model = completeNodeMetrics(projectFlow(data), data);
    for (const stage of model.stages) {
        for (const branch of stageResults(stage)) {
            assert.equal(branch.count, model.metrics.get(branch.id).count);
            assert.equal(branch.url, model.metrics.get(branch.id).url);
            assert.ok(!["intel-input", "triage-processed", "analyze"].includes(branch.id));
        }
    }
    const triage = stageResults(model.stages.find((s) => s.id === "triage"));
    assert.equal(triage.find((b) => b.id === "triage:result:pass").count, 1);
    assert.ok(!triage.some((b) => b.id === "triage-passthrough"));
    assert.equal(model.metrics.get("triage:mode:direct").count, 1);
    assert.equal(model.edges.find((e) => e.id === "e6").metric.id, "reviews-pass");
    assert.equal(model.edges.find((e) => e.id === "e6").metric.count, 1);
    assert.equal(model.metrics.get("packages").count, 0);
});


test("compact cards hide zeros, cap visible results and keep recommendations discoverable", () => {
    const stage = {id: "intel", metrics: [
        ["intel-unstarted", 674], ["intel-waiting-records", 0], ["intel-running-records", 0],
        ["intel-failed-records", 4], ["intel-evidence", 18], ["intel-watch", 4],
        ["intel-outside", 2], ["intel-recommend", 2],
    ].map(([id, count]) => ({id, count}))};
    const preview = stagePreview(stage);
    assert.equal(preview.visible.length, 4);
    assert.ok(preview.visible.every((m) => m.count > 0));
    assert.ok(preview.visible.some((m) => m.id === "intel-recommend"));
    assert.equal(stageResults(stage).length, 8);
    assert.equal(preview.hidden, 4);
    stage.metrics.forEach((m) => m.count = 0);
    assert.equal(stagePreview(stage).visible.length, 0);
    stage.metrics[0].count = null;
    assert.equal(stagePreview(stage).visible[0].count, null);
});


test("orthogonal dimensions partition a fixed population and separate direct routing from pass", () => {
    const data = fixture();
    data.discovery = {selected: "batch-a", sources: [], articles: [
        {id: "pending", material_id: "p", state: "awaiting_triage"},
        {id: "pass", material_id: "a", route: "analyze", state: "awaiting_intelligence", triage_method: "local_model"},
        {id: "reject", material_id: "r", route: "exclude", state: "not_selected"},
        {id: "fail", material_id: "f", route: "analyze", triage_error: "Timeout", state: "awaiting_triage"},
        {id: "direct", material_id: "d", route: "analyze", state: "awaiting_intelligence", triage_method: "semantic_passthrough"},
        {id: "reuse", material_id: "h", state: "already_seen"},
    ]};
    data.artifacts.task_links = [];
    data.artifacts.decisions = [];
    data.artifacts.receipts = [];
    const model = completeNodeMetrics(projectFlow(data), data);
    for (const stage of model.stages) for (const dimension of stage.dimensions) {
        if (dimension.total === undefined || dimension.total === null) continue;
        assert.equal(dimension.metrics.reduce((sum, m) => sum + m.count, 0), dimension.total, dimension.title);
        const ids = dimension.metrics.flatMap((m) => m.rows.map((r) => r.id));
        assert.equal(ids.length, new Set(ids).size, dimension.title);
    }
    assert.equal(model.metrics.get("triage:result:pending").count, 1);
    assert.equal(model.metrics.get("triage:result:pass").count, 1);
    assert.equal(model.metrics.get("triage:result:reject").count, 1);
    assert.equal(model.metrics.get("triage:result:failed").count, 1);
    assert.equal(model.metrics.get("triage:mode:direct").count, 1);
    assert.equal(model.metrics.get("triage:mode:reuse").count, 1);
    assert.ok(model.stages.find((s) => s.id === "intel").dimensions.some((d) => d.title === "精筛结果"));
});


test("intelligence has five exclusive results; historical watch and evidence gaps are rejected", () => {
    const data = fixture();
    const cases = [
        ["new", null, "", "pending"], ["queued", "pending", "", "pending"],
        ["running", "running", "", "running"], ["error", "failed", "recommend", "failed"],
        ["blocked", "blocked", "", "failed"], ["invalid", "complete", "", "failed"],
        ["recommend", "complete", "recommend", "recommend"], ["watch", "complete", "watch", "rejected"],
        ["evidence", "complete", "needs_evidence", "rejected"], ["outside", "complete", "out_of_scope", "rejected"],
    ];
    data.discovery = {selected: "batch-a", sources: [], articles: cases.map(([id]) => ({
        id, material_id: id, source_id: "s", route: "analyze", state: "awaiting_intelligence",
    }))};
    data.intelligence.items = cases.filter(([, status]) => status).map(([id, status, disposition]) => ({
        id: "task-" + id, material_ref: id, source_record_id: id, parent_ref: "batch-a",
        mode: "production", status, disposition,
    }));
    const model = completeNodeMetrics(projectFlow(data), data);
    const stage = model.stages.find((s) => s.id === "intel");
    const dimension = stage.dimensions.find((d) => d.title === "精筛结果");
    assert.equal(dimension.metrics.length, 5);
    assert.equal(dimension.total, cases.length);
    assert.equal(dimension.metrics.reduce((sum, m) => sum + m.count, 0), cases.length);
    for (const [id, , , expected] of cases) {
        assert.ok(model.metrics.get("intel:result:" + expected).rows.some((r) => r.id === id));
    }
    assert.equal(stage.dimensions.some((d) => d.title === "已完成评估的结论"), false);
    assert.ok(!stageResults(stage).some((m) => /观察|待补证|处理完成/.test(m.label)));
    assert.equal(model.metrics.get("intel:result:rejected").rows.find((r) => r.id === "evidence").detail.original_disposition, "needs_evidence");
    assert.ok(!model.attention.rows.some((r) => r.id === "evidence" || r.id === "task-evidence"));
    assert.ok(model.attention.rows.some((r) => r.id === "invalid"));
});

test("terminal intelligence exclusion is completed and never becomes follow-up work", () => {
    const data = fixture();
    data.intelligence.items[0].disposition = "exclude";
    data.intelligence.items[0].status = "complete";
    const initial = projectFlow(data);
    assert.equal(initial.metrics.get("intel-excluded").count, 1);
    assert.ok(!initial.attention.rows.some(r => r.id === "production-a"));
    const model = completeNodeMetrics(initial, data);
    assert.equal(model.metrics.get("intel-excluded").count, 1);
    assert.equal(model.metrics.get("intel-evidence").count, 0);
    assert.ok(!model.attention.rows.some(r => r.id === "production-a"));
});
