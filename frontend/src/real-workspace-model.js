/** 新版业务阶段使用正式投影和服务端批准记录，不读取样例或浏览器业务状态。 */
export const realStages = [
  ['ingest', '资料采集'], ['triage', '主题初筛'], ['rank', '价值排序'],
  ['selection', '论文精选'],
  ['production', '稿件制作'], ['human', '审稿定稿'], ['delivery', '稿件交付'],
];
export function canonicalStage(stage) {
  return stage === 'fulltext' ? 'selection' : stage || 'overview';
}
export function groupStories(production, packages) {
  if (!production || !packages) return null;
  return production.stories.map(story => {
    const ids = new Set(production.tasks.filter(t => t.story_id === story.id || t.result_ref === story.id).map(t=>t.id));
    let size;
    do {
      size = ids.size;
      for (const task of production.tasks) if (ids.has(task.id) && task.parent_task_id) ids.add(task.parent_task_id);
    } while (size !== ids.size);
    const tasks = production.tasks.filter(t=>ids.has(t.id));
    const paperTitles=(production.paper_flows || []).filter(f=>f.story_ids.includes(story.id)).flatMap(f=>f.paper_titles);
    const related = packages.items.filter(p => p.story_id === story.id);
    const current = related.filter(p => p.valid);
    const ready = current.at(-1)?.approval ? [current.at(-1)] : [];
    // 审稿派生任务未完成时继续留在审稿，不能误退回最初制作阶段。
    const reachedHuman = related.length > 0;
    const currentTask=tasks.filter(t=>t.story_id===story.id && t.expected_revision===story.revision).at(-1);
    const latestTask = tasks.filter(t=>t.story_id === story.id).at(-1);
    const readerAttention = story.status === 'deferred' && (latestTask?.phase === 'reading-review' || latestTask?.phase === 'fact-check' && latestTask?.limits?.max_revision_rounds != null);
    const productionStatus=story.status==='deferred' ? (readerAttention ? '要求返修 · 自动修订已停止' : '选题已暂停') : ({paused:'任务已暂停',cancelled:'任务已停止',blocked:'等待处理阻断',failed:'任务失败',queued:'待执行',running:'制作中'})[currentTask?.status] || '制作中';
    const stage = story.status === 'cancelled' ? 'complete' : ready.length ? (ready.every(p => p.exported) ? 'complete' : 'delivery') : reachedHuman ? 'human' : 'production';
    return { ...story, tasks, paperTitles, packages: related, stage, statusLabel: story.status === 'cancelled' ? '已取消' : stage === 'complete' ? '已导出' : stage === 'delivery' ? '待交付' : stage === 'human' ? (current.length ? '待我审稿' : '等待修订 / 核验') : productionStatus };
  });
}
export function visibleStories(rows, stage, history = false) {
  if (!rows) return null;
  return rows.filter(row => history ? stage === 'production' || (stage === 'human' ? row.packages.length > 0 : row.packages.some(p => p.approval)) : row.stage === stage);
}

/** 以详情默认打开的图文包为准，缺少生成时间的稿件始终置底。 */
export function manuscriptRows(rows, artifacts, query, oldestFirst) {
  if (!rows) return null;
  const times = new Map((artifacts?.items || [])
    .filter(item => item.kind === 'package').map(item => [item.id, item.created_at]));
  const term = query.trim().toLowerCase();
  return rows
    .filter(row => [row.brief?.title, ...row.paperTitles].filter(Boolean).join(' ').toLowerCase().includes(term))
    .map(row => {
      const selected = row.packages.findLast(item => item.valid) || row.packages.at(-1);
      const generatedAt = times.get(selected?.id);
      return { ...row, generatedAt, timestamp: Date.parse(generatedAt) };
    })
    .sort((a, b) => {
      if (!Number.isFinite(a.timestamp)) return Number.isFinite(b.timestamp) ? 1 : 0;
      if (!Number.isFinite(b.timestamp)) return -1;
      return oldestFirst ? a.timestamp - b.timestamp : b.timestamp - a.timestamp;
    });
}

export const taskLabels = { editor:'主编立项', writer:'内容写作', reviewer:'事实核验', publisher:'图文编排','layout-production':'图文编排','article-revision':'协作改稿', 'paper-understanding':'论文理解', 'context-research':'背景调查', 'topic-selection':'主编选题', 'draft-writing':'初稿撰写', 'reading-review':'阅读评审', 'fact-check':'事实核验', succeeded:'完成', queued:'排队', running:'运行中', failed:'技术失败', blocked:'业务阻断', paused:'已暂停', cancelled:'已停止' };

/** 显示服务端按准确输入判定的初稿/修订；历史投影缺字段时沿阶段名回退。 */
export const productionTaskLabel = task => task?.activity_label || taskLabels[task?.phase || task?.role] || task?.phase || task?.role || '未记录';
