/** 显式任务范围的真实观测，原始事件缺失时不补造对话。 */
import React, { useEffect, useState } from 'react';
import ProductionTaskDetail from './production-task-detail';
import ProductionOutputs from './production-outputs';
import {outputTasks} from './production-outputs-model';
import ProductionTaskControls from './production-task-controls';
import { ProductionTaskInput, ProductionTaskOutput } from './production-task-document';
import './production-task-document.css';
import { taskLabels, productionTaskLabel } from './real-workspace-model';
import ExecutionViewer from './execution-viewer';
import { paperProductionState } from './paper-production-state.js';
import {fieldDescriptions} from './execution-labels';
import {productionTraceLabels} from './production-trace-labels';
const traceDefinitions={...fieldDescriptions,...productionTraceLabels};
export default function RealTaskProcess({ taskId, api, post, refresh, onTask, tasks = [], production, artifacts }) {
  const [record, setRecord] = useState(null), [events, setEvents] = useState(null), [error, setError] = useState('');
  const [running, setRunning] = useState(false);
  const [view, setView] = useState('auto');
  const [pollGeneration,setPollGeneration]=useState(0);
  const flow = production?.paper_flows?.find(item=>item.task_ids.includes(taskId));
  const latest = flow?.candidate_ids.length === 1 ? paperProductionState({human_recommendation:{id:flow.candidate_ids[0]}},{production}) : null;
  const historical = latest?.current && latest.flow.root_task_id !== flow.root_task_id;
  const hasSuccessor = tasks.some(t=>t.parent_task_id===taskId);
  const hasOutput = record?.task.status === 'succeeded'
    || record?.writing_research
    || record?.background_research?.some(snapshot => snapshot.task_id === taskId);
  const contentView = view === 'auto' ? (hasOutput ? 'output' : 'input') : view;
  async function controlled(result) {
    setPollGeneration(g=>g+1);
    if (result.id===taskId) setRecord(await api('/api/production/tasks/'+encodeURIComponent(taskId)));
    await refresh?.();if(result.id!==taskId)onTask?.(result.id);
  }
  async function run() {
    setRunning(true); setError('');
    try { await post('/api/production/run', { task_id: taskId }); setPollGeneration(g=>g+1); await refresh?.(); }
    catch (e) { setError(e.message); } finally { setRunning(false); }
  }
  useEffect(() => {
    let active = true, timer;
    async function load() {
      try {
        const detail = await api('/api/production/tasks/' + encodeURIComponent(taskId));
        if (!active) return;
        setRecord(detail); setError('');
        if (['queued','running'].includes(detail.task.status)) timer = setTimeout(load,5000);
      } catch (e) {
        if (active) { setError(e.message); timer = setTimeout(load,5000); }
      }
    }
    load();
    return () => { active = false; clearTimeout(timer); };
  }, [taskId, api, pollGeneration]);
  const taskStatus = record?.task.status;
  useEffect(() => {
    if (view !== 'execution') return;
    let active = true, cursor = 0, accumulated = [], timer, terminalReads = 0;
    async function load() {
      try {
        const observation = await api('/api/production/tasks/' + encodeURIComponent(taskId) + '/observations?after=' + cursor);
        if (!active) return;
        cursor = observation.next_cursor;
        accumulated = [...accumulated, ...observation.items];
        setEvents({...observation,items:accumulated});
        const terminal = taskStatus && !['queued','running'].includes(taskStatus);
        terminalReads = terminal ? terminalReads+1 : 0;
        const openAgent = accumulated.some(e=>e.execution?.kind==='agent' && e.status==='started' && !accumulated.some(end=>end.execution?.span_id===e.execution.span_id && end.status!=='started'));
        if (observation.has_more) timer = setTimeout(load,0);
        else if (!terminal || openAgent && terminalReads<3) timer = setTimeout(load,5000);
      } catch(e) { if(active) {setError(e.message);timer=setTimeout(load,5000);} }
    }
    setEvents(null); load();
    return () => { active = false; clearTimeout(timer); };
  }, [taskId, api, pollGeneration, view, taskStatus]);
  return <section className="real-process task-reading-workspace">
    {historical && <p role="status">正在查看历史制作记录。<button onClick={()=>onTask?.(latest.task.id)}>查看最新制作：{latest.label}</button></p>}
    <nav className="task-content-views" aria-label="制作详情视图">{[['output','本节点产出'],['input','输入'],['execution','执行记录'],['all-outputs','全部产出']].map(([id,label]) => <button key={id} aria-pressed={contentView === id} onClick={() => setView(id)}>{label}</button>)}</nav>
    {contentView === 'all-outputs' ? <ProductionOutputs index={artifacts} tasks={outputTasks(production,{taskId})} api={api}/> : <>
    {error && <p role="alert">{error}</p>}
    {!record ? <p role="status" className="task-document-loading">正在读取…</p> : <>
      {record.task.error && (historical ? <details><summary>历史失败说明</summary><p>{record.task.error}</p></details> : <p className="real-error" role="alert">{record.task.error}</p>)}
      {!hasSuccessor && record.task.status === 'queued' && !record.task.auto_run && <section className="task-execution-authorization">
        <button disabled={running} onClick={run}>开始制作（调用模型）</button>
      </section>}
      <ProductionTaskControls task={record.task} post={post} onChange={controlled} hasSuccessor={hasSuccessor}/>
      {contentView === 'output' ? <div className="task-paper-surface"><ProductionTaskOutput record={record} post={post}/></div> : contentView === 'input' ? <div className="task-paper-surface"><ProductionTaskInput record={record}/></div> : <section className="task-execution-records" aria-label="执行记录">
        {events && <ExecutionViewer events={events.items} hasMore={events.has_more} definitions={traceDefinitions} agent={{id:record.task.actor_id,name:taskLabels[record.task.role] || record.task.role,framework:'deepseek-harness',model:record.task.model,status:record.task.status,input:record.context_json,prompt:record.prompt,output:record.task.editing_result_json || JSON.stringify({result_ref:record.task.result_ref || null})}}/>}
        <details className="task-execution-metadata"><summary>用量、额度与任务配置</summary>
        <h3>{productionTaskLabel(record.task)} · {taskLabels[record.task.status] || record.task.status}</h3>
        <details className="task-identities"><summary>任务身份与冻结配置</summary><dl>
          <dt>任务</dt><dd>{taskId}</dd><dt>父任务</dt><dd>{record.task.parent_task_id || '无'}</dd><dt>产物引用</dt><dd>{record.task.result_ref || '未提交'}</dd><dt>冻结模型</dt><dd>{record.task.model}</dd><dt>尝试</dt><dd>{record.task.attempt} / {record.task.limits.max_attempts}</dd>
        </dl></details>
        <ProductionTaskDetail task={record.task} post={post} view="execution"/>

        </details>
      </section>}
    </>}
    </>}
  </section>;
}
