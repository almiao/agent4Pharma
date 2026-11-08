/** 与业务无关的Agent/模型/工具/MCP查看器；中文释义不替换原始参数。 */
import React, {useMemo,useState} from 'react';
import {Activity, Bot, Check, ChevronRight, CircleAlert, Clock3, Search, Terminal, Wrench} from 'lucide-react';
import {executionInputPreview,executionNodes,executionStream,parseTrace,publicTraceText,traceRoots} from './execution-model';
import {executionKinds,executionStates,fieldDescriptions} from './execution-labels';
import './execution-viewer.css';
import ExecutionText from './execution-text';

function Disclosure({label,children,initialOpen=false}) {
  const [open,setOpen]=useState(initialOpen);
  return <details open={open} onToggle={event=>setOpen(event.currentTarget.open)}><summary>{label}</summary>{open && (typeof children === 'function' ? children() : children)}</details>;
}

export function TraceValue({value,definitions=fieldDescriptions,depth=0}) {
  if(value===undefined)return <span className="trace-muted">未采集</span>;
  if(value===null)return <code>null</code>;
  if(typeof value==='string')return value.length>700 ? <details><summary>{value.slice(0,180)}… 展开完整文本（{value.length} 字符）</summary><div className="execution-text">{value}</div></details> : <div className="execution-text">{value || '（空字符串）'}</div>;
  if(typeof value!=='object')return <code>{String(value)}</code>;
  const rows=Object.entries(value);
  if(!rows.length)return <code>{Array.isArray(value)?'[]':'{}'}</code>;
  const content=<dl className="execution-values">{rows.map(([key,item])=><React.Fragment key={key}><dt><span>{definitions[key] || key}</span>{definitions[key] && <code>{key}</code>}</dt><dd><TraceValue value={item} definitions={definitions} depth={depth+1}/></dd></React.Fragment>)}</dl>;
  return depth>1 ? <Disclosure label={`${Array.isArray(value)?'列表':'对象'} · ${rows.length} 项`}>{content}</Disclosure> : content;
}

function Payload({label,value,definitions,initialOpen=false}) {
  const [error,setError]=useState('');
  async function copy() {try {await navigator.clipboard.writeText(typeof value==='string'?value:JSON.stringify(value,null,2));setError('已复制');}catch {setError('复制失败，可展开原始 JSON 手动复制');}}
  const text=publicTraceText(value);
  const body=parseTrace(text),bodyText=publicTraceText(body);
  return <Disclosure initialOpen={initialOpen} label={label+(value===undefined?' · 未采集':'')}>{text ? <>{bodyText ? <ExecutionText text={bodyText}/> : <TraceValue value={body} definitions={definitions}/>}{typeof value==='object' && <Disclosure label="完整结构"><TraceValue value={value} definitions={definitions}/></Disclosure>}</> : <TraceValue value={value} definitions={definitions}/>} {value!==undefined && <Disclosure label="原始键和值">{()=><><button onClick={copy}>复制</button>{error && <span role="status">{error}</span>}<pre>{JSON.stringify(value,null,2)}</pre></>}</Disclosure>}</Disclosure>;
}

function streamStatus(node,live,terminalStatus) {
  return node.status==='incomplete' ? live ? 'started' : ['paused','cancelled'].includes(terminalStatus) ? terminalStatus : node.status : node.status;
}

function StreamRow({node,definitions,live,terminalStatus,expanded}) {
  const [open,setOpen]=useState(expanded ?? ['failed','blocked'].includes(node.status));
  const [fullText,setFullText]=useState(false);
  const status=streamStatus(node,live,terminalStatus);
  const shortName=node.name?.split('__').at(-1) || node.name;
  const label=definitions[node.name] || definitions[shortName] || node.name;
  const Icon=node.kind==='model'?Bot:node.kind==='mcp'?Terminal:node.kind==='tool'?Wrench:Activity;
  const StateIcon=['failed','blocked'].includes(status)?CircleAlert:status==='succeeded'?Check:Clock3;
  const text=node.kind==='model'?publicTraceText(node.output):'';
  const preview=node.kind==='model' ? '' : executionInputPreview(node.input,definitions);
  const duration=node.duration==null?'':node.duration<1000?`${Math.round(node.duration)} ms`:`${(node.duration/1000).toFixed(1)} s`;
  const step=node.identity?.step;
  return <article className={`execution-stream-row kind-${node.kind} state-${status}`}>
    <details open={open} onToggle={event=>setOpen(event.currentTarget.open)}>
      <summary><ChevronRight className="execution-chevron" size={14} aria-hidden="true"/><Icon size={17} aria-hidden="true"/><span className="execution-row-label">{label}</span>{node.kind==='mcp' && <span className="execution-source">宿主 MCP</span>}{node.kind==='model' && step!=null && <span className="execution-source">步骤 {step}</span>}{preview && <span className="execution-row-preview" title={preview}>{preview}</span>}<span className="execution-row-outcome" title={executionStates[status] || status}><StateIcon size={14} aria-hidden="true"/><span>{executionStates[status] || status}</span></span>{duration && <time className="execution-duration">{duration}</time>}</summary>
      {open && <div className="execution-row-detail">
        <code className="execution-operation">{node.name}</code>
        {node.detail && <p role={node.status==='failed'?'alert':undefined}>{node.detail}</p>}
        {node.legacy && <p className="trace-muted">历史记录未采集调用关联。</p>}
        <Payload label="输入" value={node.input} definitions={definitions}/><Payload label="输出" value={node.output} definitions={definitions} initialOpen={node.kind!=='model'}/>
        <Disclosure label={`身份与原始记录 · ${node.events.length}`}>{()=><><TraceValue value={node.identity} definitions={definitions}/><p>{node.started || '开始时间未记录'} → {node.ended || '结束时间未记录'}</p><pre>{JSON.stringify(node.events,null,2)}</pre></>}</Disclosure>
      </div>}
    </details>
    {text && <div className="execution-message"><div className={`execution-text ${!fullText && text.length>1600?'is-preview':''}`}><ExecutionText text={text}/></div>{text.length>1600 && <button className="execution-text-toggle" aria-expanded={fullText} onClick={()=>setFullText(!fullText)}>{fullText?'收起正文':'展开完整正文'}</button>}</div>}
  </article>;
}

function Node({node,definitions,visited=new Set(),live,terminalStatus,flat=false}) {
  if(visited.has(node.id))return <p>执行关联存在循环，原始记录保留。</p>;
  const seen=new Set([...visited,node.id]);
  const status=node.status==='incomplete' ? live ? 'started' : ['paused','cancelled'].includes(terminalStatus) ? terminalStatus : node.status : node.status;
  return <details className="execution-node" open={node.status==='failed' || node.kind==='agent' || undefined}>
    <summary><span className="execution-kind">{executionKinds[node.kind] || node.kind}</span><strong>{definitions[node.name] || node.name}</strong><span className={'execution-state '+status}>{executionStates[status] || status}</span><small>{node.duration==null?'耗时未记录':`${(node.duration/1000).toFixed(2)} 秒`}</small></summary>
    {definitions[node.name] && <code>{node.name}</code>}{node.detail && <p role={node.status==='failed'?'alert':undefined}>{node.detail}</p>}
    {node.legacy && <small>历史记录：未采集Agent/模型调用关联；仅展示已保存内容。</small>}
    {node.kind==='mcp' && node.identity?.input_scope && <small>{node.identity.input_scope}</small>}
    <Payload label="输入" value={node.input} definitions={definitions}/><Payload label="输出" value={node.output} definitions={definitions}/>
    <Disclosure label={`身份、时间与原始事件（${node.events.length}）`}>{()=><><TraceValue value={node.identity} definitions={definitions}/><p>{node.started || '开始时间未记录'} → {node.ended || '结束时间未记录'}</p><pre>{JSON.stringify(node.events,null,2)}</pre></>}</Disclosure>
    {!flat && node.children.length>0 && <div className="execution-children" aria-label="下游调用">{node.children.map(child=><Node key={child.id} node={child} definitions={definitions} visited={seen} live={live} terminalStatus={terminalStatus}/>)}</div>}
  </details>;
}

export default function ExecutionViewer({events=[],agent,hasMore=false,definitions=fieldDescriptions}) {
  const [kind,setKind]=useState(''),[query,setQuery]=useState(''),[mode,setMode]=useState('stream'),[failuresOnly,setFailuresOnly]=useState(false),[includeHost,setIncludeHost]=useState(false);
  const [expansion,setExpansion]=useState({open:undefined,version:0});
  const nodes=useMemo(()=>executionNodes(events),[events]), usage=nodes.filter(n=>n.name==='token_usage');
  const visible=nodes.filter(n=>n.name!=='token_usage');
  const filteredTree=!!(kind || query || failuresOnly);
  const rows=mode!=='tree'?[]:filteredTree ? visible.filter(n=>(!kind || n.kind===kind) && (!failuresOnly || ['failed','blocked'].includes(n.status)) && (!query || `${n.name} ${JSON.stringify(n.input)} ${publicTraceText(n.output)}`.toLowerCase().includes(query.toLowerCase()))) : traceRoots(visible);
  const live=agent?.status==='running';
  const hasAgent=nodes.some(node=>node.kind==='agent');
  const stream=executionStream(nodes,{kind,query,failuresOnly,definitions,includeHost});
  const failures=visible.filter(n=>['failed','blocked'].includes(n.status)).length;
  return <section className="execution-viewer" aria-label="通用执行追踪">
    <header className="execution-heading"><div><h3>执行过程</h3>{agent && <span className="execution-model-label">{agent.model || '模型未记录'}</span>}</div><nav aria-label="执行过程视图">{[['stream','过程'],['tree','调用树']].map(([id,label])=><button key={id} aria-pressed={mode===id} onClick={()=>{setMode(id);if(id==='stream' && kind==='agent')setKind('');}}>{label}</button>)}</nav></header>
    <div className="execution-tools"><label className="execution-search"><Search size={15} aria-hidden="true"/><input aria-label="搜索执行记录" placeholder="搜索工具、参数或正文" value={query} onChange={e=>setQuery(e.target.value)}/></label><select aria-label="执行类型" value={kind} onChange={e=>setKind(e.target.value)}><option value="">全部类型</option>{Object.entries(executionKinds).filter(([key])=>mode==='tree' || key!=='agent').map(([key,label])=><option key={key} value={key}>{label}</option>)}</select><button className="execution-failure-filter" aria-pressed={failuresOnly} onClick={()=>setFailuresOnly(!failuresOnly)}><CircleAlert size={14} aria-hidden="true"/>异常 {failures}</button>{mode==='stream' && <button aria-pressed={includeHost} onClick={()=>setIncludeHost(!includeHost)}>宿主 MCP {nodes.filter(n=>n.kind==='mcp').length}</button>}{mode==='stream' && <><span className="execution-shown" role="status">{stream.length} 条</span><button onClick={()=>setExpansion(s=>({open:s.open!==true,version:s.version+1}))}>{expansion.open===true?'收起详情':'展开详情'}</button></>}</div>
    {agent && <details className="execution-agent"><summary>会话信息 <span>{agent.framework || '框架未记录'}</span></summary><p>身份：{agent.id}</p>{!hasAgent && <><small>以下为已保存的绑定输入与阶段产物；历史逐次模型请求未采集。</small><Payload label="绑定输入" value={parseTrace(agent.input)} definitions={definitions}/><Payload label="系统提示词快照" value={agent.prompt} definitions={definitions}/><Payload label="已保存输出" value={parseTrace(agent.output)} definitions={definitions}/></>}</details>}
    {hasMore && <p role="status">正在继续读取历史，当前调用链可能不完整。</p>}
    {mode==='stream' ? <div className="execution-stream">{!stream.length && <p className="execution-empty">{visible.length?'没有符合筛选条件的记录。':'尚无已采集执行记录。'}</p>}{stream.map(node=><StreamRow key={`${node.id}:${expansion.version}`} node={node} definitions={definitions} live={live} terminalStatus={agent?.status} expanded={expansion.open}/>)}</div> : <div className="execution-tree">{!rows.length && <p className="execution-empty">没有符合筛选条件的记录。</p>}{rows.map(node=><Node key={node.id} node={node} definitions={definitions} live={live} terminalStatus={agent?.status} flat={filteredTree}/>)}</div>}
    {usage.length>0 && <details><summary>用量快照（{usage.length}）</summary>{usage.map(node=><Node key={node.id} node={node} definitions={definitions} live={live} terminalStatus={agent?.status}/>)}</details>}
  </section>;
}
