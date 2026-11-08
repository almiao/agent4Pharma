/** 单篇运行观察：任务关系常驻，过程内容就地展开，产物与调用双向定位。 */
import React, { useEffect, useState } from 'react';
import { Activity, ArrowDown, ArrowLeft, ArrowRight, Check, ChevronDown, FileText, GitBranch, Search, X } from 'lucide-react';
import { story, tasks, artifacts, edges, taskById } from './observation-sample';
import './observation.css';
import BatchObservation from './batch-observation';
import EditorialWorkspace from './editorial-workspace';
import { editorialKey, initialEditorial } from './editorial-sample';

function State({ status, children }) { return <span className={`trace-state ${status}`}>{status==='succeeded' && <Check size={12}/>} {children || ({succeeded:'完成',rejected:'校验拒绝',changes:'要求修改',queued:'未开始'})[status]}</span>; }
function Value({ value }) {
  if (typeof value==='string') return <div className="trace-text">{value}</div>;
  if (Array.isArray(value) && !value.length) return <span className="trace-muted">[]（空数组）</span>;
  if (Array.isArray(value)) return <div>{value.map((v,i)=><Value key={i} value={v}/>)}</div>;
  if (value && typeof value==='object') return <dl className="trace-values">{Object.entries(value).map(([k,v])=><React.Fragment key={k}><dt>{k}</dt><dd><Value value={v}/></dd></React.Fragment>)}</dl>;
  return <span>{String(value)}</span>;
}
function ProductionObservation() {
  const read = () => { const p=new URLSearchParams(location.search); return {task:p.get('task') || 'write1',event:p.get('event') || '',artifact:p.get('artifact') || '',mode:p.get('mode') || 'process'}; };
  const [route,setRoute]=useState(read),[query,setQuery]=useState(''),[issues,setIssues]=useState(false);
  const [editorial, setEditorial] = useState(() => { try { return JSON.parse(localStorage.getItem(editorialKey)) || initialEditorial(); } catch { return initialEditorial(); } });
  const latestVersion = editorial.versions?.at(-1)?.id;
  const release = editorial.releases?.find(r => r.package.version === latestVersion);
  const humanOutcome = release ? '已定稿 · ' + latestVersion : '等待人工审稿 · ' + (latestVersion || 'v2');
  const publishOutcome = release?.status || '等待人工批准';
  const publicationState = release?.status === '发布成功（模拟）' ? 'succeeded' : release?.status === '发布失败（模拟）' ? 'rejected' : 'queued';
  function navigate(patch) { const next={...route,...patch}; const url=new URL(location.href); for(const k of ['run','agent','attempt','stage']) url.searchParams.delete(k); url.searchParams.set('view','observe'); Object.entries(next).forEach(([k,v])=>v?url.searchParams.set(k,v):url.searchParams.delete(k)); history.pushState({},'',url);setRoute(next);if ('task' in patch || 'mode' in patch || 'event' in patch) { setQuery('');setIssues(false); } }
  useEffect(()=>{ const fn=()=>setRoute(read());window.addEventListener('popstate',fn);document.title='采编执行观察 · MVP';return()=>window.removeEventListener('popstate',fn);},[]);
  useEffect(()=>{if(route.event) document.getElementById(`event-${route.event}`)?.scrollIntoView({block:'center',behavior:'instant'});},[route.event,route.task,route.mode]);
  useEffect(()=>{if(!route.event) window.scrollTo({top:0,behavior:'instant'});},[route.task,route.mode]);
  const task=taskById(route.task) || tasks[1], artifact=artifacts[route.artifact];
  const visible=task.events.filter(e=>(!issues || e.status==='rejected') && (!query || JSON.stringify(e).toLowerCase().includes(query.toLowerCase())));
  const openTask=(id,event='')=>navigate({task:id,event,mode:id==='human'?'editorial':id==='publish'?'release':'process',artifact:''});
  const openArtifact=id=>navigate({artifact:id});
  const chip=id=><button className="artifact-chip" key={id} onClick={()=>openArtifact(id)}><FileText size={13}/>{artifacts[id].title}<ArrowRight size={12}/></button>;
  return <div className="trace-app">
    <header className="trace-top"><a href="/console" className="trace-brand">a4p <span>运行观察</span></a><span className="trace-top-path">采编工作流 / {story.batch}</span><span className="trace-sample">虚构样例 · 非生产日志</span><a href="/console">返回生产台</a></header>
    <div className="batch-breadcrumb"><a href="/console?view=observe&screen=batch&stage=overview">← 批次总览</a><span>/</span><a href="/console?view=observe&screen=batch&stage=fulltext&paper=paper-01">论文处理历程 · paper-01</a><span>/ 本篇制作</span></div>
    <div className="trace-heading"><div><p>{story.id} <span>样例工作流 · 人工批准后直接发布</span></p><h1>{story.title}</h1></div><div className="trace-heading-actions"><State status={publicationState}>{release ? publishOutcome : humanOutcome}</State><button className="trace-primary" onClick={()=>openTask('human')}><FileText size={15}/>进入人工审稿</button></div></div>
    <div className="trace-layout">
      <aside className="trace-rail"><button className={`rail-overview ${route.mode==='overview'?'active':''}`} onClick={()=>navigate({mode:'overview',event:'',artifact:''})}><GitBranch size={16}/>整体流转与交接</button><div className="rail-label">本篇执行链 <span>8 个节点</span></div><div className="rail-chain">{tasks.map((t,i)=><React.Fragment key={t.id}>{i>0 && <button className={`rail-edge ${t.id==='write2'?'rework':''}`} onClick={()=>{openTask(t.id);}}><ArrowDown size={13}/>{edges.find(e=>e.to===t.id).reason}{t.id==='write2' && ' · R-01'}</button>}<button className={`rail-task ${(task.id===t.id && route.mode==='process') || (t.id==='human' && route.mode==='editorial') || (t.id==='publish' && route.mode==='release')?'active':''}`} onClick={()=>openTask(t.id)}><div><span className="rail-number">{i+1}</span><strong>{t.label}</strong><span className={`rail-dot ${t.id==='human' && release?'succeeded':t.id==='publish'?publicationState:t.status}`}/></div><small>{t.role} · {t.id}</small><span className="rail-outcome">{t.id==='human'?humanOutcome:t.id==='publish'?publishOutcome:t.outcome}</span>{t.id==='write1' && <span className="rail-warning">1 次提交拒绝 · 会话内已修正</span>}</button></React.Fragment>)}</div><div className="rail-foot">修订回到人工审稿；批准后直接发布。</div></aside>
      <main className="trace-main">
      {['editorial','release'].includes(route.mode)? <EditorialWorkspace onTrace={openTask} section={route.mode==='release'?'publish':'human'} onNavigate={openTask} onState={setEditorial}/> : route.mode==='overview'? <><div className="trace-section-title"><div><h2>数据如何流转</h2><p>每条交接关联实际样例产物及接收任务。</p></div><span>7 条交接 · 人工定稿后发布</span></div><div className="handoff-list"><div className="source-row"><FileText size={20}/><div><strong>输入：论文证据</strong><p>原文与验证限制随任务冻结</p></div>{chip('paper')}</div>{edges.map(e=><section key={e.to} className={`handoff-row ${e.to==='write2'?'rework':''}`}><div className="handoff-line"><button onClick={()=>openTask(e.from)}>{taskById(e.from).label}<small>{e.from}</small></button><div><span>{e.reason}</span><ArrowRight size={24}/></div><button onClick={()=>openTask(e.to)}>{taskById(e.to).label}<small>{e.to}</small></button></div><div className="handoff-data"><span>交接内容</span>{taskById(e.to).inputs.map(chip)}{e.to==='publish' && <button onClick={()=>openTask('publish')}>{release ? `已批准 ${release.package.id} · ${release.status}` : '等待人工批准的完整发布包'}</button>}</div>{e.to==='write2' && <p className="review-warning">核验发现稿件 v1 的泛化结论超出证据，产生 R-01，派生新的写作任务。</p>}</section>)}</div></> : <>
        <div className="task-header"><div><span className="task-role">{task.role} / {task.phase}</span><h2>{task.label}<State status={task.status}>{task.outcome}</State></h2><p>{task.id} · {task.start?`${task.start} — ${task.end}`:'排队等待执行'} · 执行尝试 1</p></div><button onClick={()=>navigate({mode:'overview',event:''})}><GitBranch size={15}/>在整体流转中查看</button></div>
        <div className="task-context"><div><span>输入</span><div>{task.inputs.map(chip)}</div></div><div><span>输出</span><div>{task.outputs.length?task.outputs.map(chip):<span className="trace-muted">尚无产物</span>}</div></div><details><summary>冻结配置与执行上下文<ChevronDown size={13}/></summary><dl className="config-grid"><dt>模型</dt><dd>{story.model}</dd><dt>技能包</dt><dd>{story.skill}</dd><dt>父任务</dt><dd>{task.parent?<button onClick={()=>openTask(task.parent)}>{task.parent}</button>:'无；本链起点'}</dd><dt>任务约束</dt><dd>仅使用绑定材料；结构化提交由宿主校验。</dd><dt>记录范围</dt><dd>公开响应与调用记录；示例工具名称用于表达交互，不等同生产协议。</dd></dl></details></div>
        <div className="process-toolbar"><strong>执行过程 <span>{task.events.length} 条</span></strong><label><Search size={14}/><input aria-label="搜索执行内容" placeholder="搜索消息、参数、返回" value={query} onChange={e=>setQuery(e.target.value)}/></label><button aria-pressed={issues} className={issues?'active':''} onClick={()=>setIssues(!issues)}>只看拒绝</button></div>
        <div className="process-stream"><div className="context-entry"><span className="event-bullet"><Activity size={13}/></span><strong>{task.start?'宿主创建并领取任务':'宿主已创建任务，等待执行'}</strong><p>{task.parent?`由 ${task.parent} 的结构化交接创建，绑定以上输入版本。`:'绑定论文证据，开始采编制作。'}</p></div>
        {!visible.length && <div className="trace-empty">{task.events.length?'没有匹配的事件。调整搜索或取消筛选。':'尚未领取，未调用模型，也没有输出。上游通过不代表本阶段已执行。'}</div>}
        {visible.map(e=><section id={`event-${e.id}`} key={e.id} className={`process-event ${e.kind} ${e.status} ${route.event===e.id?'focused':''}`}><span className="event-bullet">{e.kind==='model'? 'A' : '↳'}</span><header><span>{e.kind==='model'?task.role:'工具调用'}<small>{e.time}</small></span><State status={e.status}/><button aria-label={`定位 ${e.title}`} className="event-anchor" onClick={()=>navigate({event:e.id})}>#</button></header>
          {e.kind==='model'?<><div className="agent-message">{e.text}</div>{e.input && <details className="model-context"><summary>本轮模型输入与用量</summary><p>{e.input}</p><small>{e.tokens} · 此处为样例摘要，非完整请求载荷</small></details>}</>:<><h3>{e.title}</h3><div className="tool-name">{e.name}{e.parentCall && <button onClick={()=>navigate({event:e.parentCall})}>由上一轮模型响应触发 ↗</button>}</div><div className="call-io"><div className="call-input"><h4>调用输入</h4><Value value={e.input}/></div><div className="call-output"><h4>{e.status==='rejected'?'拒绝原因与返回':'工具返回'}</h4><Value value={e.output}/>{e.outputArtifact && <div className="output-artifact">{chip(e.outputArtifact)}</div>}</div></div>{e.diff && <div className="revision-diff"><div><span>稿件 v1</span><del>{e.diff.before}</del></div><div><span>稿件 v2</span><ins>{e.diff.after}</ins></div></div>}
          {e.next && <button className={`next-handoff ${e.next==='write2'?'rework':''}`} onClick={()=>openTask(e.next)}><GitBranch size={15}/>{e.next==='write2'?'要求修改，派生':'交接至'} {taskById(e.next).label}<span>{e.next}</span><ArrowRight size={15}/></button>}</>}
          <details className="raw-event"><summary>原始样例记录</summary><pre>{JSON.stringify(e,null,2)}</pre></details>
        </section>)}
        {task.events.length>0 && !query && !issues && <div className="context-entry ending"><span className="event-bullet"><Check size={13}/></span><strong>任务执行结束 · {task.outcome}</strong><p>{task.status==='changes'?'核验任务成功提交了修改要求；下游进入返工。':'业务产物已保存，完整交接关系保留在左侧执行链。'}</p></div>}</div>
      </>}
      </main>
      {artifact && <aside className="artifact-panel" aria-label="产物阅读器"><header><span>{artifact.type}</span><button aria-label="关闭产物" onClick={()=>navigate({artifact:''})}><X size={18}/></button></header><h2>{artifact.title}</h2><div className="artifact-links">{artifact.producer?<button onClick={()=>openTask(artifact.producer,artifact.event)}><ArrowLeft size={14}/>定位生成此版本的调用</button>:<span>外部输入 · 无生成任务</span>}<details><summary>被哪些任务读取</summary>{tasks.filter(t=>t.inputs.includes(route.artifact)).map(t=><button key={t.id} onClick={()=>openTask(t.id)}>{t.label} · {t.id}</button>)}</details></div>{['draft1','draft2'].includes(route.artifact) && <div className="version-switch"><button className={route.artifact==='draft1'?'active':''} onClick={()=>openArtifact('draft1')}>历史 v1</button><button className={route.artifact==='draft2'?'active':''} onClick={()=>openArtifact('draft2')}>当前 v2</button></div>}<article>{artifact.body.split('\n').filter(Boolean).map((p,i)=>p.startsWith('## ')?<h3 key={i}>{p.slice(3)}</h3>:<p key={i}>{p}</p>)}</article><div className="artifact-note">虚构内容，仅用于验证执行观察与版本交接。</div></aside>}
    </div>
  </div>;
}

export default function ObservationWorkspace() {
  const query = new URLSearchParams(location.search);
  return query.get('screen') === 'batch' || (!query.has('mode') && !query.has('task')) ? <BatchObservation/> : <ProductionObservation/>;
}
