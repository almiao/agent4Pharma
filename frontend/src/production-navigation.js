/** 制作集合与准确详情之间的导航；筛选和页码由同一 URL 保留。 */
const collections = new Set(['current', 'preparation', 'active', 'history']);
const labels = { current: '当前任务', preparation: '待立项', active: '制作中', history: '已结束' };

export function productionDetailRoute(row, section = '') {
  return { stage: 'production', section: '', role: '', work: collections.has(section) ? section : 'current', ...row.target };
}

export function productionReturnRoute(route) {
  const section = collections.has(route.work) ? route.work : route.range === 'history' ? 'history' : 'current';
  return { stage: 'production', section: section === 'current' ? '' : section, story: '', task: '', role: '', work: '', range: 'current' };
}

export function productionReturnLabel(route) {
  return labels[productionReturnRoute(route).section || 'current'];
}
