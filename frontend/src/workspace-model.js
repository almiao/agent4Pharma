/** 五个工序的对象导航；只组合既有投影，不推导新的业务结论。 */
import { comparisonPool } from './intelligence-node-model.js';

export const labels = { ready: '阅读通过', rebrief_needed: '需重新判断选题', queued: '排队', pending: '待处理', running: '运行中', succeeded: '完成', complete: '完成', failed: '技术失败', interrupted: '已中断', blocked: '业务阻断', cancelled: '取消', recommend: '正式推荐', exclude: '已排除', needs_evidence: '证据不足', watch: '观察', out_of_scope: '范围外', pass: '核验通过', changes_requested: '要求返修', active: '制作中', deferred: '已延期', completed: '已完成', valid: '有效', invalid: '失效', exported: '已导出', rejected: '未通过', commission: '立项', defer: '暂缓 · 待事件', decline: '不立项', request_evidence: '暂缓 · 待补证' };
export const workspaces = {
  intel: { title: '论文筛选', focus: '摘要先筛，个位数全文逐篇读', base: '/intelligence', tabs: [
    { key: 'selection', label: '精选论文', custom: 'selection', object: '精选论文', statusLabel: '论文状态', empty: '精选名单尚未生成', next: '完成摘要筛选后，这里显示入选论文及下载按钮。' },
    { key: 'abstracts', label: '全部论文', custom: 'abstracts', object: '论文', statusLabel: '处理状态', empty: '当前批次尚未建立摘要筛选运行', next: '这里显示完整待筛池和逐篇筛选结果。' },
    { key: 'results', label: '筛选判断', groups: ['精筛结果'], object: '来源项', statusLabel: '筛选结果', empty: '当前批次暂无精筛输入', next: '可切换批次，或查看采集与粗筛明细。' },
    { key: 'candidates', label: '交主编记录', ids: ['candidates'], object: '候选', statusLabel: '候选状态', empty: '当前范围尚无正式候选', next: '正式推荐形成后，候选会出现在这里并供主编引用。' },
    { key: 'tasks', label: '模型执行记录', ids: ['intel-tasks'], object: '评估任务', statusLabel: '任务状态', empty: '当前范围尚无评估任务', next: '查看精筛输入和批次范围，确认是否已创建任务。' },
    { key: 'records', label: '采集资料', ids: ['collected-records'], object: '来源项', statusLabel: '处理状态', empty: '当前批次暂无采集资料', next: '切换批次查看已归档来源项。' },
    { key: 'triage', label: '初步筛选记录', custom: 'triage', object: '判别结果', statusLabel: '分流结果', empty: '当前批次暂无粗筛判断', next: '直送精筛或复用不一定产生新的粗筛判断。' },
    { key: 'downloads', label: 'PDF 下载记录', custom: 'downloads', object: '下载记录', statusLabel: '实测状态', empty: '当前批次尚无 PDF 实测记录', next: '未测试不等于来源不可下载。' },
    { key: 'history', label: '历史评估', ids: ['intel-history'], object: '评估任务', statusLabel: '任务状态', empty: '暂无历史评估', next: '被替代的正式评估按原版本保留。' },
    { key: 'experiments', label: '回放与自检', custom: 'experiments', object: '评估任务', statusLabel: '任务状态', empty: '已读取范围无回放或自检', next: '这些评估不计入正式推荐。' },
  ] },
  editorial: { title: '主编选题', focus: '从候选取舍，形成可执行的任务书', base: '/editorial', tabs: [
    { key: 'candidates', label: '候选待选池', groups: ['候选处理情况'], object: '候选', statusLabel: '处理情况', empty: '当前范围没有候选', next: '从情报筛选中的正式候选开始。' },
    { key: 'stories', label: '选题与任务书', groups: ['选题状态'], object: '选题', statusLabel: '选题状态', empty: '尚无选题', next: '主编作出立项决定后，这里显示选题及任务书。' },
    { key: 'decisions', label: '编辑决定', groups: ['编辑决定'], object: '决定', statusLabel: '决定', empty: '尚无编辑决定', next: '查看主编任务的执行进展。' },
    { key: 'tasks', label: '主编任务', ids: ['editorial-tasks'], object: '任务', statusLabel: '任务状态', empty: '没有主编任务', next: '在候选池选择准确候选后排队主编。' },
  ] },
  content: { title: '内容写作', focus: '关注执行阻断与返工，检查当前稿件及依据', base: '/content', tabs: [
    { key: 'tasks', label: '写作任务', ids: ['content-tasks'], object: '任务', statusLabel: '任务状态', empty: '没有当前写作任务', next: '先查看主编是否已经立项并交接。' },
    { key: 'drafts', label: '当前稿件', ids: ['drafts'], object: '稿件版本', statusLabel: '版本状态', empty: '尚未形成当前稿件', next: '查看写作任务是否排队、运行或被阻断。' },
    { key: 'history', label: '历史稿件', ids: ['drafts-history'], object: '稿件版本', statusLabel: '版本状态', empty: '暂无历史稿件', next: '新版本保存后，旧版本仍在这里保留。' },
  ] },
  review: { title: '编辑核验', focus: '按准确稿件版本检查结论、问题与返工要求', base: '/review', tabs: [
    { key: 'reports', label: '当前核验结论', ids: ['reading-changes','reading-rebrief','reading-ready','review-changes', 'review-blocked', 'reviews-pass'], object: '报告', statusLabel: '核验结论', empty: '尚无当前版本核验报告', next: '检查核验任务是否已创建或完成。' },
    { key: 'tasks', label: '核验任务', ids: ['review-tasks'], object: '任务', statusLabel: '任务状态', empty: '暂无当前核验任务', next: '需要先取得符合要求的稿件并交审。' },
    { key: 'history', label: '历史报告', ids: ['reviews-history','reading-history'], object: '报告', statusLabel: '历史结论', empty: '暂无历史报告', next: '旧版报告只供追溯，不作为当前发行许可。' },
  ] },
  publishing: { title: '发行归档', focus: '核对包的有效性，再查看内容与本地导出记录', base: '/publishing', tabs: [
    { key: 'packages', label: '有效渠道包', ids: ['packages'], object: '渠道包', statusLabel: '有效性', empty: '暂无有效渠道包', next: '先查看当前稿件是否通过核验并生成渠道包。' },
    { key: 'invalid', label: '失效渠道包', ids: ['packages-invalid'], object: '渠道包', statusLabel: '有效性', empty: '没有失效渠道包', next: '任务书、稿件或核验版本变更后，相关旧包可能失效。' },
    { key: 'receipts', label: '导出记录', ids: ['export-receipts'], object: '回执', statusLabel: '导出状态', empty: '尚无本地导出记录', next: '导出有效包后记录回执；本地导出不表示外部发布。' },
    { key: 'exported', label: '已导出包', ids: ['packages-exported'], object: '渠道包', statusLabel: '有效性', empty: '没有已导出的包', next: '导出与有效性独立，已导出包仍可能失效。' },
    { key: 'tasks', label: '发行任务', ids: ['publishing-tasks'], object: '任务', statusLabel: '任务状态', empty: '暂无当前发行任务', next: '编辑通过的准确版本才可进入发行。' },
  ] },
};

export function tabCollection(model, stageId, tab, data = {}) {
  if (tab.custom) {
    let rows;
    if (tab.custom === 'triage') rows = data.preselection?.decisions?.map(d => ({ id: d.id, kind: 'decision', title: d.input.title, subtitle: d.routing_reason || d.result?.reason || '', status: { analyze: '进入详情分析', exclude: '不进入', needs_evidence: '历史待补证' }[d.route] || d.route, source: d.input.source_id, detail: d, href: '/console?stage=intel&work=triage&object=' + encodeURIComponent(d.id) }));
    if (['selection', 'abstracts'].includes(tab.custom)) {
      const runs = data.preselection?.paper_selections;
      const latest = runs?.at(-1);
      const states = { abstract_missing: '待筛选', local_failed: '处理失败', review_pending: '待复核', review_failed: '处理失败', review_excluded: '不推荐', abstract_running: '筛选中', awaiting_abstract: '待筛选', abstract_failed: '处理失败', abstract_excluded: '不推荐', eligible: '筛选中', deferred: '暂不下载', shortlisted: '待下载', manual_required: '需人工处理', unavailable: '缺少全文', download_failed: '处理失败', fulltext_failed: '处理失败', complete: '已分析' };
      const comparisonIds = new Set(tab.includeComparison ? comparisonPool(latest).map(item => item.record_id) : []);
      rows = runs ? (latest?.items || []).filter(item => tab.custom === 'abstracts' || item.can_download === true || !!item.manual_selected_at || comparisonIds.has(item.record_id)).map(item => ({ id: item.id, kind: 'paper-selection', title: (item.rank ? `${item.rank}. ` : '') + item.title, source: item.source_id, status: ({queued:'处理排队',analyzing:'全文分析中',editorial:'主编处理中',content:'撰写中',review:'审核中',publishing:'生成发行预览',succeeded:'流程已结束',not_recommended:'历史全文未推荐',failed:'处理失败',waiting:'待继续处理'}[item.continuation_status]) || (item.human_recommendation ? '人工已推荐' : item.pdf_imported && item.state !== 'complete' ? '已关联 · 待人工推荐' : states[item.state] || item.state), rank: item.rank ?? item.local_rank, subtitle: [item.journal || '载体未知', item.reason].filter(Boolean).join(' · '), detail: { ...item, kind: 'paper-selection' }, href: '/console?stage=intel&work=' + tab.key + '&object=' + encodeURIComponent(item.id) })) : undefined;
    }
    if (tab.custom === 'downloads') rows = data.preselection?.downloads?.map(d => ({ id: d.id || d.request.decision_id, kind: 'download', title: d.request.title || d.request.doi || d.request.decision_id, source: d.request.source_id, status: { downloaded: '已确认 PDF', failed: '本次未取得 PDF', timeout: '超时', identity_unverified: 'PDF 身份未确认', ancillary_document: '仅附件', unresolved: '标识未解析', invalid_pdf: 'PDF 校验失败', manual_required: '待人工处理', unavailable: '本次无法获取', cooldown: '等待冷却' }[d.status] || d.status, subtitle: d.error || d.channel || '', detail: d, href: '/console?stage=intel&work=downloads' }));
    if (tab.custom === 'experiments') rows = data.intelligence?.items?.filter(t => t.mode !== 'production').map(t => ({ id: t.id, kind: 'intelligence', title: t.title || t.id, status: t.status, subtitle: t.mode === 'replay' ? '回放' : '自检', detail: t, href: '/console?stage=intel&work=experiments&object=' + encodeURIComponent(t.id) }));
    return rows ? { rows: rows.map(row => ({ ...row, key: row.kind + ':' + row.id, displayStatus: labels[row.status] || row.status, rawStatus: row.status })), unknown: false } : { rows: [], unknown: true };
  }
  const stage = model.stages.find(s => s.id === stageId);
  const metrics = tab.ids ? tab.ids.map(id => model.metrics.get(id)).filter(Boolean)
    : stage?.dimensions.filter(d => tab.groups.includes(d.title)).flatMap(d => d.metrics) || [];
  if (!metrics.length || metrics.some(m => m.rows === null)) return { rows: [], unknown: true };
  const records = new Map();
  for (const metric of metrics) for (const row of metric.rows || []) {
    const identity = `${row.kind}:${row.id}`;
    if (records.has(identity)) continue;
    const result = row.detail?.intelligence_result;
    const businessStatus = tab.key === 'candidates' && stageId === 'editorial' ? metric.label
      : tab.key === 'decisions' ? row.detail?.decision
      : row.kind === 'package' ? row.detail?.validity : '';
    const stateNames = { awaiting_triage: '待粗筛', awaiting_intelligence: '待精筛', selected: '正式推荐', not_selected: '未入选', already_seen: '历史复用' };
    const abstractStage = row.detail?.stage === 'abstract' || row.detail?.request?.stage === 'abstract';
    const status = abstractStage && row.detail?.disposition === 'recommend' ? '摘要通过' : result || businessStatus || row.status || (tab.key === 'records' ? row.error ? '技术失败' : stateNames[row.state] || row.state : '') || row.detail?.status || metric.label;
    records.set(identity, { ...row, key: identity, displayStatus: labels[status] || status, rawStatus: status, group: metric.label });
  }
  return { rows: [...records.values()], unknown: false };
}

export function filterCollection(rows, { search = '', status = '' }) {
  const needle = search.trim().toLocaleLowerCase();
  return rows.filter(row => (!status || row.displayStatus === status) && (!needle || `${row.title} ${row.subtitle || ''} ${row.detail?.doi || ''} ${row.id}`.toLocaleLowerCase().includes(needle)));
}


export const intelligenceGroups = [
  { key: 'papers', label: '论文清单', tabs: ['selection', 'abstracts'], defaultTab: 'selection' },
  { key: 'sources', label: '来源记录', tabs: ['records', 'triage'], defaultTab: 'records' },
  { key: 'activity', label: '判断与执行', tabs: ['candidates', 'tasks', 'downloads', 'results', 'history', 'experiments'], defaultTab: 'tasks' },
];
export function intelligenceGroup(tabKey) {
  return intelligenceGroups.find(group => group.tabs.includes(tabKey)) || intelligenceGroups[0];
}
