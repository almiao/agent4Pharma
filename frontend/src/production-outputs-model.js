/** 按显式任务/产物身份列出全部版本，不能用当前产物替换历史输出。 */
export const outputLabels = {
  decision: '选题决定与任务书', writing_research: '作者研究底稿', background_research: '背景调研',
  draft: '稿件', reader_observation: '首次阅读观察', reader_review: '阅读评审报告',
  review: '事实核验', package: '图文包', article_edit: '改稿候选',
};

/** 同一明确制作轮次的任务集合，包含立项前任务，不混入同论文的其他重做。 */
export function outputTasks(production, {storyId = '', taskId = ''}) {
  if (!production) return [];
  const flows = (production.paper_flows || []).filter(flow => storyId ? flow.story_ids.includes(storyId) : flow.task_ids.includes(taskId));
  const ids = new Set(flows.flatMap(flow => flow.task_ids));
  for (const task of production.tasks) if (storyId ? task.story_id === storyId || task.result_ref === storyId : task.id === taskId) ids.add(task.id);
  let size;
  do {
    size = ids.size;
    for (const task of production.tasks) if (ids.has(task.id) && task.parent_task_id) ids.add(task.parent_task_id);
  } while (ids.size !== size);
  return production.tasks.filter(task => ids.has(task.id));
}

export function productionOutputs(index, tasks) {
  if (!index) return null;
  const ids = new Set(tasks.map(task => task.id));
  const drafts = (index.items || []).filter(item=>item.kind === 'draft');
  const draftForTask = taskId => drafts.find(item=>item.id === tasks.find(task=>task.id === taskId)?.result_ref) || drafts.find(item=>item.id === index.task_links?.find(link=>link.task_id === taskId)?.draft_id);
  const rows = (index.intermediates || []).filter(item => ids.has(item.task_id)).map(item => ({
    ...item, label: outputLabels[item.kind], versionLabel: [item.version ? `v${item.version}` : '',draftForTask(item.task_id)?.version].filter(Boolean).join(' · '), status: '已保存',
  }));
  for (const item of index.items || []) {
    const task = tasks.find(task => task.result_ref === item.id);
    if (task) rows.push({...item, task_id: task.id, label: outputLabels[item.kind], versionLabel: drafts.find(draft=>draft.id === item.reference_id)?.version || item.version});
  }
  for (const item of index.decisions || []) {
    if (ids.has(item.task_id)) rows.push({id:`decision:${item.task_id}`, task_id:item.task_id, kind:'decision',
      label:outputLabels.decision, versionLabel:'', status:item.decision.decision, created_at:item.created_at});
  }
  const savedTime = row => {
    const time = Date.parse(row.created_at);
    return Number.isNaN(time) ? Infinity : time;
  };
  return rows.sort((a,b) => savedTime(a) - savedTime(b) || a.id.localeCompare(b.id));
}
