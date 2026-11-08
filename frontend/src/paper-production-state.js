/** 只沿人工推荐身份与制作链读取最新任务；旧采编回调不覆盖后续重做状态。 */
const states = {queued:'待执行',running:'执行中',succeeded:'已完成',failed:'执行失败',blocked:'业务阻断',paused:'已暂停',cancelled:'已停止'};
const phases = {'paper-understanding':'论文理解','context-research':'背景调查','topic-selection':'主编选题','draft-writing':'初稿撰写','reading-review':'阅读评审', 'fact-check':'事实核验',publisher:'图文编排','layout-production':'图文编排','article-revision':'协作改稿',editor:'主编选题',writer:'稿件制作',reviewer:'事实核验'};
const legacy = {idle:'未启动',queued:'处理排队',analyzing:'历史全文分析中',editorial:'主编处理中',content:'撰写中',review:'审核中',publishing:'生成发行预览',succeeded:'流程已结束',not_recommended:'历史全文未推荐',failed:'处理失败',waiting:'待继续处理'};
const priority = {running:0,failed:1,blocked:2,paused:3,queued:4,succeeded:5,cancelled:6};

export function latestPaperFlow(candidateId, production) {
  if (!candidateId || !production) return null;
  const flows = (production.paper_flows || []).filter(flow=>flow.candidate_ids.includes(candidateId));
  if (flows.length === 1) return flows[0];
  const dated = flows.map(flow=>({flow,time:Date.parse(production.tasks.find(task=>task.id === flow.root_task_id)?.created_at)}));
  if (!dated.length || dated.some(item=>!Number.isFinite(item.time))) return null;
  dated.sort((a,b)=>b.time-a.time);
  return dated.length > 1 && dated[0].time === dated[1].time ? null : dated[0].flow;
}

export function paperProductionState(paper, data = {}) {
  const flow = latestPaperFlow(paper.human_recommendation?.id,data.production);
  const tasks = data.production?.tasks || [];
  const leaves = flow ? tasks.filter(task=>flow.leaf_task_ids.includes(task.id)).sort((a,b)=>priority[a.status]-priority[b.status]) : [];
  const task = leaves[0] || tasks.find(item=>item.id === paper.continuation_task_id);
  const taskId = task?.id || paper.continuation_task_id;
  const params = taskId && new URLSearchParams({view:'workspace',stage:'production',task:taskId,...(task?.story_id ? {story:task.story_id} : {})});
  if (params && data.discovery?.selected) params.set('batch',data.discovery.selected);
  const current = !!flow && leaves.length > 0;
  return {flow,task,current,href:params ? '/console?'+params : '',
    failed:data.production === null ? false : current ? leaves.some(item=>['failed','blocked'].includes(item.status)) : paper.continuation_status === 'failed' || !!paper.continuation_error,
    label:data.production === null ? '制作状态读取失败' : current ? [...new Set(leaves.map(item=>`${item.activity_label || phases[item.phase || item.role] || item.phase || item.role} · ${states[item.status] || item.status}`))].join(' / ') : legacy[paper.continuation_status] || '未记录',
  };
}
