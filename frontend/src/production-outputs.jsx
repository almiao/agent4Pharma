/** 全部中间产出按版本列出，正文仅在用户打开对应版本时读取。 */
import React, {useEffect, useState} from 'react';
import {Alert, Button, Drawer, Empty, Spin, Table} from 'antd';
import {productionOutputs} from './production-outputs-model';
import GeneratedTime from './generated-time';
import {WritingResearchReport} from './writing-research-report';
import {BackgroundReport} from './background-research-report';
import {ProductionTaskOutput} from './production-task-document';
import './production-task-document.css';

const statuses = {saved:'已保存',pass:'通过',ready:'通过',changes_requested:'要求返修',blocked:'历史：阻断',
  rebrief_needed:'需重新判断选题',local_preview:'本地预览',commission:'已立项',defer:'暂缓 · 待事件',decline:'不立项',request_evidence:'暂缓 · 待补证'};

export function OutputContent({item, record}) {
  if (!item || !record) return null;
  if (item.kind === 'writing_research') {
    const snapshot = record.writing_research_versions?.find(row => row.task_id === item.task_id && row.version === item.version);
    return snapshot ? <WritingResearchReport snapshot={snapshot} excerpts={record.writing_research_excerpts}/> : <Alert type="error" title="未找到此版底稿"/>;
  }
  if (item.kind === 'background_research') {
    const snapshot = record.background_research_versions?.find(row => row.task_id === item.task_id && row.version === item.version);
    return snapshot ? <BackgroundReport snapshot={snapshot} excerpts={record.background_research_excerpts} sources={record.background_research_sources}/> : <Alert type="error" title="未找到此版调研"/>;
  }
  if (item.kind === 'reader_observation') {
    const observation = JSON.parse(record.task.reader_observation_json || 'null');
    return observation ? <section><h3>读到了什么变化</h3><p>{observation.news_understood}</p><h3>阅读理由</h3><p>{observation.reason_to_read}</p><h3>首次阅读障碍</h3><p>{observation.first_obstacle}</p></section> : <Alert type="error" title="未找到首次阅读观察"/>;
  }
  if (!['decision','article_edit'].includes(item.kind) && record.artifact?.content?.id !== item.id) return <Alert type="error" title="任务产物与所选版本不一致"/>;
  return <ProductionTaskOutput record={{...record,background_research:[],writing_research:null,writing_research_versions:[]}}/>;
}

export default function ProductionOutputs({index, tasks, api}) {
  const rows = productionOutputs(index, tasks);
  const [selected,setSelected] = useState(null), [record,setRecord] = useState(null), [error,setError] = useState('');
  useEffect(()=>{
    let active = true;
    setRecord(null);setError('');
    if (selected) api('/api/production/tasks/'+encodeURIComponent(selected.task_id))
      .then(value=>{if(active)setRecord({id:selected.id,data:value});}).catch(e=>{if(active)setError(e.message);});
    return ()=>{active=false;};
  },[selected,api]);
  return <section className="production-outputs" aria-label="全部中间产出">
    <h3>全部产出{rows ? `（${rows.length}）` : ''}</h3>
    {!rows ? <Alert type="error" title="产出目录读取失败，请刷新重试"/> : <Table size="small" rowKey="id" dataSource={rows} pagination={false} scroll={{x:640,y:320}} locale={{emptyText:<Empty description="尚无已保存产出"/>}} columns={[
      {title:'产出',key:'output',render:(_,row)=><Button type="link" onClick={()=>setSelected(row)}>{row.label}{typeof row.version === 'number' && row.version ? ` v${row.version}` : ''}</Button>},
      {title:'版本 / 对应稿件',dataIndex:'versionLabel',key:'version',ellipsis:true},
      {title:'结论',key:'status',render:(_,row)=>statuses[row.status] || row.status},
      {title:'生成时间（北京时间）',key:'time',width:190,render:(_,row)=><GeneratedTime value={row.created_at}/>},
    ]}/>}
    <Drawer title={selected ? `${selected.label}${typeof selected.version === 'number' && selected.version ? ` v${selected.version}` : ''}` : ''} open={!!selected} onClose={()=>setSelected(null)} size={900} destroyOnHidden>
      {error ? <Alert type="error" title={error}/> : selected && record?.id === selected.id ? <div className="task-paper-surface"><OutputContent item={selected} record={record.data}/></div> : <Spin/>}
    </Drawer>
  </section>;
}
