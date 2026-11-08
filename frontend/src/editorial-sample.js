/** 本地精修演练：每次修改产生新版本，审核及任务冻结到准确版本。 */
import { artifacts, story } from './observation-sample.js';
export const editorialKey = 'a4p-editorial-demo-v1';
export function initialEditorial() {
  return {
    versions: [1, 2].map(n => ({ id: `v${n}`, title: story.title, body: artifacts[`draft${n}`].body, parent: n === 2 ? 'v1' : null, author: '写作 Agent（样例）', review: n === 2 ? '样例核验通过' : '要求修改', note: n === 2 ? '解决 R-01：收束泛化结论' : '首次成稿' })),
    comments: [], jobs: [], config: { skill: '以具体结果开篇；说明验证范围；每条事实主张绑定证据。', tools: ['read_material', 'submit_draft'] }, buffer: null,
  };
}
export function saveRevision(state, baseId, title, body, note) {
  const base = state.versions.find(v => v.id === baseId);
  if (!base || !title.trim() || !body.trim() || !note.trim()) throw new Error('请填写标题、正文和修订说明。');
  if (base.title === title.trim() && base.body === body.trim()) throw new Error('内容未修改，无需生成新版本。');
  const version = { id: `v${state.versions.length + 1}`, parent: baseId, title: title.trim(), body: body.trim(), note: note.trim(), author: '人工精修', review: '待核验' };
  return { ...state, versions: [...state.versions, version], buffer: null };
}
export function queueEditorial(state, versionId, kind, instruction) {
  const version = state.versions.find(v => v.id === versionId);
  if (!version || !instruction.trim()) throw new Error('请填写本次执行要求。');
  if (state.jobs.some(j => j.version.id === versionId && j.kind === kind && j.status === '待执行')) throw new Error('该版本已有同类待执行任务。');
  if (kind === 'Agent 修订' && (!state.config.skill.trim() || !state.config.tools.includes('submit_draft'))) throw new Error('修订任务需要写作技能和 submit_draft 工具，请先调整配置。');
  return { ...state, jobs: [...state.jobs, { id: `local-${state.jobs.length + 1}`, kind, status: '待执行', version: { ...version }, comments: state.comments.filter(c => c.version === versionId && !c.resolved).map(c => ({ ...c })), instruction: instruction.trim(), config: { skill: state.config.skill, tools: [...state.config.tools] } }] };
}

/** 发布包同时冻结内容和呈现配置；批准后不再安排排版任务。 */
export function releasePackage(version) {
  return { id: `release-${version.id}`, version: version.id, title: version.title, body: version.body, layout: 'article-layout-v1', media: [], references: ['paper-v1'] };
}
export function approvalBlocker(state, versionId) {
  const version = state.versions.find(v => v.id === versionId);
  if (!version || versionId !== state.versions.at(-1).id) return '请选择最新稿件版本审稿。';
  if (state.buffer) return '还有未保存的编辑草稿，请先保存或放弃。';
  if (version.review === '要求修改') return '此版本仍有核验必改问题。';
  if (state.comments.some(c => c.version === versionId && !c.resolved)) return '请先处理此版本的人工意见。';
  if (state.jobs.some(j => j.version.id === versionId && j.status === '待执行')) return '此版本仍有修订或核验任务待执行。';
  return '';
}
export function approveRelease(state, versionId, checked) {
  const blocker = approvalBlocker(state, versionId);
  if (blocker) throw new Error(blocker);
  if (!checked) throw new Error('请确认已核对事实、正文与最终排版。');
  if (state.releases?.some(r => r.package.version === versionId)) throw new Error('该版本已批准，请到发布节点查看。');
  const version = state.versions.find(v => v.id === versionId);
  return { ...state, releases: [...(state.releases || []), { package: releasePackage(version), approval: { actor: '人工编辑（本地演练）', at: new Date().toISOString(), factCheck: '人工核对确认', decision: '批准此发布包' }, status: '待发布', attempts: [] }] };
}
export function simulatePublication(state, packageId, success) {
  const record = state.releases?.find(r => r.package.id === packageId);
  if (!record || record.status === '发布成功（模拟）') throw new Error('没有可执行的已批准发布包。');
  if (record.package.version !== state.versions.at(-1).id || approvalBlocker(state, record.package.version)) throw new Error('批准后稿件或待办已变化，请回到人工审稿。');
  return { ...state, releases: state.releases.map(r => r !== record ? r : { ...r, status: success ? '发布成功（模拟）' : '发布失败（模拟）', attempts: [...r.attempts, { at: new Date().toISOString(), result: success ? '成功（模拟）' : '网络超时（模拟）' }] }) };
}
