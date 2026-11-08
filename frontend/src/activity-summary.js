/** 当前执行与历史记录使用不同计数口径。 */
export const activityLabels = { running: '运行中', queued: '排队', waiting: '待推进', paused: '已暂停', unknown: '状态待确认', attention: '历史失败', complete: '已完成' };
export function summarizeActivity(items = []) {
  const current = items.filter(item => !['attention', 'complete'].includes(item.status));
  const history = items.filter(item => ['attention', 'complete'].includes(item.status));
  const running = current.filter(item => item.status === 'running').length;
  const queued = current.filter(item => item.status === 'queued').length;
  const paused = current.filter(item => item.status === 'paused').length;
  const uncertain = current.filter(item => item.status === 'unknown').length;
  return { current, history, running, queued, paused, uncertain };
}
