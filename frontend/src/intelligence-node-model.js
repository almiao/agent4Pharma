/** 正式工作台的阶段视图；只读取本阶段事实，不用后续结论改写输入状态。 */
export const nodeTabs = {
  ingest: [{ key: 'records', label: '归档资料', ids: ['collected-records'], object: '来源项', statusLabel: '归档内容', empty: '本批暂无归档资料', next: '可返回来源任务查看采集情况。' }],
  triage: [{ key: 'triage', label: '逐篇分流', ids: ['decisions'], metricStage: 'triage', object: '来源项', statusLabel: '初筛结果', empty: '本批暂无初筛输入', next: '先查看来源采集是否已有归档资料。' }],
  fulltext: ['selection', 'results', 'tasks', 'downloads', 'candidates'],
  selection: ['selection', 'downloads'],
};

export function stageRows(rows, node) {
  return rows.filter(row => node !== 'fulltext' || (row.detail?.stage || row.detail?.request?.stage) !== 'abstract').map(row => {
    let displayStatus = row.displayStatus;
    if (node === 'ingest') displayStatus = { fulltext: '已保存正文', partial: '已保存片段', abstract_only: '仅摘要', link_only: '仅链接', restricted: '访问受限', unavailable: '未取得文本' }[row.detail?.availability] || '已归档';
    if (node === 'triage') displayStatus = row.error ? '技术失败' : row.triage_method === 'semantic_passthrough' && row.route === 'analyze' ? '直送后续（未粗筛）' : { analyze: '初筛通过', exclude: '初筛未通过', needs_evidence: '历史待补证' }[row.route] || (row.state === 'awaiting_triage' ? '待初筛' : row.state === 'already_seen' ? '既有评估复用' : '尚未分流 / 其他');
    return { ...row, displayStatus };
  });
}

export function comparisonPool(run) {
  if (!run) return [];
  // 已冻结请求优先；旧运行没有该字段时只使用持久化本地排名。
  const ids = run.comparison_record_ids;
  return run.items.filter(item => ids ? ids.includes(item.record_id) : item.local_rank != null && item.local_rank <= (run.review_pool_limit ?? 10))
    .sort((a, b) => a.local_rank - b.local_rank);
}

export function localStatus(item) {
  if (item.local_rank != null) return '已排序';
  if (item.local_route === 'exclude') return '本地未通过';
  if (item.state === 'local_failed') return '技术失败';
  if (item.local_route) return '已判断 · 待排序';
  return '未记录本地结果';
}
