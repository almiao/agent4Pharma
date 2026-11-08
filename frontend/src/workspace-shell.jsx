/** 正式工作台共用导航；配置页不加载业务批次或任务索引。 */
import React, {useState} from 'react';
import { RotateCw } from 'lucide-react';
import BackgroundActivity from './background-activity';
import { realStages } from './real-workspace-model';
import './observation.css';
import './real-workspace.css';

export function WorkspaceHeader({ api, post, refresh, active, dailyBatch }) {
  const [refreshing,setRefreshing] = useState(false), [error,setError] = useState('');
  async function reload() {
    if (refreshing) return;
    setRefreshing(true); setError('');
    try { await refresh(); } catch(e) { setError(e.message); }
    finally { setRefreshing(false); }
  }
  return <header className="trace-top">
    <a className="trace-brand" href="/console">a4p <span>运行工作台</span></a>
    <nav className="workspace-global-links" aria-label="配置"><a href="/sources" aria-current={active === 'sources' ? 'page' : undefined}>来源与计划</a><a href="/settings" aria-current={active === 'settings' ? 'page' : undefined}>规范与提示词</a></nav>
    {refresh && <button className="workspace-refresh" aria-label="刷新当前视图" title={refreshing ? "正在刷新" : "刷新当前视图"} disabled={refreshing} aria-busy={refreshing} onClick={reload}><RotateCw size={17} aria-hidden="true"/>{refreshing && <span role="status">刷新中</span>}</button>}
    {error && <span role="alert">{error}</span>}
    <BackgroundActivity api={api} post={post}/>
    {dailyBatch}
  </header>;
}

export function WorkspaceNavigation({ active, navigate, stageCount }) {
  function follow(event, stage) {
    if (!navigate || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate({ stage, story: '', task: '', range: 'current', section: '', role: '', work: '', q: '', phase: '', state: '', page: '' });
  }
  return <aside className="trace-rail workspace-navigation-rail" aria-label="主导航">
    <a className={'rail-overview' + (active === 'overview' ? ' active' : '')} aria-current={active === 'overview' ? 'page' : undefined} href="/console" onClick={event => follow(event, 'overview')}>运行总览</a>
    {realStages.map(([id, label], i) => {
      const current = active === id || id === 'production' && active === 'intake';
      const count = stageCount && ['production', 'human', 'delivery'].includes(id) ? stageCount(id) : null;
      return <a key={id} aria-current={current ? 'page' : undefined} aria-label={count !== null ? `${label}，${count} 项当前停留，全部选题` : label} className={'rail-task' + (current ? ' active' : '')} href={'/console?view=workspace&stage=' + id} onClick={event => follow(event, id)}><div><span>{i + 1}</span><strong>{label}</strong>{count !== null && <b>{count}</b>}</div></a>;
    })}
    <nav className="configuration-nav" aria-label="配置管理">{[['sources','来源与计划'],['settings','规范与提示词']].map(([id,label]) => <a key={id} className={'rail-task ' + (active === id ? 'active' : '')} aria-current={active === id ? 'page' : undefined} href={'/' + id}><div><strong>{label}</strong></div></a>)}</nav>
  </aside>;
}

export function ConfigurationShell({ api, post, active, children }) {
  return <div className="trace-app real-app workspace-shell configuration-shell"><WorkspaceHeader {...{api, post, active}}/><div className="trace-layout"><WorkspaceNavigation active={active}/><main className="trace-main real-main">{children}</main></div></div>;
}
