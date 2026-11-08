/** 唯一正式工作台入口；旧地址只映射到新界面，不再装配旧运行台。 */
import React, {lazy, Suspense} from 'react';
import { createRoot } from 'react-dom/client';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
const ObservationWorkspace = lazy(() => import('./observation-workspace'));
import RealWorkspace from './real-workspace';
const PromptWorkspace = lazy(() => import('./prompt-workspace'));
import { ConfigurationShell } from './workspace-shell';
const SourcesWorkspace = lazy(() => import('./sources-workspace'));
import './configuration.css';
import { consoleUrl } from '../../src/agent4pharma/entrypoints/web/static/console_routes.js';
import './live.css';
import './workspace.css';
import { createWorkbenchApi } from './workbench-api';
const token = document.querySelector('meta[name="workbench-token"]')?.content;
const nonce = document.querySelector('meta[name="style-nonce"]')?.content;
const {api, post} = createWorkbenchApi(token);

const settings = location.pathname === '/settings';
const sources = location.pathname === '/sources';
const observe = new URLSearchParams(location.search).get('view') === 'observe';
if (!settings && !sources && !observe) {
  const next = consoleUrl(location.pathname + location.search);
  if (next !== location.pathname + location.search) history.replaceState(null, '', next);
}
function Configuration() {
  document.title = `${sources ? '来源与计划' : '规范与提示词'} · agent4Pharma`;
  return <ConfigurationShell {...{api,post}} active={sources ? 'sources' : 'settings'}>{sources ? <SourcesWorkspace {...{api,post}}/> : <PromptWorkspace {...{api,post}}/>}</ConfigurationShell>;
}
createRoot(document.getElementById('root')).render(<ConfigProvider locale={zhCN} csp={{nonce}} theme={{token:{motion:!matchMedia('(prefers-reduced-motion: reduce)').matches,motionDurationSlow:'0.12s',motionDurationMid:'0.1s',motionDurationFast:'0.06s',colorPrimary:'#245a46',colorText:'#263c32',borderRadius:7,fontFamily:'"Microsoft YaHei",system-ui,sans-serif'}}}><Suspense fallback={<p role="status">正在读取工作区…</p>}>{observe ? <ObservationWorkspace/> : (settings || sources) ? <Configuration/> : <RealWorkspace api={api} post={post}/>}</Suspense></ConfigProvider>);
