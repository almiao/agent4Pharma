/** 批次业务阶段投影：当前停留互斥，累计到达保留历史，人工返工不退回制作阶段。 */
import { collection, outcome } from './batch-observation-sample.js';
import { approvalBlocker, initialEditorial } from './editorial-sample.js';

export const editorialStages = ['production', 'human', 'publication'];
export function editorialProgress(state = initialEditorial()) {
  const version = state.versions.at(-1);
  const release = state.releases?.find(r => r.package.version === version.id);
  const waiting = state.jobs.filter(j => j.version.id === version.id && j.status === '待执行');
  const blocked = approvalBlocker(state, version.id);
  let current = 'human', human = waiting.length ? '等待修订 / 核验' : '待我审稿';
  if (release && !blocked) { current = release.status === '发布成功（模拟）' ? 'complete' : 'publication'; human = '已定稿'; }
  const publication = release ? (blocked ? '需重新处理审稿事项' : release.status) : state.releases?.length ? '历史版本已批准 · 新稿待审' : '尚未批准';
  return { current, version: version.id, title: version.title, human, publication, reachedPublication: !!state.releases?.length };
}
export function stageCollection(stage, state, range = 'history') {
  if (editorialStages.includes(stage)) {
    const progress = editorialProgress(state);
    const reached = stage !== 'publication' || progress.reachedPublication;
    return reached && (range === 'history' || progress.current === stage) ? collection('production') : [];
  }
  const all = collection(stage);
  if (range === 'history') return all;
  if (stage === 'coarse') return all.filter(p => p.coarse === 'failed');
  if (stage === 'fulltext') return all.filter(p => p.pdf === 'waiting');
  return [];
}
export function stageOutcome(paper, stage, state) {
  if (stage === 'production') return '制作完成 · 已交人工审稿';
  if (stage === 'human') return editorialProgress(state).human;
  if (stage === 'publication') return editorialProgress(state).publication;
  return outcome(paper, stage);
}
export function stageLink(stage) {
  return stage === 'human' ? '/console?view=observe&mode=editorial&task=human' : stage === 'publication' ? '/console?view=observe&mode=release&task=publish' : '/console?view=observe&mode=overview&task=pitch';
}
export function cumulativeHandoff(stage, state) {
  const labels = { coarse: '14 篇输入', rank: '12 篇通过', review: '前 10 篇冻结', fulltext: '5 篇入选', production: '1 篇正式推荐', human: '1 篇待审稿已交接', publication: `${editorialProgress(state).reachedPublication ? 1 : 0} 篇已批准` };
  return labels[stage];
}
