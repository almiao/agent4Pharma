/** 任务配置、材料读取与供应商实际用量；缺失用量不补估算。 */
import React, { useState } from 'react';
import { Alert, Button, Collapse, Descriptions } from 'antd';
import { productionTaskLabel } from './real-workspace-model';
import { productionCostLabel } from './production-cost';

const number = value => value == null ? '未记录' : value.toLocaleString();

export default function ProductionTaskDetail({ task, post, view = 'all' }) {
  const [preparing, setPreparing] = useState(false), [preparation, setPreparation] = useState('');
  async function prepare() {
    setPreparing(true);
    try {
      const results = await post('/api/production/prepare-documents', { task_id: task.id });
      setPreparation(results.length ? results.map(item => `${item.sha256.slice(0, 10)}：${({ready:'整篇缓存就绪', pending:'解析中，可稍后刷新缓存状态', failed:'准备失败', unavailable:'解析不可用'})[item.status]}${item.error_code ? `（${item.error_code}）` : ''}`).join('；') : '此任务没有绑定 PDF');
    } catch (error) { setPreparation(error.message); }
    finally { setPreparing(false); }
  }
  return <>
    {view !== 'output' && <>
    <Descriptions column={1} size="small" items={[
      {key:'phase',label:'业务阶段',children:productionTaskLabel(task)},
      ...(task.limits.max_revision_rounds != null ? [{key:'revision_rounds',label:'全流程自动返修上限',children:`${task.limits.max_revision_rounds} 次`}] : []),
      {key:'timeout',label:'冻结硬时限',children:`${task.limits.timeout_seconds} 秒`},
      {key:'reads',label:'材料读取',children:`${task.reads} / ${task.limits.max_reads}`},
      ...(task.newsroom_policy === 'news-focus-v2' ? [{key:'lookups',label:'本地引用定位',children:`${task.citation_lookups?.length || 0} / 32（不同查询）`}] : []),
      ...(task.role === 'publisher' ? [{key:'figure_queries',label:'图目录查询',children:`${task.figure_queries?.length || 0} / 6（不同查询）`},{key:'figure_images',label:'已返回图像',children:`${task.figure_inspections?.length || 0} / 6（总览与子图）`}] : []),
      ...(task.execution_timings?.length ? [{key:'elapsed',label:'执行耗时',children:task.execution_timings.map(t=>`尝试 ${t.attempt}：${t.elapsed_seconds} 秒`).join('；')}] : []),
      {key:'usage',label:'已上报 token 合计',children:number(task.usage?.reported_total_tokens)},
      {key:'cost',label:'本节点费用估算',children:productionCostLabel(task.cost)}
    ]} />
    {task.cost && <details><summary>计价依据</summary><p>{task.cost.note}</p><p>人民币 · {task.cost.pricing_version} · <a href={task.cost.source} target="_blank" rel="noreferrer">DeepSeek 官方定价</a></p></details>}
    {!!task.usage?.attempts?.length && <Collapse items={task.usage.attempts.map(row=>({key:row.attempt,label:`尝试 ${row.attempt} · ${row.requests} 次用量回报${row.complete ? '' : ' · 未结束/部分记录'}`,children:<>
      <Descriptions column={1} size="small" items={[
        ['uncached_input_tokens','未缓存输入'],['cache_read_tokens','缓存读取'],['cache_write_tokens','缓存写入'],['output_tokens','输出'],['last_input_tokens','最近上下文 token'],['compactions','压缩次数'],['search_calls','搜索调用次数']
      ].map(([key,label])=>({key,label,children:row.requests || ['compactions','search_calls'].includes(key) ? number(row[key]) : '未记录'}))} /><p>{row.note}</p>
    </>}))} />}
    <Button loading={preparing} onClick={prepare}>准备／检查 PDF 缓存</Button>
    {preparation && <Alert type="info" title={preparation} />}
    </>}
  </>;
}
