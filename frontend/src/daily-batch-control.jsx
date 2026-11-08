/** 今日采集至论文精选的显式入口；查询只读，未知结果重试复用请求身份。 */
import React, { useEffect, useRef, useState } from 'react';
import { Alert, Button, Space, Tag } from 'antd';

const active = new Set(['queued', 'collecting', 'selecting']);
const labels = {queued:'排队中', collecting:'采集与本地预选', selecting:'本地排序与论文精选', succeeded:'已完成', partial:'部分完成', failed:'执行失败'};

export default function DailyBatchControl({ api, post, refresh, navigate, stage, selectedBatch, selectionRun }) {
  const [snapshot, setSnapshot] = useState(null), [error, setError] = useState(''), [submitting, setSubmitting] = useState(false);
  const request = useRef(null), callbacks = useRef({refresh}), lastRefresh = useRef(0), previous = useRef(null);
  callbacks.current = {refresh};
  useEffect(() => {
    let alive = true, timer;
    async function load() {
      try {
        const next = await api('/api/daily-batch');
        if (!alive) return;
        const changed = previous.current?.job?.status !== next.job?.status;
        previous.current = next; setSnapshot(next);
        setError('');
        if (changed || active.has(next.job?.status) && Date.now() - lastRefresh.current > 10000) {
          lastRefresh.current = Date.now();
          Promise.resolve(callbacks.current.refresh?.()).catch(e => {if (alive) setError(`视图刷新失败：${e.message}`);});
        }
      } catch (e) { if (alive) setError(e.message); }
      finally { if (alive) timer = setTimeout(load, 3000); }
    }
    load();
    return () => {alive = false; clearTimeout(timer);};
  }, [api]);
  const job = snapshot?.job, running = active.has(job?.status);
  const sameScope = !selectedBatch || !job?.batch_id || selectedBatch === job.batch_id;
  const laterFailure = sameScope && job?.status === 'failed' && job.selection_run_id && ['ingest','triage','rank'].includes(stage);
  const detailHasError = stage === 'selection' && sameScope && selectionRun?.comparison_error;
  async function start() {
    setSubmitting(true); setError('');
    if (!request.current || request.current.day !== snapshot.day) request.current = {day:snapshot.day, request_id:crypto.randomUUID()};
    try {
      const next = await post('/api/daily-batch', request.current);
      setSnapshot(value => ({...value, job:next})); request.current = null;
      await refresh();
    } catch (e) { setError(e.message); }
    finally { setSubmitting(false); }
  }
  return <section className="daily-batch-control" aria-label="日常批次跑批" aria-busy={submitting || running}>
    <Space wrap>
      <Button type="primary" loading={submitting || running} disabled={!snapshot || submitting || running} onClick={start}>运行今日批次（调用模型）</Button>
      {job && <Tag color={laterFailure?'warning':job.status==='failed'?'error':job.status==='partial'?'warning':running?'processing':'success'}>{laterFailure?'后续精选失败':labels[job.status]}</Tag>}
      {job?.batch_id && <Button onClick={() => navigate({stage:'selection', batch:job.batch_id, run:job.result?.selection_run_id || job.selection_run_id || '', story:'', task:''})}>查看批次</Button>}
      <span role="status">{job?.result ? `去重资料 ${job.result.materials} 篇 · 建议下载 ${job.result.suggested_downloads} 篇` : running ? '正在后台执行，可离开当前页面' : ''}</span>
    </Space>
    {laterFailure && <Alert type="warning" showIcon title="后续论文精选比较失败，本页本地结果已保留" action={<Button onClick={() => navigate({stage:'selection',batch:job.batch_id,run:job.selection_run_id,story:'',task:''})}>查看精选</Button>}/>}
    {(error || sameScope && job?.error && !laterFailure && !detailHasError) && <Alert type="error" showIcon title={error || job.error} />}
    {job?.status==='partial' && <Alert type="warning" showIcon title={sameScope && selectionRun?.phase === 'fulltexts' && job.result?.coverage !== 'complete' ? '论文精选已完成，部分来源采集未完成；可到资料采集查看来源状态。' : '部分来源或筛选步骤未完成，请查看批次中的状态；已保存结果保留。'} />}
  </section>;
}
