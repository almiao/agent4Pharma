/* 每组固定统计对象和维度；互斥分区各对象只归一类，汇总和子集另列。 */
export function configureStageDimensions(model, data) {
    const stage = (id) => model.stages.find((s) => s.id === id);
    for (const item of model.stages) item.dimensions = [];
    const partition = (owner, key, title, unit, rows, categories, classify, note = "") => {
        const target = stage(owner);
        const metrics = categories.map(([value, label, tone = "green"]) => {
            const selected = rows === null ? null : rows.filter((r) => classify(r) === value);
            const base = key === "tasks" ? model.metrics.get(owner + "-tasks") : target.primary;
            const metric = { id: `${owner}:${key}:${value}`, label, unit, rows: selected,
                count: selected === null ? null : selected.length, tone, scope: data.runScope,
                url: key === "candidates" ? "/editorial?view=candidates" : base.url, filterable: ["ingest", "triage"].includes(owner) };
            model.metrics.set(metric.id, metric); target.metrics.push(metric);
            return metric;
        });
        target.dimensions.push({ title, unit, total: rows === null ? null : rows.length, note, metrics });
        return metrics;
    };
    const group = (owner, title, ids, note = "") => stage(owner).dimensions.push({
        title, note, metrics: ids.map((id) => model.metrics.get(id)).filter(Boolean),
    });
    const lifecycle = [
        ["pending", "待处理", "running"], ["running", "处理中", "running"],
        ["complete", "处理完成"], ["failed", "处理失败", "red"],
        ["blocked", "业务阻断", "amber"], ["other", "其他状态", "neutral"],
    ];
    const taskState = (status) => {
        if (["pending", "queued", "not_started"].includes(status)) return "pending";
        if (["complete", "succeeded"].includes(status)) return "complete";
        if (["failed", "interrupted"].includes(status)) return "failed";
        return ["running", "blocked"].includes(status) ? status : "other";
    };
    if (data.discovery) {
        partition("ingest", "collection", "采集状态", "个来源", model.sourceRows,
            [["pending", "待采集", "running"], ["running", "采集中", "running"],
                ["complete", "采集完成"], ["partial", "部分完成", "amber"],
                ["failed", "采集失败", "red"], ["other", "其他状态", "neutral"]],
            (r) => r.status === "partial" ? "partial" : ["pending", "running", "complete", "failed"].includes(taskState(r.status)) ? taskState(r.status) : "other");
        group("ingest", "来源记录规模", ["collected-records"]);
        group("ingest", "去重资料规模", ["unique-materials"], "按资料身份去重，不与来源记录数相加。");
        group("ingest", "重复与复用标记", ["duplicate-records", "ignored-duplicates"], "复用可忽略是重复记录的子集，两个标记不相加。");
        group("ingest", "完成来源的取得情况", ["sources-empty"], "成功空结果是采集完成来源的子集，不与采集状态相加。");

        const articles = model.metrics.get("collected-records").rows;
        const direct = (r) => r.triage_method === "semantic_passthrough" && !r.error;
        const reused = (r) => r.state === "already_seen" && !r.error;
        partition("triage", "result", "粗筛结果", "条来源项", articles.filter((r) => !direct(r) && !reused(r)),
            [["pending", "待处理", "running"], ["pass", "筛选通过"], ["reject", "未通过"], ["failed", "筛选失败", "red"]],
            (r) => r.error ? "failed" : r.route === "analyze" ? "pass" : r.route === "exclude" ? "reject" : "pending",
            "仅统计参与粗筛的来源项；直送精筛与复用不算筛选通过。历史待补证、尚未分流归待处理。");
        partition("triage", "mode", "处理方式", "条来源项", articles,
            [["direct", "直送精筛", "neutral"], ["reuse", "复用既有评估", "neutral"], ["screened", "已有粗筛判断"], ["waiting", "粗筛尚未完成", "running"]],
            (r) => direct(r) ? "direct" : reused(r) ? "reuse" : !r.error && ["analyze", "exclude"].includes(r.route) ? "screened" : "waiting",
            "覆盖本批全部来源项。处理方式与粗筛结果是两个维度，不跨组相加。");

        const tasks = data.intelligence?.items.filter((t) => t.current !== false && t.mode === "production" && t.stage !== "abstract");
        const latest = (r) => tasks?.filter((t) => t.material_ref === r.material &&
            (r.detail.intelligence_task_id === t.id || (t.parent_ref === data.discovery.selected && t.source_record_id === r.id)))
            .sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || "") || b.id.localeCompare(a.id))[0];
        const input = model.metrics.get("intel-input").rows;
        const result = (r) => {
            const task = latest(r);
            if (!task || taskState(task.status) === "pending") return "pending";
            if (taskState(task.status) === "running") return "running";
            if (taskState(task.status) !== "complete") return "failed";
            if (task.disposition === "recommend") return "recommend";
            return ["exclude", "watch", "needs_evidence", "out_of_scope"].includes(task.disposition) ? "rejected" : "failed";
        };
        const resultLabels = { pending: "待处理", running: "处理中", failed: "处理失败", recommend: "正式推荐", rejected: "未通过" };
        const reasons = { exclude: "本次精筛已排除，不进入补证队列", watch: "历史评估为观察，未达到正式推荐标准", needs_evidence: "证据不足，未达到正式推荐标准", out_of_scope: "未达到推荐范围或标准" };
        const resultRows = tasks ? input.map((r) => {
            const task = latest(r), state = result(r);
            const reason = task?.error || task?.summary || reasons[task?.disposition] ||
                (state === "failed" ? "任务未完成或未形成有效筛选结论" : "");
            return { ...r, stage: "intel", subtitle: resultLabels[state] + (reason ? " · " + reason : ""),
                detail: { ...r.detail, intelligence_result: state, intelligence_reason: reason,
                    original_disposition: task?.disposition || "" } };
        }) : null;
        partition("intel", "result", "精筛结果", "条来源项", resultRows,
            [["pending", "待处理", "running"], ["running", "处理中", "running"], ["failed", "处理失败", "red"],
                ["recommend", "正式推荐"], ["rejected", "未通过", "neutral"]],
            (r) => r.detail.intelligence_result,
            "每条来源项只归一种结果。历史观察、证据不足归未通过，原因保留在详情；不生成待人工补证分支。任务失败、阻断或缺少有效结论归处理失败。");
        const attention = new Map((model.attention.rows || []).filter((r) => r.kind !== "intelligence").map((r) => [r.kind + ":" + r.id, r]));
        for (const row of model.metrics.get("intel:result:failed").rows || []) attention.set(row.kind + ":" + row.id, row);
        model.attention.rows = [...attention.values()];
        model.attention.count = model.attention.rows.length;
        group("intel", "全文取得情况", ["intel-fulltext", "intel-no-fulltext"], "按评估次数统计，全文状态不与处理结果相加。");
        group("intel", "摘要取得情况", ["intel-abstract", "intel-abstract-error"], "仅列已取得/读取失败的评估，未知或尚未读取不等于失败；可与全文同时存在。");
        group("intel", "历史评估", ["intel-history"], "已替代版本不计入当前处理结果。");
    }
    for (const owner of ["editorial", "content", "review", "publishing"]) {
        const rows = model.metrics.get(owner + "-tasks")?.rows ?? null;
        partition(owner, "tasks", "当前任务处理状态", "个任务", rows, lifecycle, (r) => taskState(r.status),
            "只统计当前任务；业务决定、产物版本及历史任务分别列组，不与任务状态相加。");
        group(owner, "历史任务", [owner + "-historical-failed"], "历史失败已不代表当前任务失败。");
    }
    const decisions = data.artifacts?.decisions;
    const candidates = model.metrics.get("candidates").rows;
    const decided = new Set(decisions?.flatMap((d) => d.decision.candidate_ids));
    partition("editorial", "candidates", "候选处理情况", "个候选", decisions && candidates ? candidates : null,
        [["pending", "待处理", "running"], ["decided", "已有编辑决定"]], (r) => decided.has(r.id) ? "decided" : "pending",
        "统计候选，不统计任务或决定份数。一份决定可涉及多个候选。");
    group("editorial", "编辑决定", ["editorial-commission", "editorial-defer", "editorial-decline", "editorial-request_evidence"], "按决定份数统计，与候选数量不是同一对象。");
    group("editorial", "选题状态", ["stories-active", "stories-deferred", "stories-cancelled", "stories-completed"], "按选题统计。");
    group("content", "稿件版本", ["drafts", "drafts-history"], "当前与历史版本分别保留，不与写作任务数相加。");
    group("content", "写作任务属性", ["content-rework"], "返工是当前任务的属性，可以与任务处理状态交叉。");
    group("content", "当前稿件交审情况", ["draft-handoff"], "已交审是当前稿件的子集，不与任务数相加。");
    group("review", "当前版本核验结论", ["reviews-pass", "review-changes", "review-blocked"], "按当前审核报告所引用的稿件版本统计，不与核验任务数相加。");
    group("review", "历史审核版本", ["reviews-history"]);
    group("publishing", "本地包有效性", ["packages", "packages-invalid"], "有效性与是否导出是两个维度。");
    group("publishing", "本地包导出情况", ["packages-exported"], "是否导出与包是否有效是两个维度，可以交叉。");
    group("publishing", "导出回执规模", ["export-receipts"], "按回执统计，同一包可有多条回执，不与包数相加。");
    for (const item of model.stages) {
        item.resultDimension = item.dimensions.find((d) => d.title === "候选处理情况") || item.dimensions[0];
    }
}
