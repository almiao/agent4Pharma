/** 新版正式工作台：真实集合、准确任务、服务端人工批准和真实本地导出。 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft } from 'lucide-react';
import StageWorkspace from './stage-workspace';
import IntelligenceNodes from './intelligence-nodes';
import PublicationPreview from './publication-preview';
import ReviewEvidence from './review-evidence';
import RealTaskProcess from './real-task-process';
import {PaperFlowTasks, ProductionStageTasks} from './production-paper-flows';
import ProductionBoard from './production-board';
import { productionReturnRoute, productionReturnLabel } from './production-navigation';
import { productionCollections, productionTaskStages, currentProductionRows } from './production-board-model';
import { WorkspaceHeader, WorkspaceNavigation } from './workspace-shell';
import ProductionRecords from './production-records';
import GeneratedTime from './generated-time';
import { consoleUrl, workspaceRoute } from '../../src/agent4pharma/entrypoints/web/static/console_routes.js';
import { loadWorkspaceData } from './real-workspace-data';
import { realStages, groupStories, visibleStories, manuscriptRows, taskLabels } from './real-workspace-model';
import './observation.css';
import './batch-observation.css';
import DailyBatchControl from './daily-batch-control';
import './real-workspace.css';

const parseRoute = () => workspaceRoute(location.pathname + location.search);
export default function RealWorkspace({ api, post: sendPost }) {
  const [route, setRoute] = useState(parseRoute), [data, setData] = useState(null), [model, setModel] = useState(null);
  const [errors, setErrors] = useState([]), [busy, setBusy] = useState(false), [message, setMessage] = useState('');
  const [detail, setDetail] = useState(null), [packageId, setPackageId] = useState(''), [confirmed, setConfirmed] = useState(false);
  const [oldestFirst, setOldestFirst] = useState(false);
  const generation = useRef(0), snapshots = useRef(new Map());
  const reviewRead = useRef(0);
  const post = useCallback(async (path,body) => {
    generation.current++; snapshots.current.clear();
    try { return await sendPost(path,body); }
    finally { generation.current++; snapshots.current.clear(); }
  },[sendPost]);
  const selectionScope = route.stage === 'selection' && route.section !== 'records';
  const rankingScope = route.stage === 'rank' && route.section !== 'records';
  const intelligenceScope = ['overview','ingest','triage','rank','selection'].includes(route.stage);
  const recordBatch = route.scope === 'batch' ? route.batch : '';
  const operationsScope = route.section === 'records' || route.stage === 'intake';
  const query = route.q || '';
  const setQuery = value => navigate({q:value,page:''}, true);
  function navigate(patch, replace = false) {
    const next = { ...parseRoute(), ...patch }; const url = new URL(location.href); url.search = new URLSearchParams({ view: 'workspace', ...Object.fromEntries(Object.entries(next).filter(([, v]) => v)) });
    history[replace ? 'replaceState' : 'pushState']({}, '', url); setRoute(next); setMessage('');
  }
  const dataNode = intelligenceScope ? route.stage : '';
  const scopeKey = JSON.stringify([dataNode,route.batch,operationsScope,recordBatch,selectionScope,route.run]);
  const refresh = useCallback(async () => {
    const ticket = ++generation.current;
    const {data:next, errors:failures} = await loadWorkspaceData(api,{batch:route.batch,intelligence:intelligenceScope,operations:operationsScope,recordBatch,selection:selectionScope,run:route.run,node:dataNode});
    if (ticket !== generation.current) return;
    if (selectionScope || rankingScope || !intelligenceScope && !operationsScope) {
      snapshots.current.set(scopeKey,{data:next,model:null,errors:failures});
      if(snapshots.current.size>8) snapshots.current.delete(snapshots.current.keys().next().value);
      setData(next); setErrors(failures); setModel(null); return;
    }
    try {
      const [flow, metrics, scope] = await Promise.all(['/assets/flow_projection.js','/assets/node_metrics.js','/assets/run_scope.js'].map(path => import(/* @vite-ignore */ path)));
      next.rss = next.discovery ? { selected: next.discovery.selected, decisions: [], batches: next.discovery.batches.map(id => ({ batch_id: id, states: {} })) } : null;
      const scoped = intelligenceScope || recordBatch ? scope.scopeRun(next, '') : next;
      if (ticket !== generation.current) return;
      // 制作集合显式显示全部选题，不以当天或最近批次推断归属。
      const projected = scope.bindRunLinks(metrics.completeNodeMetrics(flow.projectFlow(scoped), scoped));
      snapshots.current.set(scopeKey,{data:next,model:projected,errors:failures});
      if(snapshots.current.size>8) snapshots.current.delete(snapshots.current.keys().next().value);
      setModel(projected);
      setData({ ...next }); setErrors(failures);
    } catch (e) { if (ticket === generation.current) { setModel(null); setData({ ...next }); setErrors([...failures, e.message]); } }
  }, [api, route.batch, intelligenceScope, operationsScope, recordBatch, selectionScope, rankingScope, route.run, dataNode, scopeKey]);
  useEffect(() => { const cached=snapshots.current.get(scopeKey); setModel(cached?.model || null); setData(cached?.data || null); setErrors(cached?.errors || []); refresh(); const timer = selectionScope ? undefined : setInterval(() => { if (!document.hidden) refresh(); }, 30000); return () => { generation.current++; clearInterval(timer); }; }, [refresh]);
  const selectionRunning = selectionScope && (data?.production?.tasks?.some(task=>['queued','running'].includes(task.status)) || data?.preselection?.paper_selections?.some(run=>!run.paused && (run.processing_stage!=='idle' || run.items.some(item=>['queued','analyzing','editor','writer','reviewer','publisher'].includes(item.continuation_status)))));
  useEffect(()=>{if(!selectionRunning)return;const timer=setInterval(()=>{if(!document.hidden)refresh();},15000);return()=>clearInterval(timer);},[selectionRunning,refresh]);
  useEffect(() => { const restore = () => setRoute(parseRoute()); addEventListener('popstate', restore); document.title = '运行工作台 · 真实数据'; return () => removeEventListener('popstate', restore); }, []);
  const readReviewDetail = useCallback(async (preferredId = '') => {
    const ticket = ++reviewRead.current;
    const value = await api('/api/production/human-review?story=' + encodeURIComponent(route.story));
    const current = parseRoute();
    if (ticket !== reviewRead.current || current.story !== route.story || current.stage !== route.stage) return;
    if (preferredId && !value.items.some(p => p.id === preferredId)) throw new Error('暂未读取到新图文版本');
    setDetail(value);
    setPackageId(previous => preferredId || (value.items.some(p => p.id === previous) ? previous :
      (route.stage === 'delivery' && value.items.filter(p => p.valid).at(-1)?.id) || value.items.at(-1)?.id || ''));
  }, [api, route.story, route.stage]);
  useEffect(() => { setDetail(null); setPackageId(''); setConfirmed(false); }, [route.story, route.stage]);
  useEffect(() => { setConfirmed(false); }, [packageId]);
  useEffect(() => {
    let active = true;
    if (route.story && ['human', 'delivery'].includes(route.stage)) {
      readReviewDetail().catch(e => { if (active) setMessage(e.message); });
    }
    return () => { active = false; };
  }, [readReviewDetail, JSON.stringify(data?.human?.items.filter(p => p.story_id === route.story))]);
  useEffect(() => {
    const revealCurrent = () => {
      const rail = document.querySelector('.real-app .trace-rail'), selected = rail?.querySelector('.rail-task.active');
      if (!selected) return;
      if (rail.scrollWidth > rail.clientWidth) rail.scrollLeft = Math.max(0, selected.offsetLeft - rail.offsetLeft - 16);
      else rail.scrollTop = Math.max(0, selected.offsetTop - 80);
    };
    revealCurrent(); addEventListener('resize', revealCurrent);
    return () => removeEventListener('resize', revealCurrent);
  }, [route.stage]);
  const stories = useMemo(()=>groupStories(data?.production, data?.human),[data?.production,data?.human]);
  const rows = visibleStories(stories, route.stage, route.range === 'history');
  const manuscripts = ['human', 'delivery'].includes(route.stage) && !route.story && route.section !== 'records'
    ? manuscriptRows(rows, data?.artifacts, query, oldestFirst) : null;
  const story = stories?.find(s => s.id === (route.story || data?.production?.tasks?.find(task => task.id === route.task)?.story_id));
  const stageTasks = productionTaskStages(story?.tasks || []).map(group => group.primary).filter(Boolean);
  const storyTaskId = route.task || (stageTasks.find(task => ['running', 'failed', 'blocked', 'paused', 'queued'].includes(task.status)) || stageTasks.at(-1))?.id || '';
  const collections = useMemo(()=>productionCollections(data?.production, stories, data?.artifacts),[data?.production,stories,data?.artifacts]);
  const productionScope = ['production', 'intake'].includes(route.stage);
  const selectedFlow = data?.production?.paper_flows?.find(f=>f.task_ids.includes(route.task));
  const stageCount = id => id === 'production' ? collections ? currentProductionRows(collections).length : '未知' : stories ? visibleStories(stories,id).length : '未知';
  const selectedPackage = detail?.items.find(p => p.id === packageId);
  function workLink(path) { return consoleUrl(path); }
  async function approve() {
    setBusy(true); setMessage('');
    try { await post('/api/production/approve-package', { package_id: selectedPackage.id, expected_fingerprint: selectedPackage.fingerprint, reason: '人工已核对当前完整稿件、排版、配图和引用，确认定稿。' }); navigate({ stage: 'delivery', task: '' }); await refresh(); }
    catch (e) { setMessage(e.message); } finally { setBusy(false); }
  }
  const selectionRun = data?.preselection?.paper_selections?.at(-1);
  const overviewInputs = { ingest: ['来源任务', data?.discovery?.sources?.length], triage: ['初筛输入', data?.discovery?.articles?.length], rank: ['排序输入', selectionRun?.items?.length], selection: ['建议下载', selectionRun?.items?.filter(item=>item.can_download).length], fulltext: ['全文清单', selectionRun?.items?.filter(item=>item.can_download).length] };
  const selectedBatch = route.batch || data?.discovery?.selected || '';
  const batches = data?.discovery?.batches || [];
  const dailyBatch = (intelligenceScope || route.stage === 'overview') && <div className="workspace-batch-tools">
    {intelligenceScope && <label className="workspace-batch-picker">情报批次
      <select aria-label="情报批次" value={selectedBatch} disabled={!batches.length} onChange={e => navigate({ batch: e.target.value, story: '', task: '', run:'' })}>
        {!batches.includes(selectedBatch) && <option value={selectedBatch}>{selectedBatch || '暂无批次'}</option>}
        {batches.map(id => <option key={id}>{id}</option>)}
      </select>
    </label>}
    <DailyBatchControl {...{api,post,refresh,navigate,selectionRun,selectedBatch}} stage={route.stage}/>
  </div>;
  return <div className="trace-app real-app workspace-shell"><WorkspaceHeader {...{api,post,refresh,dailyBatch}}/>
    <div className="trace-layout"><WorkspaceNavigation active={route.stage} navigate={navigate} stageCount={stories ? stageCount : undefined}/><main className="trace-main real-main">
      <h1 className="workspace-page-title">{story?.brief?.title || realStages.find(([id]) => id === route.stage)?.[1] || (route.stage==='intake'?'选择立项候选':'运行总览')}</h1>

      {!!errors.length && <div className="real-error" role="alert">部分数据读取失败，计数保持未知：{errors.join('；')}</div>}{message && <p role="status">{message}</p>}
      {productionScope && !route.story && !route.task ? <nav className="real-story-tabs collection-tabs production-tabs" aria-label="稿件制作视图">
        <button aria-pressed={route.stage !== 'intake' && !['history','records'].includes(route.section) && route.range !== 'history'} onClick={() => navigate({stage:'production',section:'',role:'',work:'',story:'',task:'',range:'current',q:'',phase:'',state:'',page:''})}>当前任务 <span>{collections ? currentProductionRows(collections).length : '未知'}</span></button>
        <button aria-pressed={['history','records'].includes(route.section) || route.range === 'history'} onClick={() => navigate({stage:'production',section:'history',role:'',work:'',story:'',task:'',range:'current',q:'',phase:'',state:'',page:''})}>已结束 <span>{collections ? collections.history.length : '未知'}</span></button>
      </nav> : ['human','delivery'].includes(route.stage) && !route.story && !route.task && <nav className="real-story-tabs collection-tabs" aria-label="工作区视图"><button aria-pressed={!route.section && route.range !== 'history'} onClick={() => navigate({section:'',role:'',work:'',story:'',task:'',range:'current'})}>{route.stage === 'human' ? '待审稿件' : '待交付稿件'}</button><button aria-pressed={route.range === 'history' || route.section === 'records'} onClick={() => navigate({section:'records',scope:'all',role:route.stage === 'human' ? 'review' : 'publishing',work:'',story:'',task:''})}>历史记录</button></nav>}
      {!data ? <p>正在读取正式后台…</p> : (selectionScope || rankingScope) ? <IntelligenceNodes nodeId={route.stage} {...{data,model,api,post,refresh,workLink,navigate}}/> : route.section === 'records' ? model ? <><button onClick={() => navigate({section:productionScope?'history':'',range:productionScope?'current':'history',role:'',work:'',story:'',task:''})}>返回{productionScope ? '已结束' : '历史记录'}</button><ProductionRecords {...{model,data,api,post,refresh,workLink,navigate}} stage={route.stage} role={route.role || 'editorial'}/></> : <p role="alert">业务记录读取失败，请刷新重试。</p> : route.task && !route.story && !story ? <><button className="production-back" onClick={()=>navigate(productionReturnRoute(route))}><ArrowLeft size={17} aria-hidden="true"/>{productionReturnLabel(route)}</button>{!selectedFlow ? <p role="alert">未找到此任务对应的论文制作链。</p> : <div className="real-task-layout"><aside><PaperFlowTasks flow={selectedFlow} tasks={data.production.tasks} selected={route.task} navigate={navigate} layout="rail"/></aside><div><RealTaskProcess key={route.task} taskId={route.task} api={api} post={post} refresh={refresh} onTask={id=>navigate({task:id,story:''})} production={data.production} artifacts={data.artifacts} tasks={data.production?.tasks}/></div></div>}</> : route.stage === 'overview' ? <><h2>制作与人工待办</h2>{['production', 'human', 'delivery'].map(id => <button className="real-overview-row" key={id} onClick={() => navigate({ stage: id, story: '', task: '' })}><strong>{realStages.find(s => s[0] === id)[1]}</strong><span>{stageCount(id)} 篇当前停留</span></button>)}<h2>当前批次情报</h2>{realStages.slice(0,4).map(([id,label])=><button className="real-overview-row" key={id} onClick={()=>navigate({stage:id,story:'',task:''})}><strong>{label}</strong><span>{overviewInputs[id][0]} {overviewInputs[id][1] ?? (data?.preselection?.paper_selections?.length===0 && ['rank','selection','fulltext'].includes(id) ? '尚未建立运行' : '未知')}</span></button>)}</> : ['ingest', 'triage', 'rank', 'selection', 'fulltext','intake'].includes(route.stage) ? model ? route.stage === 'intake' ? <><button onClick={() => navigate({stage:'production',section:'preparation',work:'',role:''})}>返回待立项</button><StageWorkspace embedded tabKeys={['candidates']} stageId="editorial" model={model} data={data} api={api} post={post} refresh={refresh} workLink={workLink}/></> : <IntelligenceNodes key={route.stage + route.batch} nodeId={route.stage} model={model} data={data} api={api} post={post} refresh={refresh} workLink={workLink} navigate={navigate}/> : <p role="alert">批次工作区投影未成功读取，请点击刷新重试。</p> : (route.story || story) ? <>
        <button className="production-back" onClick={() => navigate(route.stage === 'production' ? productionReturnRoute(route) : {story:'',task:'',work:''})}><ArrowLeft size={17} aria-hidden="true"/>{route.stage === 'production' ? productionReturnLabel(route) : realStages.find(s => s[0] === route.stage)?.[1]}</button>
        {!story ? <p>当前读取范围没有此选题，请重新选择。</p> : <>
          {route.stage === 'production' ? <div className="real-task-layout"><aside><ProductionStageTasks tasks={story.tasks} selected={storyTaskId} onTask={id=>navigate({task:id})}/>{!story.tasks.length && <p>无关联执行任务</p>}</aside><div>{storyTaskId && <RealTaskProcess key={storyTaskId} taskId={storyTaskId} api={api} post={post} refresh={refresh} onTask={id=>navigate({task:id,story:''})} production={data.production} artifacts={data.artifacts} tasks={data.production?.tasks}/>}</div></div> : <>
            {!detail ? <p>正在读取图文…</p> : !detail.items.length ? <p>尚未生成图文。</p> : selectedPackage && <>
              <h2>{selectedPackage.title}</h2>
              {selectedPackage.blocker && <p role="alert">{selectedPackage.blocker}</p>}
              {selectedPackage.package && <PublicationPreview key={route.stage === 'human' ? route.story : selectedPackage.id} item={selectedPackage.package} post={post} refresh={refresh} reviewMode={route.stage === 'human'} onVersionChange={readReviewDetail} readEditState={sendPost}/>}
              {route.stage === 'human' ? selectedPackage.approval ? <p>已定稿 · <GeneratedTime value={selectedPackage.approval.created_at}/> <button onClick={() => navigate({ stage: 'delivery' })}>进入稿件交付</button></p>
                : selectedPackage.valid && <section className="human-approval">
                  <label><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)}/>已审阅此版本完整正文、配图、排版及引用，确认定稿</label>
                  <button className="trace-primary" disabled={busy || !confirmed || !selectedPackage.valid} onClick={approve}>确认定稿并进入交付</button>
                </section>
                : <p>{selectedPackage.approval ? selectedPackage.exported ? '此版本已有本地导出回执；不表示外部发布成功。' : '已获人工批准，可导出当前包。' : '尚未获得人工批准，无法导出此版本。'}</p>}
              <details className="real-evidence">
                <summary>版本与核验依据</summary>
                <label>图文版本 <select value={packageId} onChange={e => { setPackageId(e.target.value); setConfirmed(false); }}>
                  {detail.items.map((p, index) => <option key={p.id} value={p.id}>
                    {['版本 ' + (index + 1), p.package?.created_at && Number.isFinite(Date.parse(p.package.created_at))
                      ? new Date(p.package.created_at).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai', hour12:false}) : '',
                    p.approval ? '已定稿' : p.valid ? '可定稿' : '不可定稿'].filter(Boolean).join(' · ')}
                  </option>)}
                </select></label>
                <details>
                  <summary>核验报告与证据材料</summary>
                  {selectedPackage.review?.submission.summary && <p>{selectedPackage.review.submission.summary}</p>}
                  {selectedPackage.review?.submission.issues.map(issue => <p key={issue.id}>{issue.severity} · {issue.location} · {issue.reason}</p>)}
                  <ReviewEvidence evidence={selectedPackage.evidence}/>
                </details>
                <button onClick={() => navigate({stage:'production', task:''})}>查看制作过程</button>
              </details>
            </>}
          </>}
        </>}
      </> : route.stage === 'production' ? <ProductionBoard artifacts={data.artifacts} production={data.production} api={api} key={route.section + route.range} collections={collections} section={route.range === 'history' ? 'history' : route.section} query={query} setQuery={setQuery} navigate={navigate} phase={route.phase} status={route.state} page={route.page} onFilter={patch=>navigate(patch,true)}/> : <>
        <div className="batch-toolbar">
          <label>搜索稿件 <input value={query} onChange={e => setQuery(e.target.value)} placeholder="稿件或论文标题"/></label>
          <label>生成时间 <select value={oldestFirst ? 'oldest' : 'newest'} onChange={e => setOldestFirst(e.target.value === 'oldest')}>
            <option value="newest">最新在前</option>
            <option value="oldest">最早在前</option>
          </select></label>
        </div>
        {manuscripts === null ? <p role="alert">此集合读取失败，请刷新后重试。</p>
          : !manuscripts.length ? <p>{rows.length ? '没有符合搜索条件的稿件。' : '此范围暂无稿件。'}</p>
          : manuscripts.map(story => <button className="real-overview-row" key={story.id} onClick={() => navigate({ story: story.id, task: '' })}>
            <div><strong>{story.brief?.title || story.paperTitles?.join(' / ') || '未命名稿件'}</strong>
              <small>图文生成时间 <GeneratedTime value={story.generatedAt} empty="未记录"/></small>
            </div>
            <span>{story.statusLabel}</span>
          </button>)}
      </>}
    </main></div></div>;
}
