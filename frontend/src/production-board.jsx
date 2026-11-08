/** 按待立项、制作中和历史组织制作对象，执行和恢复操作留在准确任务详情。 */
import React from 'react';
import GeneratedTime from './generated-time';
import ProductionOutputs from './production-outputs';
import {outputTasks} from './production-outputs-model';
import { currentProductionRows } from './production-board-model';
import { productionDetailRoute } from './production-navigation';
import { Alert, Button, Empty, Input, Select, Table, Tag } from 'antd';

export default function ProductionBoard({ collections, section, query, setQuery, navigate, phase = '', status = '', page = 1, onFilter = () => {}, artifacts, production, api }) {
  const key = section === 'preparation' ? 'pending' : section === 'history' ? 'history' : section === 'active' ? 'active' : 'current';
  if (!collections) return <Alert type="error" title="制作集合读取失败" action={<Button onClick={() => location.reload()}>重新读取</Button>}/>;
  const allRows = key === 'current' ? currentProductionRows(collections) : key === 'pending' ? collections.pending : collections[key];
  const matches = row => `${row.title} ${row.search}`.toLowerCase().includes(query.toLowerCase()) &&
    (!phase || row.phase === phase) && (!status || (status === 'attention' ? row.attention : row.status === status));
  const rows = allRows.filter(matches);
  function open(row) {
    navigate(productionDetailRoute(row, section));
  }
  const columns = [
    { title: '论文 / 选题', key: 'title', render: (_, row) => <div className="production-title"><strong>{row.title}</strong><small>{row.saved ? `已保存 ${row.saved} 个阶段产物` : '尚无已提交阶段产物'}</small></div> },
    { title: '当前环节', dataIndex: 'phase', key: 'phase', width: 150 },
    { title: '处理状态', key: 'status', width: 150, render: (_, row) => <Tag color={row.attention ? 'orange' : undefined}>{row.status}</Tag> },
    { title: '最新稿件生成时间（北京时间）', key: 'generated', width: 190, render: (_, row) => <GeneratedTime value={row.latestDraft?.created_at} empty={collections.artifactsUnknown ? '读取未知' : row.latestDraft ? '未记录' : '尚未生成'}/> },
    { title: '操作', key: 'action', width: 120, render: (_, row) => <Button onClick={() => open(row)} aria-label={`${key === 'history' ? '查看记录' : '查看并处理'}：${row.title}`}>{key === 'history' ? '查看记录' : '查看并处理'}</Button> },
  ];
  return <section className="production-board" aria-label={{ current: '当前制作任务', pending: '待立项论文', active: '制作中选题', history: '已结束的制作任务' }[key]}>
    <div className="production-toolbar">
      <label>搜索<Input value={query} allowClear placeholder="论文、选题或任务编号" onChange={event => setQuery(event.target.value)}/></label>
      {key !== 'history' && <label>任务范围<Select aria-label="任务范围" value={section === 'preparation' || section === 'active' ? section : 'current'} options={[{value:'current',label:'全部当前'},{value:'preparation',label:'待立项'},{value:'active',label:'制作中'}]} onChange={value=>navigate({section:value === 'current' ? '' : value,page:''})}/></label>}
      <label>当前环节<Select value={phase || undefined} placeholder="全部环节" allowClear onChange={value => onFilter({phase:value || '',page:''})} options={[...new Set(allRows.map(row => row.phase))].map(value => ({ value, label: value }))}/></label>
      <label>处理状态<Select value={status || undefined} placeholder="全部状态" allowClear onChange={value => onFilter({state:value || '',page:''})} options={[{ value: 'attention', label: '需要处理' }, ...[...new Set(allRows.map(row => row.status))].map(value => ({ value, label: value }))]}/></label>
      {(query || phase || status) && <Button aria-label="重置" onClick={() => { onFilter({q:'',phase:'',state:'',page:''}); }}>重置</Button>}
      <span className="production-count" role="status">{allRows.filter(matches).length} / {allRows.length} 项</span>
      {key === 'pending' && <Button onClick={() => navigate({ stage: 'intake', section: '', scope: 'all', role: '', work: 'candidates', story: '', task: '' })}>从候选中选择</Button>}
      {key === 'history' && <Button onClick={() => navigate({ section: 'records', scope: 'all', role: 'editorial', work: '', story: '', task: '' })}>查询产物与决定</Button>}
    </div>
    <Table rowKey="id" columns={columns} dataSource={rows} expandable={{expandedRowRender:row=><ProductionOutputs index={artifacts} tasks={outputTasks(production,{storyId:row.target.story,taskId:row.target.task})} api={api}/>,columnTitle:'产出',columnWidth:90,expandIcon:({expanded,onExpand,record})=><Button size="small" aria-expanded={expanded} onClick={event=>onExpand(record,event)}>{expanded ? '收起产出' : '全部产出'}</Button>}} pagination={{ current:Math.min(Math.max(1,Number(page) || 1),Math.max(1,Math.ceil(rows.length/15))), onChange:value=>onFilter({page:String(value)}), pageSize: 15, showSizeChanger: false, hideOnSinglePage: true }} locale={{ emptyText: <Empty description={query || phase || status ? '没有匹配对象，请调整筛选' : { current: '暂无当前制作任务', pending: '暂无待立项论文', active: '暂无制作中的选题', history: '暂无已结束的制作任务' }[key]}/> }}/>

  </section>;
}
