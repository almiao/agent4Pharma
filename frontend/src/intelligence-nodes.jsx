/** 正式批次的采集、排序和整轮比较视图；读操作不触发采集或模型。 */
import React, { useEffect, useRef, useState } from 'react';
import { Search } from 'lucide-react';
import { Alert, Button, Descriptions, Drawer, Empty, Input, Select, Space, Table, Tabs, Tag } from 'antd';
import StageWorkspace from './stage-workspace';
import SelectionHistory from './selection-history';
import PaperMetadata from './paper-metadata';
import ManualPaperSelection from './manual-paper-selection';
import { comparisonPool, localStatus } from './intelligence-node-model';

const collectionStates = { not_started: '尚未启动', pending: '待执行', running: '正在采集', complete: '采集完成', partial: '部分完成', failed: '采集失败' };
const categories = { journal: '期刊', preprint: '预印本', media: '新闻', news: '新闻', announcement: '公告', organization: '机构', other: '其他', unknown: '未分类' };
const count = value => value ?? '未知';
const external = url => /^https?:\/\//i.test(url || '') ? url : undefined;

function CollectionNode(props) {
  const { data } = props;
  const params = new URLSearchParams(location.search);
  const [tab, setTab] = useState(params.get('work') === 'records' ? 'records' : 'sources');
  const [source, setSource] = useState(params.get('source') || '');
  const [search, setSearch] = useState(''), [status, setStatus] = useState(''), [category, setCategory] = useState('');
  const [selected, setSelected] = useState(null);
  const sources = data.discovery?.sources;
  function openRecords(id = '') {
    const url = new URL(location.href);
    for (const key of ['object', 'state', 'q', 'category', 'page', 'ids', 'collection']) url.searchParams.delete(key);
    url.searchParams.set('work', 'records'); url.searchParams.delete('source');
    if (id) url.searchParams.set('source', id);
    history.replaceState({}, '', url); setSource(id); setTab('records');
  }
  if (!sources) return <Alert type="error" title="来源任务读取失败" action={<Button onClick={props.refresh}>重试</Button>} />;
  const rows = sources.filter(s => (!search || `${s.name} ${s.source_id}`.toLowerCase().includes(search.toLowerCase())) && (!status || s.status === status) && (!category || (s.category || 'unknown') === category));
  return <section className="collection-node" aria-label="资料采集">
    <div className="collection-heading"><Tabs activeKey={tab} onChange={key => { if (key === 'records') openRecords(); else { const url = new URL(location.href); url.searchParams.delete('work'); url.searchParams.delete('source'); history.replaceState({}, '', url); setTab(key); } }} items={[{ key: 'sources', label: <>来源任务 <span className="collection-tab-count">{sources.length}</span></> }, { key: 'records', label: <>归档资料 <span className="collection-tab-count">{count(data.discovery?.articles?.length)}</span></> }]} /></div>
    {tab === 'records' ? <StageWorkspace {...props} key={source} stageId="intel" nodeId="ingest" initialTab="records" /> : <>
      <div className="collection-status-filters" role="group" aria-label="采集状态筛选"><button aria-pressed={!status} onClick={() => setStatus('')}>全部 <span>{sources.length}</span></button>{Object.entries(collectionStates).map(([key, label]) => <button key={key} className={`collection-state-${key}`} aria-pressed={status === key} onClick={() => setStatus(status === key ? '' : key)}><i aria-hidden="true"/>{label} <span>{sources.filter(s => s.status === key).length}</span></button>)}<span className="collection-empty-count" title="采集成功，但未取得来源项">空结果 {sources.filter(s => s.status === 'complete' && s.records === 0).length}</span></div>
      <div className="collection-toolbar"><Input prefix={<Search size={16} aria-hidden="true"/>} aria-label="搜索来源" placeholder="搜索来源名称或编号" value={search} onChange={e => setSearch(e.target.value)} allowClear /><Select aria-label="来源类型" placeholder="全部来源类型" value={category || undefined} allowClear onChange={v => setCategory(v || '')} options={[...new Set(sources.map(s => s.category || 'unknown'))].map(value => ({value, label:categories[value] || value}))} /><Button type="text" disabled={!search && !status && !category} onClick={() => {setSearch('');setStatus('');setCategory('');}}>重置</Button><span className="collection-result-count" role="status">{rows.length === sources.length ? `${rows.length} 个来源` : `${rows.length} / ${sources.length} 个来源`}</span></div>
      <Table rowKey="source_id" dataSource={rows} scroll={{x:800}} pagination={{pageSize:15,showSizeChanger:false}} columns={[
        {title:'来源',render:(_,s)=><Button type="link" onClick={()=>openRecords(s.source_id)}>{s.name || s.source_id}</Button>},
        {title:'类型',render:(_,s)=>categories[s.category] || s.category || '未分类'},
        {title:'采集状态',render:(_,s)=><Tag color={s.status==='failed'?'red':s.status==='partial'?'gold':'default'}>{collectionStates[s.status] || s.status}</Tag>},
        {title:'归档来源项',render:(_,s)=>s.records == null ? '未知' : <Button type="link" onClick={()=>openRecords(s.source_id)}>{s.records}</Button>},
        {title:'去重资料',render:(_,s)=>count(s.unique_materials)},
        {title:'异常',render:(_,s)=>s.error || (s.collection_mode==='archive_import'?'本地归档导入':'—')},
        {title:'任务',render:(_,s)=><Button onClick={()=>setSelected(s)}>详情</Button>},
      ]} />
    </>}
    <Drawer title={selected?.name || selected?.source_id} open={!!selected} onClose={()=>setSelected(null)} size={640}>
      {selected && <><Tag>{collectionStates[selected.status] || selected.status}</Tag>{selected.error && <Alert type="error" title={selected.error} />}<Descriptions column={1} items={[
        {key:'records',label:'已归档来源项',children:count(selected.records)}, {key:'unique',label:'去重资料',children:count(selected.unique_materials)},
        {key:'attempt',label:'尝试次数',children:count(selected.attempt)}, {key:'window',label:'采集窗口',children:`${selected.start || '未知'} 至 ${selected.end || '未知'}`},
        {key:'mode',label:'采集方式',children:selected.collection_mode==='archive_import'?'既有归档导入（未重新联网采集）':'来源采集'},
      ]}/>{external(selected.request_url) && <a href={external(selected.request_url)} target="_blank" rel="noreferrer">打开采集入口</a>}{selected.request_url_basis==='current_directory' && <p>当前目录入口，非原任务留存地址。</p>}{selected.diagnostic && <details><summary>补充复核</summary><p>{selected.diagnostic.reason}</p><p>{selected.diagnostic.checked_at}</p></details>}<details><summary>任务追溯</summary><pre>{JSON.stringify(selected,null,2)}</pre></details></>}
    </Drawer>
  </section>;
}

function RankingNode({ nodeId, data, api, post, refresh, navigate, onShowPapers, hideRunPicker = false }) {
  const runs = data.preselection?.paper_selections;
  const [runId, setRunId] = useState(''), [search, setSearch] = useState(''), [selected, setSelected] = useState(null);
  const run = runs?.find(r => r.id === runId) || runs?.at(-1);
  const ranking = nodeId === 'rank';
  const [reading, setReading] = useState(null), [readError, setReadError] = useState('');
  useEffect(() => {
    let alive = true;
    setReading(null); setReadError('');
    if (selected && api) api('/api/discovery-run/article?' + new URLSearchParams({batch:run.batch_id || data.discovery?.selected || '', record:selected.record_id}))
      .then(value=>{if(alive) setReading(value);}).catch(error=>{if(alive) setReadError(error.message);});
    return ()=>{alive=false;};
  }, [selected?.record_id, run?.id, api]);
  if (!runs) return <Alert type="error" title="摘要筛选运行读取失败" action={<Button onClick={refresh}>重试</Button>}/>;
  if (!run) return <Empty description="本批尚未建立摘要筛选运行"/>;
  const pool = comparisonPool(run);
  const cards = new Map((run.comparison_result?.cards || []).map(card=>[card.record_id,card]));
  const selectedIds = run.comparison_result?.selected_ids;
  const allRows = ranking ? [...run.items].sort((a,b)=>(a.local_rank??Infinity)-(b.local_rank??Infinity)) : pool;
  const rows = allRows.filter(item=>`${item.title} ${item.journal} ${item.doi}`.toLowerCase().includes(search.toLowerCase()));
  function reviewStatus(item) {
    if (!run.comparison_result) return run.comparison_error ? '整轮比较失败' : '尚无整轮比较结果';
    const card = cards.get(item.record_id);
    return selectedIds?.includes(item.record_id) ? '模型建议下载' : card?.verdict==='recommend' ? '有价值 · 容量暂缓' : {defer:'暂缓',exclude:'不推荐'}[card?.verdict] || '未记录';
  }
  const card = selected && cards.get(selected.record_id);
  const localComplete = run.items.every(item => item.local_route && (item.local_route === 'exclude' || item.local_margin != null));
  return <>
    <div className="business-toolbar">{!hideRunPicker && <Select aria-label="摘要筛选运行" value={run.id} onChange={v=>{if(data.preselection.runs?.length) navigate({run:v}); else setRunId(v);setSelected(null);}} options={(data.preselection.runs?.length ? data.preselection.runs : runs).map(r=>({value:r.id,label:r.id}))}/>}<Input aria-label="搜索论文" placeholder="搜索标题、期刊或 DOI" value={search} onChange={e=>setSearch(e.target.value)} allowClear/></div>
    <Space wrap className="node-summary">{ranking ? <>{localComplete && <Tag color="success">本地判别与排序已完成</Tag>}<Tag>本地输入 {run.items.length}</Tag><Tag>已有本地判断 {run.items.filter(i=>i.local_route).length}</Tag><Tag>已有排名 {run.items.filter(i=>i.local_rank!=null).length}</Tag><Tag>交摘要精选 {pool.length}</Tag></> : <><Tag>本轮共同输入 {pool.length}</Tag><Tag>比较结果 {run.comparison_result ? '已提交' : run.comparison_error ? '技术失败' : '未提交'}</Tag><Tag>建议下载 {selectedIds?.length ?? '未知'}</Tag><Button onClick={onShowPapers}>人工推荐与下载</Button></>}</Space>
    {run.paused && <Alert type="warning" title="本运行已暂停"/>}
    {!!run.processed_record_ids?.length && <Tag>已跳过已关联主文 {run.processed_record_ids.length} 篇</Tag>}
    {!ranking && run.comparison_error && <Alert type="error" title="整轮比较失败" description={run.comparison_error}/>}
    {!ranking && run.selection_method!=='local_decider_v1' && <Alert type="info" title="历史流程未记录新版本地前十比较池"/>}
    <Table rowKey="id" dataSource={rows} scroll={{x:800}} pagination={{pageSize:15,showSizeChanger:false}} columns={[
      {title:'本地排名',render:(_,item)=>item.local_rank ?? '未生成'},
      {title:'论文',render:(_,item)=><Button type="link" className="node-paper-title" onClick={()=>setSelected(item)}>{item.title}</Button>},
      {title:'期刊',dataIndex:'journal'},
      {title:'优先依据',render:(_,item)=><>{item.priority_institutions?.length ? <Tag>{item.priority_institutions.join('、')}</Tag> : null}{run.authority_journals?.some(j=>j.toLowerCase()===(item.journal || '').toLowerCase()) ? <Tag>优先期刊</Tag> : <span>其他 / 未知</span>}</>},
      ...(ranking ? [{title:'独立排序值',render:(_,item)=>item.local_margin ?? '未记录'}, {title:'本地结果',render:(_,item)=>localStatus(item)}] : [{title:'本轮决策',render:(_,item)=>reviewStatus(item)}, {title:'相对取舍理由',render:(_,item)=>cards.get(item.record_id)?.reason || '尚无结果'}]),
      {title:'人工查看与推进',render:(_,item)=><Space wrap><Button onClick={()=>setSelected(item)}>查看</Button><ManualPaperSelection paper={item} run={run} post={post} refresh={refresh}/></Space>},
    ]}/>
    <details><summary>运行与冻结策略</summary><p>运行：{run.id} · 策略：{run.news_selection_version || '历史未记录'}</p><p>处理阶段：{run.processing_stage} · 本地池上限：{run.review_pool_limit ?? '未记录'} · 下载清单上限：{run.shortlist_limit}</p><p>暂停期刊：{run.paused_journals?.join('、') || '无'} · 本轮跳过 {run.paused_papers?.length ?? 0} 篇</p><p>优先期刊：{run.authority_journals?.join('、') || '未记录'}</p></details>
    <Drawer title={selected?.title} open={!!selected} onClose={()=>setSelected(null)} size={680}>{selected && <>
      <PaperMetadata paper={selected} showSource/><ManualPaperSelection key={selected.record_id} paper={run.items.find(item=>item.record_id===selected.record_id) || selected} run={run} post={post} refresh={refresh}/>
      {external(selected.url) && <p><a href={external(selected.url)} target="_blank" rel="noreferrer">打开原始出处</a></p>}
      {readError && <Alert type="error" title={readError}/>}
      {!reading && !readError && api && <p role="status">正在读取归档摘要…</p>}
      {reading && <><h3>归档摘要与正文</h3><div className="readable-body">{reading.abstract || reading.body || '尚无归档文本，请查看原始出处'}</div></>}
      {ranking ? <Descriptions column={1} items={[{key:'rank',label:'本地排名',children:selected.local_rank ?? '未生成'},{key:'margin',label:'独立排序值',children:selected.local_margin ?? '未记录'},{key:'decision',label:'本地判别',children:{analyze:'通过',exclude:'不通过',insufficient:'不确定'}[selected.local_route] || '未记录'}]}/> : <><Tag>{reviewStatus(selected)}</Tag>{card && <><h3>{card.news_hook}</h3><p>{card.reason}</p>{['evidence_quote','ai_quote','domain_quote','institution_quote'].filter(key=>card[key]).map(key=><blockquote key={key}>{card[key]}</blockquote>)}{card.verification_question && <details><summary>历史下载前问题</summary><p>{card.verification_question}</p></details>}</>}</>}
      <details><summary>本阶段记录</summary><pre>{JSON.stringify(ranking ? {local_rank:selected.local_rank,local_margin:selected.local_margin,local_route:selected.local_route,priority_institutions:selected.priority_institutions,affiliations:selected.affiliations} : card || {},null,2)}</pre></details>
    </>}</Drawer>
  </>;
}

function SelectionNode(props) {
  const [historyOpen, setHistoryOpen] = useState(false);
  const [retryJob, setRetryJob] = useState(null), [retryError, setRetryError] = useState('');
  const retryRequest = useRef(null);
  useEffect(() => {
    if (!retryJob || !props.api) return;
    let alive = true, timer;
    async function poll() {
      try {
        const snapshot = await props.api('/api/daily-batch');
        if (!alive) return;
        if (snapshot.job?.id === retryJob && !['queued','collecting','selecting'].includes(snapshot.job.status)) {
          setRetryJob(null);
          setRetryError(snapshot.job.status === 'failed' ? snapshot.job.error : '');
          await props.refresh();
          return;
        }
      } catch (error) { if (alive) setRetryError(error.message); }
      if (alive) timer = setTimeout(poll, 2000);
    }
    poll();
    return () => {alive = false; clearTimeout(timer);};
  }, [retryJob, props.api, props.refresh]);
  const runs = props.data.preselection?.paper_selections;
  const run = runs?.at(-1);
  if (!runs) return <Alert type="error" title="论文精选读取失败" action={<Button onClick={props.refresh}>重试</Button>}/>;
  if (!run) return <Empty description="本批尚未建立论文精选运行"/>;
  const running = run.processing_stage && run.processing_stage !== 'idle';
  const failed = !running && !!(run.comparison_error || run.progress?.failed);
  async function retry() {
    setRetryError(''); setRetryJob('submitting');
    if (retryRequest.current?.selection_run_id !== run.id) retryRequest.current = {selection_run_id:run.id, request_id:crypto.randomUUID()};
    try {
      const job = await props.post('/api/daily-batch/retry-selection', retryRequest.current);
      retryRequest.current = null; setRetryJob(job.id);
      await props.refresh();
    } catch (error) { setRetryError(error.message); setRetryJob(null); }
  }
  const scopedProps = {...props, data:{...props.data,preselection:{...props.data.preselection,paper_selections:[run]}}};
  return <>{run.phase!=='fulltexts' && <Alert showIcon type={failed?'error':run.paused?'warning':'info'}
    title={failed?'本轮筛选失败，下载清单未形成':run.paused?'筛选已暂停，下载清单尚未形成':running || retryJob?'筛选进行中，下载清单尚未形成':'筛选未完成，等待继续'}
    description={<>{`本地已处理 ${run.progress?.completed ?? '未知'} / ${run.progress?.total ?? '未知'} 篇`}{run.comparison_error && <p>{run.comparison_error}</p>}</>}
    action={failed && !run.paused && props.post && <Button type="primary" loading={!!retryJob} disabled={!!retryJob || running} onClick={retry}>重试本轮比较（调用模型）</Button>}/>}
    {retryError && retryError !== run.comparison_error && <Alert type="error" showIcon title={retryError}/>}
    <div className="business-toolbar"><Button onClick={()=>setHistoryOpen(true)}>历史与诊断</Button></div>
    <StageWorkspace {...scopedProps} polling={false} suppressNavigation key={run.id} stageId="intel" nodeId="selection" initialTab="selection"/>
    <SelectionHistory open={historyOpen} onClose={()=>setHistoryOpen(false)} page={props.data.preselection} api={props.api} onRun={id=>props.navigate({run:id,work:'',section:'',role:''})}/>
  </>;
}

export default function IntelligenceNodes(props) {
  if (props.nodeId === 'ingest') return <CollectionNode {...props}/>;
  if (props.nodeId === 'selection') return <SelectionNode {...props}/>;
  if (props.nodeId === 'rank') return <RankingNode {...props}/>;
  return <StageWorkspace {...props} stageId="intel" initialTab={props.nodeId==='triage'?'triage':'selection'}/>;
}
