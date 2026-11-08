/** 论文失败只读投影：准确任务引用优先，缺失引用只沿显式人工推荐关联查找。 */
import { paperProductionState } from './paper-production-state.js';
const phases = { 'paper-understanding':'论文理解', 'context-research':'背景调查', 'topic-selection':'主编选题', 'draft-writing':'初稿撰写', 'reading-review':'阅读评审', 'fact-check':'事实核验', publisher:'图文编排','layout-production':'图文编排','article-revision':'协作改稿', editor:'主编选题', writer:'稿件制作', reviewer:'事实核验' };
const states = { queued:'排队，尚未开始执行', running:'正在执行', succeeded:'已完成', failed:'执行失败', blocked:'已阻断', cancelled:'已取消' };

export function paperProcessingFailure(paper, data = {}) {
  const current = paperProductionState(paper,data);
  if (!current.failed) return null;
  const tasks = data.production?.tasks || [];
  let task = current.task;
  if (!task && paper.human_recommendation?.id) {
    const ids = new Set((data.artifacts?.task_links || []).filter(link=>link.current && link.candidate_ids.includes(paper.human_recommendation.id)).map(link=>link.task_id));
    const linked = tasks.filter(item=>ids.has(item.id));
    const leaves = linked.filter(item=>!linked.some(child=>child.parent_task_id===item.id));
    if (leaves.length === 1) task = leaves[0];
  }
  const raw = current.current ? task?.error || (task?.id === paper.continuation_task_id ? paper.continuation_error : '') || '' : paper.continuation_error || task?.error || '';
  const generic = raw.match(/^自动处理失败[：:]\s*([A-Za-z][A-Za-z0-9_]*)$/);
  const message = generic ? `仅记录异常类型 ${generic[1]}，未保存具体说明。` : raw || '未保存失败说明。';
  const taskId = task?.id || paper.continuation_task_id;
  const params = taskId && new URLSearchParams(task?.story_id
    ? {view:'workspace',stage:'production',story:task.story_id,task:task.id}
    : {view:'workspace',stage:'production',task:taskId});
  if (params && data.discovery?.selected) params.set('batch', data.discovery.selected);
  return { message, task, phase:task ? task.activity_label || phases[task.phase || task.role] || task.phase || task.role : '', state:task ? states[task.status] || task.status : '', href:params ? '/console?' + params : '' };
}
