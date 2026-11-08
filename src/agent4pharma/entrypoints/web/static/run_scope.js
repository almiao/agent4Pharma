import { consoleUrl } from "./console_routes.js";
/* 运行视图只沿已持久化的身份关联收敛，不用同日、标题或相似度补造交接。 */
export function scopeRun(data, storyId = "") {
    const rss = data.rss;
    const sourceStories = data.production?.stories || [];
    const selectedStory = storyId ? sourceStories.find((s) => s.id === storyId) : null;
    const storyTasks = new Set(data.production?.tasks.filter((t) => t.story_id === storyId || "story-" + t.id === storyId).map((t) => t.id));
    const frozenAssessments = new Set(data.artifacts?.task_links?.filter((l) => storyTasks.has(l.task_id)).flatMap((l) => l.assessment_ids || []));
    const materialIds = new Set(data.discovery?.articles.map((a) => a.material_id) || rss?.decisions.map((d) => d.input.material_ref) || []);
    const belongsToBatch = (task) => materialIds.has(task.material_ref) && (!data.discovery ||
        data.discovery.articles.some((a) => a.material_id === task.material_ref &&
            (a.intelligence_task_id === task.id || (task.parent_ref === data.discovery.selected &&
                (!task.source_record_id || task.source_record_id === a.id)))));
    const candidates = data.candidates?.items.filter((c) => selectedStory
        ? selectedStory.candidate_ids.includes(c.id) && (!frozenAssessments.size || frozenAssessments.has(c.task_id))
        : !storyId && materialIds.has(c.material_ref));
    const candidateIds = new Set(candidates?.map((c) => c.id));
    const assessmentIds = new Set(data.intelligence?.items.filter(belongsToBatch).map((t) => t.id));
    const originalLinks = new Set(data.artifacts?.task_links?.filter((l) => l.assessment_ids?.some((id) => assessmentIds.has(id))).map((l) => l.task_id));
    const originalStories = new Set(data.production?.tasks.filter((t) => originalLinks.has(t.id)).map((t) => t.story_id || "story-" + t.id));
    const stories = storyId ? sourceStories.filter((s) => s.id === storyId)
        : sourceStories.filter((s) => s.candidate_ids.some((id) => candidateIds.has(id)) || originalStories.has(s.id));
    const storyIds = new Set(stories.map((s) => s.id));
    const linkedTasks = new Set(data.artifacts?.task_links?.filter((l) => l.candidate_ids.some((id) => candidateIds.has(id))).map((l) => l.task_id));
    const tasks = data.production?.tasks.filter((t) => storyIds.has(t.story_id)
        || (!t.story_id && (linkedTasks.has(t.id) || originalLinks.has(t.id) || stories.some((s) => s.id === "story-" + t.id))));
    const candidateTasks = new Set(candidates?.map((c) => c.task_id));
    const intelligence = data.intelligence?.items.filter((t) => selectedStory
        ? (frozenAssessments.size ? frozenAssessments.has(t.id) : candidateTasks.has(t.id)) : !storyId && belongsToBatch(t));
    const known = storyId ? !!selectedStory : !!rss && !!data.candidates;
    const scope = storyId
        ? `选题 ${storyId}：仅展示该选题及明确候选引用；采集批次未在此范围关联。`
        : `批次 ${rss?.selected || "未选择"}：按预选资料身份关联评估、候选和选题，不推定同日记录属于本批。`;
    return {
        ...data,
        rss: storyId ? null : rss ? { ...rss, batches: rss.batches.filter((b) => b.batch_id === rss.selected) } : null,
        sources: null,
        discovery: storyId ? null : data.discovery,
        candidates: known && data.candidates ? { items: candidates } : null,
        intelligence: known && data.intelligence ? { items: intelligence } : null,
        production: known && data.production ? { ...data.production, stories, tasks } : null,
        artifacts: known && data.artifacts && data.production ? { ...data.artifacts,
            items: data.artifacts.items.filter((a) => storyIds.has(a.story_id)),
            task_links: data.artifacts.task_links?.filter((l) => tasks.some((t) => t.id === l.task_id)),
            decisions: data.artifacts.decisions?.filter((d) => tasks.some((t) => t.id === d.task_id)),
            receipts: data.artifacts.receipts?.filter((r) => data.artifacts.items.some((a) => a.id === r.package_id && storyIds.has(a.story_id))),
        } : null,
        proposals: null,
        runScope: scope + (storyId ? " 精筛沿任务冻结的评估身份读取，不以最近列表替代历史输入。" : data.discovery ? " 批次成员与父运行评估完整读取，按来源版本统计；多来源文章另外去重。" : " 评估最多读取最近 100 次、候选最多 200 项；未读取到关联不等于历史从未运行。"),
    };
}

export function bindRunLinks(model) {
    // 完整工作区沿用抽查集合快照；对象链接仍保留各自准确身份。
    for (const metric of model.metrics.values()) {
        for (const row of metric.rows || []) row.href = consoleUrl(row.href);
        if (metric.rows === null || metric.id === "attention" || metric.rows.some((r) => ["run-source", "receipt"].includes(r.kind))) {
            metric.url = consoleUrl(metric.url, metric.id);
            continue;
        }
        const url = new URL(metric.url, "http://localhost");
        url.searchParams.set("ids", metric.rows.map((r) => r.id).join(","));
        metric.url = consoleUrl(url.pathname + url.search, metric.id);
    }
    return model;
}
