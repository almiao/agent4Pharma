/** 首页仅呈现一行任务状态；完整任务和历史按需在抽屉读取。 */
import React, { useEffect, useState } from 'react';
import { Alert, Button, Drawer, Empty, Segmented, Space, Table, Tag, Tabs, Modal } from 'antd';
import { activityLabels, summarizeActivity } from './activity-summary';

const time = value => value ? new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : '未知';

export function DependencyServiceActions({item,busy,available,onAction}) {
  return <Space wrap>{(item.operation?.status === 'running' ? [] : item.actions || []).map(action=><Button key={action} size="small" disabled={!available || !!busy} loading={busy === item.id} onClick={()=>onAction(item,action)}>{action === 'start' ? '启动' : '重启'}</Button>)}</Space>;
}

export default function BackgroundActivity({ api, post }) {
  const [snapshot, setSnapshot] = useState(null), [error, setError] = useState(''), [open, setOpen] = useState(false), [view, setView] = useState('current'), [version, setVersion] = useState(0);
  const [dependencies, setDependencies] = useState(null), [dependencyError, setDependencyError] = useState('');
  const [serviceBusy, setServiceBusy] = useState(''), [serviceError, setServiceError] = useState(''), [restartService, setRestartService] = useState(null);
  const [parserTasks, setParserTasks] = useState(null), [parserError, setParserError] = useState('');
  const [tab,setTab] = useState('services');
  useEffect(() => {
    if (!open || tab !== 'parsing') return;
    let stopped = false, timer;
    async function poll() {
      try {
        const value = await api('/api/dependencies/mineru-tasks');
        if (!stopped) { setParserTasks(value); setParserError(''); }
      } catch (err) { if (!stopped) setParserError(err.message); }
      finally { if (!stopped) timer = setTimeout(poll, 15000); }
    }
    poll();
    return () => { stopped = true; clearTimeout(timer); };
  }, [open, tab, version, api]);
  useEffect(() => {
    if (!open || tab !== 'services') return;
    let stopped=false,timer;
    async function poll() {
      try { const value=await api('/api/dependencies'); if(!stopped) {setDependencies(value);setDependencyError('');} }
      catch(err) {if(!stopped)setDependencyError(err.message);}
      finally {if(!stopped)timer=setTimeout(poll,15000);}
    }
    poll();return()=>{stopped=true;clearTimeout(timer);};
  },[open,tab,version,api]);
  useEffect(() => {
    let stopped = false, timer;
    async function poll() {
      try {
        const value=await api('/api/background-activity');
        if(!stopped) {setSnapshot(value);setError('');}
      } catch (err) { if (!stopped) setError(err.message); }
      finally { if (!stopped) timer = setTimeout(poll, 15000); }
    }
    poll();
    return () => { stopped = true; clearTimeout(timer); };
  }, [version, api]);
  async function controlService(item, action) {
    setServiceBusy(item.id); setServiceError('');
    try {
      const operation = await post('/api/dependencies/control', {service:item.id,action,confirm_interrupt:action === 'restart'});
      setDependencies(current => current && ({...current,items:current.items.map(row => row.id === item.id ? {...row,operation,actions:[]} : row)}));
      setRestartService(null); setVersion(value => value + 1);
    } catch (error) { setServiceError(error.message); }
    finally { setServiceBusy(''); }
  }
  const summary = summarizeActivity(snapshot?.items);
  const rows = summary[view];
  const unhealthy = dependencies?.items.filter(item => item.operation?.status !== 'running' && ['unavailable', 'permission_error', 'error', 'not_installed'].includes(item.status)).length || 0;
  const labels = { healthy: '正常', unavailable: '不可用', unknown: '暂未确认', configured: '已配置', disabled: '未启用', not_installed: '未安装', stopped: '未启动', starting: '启动中', permission_error: '权限异常', error: '检查失败' };
  return <>
    <Button className="activity-header-button" type="text" onClick={() => setOpen(true)} aria-label={`服务与任务，运行中 ${summary.running} 个，排队 ${summary.queued} 个${error || dependencyError ? '，读取失败' : ''}`}>
      {(summary.running > 0 || summary.queued > 0) && <span className="activity-status-dot" aria-hidden="true" />}
      服务与任务 · {snapshot ? `运行 ${summary.running} / 排队 ${summary.queued}` : '检查中'}{unhealthy ? ` · 服务异常 ${unhealthy}` : ''}{error || dependencyError ? ' · 读取失败' : ''}
    </Button>
    <Modal zIndex={1100} title="重启依赖服务" open={!!restartService} onCancel={()=>!serviceBusy && setRestartService(null)} onOk={()=>controlService(restartService,'restart')} confirmLoading={!!serviceBusy} okText="确认重启" cancelText="返回">
      <p>重启 {restartService?.title} 可能中断正在解析或推理的请求。已保存的任务与产物保留，被中断的业务任务不会自动重试。</p>
    </Modal>
    <Drawer title="服务与后台任务" open={open} onClose={() => setOpen(false)} size={760}>
      <Tabs activeKey={tab} onChange={setTab} items={[{ key: 'services', label: '依赖服务', children: <>
        {dependencyError && <Alert type="error" showIcon title="服务状态读取失败，以下为上次检查结果" description={dependencyError} />}
        {serviceError && <Alert type="error" role="alert" title="服务操作失败" description={serviceError}/>}
        <Space wrap><span>检查于 {time(dependencies?.checked_at)}</span><Button onClick={() => setVersion(v => v + 1)}>刷新</Button></Space>
        <Table rowKey="id" size="small" loading={!dependencies && !dependencyError} dataSource={dependencies?.items || []} pagination={false} columns={[
          {title:'服务',dataIndex:'title'},
          {title:'状态',dataIndex:'status',render:(status,item)=>item.operation?.status === 'running' ? <Tag color="processing">{item.operation.action === 'start' ? '启动中' : '重启中'}</Tag> : <Tag color={status==='healthy'?'success':['unavailable','permission_error','error','not_installed'].includes(status)?'error':status==='starting'?'processing':status==='unknown'?'warning':'default'}>{labels[status]}</Tag>},
          {title:'详情',render:(_,item)=><div>{item.operation?.status === 'running' ? item.operation.detail : item.detail}{item.work_directory && <details><summary>工作目录</summary><code style={{overflowWrap:"anywhere"}}>{item.work_directory}</code></details>}{item.control_note && <details><summary>操作说明</summary><p>{item.control_note}</p></details>}{item.operation?.status === 'failed' && <p role="alert">{item.operation.detail}</p>}{item.queued != null && <p>解析队列：{item.queued} 项（不含执行中任务）</p>}</div>},
          {title:'操作',render:(_,item)=><DependencyServiceActions item={item} busy={serviceBusy} available={!!post} onAction={(item,action)=>action === 'restart' ? setRestartService(item) : controlService(item,action)}/>},
        ]} />
      </> }, { key: 'tasks', label: `后台任务${snapshot ? ` · ${summary.running} 运行中` : ''}`, children: <>
      {error && <Alert type="error" showIcon title="读取失败，以下为上次读取结果" description={error} />}
      <p>运行中 {summary.running} · 排队 {summary.queued} · 暂停 {summary.paused} · 待确认 {summary.uncertain}；更新于 {time(snapshot?.as_of)}</p>
      <Space className="activity-drawer-tools" wrap><Segmented aria-label="任务记录范围" value={view} options={[{value:'current',label:`当前任务 ${summary.current.length}`},{value:'history',label:'历史记录'}]} onChange={setView} /><Button onClick={() => setVersion(v => v + 1)}>刷新</Button></Space>
      <Table rowKey="id" size="small" loading={!snapshot && !error} dataSource={rows} pagination={rows.length > 8 ? { pageSize: 8, showSizeChanger: false } : false} locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={error ? '任务状态未知，请重试' : '当前没有此类任务'} /> }} columns={[
        { title: '任务', key: 'task', render: (_, item) => <div className="activity-task"><strong>{item.title}</strong><p>{item.current_step}</p>{item.total != null && <small>{item.completed} / {item.total} 篇{item.status === 'paused' ? '（暂停时进度）' : ''}</small>}<small>{time(item.updated_at)}</small></div> },
        { title: '状态', dataIndex: 'status', width: 110, render: status => <Tag color={status === 'running' ? 'processing' : 'default'}>{activityLabels[status]}</Tag> },
        { title: '', key: 'open', width: 70, render: (_, item) => item.href ? <a href={item.href} aria-label={`查看任务详情：${item.title}`}>详情</a> : <span>离线处理</span> },
      ]} />
      <details className="activity-scope"><summary>统计范围</summary><p>显示已登记的日常跑批、摘要、情报、PDF、制作和调查任务。历史失败与完成记录不算正在执行的后台任务；独立采集进程、外部脚本和系统进程尚未纳入。</p></details>
      </> }, {key:'parsing',label:'PDF 解析任务',children:<>
        <Button onClick={()=>setVersion(v=>v+1)}>刷新解析任务</Button>
        {parserError && <Alert type="error" showIcon title="解析任务读取失败" description={parserError}/>}
        <p>最近 {parserTasks?.parses.length ?? '未知'} / {parserTasks?.total ?? '未知'} 条 · 更新于 {time(parserTasks?.checked_at)}。历史完成与失败不表示正在执行。</p>
        <Table rowKey="id" size="small" loading={!parserTasks && !parserError} dataSource={parserTasks?.parses || []} pagination={{pageSize:8,showSizeChanger:false}} expandable={{expandedRowRender:item=><p>解析任务 #{item.id} · 文档 {item.short_id} · {item.tier} · 页码 {item.page_range}</p>}} columns={[
          {title:'文档身份',dataIndex:'short_id'}, {title:'页码',dataIndex:'page_range'},
          {title:'状态',dataIndex:'status',render:status=><Tag color={status==='parsing'?'processing':'default'}>{{pending:'排队',parsing:'解析中',done:'已完成',failed:'失败',superseded:'已替代'}[status]}</Tag>},
        ]}/>
      </>}]} />
    </Drawer>
  </>;
}
