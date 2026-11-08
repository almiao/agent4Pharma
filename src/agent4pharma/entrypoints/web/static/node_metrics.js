/* 节点业务指标：输入/当前状态/历史版本分开；每个数量保留可抽查的同一对象集合。 */
import { productionRoleByNode } from "./console_routes.js";
import { configureStageDimensions } from "./stage_dimensions.js";
const path = (base, params) => base + "?" + new URLSearchParams(params);
const unique = (rows, key) => [...new Map(rows.map((r) => [r[key], r])).values()];
const labels = { ready: "阅读通过", rebrief_needed: "需重新判断选题", not_started: "未启动", pending: "待执行", running: "运行中", complete: "完成", partial: "部分完成", failed: "失败", selected: "推荐", watch: "观察", needs_evidence: "待补证", not_selected: "不入选", already_seen: "历史复用", awaiting_triage: "待粗筛", awaiting_intelligence: "待精筛", analyze: "进入分析", exclude: "不进入", needs_triage_evidence: "历史待补证", saved: "已保存", pass: "通过", changes_requested: "要求返修", blocked: "阻断" };

export function completeNodeMetrics(model, data) {
    const scope = data.runScope, batch = data.discovery?.selected || "";
    const metric = (stageId, id, label, unit, rows, url, tone = "green", primary = false) => {
        const stage = model.stages.find((s) => s.id === stageId);
        const m = { id, label, unit, rows, count: rows === null ? null : rows.length, url, tone, scope, filterable: ["ingest", "triage"].includes(stageId) };
        model.metrics.set(id, m);
        const index = stage.metrics.findIndex((old) => old.id === id);
        if (index >= 0) stage.metrics[index] = m; else stage.metrics.push(m);
        if (primary) stage.primary = m;
        return m;
    };
    const articleUrl = (extra = {}) => path("/intelligence", { view: "records", batch, ...extra });
    const rawArticles = data.discovery?.articles;
    const articles = rawArticles?.map((a) => ({
        id: a.id, material: a.material_id, source: a.source_id, title: a.title,
        subtitle: `${a.source_id} · ${labels[a.state] || a.state}${a.triage_error ? " · " + a.triage_error : ""}`,
        state: a.state, route: a.route, kind: "article", stage: "triage", error: a.triage_error, category: a.category || "unknown",
        triage_method: a.triage_method || "unknown",
        duplicate_kind: a.duplicate_kind || "", ignore_recommended: !!a.ignore_recommended,
        href: articleUrl({ record: a.id }),
        detail: a,
    })) ?? null;
    if (data.discovery) {
        const sourceRows = data.discovery.sources.map((s) => {
            const availability = { available: "采集入口已验证", currently_unavailable: "当前无法采集", needs_configuration: "待接入" }[s.collection_availability?.status];
            const related = rawArticles.filter((a) => a.source_id === s.source_id);
            const counts = {
                analyze: related.filter((a) => a.route === "analyze").length,
                exclude: related.filter((a) => a.route === "exclude").length,
                pending: related.filter((a) => a.state === "awaiting_triage" && !a.triage_error).length,
                failed: related.filter((a) => a.triage_error).length,
            };
            const breakdown = {};
            for (const [key, label, select] of [
                ["records", "归档来源项", () => true],
                ["unique", "去重资料", () => true],
                ["analyze", "进入精筛", (a) => a.route === "analyze"],
                ["exclude", "不进入精筛", (a) => a.route === "exclude"],
                ["pending", "待粗筛", (a) => a.state === "awaiting_triage" && !a.error],
                ["failed", "粗筛失败", (a) => !!a.error],
            ]) {
                let rows = articles.filter((a) => a.source === s.source_id && select(a));
                if (key === "unique") rows = unique(rows, "material");
                if (["records", "unique"].includes(key) && s.records === null) rows = null;
                const id = `source:${s.source_id}:${key}`;
                const m = { id, label: (s.name || s.source_id) + " · " + label, unit: key === "unique" ? "篇资料" : "个来源项", rows, count: rows === null ? null : rows.length,
                    tone: key === "failed" ? "red" : "green", scope, filterable: true,
                    url: articleUrl({ source: s.source_id }),
                };
                model.metrics.set(id, m); breakdown[key] = m;
            }
            return { id: s.source_id, title: s.name || s.source_id, kind: "run-source", stage: "ingest", status: s.status, breakdown, category: s.category || "unknown",
                subtitle: `${labels[s.status] || s.status}${availability ? " · " + availability : ""} · ${s.diagnostic ? (s.diagnostic.http_status ? "HTTP " + s.diagnostic.http_status + " · " : "") + s.diagnostic.reason : s.error || `${s.records ?? "未知"} 条已归档来源记录 · ${s.unique_materials ?? "未知"} 篇去重资料`}`,
                href: articleUrl({ source: s.source_id, collection: s.source_id }), detail: { ...s, ...counts },
            };
        });
        model.sourceRows = sourceRows;
        const target = model.stages.find((s) => s.id === "ingest"); target.metrics = [];
        metric("ingest", "collected-records", "已归档来源记录", "条来源记录", articles, articleUrl(), "green", true);
        metric("ingest", "unique-materials", "去重资料", "篇资料", unique(articles, "material"), articleUrl());
        metric("ingest", "duplicate-records", "重复来源项", "条来源记录", articles.filter((a) => a.duplicate_kind), articleUrl({ record_state: "duplicates" })).description = "按本批资料身份标记。同一资料的多个来源建议合并阅读，仍保留出处；新版本不会直接建议忽略。";
        metric("ingest", "ignored-duplicates", "已复用，可忽略", "条来源记录", articles.filter((a) => a.ignore_recommended), articleUrl({ record_state: "ignored" })).description = "仅统计明确复用既有同策略评估的来源版本。已经入库但需要重新评估的资料不计入。";
        metric("ingest", "run-sources", "来源明细", "个来源", sourceRows, articleUrl());
        for (const [id, label, states, tone] of [
            ["sources-complete", "采集完成", ["complete"], "green"],
            ["sources-pending", "未启动 / 待执行", ["not_started", "pending"], "running"],
            ["sources-running", "正在采集", ["running"], "running"],
            ["sources-partial", "部分完成", ["partial"], "amber"],
            ["sources-failed", "采集失败", ["failed"], "red"],
        ]) metric("ingest", id, label, "个来源", sourceRows.filter((s) => states.includes(s.status)), articleUrl(), tone);
        metric("ingest", "sources-empty", "成功空结果", "个来源", sourceRows.filter((s) => s.status === "complete" && s.detail.records === 0), articleUrl());
        const triage = model.stages.find((s) => s.id === "triage"); triage.metrics = [];
        for (const [id, label, select, tone] of [
            ["analyze", "累计转交精筛", (a) => a.route === "analyze", "green"],
            ["exclude", "不进入精筛", (a) => a.route === "exclude", "green"],
            ["triage-pending", "待粗筛", (a) => a.state === "awaiting_triage" && !a.error, "running"],
            ["triage-failed", "粗筛技术失败", (a) => !!a.error, "red"],
            ["triage-history", "历史待补证", (a) => a.route === "needs_evidence", "amber"],
            ["triage-reused", "既有评估复用", (a) => a.state === "already_seen" && !a.route, "green"],
            ["triage-unclassified", "尚未分流 / 其他", (a) => !a.route && !["awaiting_triage", "already_seen"].includes(a.state) && !a.error, "neutral"],
            ["decisions", "本批来源项", () => true, "green"],
        ]) metric("triage", id, label, "个来源项", articles.filter(select), articleUrl(), tone);
        const routed = articles.filter((a) => a.route && !a.error);
        metric("triage", "triage-processed", "已分流", "条已分流", routed, articleUrl(), "green", true);
        metric("triage", "triage-passthrough", "直送精筛（未粗筛）", "条来源项", routed.filter((a) => a.triage_method === "semantic_passthrough"), articleUrl(), "neutral").description = "本批冻结配置为直接语义精筛。这里只完成分流，没有本地模型粗筛判断。";
        metric("triage", "triage-screened", "已有粗筛判断", "条来源项", routed.filter((a) => ["local_model", "keyword_gate"].includes(a.triage_method)), articleUrl());
        metric("triage", "triage-admitted", "粗筛通过", "条来源项", routed.filter((a) => a.route === "analyze" && a.triage_method !== "semantic_passthrough"), articleUrl());
        const tasks = data.intelligence?.items.filter((t) => t.current !== false && t.mode === "production" && t.stage !== "abstract");
        const tasksFor = (a) => tasks?.filter((t) => t.material_ref === a.material &&
            (a.detail.intelligence_task_id === t.id || (t.parent_ref === batch && t.source_record_id === a.id)));
        const latestTask = (a) => tasksFor(a)?.sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || "") || b.id.localeCompare(a.id))[0];
        const input = articles.filter((a) => a.route === "analyze" || a.state === "awaiting_intelligence" || tasksFor(a)?.length);
        metric("intel", "intel-input", "精筛输入", "条精筛输入", input, articleUrl(), "green", true);
        metric("intel", "intel-unstarted", "待创建精筛任务", "条来源项", tasks ? input.filter((a) => !tasksFor(a).length) : null, articleUrl(), "running");
        for (const [id, label, statuses, tone] of [
            ["intel-waiting-records", "已建任务待执行", ["pending", "queued"], "running"],
            ["intel-running-records", "精筛执行中", ["running"], "running"],
            ["intel-failed-records", "精筛失败 / 中断", ["failed", "interrupted"], "red"],
            ["intel-completed-records", "已完成精筛", ["complete", "succeeded"], "green"],
        ]) metric("intel", id, label, "条来源项", tasks ? input.filter((a) => {
            const latest = latestTask(a);
            return latest && statuses.includes(latest.status);
        }) : null, articleUrl(), tone);
        const knownTaskStatuses = new Set(["pending", "queued", "running", "failed", "interrupted", "complete", "succeeded"]);
        metric("intel", "intel-other-records", "其他任务状态", "条来源项", tasks ? input.filter((a) => {
            const latest = latestTask(a);
            return latest && !knownTaskStatuses.has(latest.status);
        }) : null, articleUrl(), "neutral");
        const triageStage = model.stages.find((s) => s.id === "triage");
        triageStage.name = "粗筛与分流";
        triageStage.description = "已分流 = 转交精筛 + 排除 + 待补证；直送精筛单独标记，不计为模型粗筛完成。";
        const intelStage = model.stages.find((s) => s.id === "intel");
        intelStage.description = "主数字统计来源项输入；待创建、执行中、完成分别查看，正式候选是完成评估后的产出。";
    }
    const intel = data.intelligence?.items.filter((t) => t.current !== false && t.mode === "production" && t.stage !== "abstract");
    const assessmentRows = (rows) => rows?.map((t) => ({ id: t.id, title: t.title || t.id, kind: "intelligence", stage: "intel",
        subtitle: `${labels[t.disposition] || t.disposition || labels[t.status] || t.status} · 摘要 ${t.abstract_state} · 原文 ${t.fulltext_state}`,
        href: path("/intelligence", { view: "assessments", batch, task: t.id, mode: "production" }),
    })) ?? null;
    const intelUrl = path("/intelligence", { view: "assessments", batch, mode: "production" });
    for (const [id, label, result] of [["intel-recommend", "正式推荐", "recommend"], ["intel-watch", "观察", "watch"], ["intel-excluded", "已排除", "exclude"], ["intel-outside", "历史范围外", "out_of_scope"]])
        metric("intel", id, label, "次评估", assessmentRows(intel?.filter((t) => t.disposition === result)), intelUrl);
    metric("intel", "intel-fulltext", "完整原文已取得", "次评估", assessmentRows(intel?.filter((t) => t.fulltext_state === "available")), intelUrl);
    metric("intel", "intel-no-fulltext", "原文缺失 / 不完整 / 未知", "次评估", assessmentRows(intel?.filter((t) => t.fulltext_state !== "available")), intelUrl, "neutral");
    metric("intel", "intel-abstract", "摘要已取得", "次评估", assessmentRows(intel?.filter((t) => t.abstract_state === "found")), intelUrl);
    metric("intel", "intel-abstract-error", "摘要读取失败", "次评估", assessmentRows(intel?.filter((t) => t.abstract_state === "error")), intelUrl, "neutral");
    metric("intel", "intel-history", "已替代的历史评估", "次评估", assessmentRows(data.intelligence?.items.filter((t) => t.current === false && t.mode === "production" && t.stage !== "abstract")), path("/intelligence", { view: "assessments", batch, history: "all" }), "neutral");
    if (data.artifacts?.task_links) completeProduction(model, data, metric);
    for (const stage of model.stages) {
        const priority = ["red", "amber", "running"].map((tone) => stage.metrics.find((m) => m.tone === tone && m.count > 0)).find(Boolean);
        stage.status = priority || (stage.id === "triage" && model.metrics.get("triage-passthrough")?.count
            ? model.metrics.get("triage-passthrough") : stage.primary);
        if (stage.id === "triage" && stage.status.id === "triage-passthrough") stage.status.showCount = true;
    }
    const attention = new Map();
    for (const m of model.metrics.values()) if ((m.id !== "attention" && m.tone === "red") || ["intel-evidence", "sources-partial", "review-changes"].includes(m.id) || m.id.endsWith("-blocked"))
        for (const r of m.rows || []) attention.set(r.kind + ":" + r.id, r);
    model.attention.rows = [...attention.values()];
    model.attention.count = data.discovery || data.production || data.intelligence ? attention.size : null;
    for (const e of model.edges) if (model.metrics.has(e.metric.id)) e.metric = model.metrics.get(e.metric.id);
    if (model.metrics.has("collected-records")) model.edges.find((e) => e.id === "e1").metric = model.metrics.get("collected-records");
    model.edges.find((e) => e.id === "e6").metric = model.metrics.get("reviews-pass");
    configureStageDimensions(model, data);
    return model;
}

function completeProduction(model, data, metric) {
    const all = data.artifacts.items, links = data.artifacts.task_links, tasks = data.production?.tasks || [];
    const currentLinks = new Set(links.filter((l) => l.current).map((l) => l.task_id));
    const row = (a) => ({ id: a.id, title: a.title, subtitle: `${a.version} · ${labels[a.status] || a.status}${a.reason ? " · " + a.reason : ""}`, kind: a.kind, story: a.story_id,
        href: path("/" + ({ draft: "content", review: "review", reader_review: "review", package: "publishing" }[a.kind]), { story: a.story_id, artifact: a.id, view: "artifacts" }), detail: a });
    const artifactMetric = (role, id, label, rows, tone = "green", primary = false) => metric(role, id, label, role === "publishing" ? "个包" : "篇稿件", rows.map(row), path("/" + role, { view: "artifacts" }), tone, primary);
    for (const role of ["editorial", "content", "review", "publishing"]) {
        const original = model.metrics.get(role + "-tasks");
        metric(role, role + "-tasks", "当前处理任务", "个任务", original.rows.filter((r) => currentLinks.has(r.id)), original.url);
        for (const status of ["failed", "blocked", "queued", "running"]) {
            const previous = model.metrics.get(role + "-" + status);
            const ids = new Set(tasks.filter((t) => t.role === productionRoleByNode[role] && t.status === status && currentLinks.has(t.id)).map((t) => t.id));
            metric(role, previous.id, previous.label, previous.unit, previous.rows.filter((r) => ids.has(r.id)), previous.url, previous.tone);
        }
        const old = model.metrics.get(role + "-failed");
        const past = tasks.filter((t) => t.role === productionRoleByNode[role] && t.status === "failed" && !currentLinks.has(t.id));
        metric(role, role + "-historical-failed", "历史失败（已非当前任务）", "个任务", past.map((t) => ({ id: t.id, title: t.id, subtitle: t.error, kind: "task", href: path("/" + role, { task: t.id, view: "tasks" }) })), old.url, "neutral");
    }
    const drafts = all.filter((a) => a.kind === "draft" && a.current);
    artifactMetric("content", "drafts", "当前稿件", drafts, "green", true);
    artifactMetric("content", "drafts-history", "历史稿件版本", all.filter((a) => a.kind === "draft" && !a.current), "neutral");
    const submitted = new Set(links.filter((l) => l.draft_id && tasks.some((t) => t.id === l.task_id && t.role === "reviewer")).map((l) => l.draft_id));
    artifactMetric("content", "draft-handoff", "当前稿件已交审", drafts.filter((d) => submitted.has(d.id)));
    const reading = all.filter((a) => a.kind === "reader_review" && a.current);
    artifactMetric("review", "reading-ready", "阅读品质通过", reading.filter((a) => a.status === "ready"), "green");
    artifactMetric("review", "reading-changes", "阅读品质需修改", reading.filter((a) => a.status === "changes_requested"), "amber");
    artifactMetric("review", "reading-rebrief", "阅读需重新判断选题", reading.filter((a) => a.status === "rebrief_needed"), "amber");
    artifactMetric("review", "reading-history", "历史阅读评审", all.filter((a) => a.kind === "reader_review" && !a.current), "neutral");
    const reviews = all.filter((a) => a.kind === "review" && a.current);
    artifactMetric("review", "reviews-pass", "当前版本通过", reviews.filter((a) => a.status === "pass"), "green", true);
    artifactMetric("review", "review-changes", "当前要求返修", reviews.filter((a) => a.status === "changes_requested"), "amber");
    artifactMetric("review", "review-blocked", "当前审核阻断", reviews.filter((a) => a.status === "blocked"), "amber");
    artifactMetric("review", "reviews-history", "旧版 / 已替代审核", all.filter((a) => a.kind === "review" && !a.current), "neutral");
    const rework = links.filter((l) => l.current && l.rework);
    metric("content", "content-rework", "当前返工任务", "个任务", rework.map((l) => {
        const t = tasks.find((t) => t.id === l.task_id); return { id: t.id, title: data.production.stories.find((s) => s.id === t.story_id)?.brief.title || t.id,
            subtitle: `${labels[t.status] || t.status} · 依据 ${l.review_ids.join(", ")}`, kind: "task", href: path("/content", { story: t.story_id, task: t.id, view: "tasks" }) };
    }), "/content?view=tasks", "neutral");
    const packages = all.filter((a) => a.kind === "package");
    artifactMetric("publishing", "packages", "当前有效本地包", packages.filter((a) => a.validity === "valid"), "green", true);
    artifactMetric("publishing", "packages-invalid", "已失效的本地包", packages.filter((a) => a.validity === "invalid"), "neutral");
    const exported = new Set(data.artifacts.receipts.filter((r) => r.status === "exported").map((r) => r.package_id));
    artifactMetric("publishing", "packages-exported", "已本地导出的包", packages.filter((a) => exported.has(a.id)));
    metric("publishing", "export-receipts", "本地导出回执", "条回执", data.artifacts.receipts.filter((r) => r.status === "exported").map((r) => ({
        id: r.id, title: r.package_id, subtitle: `${r.status} · ${r.created_at}`, kind: "receipt", detail: r,
        href: path("/publishing", { view: "artifacts", artifact: r.package_id }),
    })), "/publishing?view=artifacts", "neutral");
    const decisions = data.artifacts.decisions;
    const decided = new Set(decisions.flatMap((d) => d.decision.candidate_ids));
    const candidates = model.metrics.get("candidates");
    metric("editorial", "editorial-unselected", "尚无编辑决定的候选", "个候选", candidates.rows?.filter((c) => !decided.has(c.id)) ?? null, "/editorial?view=candidates", "running");
    for (const [decision, label] of [["defer", "延期"], ["decline", "不立项"], ["request_evidence", "主编待补证"], ["commission", "已立项决定"]]) {
        metric("editorial", "editorial-" + decision, label, "份决定", decisions.filter((d) => d.decision.decision === decision).map((d) => ({
            id: d.task_id, title: d.decision.brief?.title || label, subtitle: d.decision.reason + (d.decision.revisit_condition ? " · 重看条件：" + d.decision.revisit_condition : ""), kind: "task",
            href: path("/editorial", { task: d.task_id, view: "tasks" }), detail: d.decision,
        })), "/editorial?view=tasks", "neutral");
    }
    for (const [status, label] of [["active", "进行中选题"], ["deferred", "暂停 / 延期选题"], ["cancelled", "已取消选题"], ["completed", "完成选题"]]) {
        metric("editorial", "stories-" + status, label, "个选题", data.production.stories.filter((s) => s.status === status).map((s) => ({
            id: s.id, title: s.brief.title, subtitle: `${status} · 任务书 v${s.brief_version}`, kind: "story", href: path("/editorial", { story: s.id, view: "stories" }),
        })), "/editorial?view=stories", "neutral");
    }
}
