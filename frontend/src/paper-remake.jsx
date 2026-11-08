/** 已推荐论文完整重做；创建新根任务并自动执行制作链。 */
import React, { useRef, useState } from 'react';
import { Alert, Button, Modal, Space, Spin, InputNumber } from 'antd';
import { initialLimits } from './production-limits';

const taskLink = task => '/console?' + new URLSearchParams({view:'workspace', stage:'production', task});

export default function PaperRemake({paper, data = {}, api, post}) {
  const [open,setOpen] = useState(false), [busy,setBusy] = useState(false), [loading,setLoading] = useState(false);
  const [error,setError] = useState(''), [history,setHistory] = useState([]);
  const requestId = useRef('');
  const [revisionRounds,setRevisionRounds] = useState(5);
  if (!paper.human_recommendation) return null;
  const candidateId = paper.human_recommendation.id;
  const startedIds = data.production?.started_candidate_ids;
  const starting = startedIds?.includes(candidateId) === false;
  const actionLabel = !startedIds ? '制作状态未知' : starting ? '启动采编' : '重做';
  async function show() {
    if (!requestId.current) requestId.current = crypto.randomUUID();
    setOpen(true);setError('');setHistory([]);
    if (starting) return;
    setLoading(true);
    try { const result = await api('/api/production/remakes?' + new URLSearchParams({candidate:candidateId}));setHistory(result.items); }
    catch(e) {setError(e.message);} finally {setLoading(false);}
  }
  async function confirm() {
    setBusy(true);setError('');
    try {
      const result = starting
        ? await post('/api/production/start', {candidate_ids:[candidateId], limits:{...initialLimits,max_reads:20,max_revision_rounds:revisionRounds}})
        : await post('/api/production/remake', {candidate_id:candidateId, request_id:requestId.current, max_revision_rounds:revisionRounds});
      location.assign(taskLink(result.id));
    } catch(e) {setError(e.message);} finally {setBusy(false);}
  }
  return <>
    <Button aria-label={`${actionLabel} ${paper.title}`} disabled={!startedIds} onClick={show}>{actionLabel}</Button>
    <Modal title={starting ? '启动这篇论文的采编？' : '重做这篇论文？'} open={open} onCancel={()=>!busy && setOpen(false)} onOk={confirm} confirmLoading={busy} okButtonProps={{disabled:!startedIds}} okText={starting ? '确认启动' : '确认重做'} cancelText="取消" maskClosable={!busy}>
      <p>{paper.title}</p>
      <p>{starting ? '从主编选题开始采编' : '从主编选题开始完整重做'}，采用当前已发布的提示词和规范，再由作者研究与写作。</p>
      <p>确认后自动调用模型推进到待审渠道包，会产生费用；无需逐阶段点击执行。</p>
      <label>全流程自动返修上限 <InputNumber aria-label="全流程自动返修上限" min={1} max={10} value={revisionRounds} disabled={busy} onChange={value=>setRevisionRounds(value ?? 5)} /></label>
      {loading ? <span role="status" aria-label="正在读取重做记录"><Spin size="small"/></span> : history.length > 0 && <details><summary>已有重做记录（{history.length}）</summary><Space direction="vertical">{history.map(item=><a key={item.task_id} href={taskLink(item.task_id)}>{new Date(item.created_at).toLocaleString()} · 查看制作过程</a>)}</Space></details>}
      {error && <Alert type="error" showIcon title="操作未完成" description={error}/>}
    </Modal>
  </>;
}
