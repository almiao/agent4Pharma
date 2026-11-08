/** 同轮论文精选的比较标签与列表排序。 */
const verdictLabels = { recommend: '有价值 · 容量暂缓', defer: '暂缓', exclude: '不推荐' };

export function comparisonLabel(run, paper, card) {
  if (run.comparison_result?.selected_ids?.includes(paper.record_id)) return '建议下载';
  if (paper.manual_selected_at && !card) return '人工加入精选';
  if (!run.comparison_result) return run.comparison_error ? '整轮比较失败' : '尚无比较结果';
  return verdictLabels[card?.verdict] || '未记录';
}

export const selectionSortOptions = [
  { value: 'default', label: '建议下载优先' },
  { value: 'local-rank', label: '按本地排名' },
  { value: 'journal', label: '期刊名称 A–Z' },
];

export function compareSelectionRows(a, b, sort) {
  if (sort === 'local-rank') {
    return (a.detail.local_rank ?? Infinity) - (b.detail.local_rank ?? Infinity) || 0;
  }
  if (sort === 'journal') {
    const first = a.detail.journal?.trim(), second = b.detail.journal?.trim();
    if (!first || !second) return Number(!first) - Number(!second);
    return first.localeCompare(second, 'zh-CN', { sensitivity: 'base' });
  }
  return Number(!a.detail.can_download) - Number(!b.detail.can_download);
}
