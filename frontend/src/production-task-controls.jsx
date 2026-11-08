/** 重试与继续自动执行制作链；暂停/停止撤销当前租约。 */
import React, { useState } from 'react';

export default function ProductionTaskControls({task, post, onChange, hasSuccessor}) {
  const [action,setAction] = useState(''), [busy,setBusy] = useState(false), [error,setError] = useState('');
  if (hasSuccessor || task.status==='succeeded') return null;
  const actions = ['queued','running'].includes(task.status) ? [['pause','暂停'],['stop','停止']]
    : task.status==='paused' ? [['resume','继续'],['stop','停止']]
    : ['failed','blocked'].includes(task.status) ? [['retry','重试此阶段'],['stop','停止']]
    : task.status==='cancelled' ? [['retry','重试此阶段']] : [];
  async function confirm(selectedAction = action) {
    setBusy(true);setError('');
    try {
      const result=await post('/api/production/task-control',{task_id:task.id,action:selectedAction,expected_status:task.status,expected_attempt:task.attempt});
      setAction('');await onChange(result);
    } catch(e) {setError(e.message);} finally {setBusy(false);}
  }
  return <section aria-label="制作任务控制">
    {actions.map(([id,label])=><button key={id} disabled={busy} onClick={()=>['retry','resume'].includes(id) ? confirm(id) : setAction(id)}>{label}</button>)}
    {action && <div role="alert"><p>{['pause','stop'].includes(action)
      ? action === 'stop' ? '停止当前阶段并阻止继续推进；没有其他待处理分支时，本轮制作移至“已结束”。已提交产物保留，已经发生的调用仍会计费。' : '将暂停当前阶段，阻止继续推进；已提交产物保留，已经发生的调用仍会计费。'
      : '复用上游已提交产物和原冻结配置，自动执行当前阶段及后继，保留原尝试记录。'}</p>
      <button disabled={busy} onClick={()=>confirm()}>确认{actions.find(([id])=>id===action)?.[1]}</button><button disabled={busy} onClick={()=>setAction('')}>返回</button></div>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
