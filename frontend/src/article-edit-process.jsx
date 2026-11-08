/** 按需增量读取真实执行事件；折叠状态不拉取详细日志。 */
import React, {useEffect, useState} from 'react';
import {createWorkbenchApi} from './workbench-api';
import ExecutionViewer from './execution-viewer';
import {fieldDescriptions} from './execution-labels';
import {productionTraceLabels} from './production-trace-labels';

export default function ArticleEditProcess({turn}) {
  const [open, setOpen] = useState(false), [events, setEvents] = useState([]), [error, setError] = useState('');
  const [record, setRecord] = useState(null), [more, setMore] = useState(false);
  useEffect(() => {
    if (!open) return;
    let active = true, timer, cursor = 0, accumulated = [], terminalReads = 0;
    const {api:get} = createWorkbenchApi(document.querySelector('meta[name="workbench-token"]')?.content);
    async function load() {
      try {
        const path = '/api/production/tasks/' + encodeURIComponent(turn.task_id);
        const [detail, page] = await Promise.all([get(path), get(path + '/observations?after=' + cursor)]);
        if (!active) return;
        cursor = page.next_cursor; accumulated.push(...page.items);
        setEvents([...accumulated]); setRecord(detail); setMore(page.has_more); setError('');
        const running = ['queued','running'].includes(detail.task.status);
        terminalReads = running ? 0 : terminalReads + 1;
        if (page.has_more || running || terminalReads < 3) timer = setTimeout(load, page.has_more ? 0 : 2000);
      } catch (e) { if (active) { setError(e.message); timer = setTimeout(load, 5000); } }
    }
    load();
    return () => { active = false; clearTimeout(timer); };
  }, [open, turn.task_id]);
  return <details className="article-edit-process" onToggle={e => setOpen(e.currentTarget.open)}>
    <summary>DSH 执行过程 · {{queued:'排队中',running:'执行中',succeeded:'已完成',failed:'失败',blocked:'未完成',cancelled:'已停止'}[turn.status] || turn.status}</summary>
    {open && <><a href={'/console?stage=content&work=tasks&scope=all&object='+encodeURIComponent(turn.task_id)}>任务详情与停止／重试</a>{error && <p role="alert">{error}</p>}{!record && !error && <p role="status">正在读取执行记录…</p>}
      {record && <ExecutionViewer events={events} hasMore={more} definitions={{...fieldDescriptions,...productionTraceLabels}} agent={{id:record.task.actor_id,name:'DSH',framework:'deepseek-harness',model:record.task.model,status:record.task.status,input:record.context_json,prompt:record.prompt,output:record.task.editing_result_json}}/>}
    </>}
  </details>;
}
