/** 来源目录与采集计划只读浏览；筛选由服务端执行后分页。 */
import React, { useEffect, useRef, useState } from 'react';
import { Alert, Button, Input, Select, Table, Tag, Tabs } from 'antd';
import RetrievalSettings from './retrieval-settings';
import { sourceQuery, readSourceFilters, sourceKinds, safeHttpUrl } from './sources-model';

export function SourceDetails({ item }) {
  const availability = item.collection_availability;
  return <div className="source-details">{availability && <section><strong>{availability.reason}</strong><p>复核时间：{availability.checked_at || '未知'}</p><details><summary>逐次检查记录（{availability.checks.length} 次）</summary>{availability.checks.map((check, i) => <div key={i}><p>{check.mode} · {check.reason}</p>{safeHttpUrl(check.url) ? <a href={check.url} target="_blank" rel="noreferrer">{check.url}</a> : <span>{check.url}</span>}<p>{check.checked_at || '未知'}</p>{check.final_url && <p>最终地址：{check.final_url}</p>}</div>)}</details></section>}<details><summary>目录信息</summary><pre>{JSON.stringify(item, null, 2)}</pre></details></div>;
}

export function SourceDirectory({ api }) {
  const [filters, setFilters] = useState(() => readSourceFilters(location.search));
  const [search, setSearch] = useState(filters.q);
  const [data, setData] = useState(null), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0), [expanded, setExpanded] = useState(() => new URLSearchParams(location.search).getAll('source'));
  const generation = useRef(0);
  function apply(patch) {
    const next = {...filters, offset:0, ...patch};
    const url = new URL(location.href);
    for (const key of ['q','availability','kind','status','offset']) url.searchParams.delete(key);
    for (const [key,value] of sourceQuery(next)) url.searchParams.set(key,value);
    history.pushState(null,'',url); setFilters(next); setSearch(next.q);
  }
  useEffect(() => {
    const restore = () => { const next = readSourceFilters(location.search); setFilters(next); setSearch(next.q); setExpanded(new URLSearchParams(location.search).getAll('source')); };
    addEventListener('popstate',restore); return () => removeEventListener('popstate',restore);
  }, []);
  useEffect(() => {
    const ticket = ++generation.current; setBusy(true); setError(''); setData(null);
    api('/api/sources?' + sourceQuery(filters)).then(result => { if(ticket === generation.current) setData(result); }).catch(e => { if(ticket === generation.current) setError(e.message); }).finally(() => { if(ticket === generation.current) setBusy(false); });
    return () => { generation.current++; };
  }, [api, filters, revision]);
  useEffect(() => {
    const identity = new URLSearchParams(location.search).get('source');
    if(data && identity) document.getElementById('source-' + identity)?.scrollIntoView({block:'nearest'});
  }, [data]);
  return <section aria-label="来源目录"><form className="business-toolbar source-toolbar" role="search" onSubmit={e => {e.preventDefault(); apply({q:search});}}><Input aria-label="搜索来源" placeholder="搜索名称、ID 或域名" value={search} onChange={e=>setSearch(e.target.value)}/><Select aria-label="采集可用性" value={filters.availability} onChange={availability=>apply({availability})} options={[{value:'',label:'全部可用状态'},{value:'available',label:'可用 · 入口已验证'},{value:'currently_unavailable',label:'当前不可用'},{value:'needs_configuration',label:'待配置'},{value:'unverified',label:'未复核'}]}/><Select aria-label="来源类型" value={filters.kind} onChange={kind=>apply({kind})} options={[{value:'',label:'全部类型'},...[...new Set([...(data?.kinds || []),...(filters.kind ? [filters.kind] : [])])].map(value=>({value,label:sourceKinds[value] || value}))]}/><Select aria-label="关注状态" value={filters.status} onChange={status=>apply({status})} options={[{value:'',label:'全部关注状态'},{value:'watch',label:'已关注'},{value:'paused',label:'已暂停'},{value:'candidate',label:'候选'}]}/><Button htmlType="submit">搜索</Button><Button onClick={()=>apply({q:'',availability:'',kind:'',status:''})}>重置</Button><Button onClick={()=>setRevision(v=>v+1)} disabled={busy}>刷新</Button></form>
    {error && <Alert type="error" title="来源读取失败" description={error} action={<Button onClick={()=>setRevision(v=>v+1)}>重试</Button>}/>}
    <Table rowKey="id" scroll={{y:'60vh'}} onRow={item=>({id:'source-'+item.id})} loading={busy} dataSource={data?.items || []} pagination={false} locale={{emptyText:error ? '读取失败' : '没有匹配来源'}} expandable={{expandedRowKeys:expanded,onExpandedRowsChange:setExpanded,expandedRowRender:item=><SourceDetails item={item}/>}} columns={[
      {title:'来源 / 类型',key:'name',render:(_,item)=><><strong>{item.name}</strong><div className="source-muted">{sourceKinds[item.source_kind] || item.source_kind}</div></>},
      {title:'入口',dataIndex:'endpoint_url',render:url=>safeHttpUrl(url) ? <a href={url} target="_blank" rel="noreferrer">{url}</a> : <span>{url || '未设置入口'}</span>},
      {title:'状态 / 资料',key:'status',render:(_,item)=><><Tag>{({watch:'已关注',paused:'暂停',candidate:'候选'})[item.status] || item.status}</Tag><p>{item.material_count} 条来源记录</p><span>{({available:'可用',currently_unavailable:'当前不可用',needs_configuration:'待配置'})[item.collection_availability?.status] || '未复核'}</span></>},
    ]}/><div className="source-pagination"><span role="status">{busy ? '正在读取…' : error ? '查询失败' : data?.items.length ? `${filters.offset+1}–${filters.offset+data.items.length} 条${data.has_more ? '' : ' · 已到底'}` : '0 条匹配'}</span><Button disabled={busy || !!error || !filters.offset} onClick={()=>apply({offset:Math.max(0,filters.offset-200)})}>上一页</Button><Button disabled={busy || !!error || !data?.has_more} onClick={()=>apply({offset:filters.offset+200})}>下一页</Button></div>
  </section>;
}

export function CollectionPlans({ api }) {
  const [data,setData] = useState(null), [error,setError] = useState(''), [revision,setRevision] = useState(0);
  useEffect(()=>{let active=true; setError(''); setData(null); api('/api/collection-plans').then(value=>{if(active)setData(value.items);}).catch(e=>{if(active)setError(e.message);});return()=>{active=false;};},[api,revision]);
  return <section aria-label="采集计划"><div className="source-actions"><Button onClick={()=>setRevision(v=>v+1)}>刷新计划</Button></div>{error && <Alert type="error" title="计划读取失败" description={error} action={<Button onClick={()=>setRevision(v=>v+1)}>重试</Button>}/>}<Table rowKey="source_id" loading={!data && !error} dataSource={data || []} pagination={{pageSize:20}} locale={{emptyText:error?'读取失败':'尚无登记计划'}} columns={[{title:'计划',dataIndex:'name'},{title:'来源',dataIndex:'source_id'},{title:'状态',dataIndex:'enabled',render:enabled=><Tag>{enabled?'已启用':'已暂停'}</Tag>}]} expandable={{expandedRowRender:item=><pre>{JSON.stringify(item,null,2)}</pre>}}/></section>;
}

export default function SourcesWorkspace({ api, post }) {
  const readTab = () => { const p = new URLSearchParams(location.search); return p.get('policy') ? 'retrieval' : ['directory','plans','retrieval'].includes(p.get('tab')) ? p.get('tab') : 'directory'; };
  const [tab,setTab] = useState(readTab);
  useEffect(()=>{const restore=()=>setTab(readTab());addEventListener('popstate',restore);return()=>removeEventListener('popstate',restore);},[]);
  return <><h1 className="workspace-page-title">来源与计划</h1><Tabs activeKey={tab} onChange={value=>{const url=new URL(location.href);url.searchParams.set('tab',value); if(value!=='retrieval')url.searchParams.delete('policy');history.pushState(null,'',url);setTab(value);}} items={[{key:'directory',label:'来源目录',children:<SourceDirectory api={api}/>},{key:'plans',label:'采集计划',children:<CollectionPlans api={api}/>},{key:'retrieval',label:'上游检索词',children:<RetrievalSettings {...{api,post}}/>}]}/></>;
}
