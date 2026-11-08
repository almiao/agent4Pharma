/** 制作列表的对象与任务投影；只沿显式身份关联，不把旧失败当作当前阻断。 */
import { productionTaskLabel } from './real-workspace-model.js';

const statusLabels = {
  queued: '待执行', running: '执行中', succeeded: '已完成', failed: '执行失败',
  blocked: '业务阻断', paused: '已暂停', cancelled: '已停止',
};
const unique = values => [...new Set(values)];
const taskPriority = { running: 0, failed: 1, blocked: 2, paused: 3, queued: 4, succeeded: 5, cancelled: 6 };

/** 侧栏当前数量与默认列表共用同一完整对象集合。 */
export function currentProductionRows(collections) {
  return collections ? [...collections.active, ...collections.pending] : null;
}

/** 只取没有后继的任务，重试后的旧失败仍可在详情追溯。 */
export function currentTasks(tasks) {
  const parents = new Set(tasks.map(task => task.parent_task_id).filter(Boolean));
  return tasks.filter(task => !parents.has(task.id));
}

/** 一个业务阶段可有多次执行；只沿真实祖先关系区分当前分支与历史，不丢弃并行任务。 */
export function productionTaskStages(tasks) {
  const byId = new Map(tasks.map(task => [task.id, task])), groups = new Map(), historical = new Set();
  for (const task of tasks) {
    const phase = task.phase || task.role || task.id;
    if (!groups.has(phase)) groups.set(phase, { phase, tasks: [] });
    groups.get(phase).tasks.push(task);
    let parent = byId.get(task.parent_task_id);
    const visited = new Set([task.id]);
    while (parent && !visited.has(parent.id)) {
      visited.add(parent.id);
      if ((parent.phase || parent.role || parent.id) === phase) historical.add(parent.id);
      parent = byId.get(parent.parent_task_id);
    }
  }
  return [...groups.values()].map(group => {
    const revisions = new Map();
    for (const task of group.tasks) if (task.story_id && Number.isFinite(task.expected_revision)) {
      revisions.set(task.story_id, Math.max(revisions.get(task.story_id) ?? -1, task.expected_revision));
    }
    const past = task => historical.has(task.id) ||
      (task.story_id && Number.isFinite(task.expected_revision) && task.expected_revision < revisions.get(task.story_id));
    const current = group.tasks.filter(task => !past(task));
    const primary = current.find(task => task.status === 'running') ||
      current.find(task => task.status === 'queued') || current.at(-1);
    return { ...group, primary, current, history: group.tasks.filter(past) };
  });
}

function taskSummary(tasks) {
  return {
    tasks,
    phase: unique(tasks.map(task => productionTaskLabel(task))).join(' / ') || '未记录',
    status: unique(tasks.map(task => statusLabels[task.status] || task.status)).join(' / ') || '未记录',
    attention: tasks.some(task => ['failed', 'blocked', 'paused'].includes(task.status)),
  };
}

/** 当前、历史互斥；任务暂停仍保留在原集合，便于继续。未知读取不返回空集合。 */
export function productionCollections(production, stories, artifacts = null) {
  if (!production || !stories) return null;
  const result = { pending: [], active: [], history: [] };
  result.artifactsUnknown = artifacts === null;
  const commissionedFlows = (production.paper_flows || []).filter(flow=>flow.story_ids.length);
  const rootTime = flow => Date.parse(production.tasks.find(task=>task.id === flow.root_task_id)?.created_at);
  for (const flow of production.paper_flows || []) {
    if (flow.story_ids.length) continue;
    const tasks = production.tasks.filter(task => flow.leaf_task_ids.includes(task.id))
      .sort((a, b) => taskPriority[a.status] - taskPriority[b.status]);
    const row = {
      id: flow.root_task_id, title: flow.paper_titles.join(' / ') || flow.candidate_ids.join(' / ') || flow.root_task_id,
      ...taskSummary(tasks), saved: flow.savepoint_task_ids.length,
      target: { story: '', task: tasks[0]?.id || flow.root_task_id },
      search: [...flow.paper_titles, ...flow.candidate_ids, ...flow.task_ids].join(' '),
    };
    const ended = tasks.length > 0 && tasks.every(task => ['succeeded', 'cancelled'].includes(task.status));
    const superseded = flow.candidate_ids.length > 0 && flow.candidate_ids.every(id => commissionedFlows.some(other=>
      other.candidate_ids.includes(id) && rootTime(other) > rootTime(flow)));
    if (superseded) {
      row.status = '已立项 · 历史研究';
      row.attention = false;
    }
    result[ended || superseded ? 'history' : 'pending'].push(row);
  }
  for (const story of stories) {
    const tasks = currentTasks(story.tasks).filter(task =>
      task.story_id !== story.id || story.revision == null || task.expected_revision === story.revision)
      .sort((a, b) => taskPriority[a.status] - taskPriority[b.status]);
    const stopped = tasks.length > 0 && tasks.every(task=>['cancelled','succeeded'].includes(task.status)) && tasks.some(task=>task.status === 'cancelled');
    const latestTask = story.tasks.filter(t=>t.story_id === story.id).at(-1);
    const readerAttention = story.status === 'deferred' && (latestTask?.phase === 'reading-review' || latestTask?.phase === 'fact-check' && latestTask?.limits?.max_revision_rounds != null) && latestTask?.status === 'succeeded';
    const archived = !readerAttention && (stopped || story.stage !== 'production' || ['deferred', 'cancelled', 'completed'].includes(story.status));
    const row = {
      id: story.id, title: story.brief?.title || story.paperTitles.join(' / ') || story.id,
      latestDraft: artifacts?.items.filter(item => item.kind === 'draft' && item.story_id === story.id)
        .reduce((latest, item) => !latest || Date.parse(item.created_at) > Date.parse(latest.created_at) ? item : latest, null),
      ...taskSummary(tasks),
      saved: story.tasks.filter(task => task.status === 'succeeded' && task.result_ref).length,
      target: { story: story.id, task: tasks[0]?.id || '' },
      search: [story.id, ...story.paperTitles, ...story.tasks.map(task => task.id)].join(' '),
    };
    if (readerAttention) { row.status = '要求返修 · 自动修订已停止'; row.phase = productionTaskLabel(latestTask); row.attention = true; row.target.task = latestTask.id; }
    if (archived) {
      row.status = ({ deferred: '选题已暂停', cancelled: '选题已取消', completed: '已完成' })[story.status] || ({human:'已移交审稿',delivery:'已移交交付',complete:'制作已结束'})[story.stage] || story.statusLabel;
      const last = productionTaskStages(story.tasks).map(group => group.primary).filter(Boolean).at(-1);
      row.phase = last ? productionTaskLabel(last) : '未记录';
      row.target.task = last?.id || '';
      if (stopped && story.stage === 'production' && story.status === 'active') row.status = '已停止';
    }
    result[archived ? 'history' : 'active'].push(row);
  }
  return result;
}
