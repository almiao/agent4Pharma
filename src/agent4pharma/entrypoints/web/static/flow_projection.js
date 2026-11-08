import { productionRoleByNode } from "./console_routes.js";
/* 只投影已读取的业务记录；每个指标保留同一组对象及进入工作区的筛选。 */
export const positions = {
    ingest: [60, 60], triage: [300, 60], intel: [540, 60], editorial: [780, 60],
    content: [780, 350], review: [540, 350], publishing: [300, 350],
};
export const names = {
    ingest: "来源与采集", triage: "粗筛", intel: "情报精筛", editorial: "主编选题",
    content: "内容写作", review: "编辑核验", publishing: "发行归档",
};
export const statusNames = {
    queued: "排队", pending: "待评估", running: "运行中", succeeded: "完成", failed: "失败", blocked: "阻断",
    cancelled: "取消", complete: "完成", interrupted: "中断", recommend: "正式推荐",
    exclude: "已排除", needs_evidence: "历史待补证", watch: "观察", out_of_scope: "范围外", pass: "审核通过",
    changes_requested: "要求返修", local_preview: "本地包 · 导出前校验", saved: "已保存",
};
const href = (path, values = {}) => path + "?" + new URLSearchParams(Object.entries(values).filter(([, v]) => v));
const metric = (id, label, unit, rows, url, scope, tone = "green") => ({
    id, label, unit, rows, url, scope, tone, count: rows === null ? null : rows.length,
});

/* 方框只展示本环节的处理分支；连线单独表示下一环节输入。 */
export function stageResults(stage) {
    if (stage.resultDimension) return stage.resultDimension.metrics;
    const branches = {
        ingest: [["collected-records", "已归档"], ["sources-complete", "采集完成"], ["sources-failed", "采集失败"], ["sources-partial", "部分完成"], ["sources-running", "采集中"], ["sources-pending", "待采集"], ["duplicate-records", "重复记录"]],
        triage: [["triage-pending", "待粗筛"], ["triage-admitted", "粗筛通过"], ["exclude", "粗筛排除"], ["triage-passthrough", "直送精筛"], ["triage-history", "待补证"], ["triage-failed", "粗筛失败"], ["triage-reused", "已复用"], ["triage-unclassified", "其他待处理"]],
        intel: [["intel-unstarted", "待精筛"], ["intel-waiting-records", "排队中"], ["intel-running-records", "执行中"], ["intel-failed-records", "失败 / 中断"], ["intel-evidence", "待补证"], ["intel-watch", "观察"], ["intel-excluded", "已排除"], ["intel-outside", "历史范围外"], ["intel-recommend", "正式推荐"]],
        editorial: [["editorial-unselected", "待处理"], ["editorial-commission", "已立项"], ["editorial-defer", "延期"], ["editorial-decline", "不立项"], ["editorial-request_evidence", "待补证"], ["editorial-running", "处理中"], ["editorial-failed", "处理失败"]],
        content: [["content-queued", "待写作"], ["content-running", "写作中"], ["drafts", "已有稿件"], ["content-rework", "返工"], ["content-blocked", "阻断"], ["content-failed", "写作失败"]],
        review: [["review-queued", "待核验"], ["review-running", "核验中"], ["reviews-pass", "通过"], ["review-changes", "要求返修"], ["review-blocked", "阻断"], ["review-failed", "核验失败"]],
        publishing: [["publishing-queued", "待打包"], ["publishing-running", "打包中"], ["packages", "有效本地包"], ["packages-exported", "已导出"], ["packages-invalid", "已失效"], ["publishing-blocked", "阻断"], ["publishing-failed", "打包失败"]],
    };
    return (branches[stage.id] || []).flatMap(([id, label]) => {
        const metric = stage.metrics.find((m) => m.id === id);
        return metric ? [{ ...metric, label }] : [];
    });
}

export function stagePreview(stage) {
    const results = stageResults(stage);
    const active = results.filter((m) => m.count === null || m.count > 0);
    const pinned = new Set(["collected-records", "triage-pending", "intel-unstarted", "intel-recommend", "intel:result:recommend"]);
    const selected = [...active.filter((m) => pinned.has(m.id)), ...active.filter((m) => !pinned.has(m.id))].slice(0, 4);
    return {
        visible: results.filter((m) => selected.some((s) => s.id === m.id)),
        hidden: (stage.dimensions ? stage.dimensions.reduce((sum, d) => sum + d.metrics.length, 0) : results.length) - selected.length,
    };
}

export function projectFlow(data) {
    const stages = [], all = new Map();
    const add = (id, description, metrics, primary = 0) => {
        metrics.forEach((m) => all.set(m.id, m));
        const attention = ["red", "amber", "running"].map((tone) => metrics.find((m) => m.tone === tone && m.count > 0)).find(Boolean);
        stages.push({ id, name: names[id], description, metrics, primary: metrics[primary], status: attention || metrics[primary] });
    };
    const rss = data.rss;
    const batch = rss?.selected || "";
    const batchScope = `RSS 批次 ${batch || "暂无"}，最近最多 50 个批次；不代表所有来源覆盖。`;
    const batches = rss ? rss.batches.map((b) => ({ id: b.batch_id, title: b.batch_id,
        subtitle: Object.entries(b.states).map(([k, v]) => `${k}: ${v}`).join(" · "),
        href: href("/intelligence", { view: "triage", batch: b.batch_id }), kind: "batch" })) : null;
    const sources = data.sources ? data.sources.items.map((s) => ({ id: s.id, title: s.name,
        subtitle: `${s.source_kind} · ${s.status}`,
        href: href("/sources", { source: s.id }), kind: "source" })) : null;
    add("ingest", data.runScope ? "当前批次的采集与预选记录" : "登记来源与已存在的 RSS 批次", [
        metric("batches", "RSS 批次", "个批次", batches, "/sources", batchScope),
        ...data.runScope ? [] : [metric("sources", "目录来源", "个来源", sources, "/sources", "来源目录最多读取 200 项；目录登记不表示已完成采集。")],
    ]);
    const decisions = rss ? rss.decisions.map((d) => ({ id: d.id, title: d.input.title,
        subtitle: `${d.input.journal} · ${d.route}`, status: d.route,
        href: href("/intelligence", { view: "triage", batch, route: d.route, decision: d.id, material: d.input.material_ref }),
        kind: "decision", detail: { route: d.route, reason: d.routing_reason, model: d.model_revision, policy_hash: d.policy_hash },
    })) : null;
    add("triage", "本批次的预选结果，不直接生成候选", [
        metric("analyze", "进入详情分析", "篇资料", decisions?.filter((d) => d.status === "analyze") ?? null, href("/intelligence", { view: "triage", batch, route: "analyze" }), batchScope),
        metric("decisions", "全部预选结果", "份结果", decisions, href("/intelligence", { view: "triage", batch }), batchScope),
        metric("exclude", "粗筛排除", "篇资料", decisions?.filter((d) => d.status === "exclude") ?? null, href("/intelligence", { view: "triage", batch, route: "exclude" }), batchScope),
    ]);
    const intelScope = "最近 100 次评估中 mode=production 的任务；回放/自检不计入。独立于所选 RSS 批次。";
    const intel = data.intelligence ? data.intelligence.items.filter((t) => t.mode === "production" && t.stage !== "abstract" && t.current !== false).map((t) => ({
        id: t.id, title: t.title || t.id, subtitle: t.summary || `${statusNames[t.status] || t.status} · ${statusNames[t.disposition] || t.disposition || "未形成结论"}`,
        status: t.status, disposition: t.disposition, kind: "intelligence", stage: "intel",
        href: href("/intelligence", { view: "assessments", mode: "production", task: t.id }),
    })) : null;
    const candidates = data.candidates ? data.candidates.items.map((c) => ({ id: c.id,
        title: data.intelligence?.items.find((t) => t.id === c.task_id)?.title || c.card.summary || c.id,
        subtitle: c.card.attention_reason || c.card.reason, kind: "candidate", stage: "intel",
        href: href("/intelligence", { view: "candidates", candidate: c.id }), detail: { id: c.id, material_ref: c.material_ref, task_id: c.task_id, policy_id: c.policy_id },
    })) : null;
    add("intel", "正式评估与候选，保留证据和版本", [
        metric("candidates", "正式候选", "个候选", candidates, href("/intelligence", { view: "candidates" }), "正式候选池最近最多 200 项，不限当前 RSS 批次。"),
        metric("intel-excluded", "已排除", "次评估", intel?.filter((t) => t.disposition === "exclude") ?? null, href("/intelligence", { view: "assessments", mode: "production", disposition: "exclude" }), intelScope, "neutral"),
        metric("intel-evidence", "待补证", "次评估", intel?.filter((t) => t.disposition === "needs_evidence") ?? null, href("/intelligence", { view: "assessments", mode: "production", disposition: "needs_evidence" }), intelScope, "amber"),
        metric("intel-failed", "失败或中断", "个任务", intel?.filter((t) => ["failed", "interrupted"].includes(t.status)) ?? null, href("/intelligence", { view: "assessments", mode: "production", task_status: "error" }), intelScope, "red"),
        metric("intel-pending", "待评估", "个任务", intel?.filter((t) => t.status === "pending") ?? null, href("/intelligence", { view: "assessments", mode: "production", task_status: "pending" }), intelScope, "running"),
        metric("intel-running", "运行中", "个任务", intel?.filter((t) => t.status === "running") ?? null, href("/intelligence", { view: "assessments", mode: "production", task_status: "running" }), intelScope, "running"),
        metric("intel-tasks", "正式评估", "个任务", intel, href("/intelligence", { view: "assessments", mode: "production" }), intelScope),
    ]);
    const production = data.production;
    const nodeForRole = Object.fromEntries(Object.entries(productionRoleByNode).map(([node, role]) => [role, node]));
    const tasks = production ? production.tasks.map((t) => ({ id: t.id, stage: nodeForRole[t.role],
        title: production.stories.find((s) => s.id === t.story_id)?.brief.title || ({"paper-understanding":"论文理解", "context-research":"背景调查", "topic-selection":"新闻选题"}[t.phase] || "立项任务"),
        subtitle: `${statusNames[t.status] || t.status} · 尝试 ${t.attempt}/${t.limits.max_attempts}${t.error ? " · " + t.error : ""}`,
        status: t.status, kind: "task", result: t.result_ref, story: t.story_id,
        href: href("/" + nodeForRole[t.role], { task: t.id, story: t.story_id, view: "tasks" }),
    })) : null;
    const artifacts = data.artifacts ? data.artifacts.items.map((a) => ({ id: a.id, title: a.title,
        subtitle: `${a.version} · ${statusNames[a.status] || a.status}`, status: a.status,
        kind: a.kind, reference: a.reference_id, story: a.story_id, created_at: a.created_at,
        href: href("/" + ({ draft: "content", review: "review", package: "publishing" }[a.kind]), { story: a.story_id, artifact: a.id, view: "artifacts" }),
    })) : null;
    const scope = "当前持久化制作记录，包含历史任务与产物版本；按准确对象身份计数，不等同于今日新增或有效发行量。";
    for (const role of ["editorial", "content", "review", "publishing"]) {
        const roleTasks = tasks?.filter((t) => t.stage === role) ?? null;
        const base = "/" + role;
        const metrics = [];
        if (role === "editorial") metrics.push(metric("stories", "选题", "个选题", production ? production.stories.map((s) => ({ id: s.id, title: s.brief.title, subtitle: `任务书 v${s.brief_version} · 修订 ${s.revision} · ${s.status}`, kind: "story", href: href(base, { story: s.id, view: "stories" }) })) : null, href(base, { view: "stories" }), scope));
        if (role === "content") metrics.push(metric("drafts", "已存稿件版本", "个版本", artifacts?.filter((a) => a.kind === "draft") ?? null, href(base, { view: "artifacts" }), scope));
        if (role === "review") metrics.push(metric("reviews-pass", "通过报告", "份报告", artifacts?.filter((a) => a.kind === "review" && a.status === "pass") ?? null, href(base, { view: "artifacts", decision: "pass" }), scope + "通过报告包含历史版本，不作为当前发行许可。"));
        if (role === "publishing") metrics.push(metric("packages", "本地预览包", "个包", artifacts?.filter((a) => a.kind === "package") ?? null, href(base, { view: "artifacts" }), scope + "导出前仍须校验有效性，外部发送尚未接入。"));
        metrics.push(metric(role + "-failed", "技术失败", "个任务", roleTasks?.filter((t) => t.status === "failed") ?? null, href(base, { status: "failed", view: "tasks" }), scope, "red"));
        metrics.push(metric(role + "-blocked", "业务阻断", "个任务", roleTasks?.filter((t) => t.status === "blocked") ?? null, href(base, { status: "blocked", view: "tasks" }), scope, "amber"));
        metrics.push(metric(role + "-queued", "排队", "个任务", roleTasks?.filter((t) => t.status === "queued") ?? null, href(base, { status: "queued", view: "tasks" }), scope, "running"));
        metrics.push(metric(role + "-running", "运行中", "个任务", roleTasks?.filter((t) => t.status === "running") ?? null, href(base, { status: "running", view: "tasks" }), scope, "running"));
        metrics.push(metric(role + "-tasks", "全部任务", "个任务", roleTasks, href(base, { view: "tasks" }), scope));
        if (role === "review") metrics.push(metric("review-changes", "修改报告", "份报告", artifacts?.filter((a) => a.kind === "review" && a.status === "changes_requested") ?? null, href(base, { decision: "changes_requested", view: "artifacts" }), scope + "修改报告保留历史，不能推断所有报告仍待返工。", "neutral"));
        add(role, { editorial: "候选引用、任务书与立项", content: "稿件版本与写作任务", review: "准确版本审核与修改要求", publishing: "本地预览、有效性校验与导出" }[role], metrics);
    }
    const attentionRows = new Map();
    for (const m of all.values()) if (m.id === "intel-evidence" || m.id === "intel-failed" || /-(failed|blocked)$/.test(m.id)) for (const r of m.rows || []) attentionRows.set(r.kind + ":" + r.id, r);
    const knownAttention = data.production || data.intelligence;
    const attention = metric("attention", "已读取范围待处理", "项", knownAttention ? [...attentionRows.values()] : null, "/console?panel=attention", "按对象去重的技术失败、业务阻断与待补证；不把历史修改报告直接计作未处理。部分接口失败时仅代表已读取范围。", "amber");
    all.set(attention.id, attention);
    const edges = [
        ["e1", "ingest", "triage", "decisions"], ["e2", "triage", "intel", "analyze"],
        ["e3", "intel", "editorial", "candidates"], ["e4", "editorial", "content", "content-tasks"],
        ["e5", "content", "review", "reviews-pass"], ["e6", "review", "publishing", "packages"],
        ["f1", "review", "content", "review-changes"],
    ].map(([id, from, to, key]) => ({ id, from, to, metric: all.get(key), feedback: id === "f1" }));
    // e5 只列真正引用稿件的审核任务；成功写作并不自动等于审核通过。
    const draftIds = new Set(artifacts?.filter((a) => a.kind === "draft").map((a) => a.id));
    const reviewedIds = new Set(artifacts?.filter((a) => a.kind === "review" && draftIds.has(a.reference)).map((a) => a.reference));
    const handoff = metric("draft-handoff", "有审核报告的稿件", "个版本", artifacts ? artifacts.filter((a) => a.kind === "draft" && reviewedIds.has(a.id)) : null, href("/review", { view: "artifacts" }), scope + "仅按报告引用确认关联，不推断未完成审核的交接时延。");
    all.set(handoff.id, handoff); edges.find((e) => e.id === "e5").metric = handoff;
    if (data.runScope) {
        all.get("batches").url = href("/intelligence", { view: "triage", batch });
        for (const m of all.values()) m.scope = data.runScope +
            (m.id === "packages" ? " 本地包导出前重新校验有效性。" : "") +
            (m.id === "review-changes" ? " 修改报告包含历史版本，不等同于未处理返工。" : "");
    }
    return { stages, metrics: all, edges, attention };
}
