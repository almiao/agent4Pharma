/** 单篇 PDF 人工入口；只对冻结清单开放，连接地址短暂显示，轮询不会触发下载。 */
import React, { useEffect, useRef, useState } from 'react';
import { Alert, Button, Checkbox, Collapse, Input, Modal, Select, Space, Tag } from 'antd';

const active = new Set(['queued', 'waiting_connection', 'downloading', 'validating']);
const phases = { not_started: '尚未获取 PDF', queued: '已受理', waiting_connection: '等待浏览器连接', downloading: '正在获取 PDF', validating: '正在校验主文', downloaded: 'PDF 已校验', manual_required: '需要人工处理', failed: '本次获取未完成' };
const reasons = {
  account_blocked_requires_manual: '账号下载仍处于暂停状态。请确认恢复后再尝试。',
  daily_attempt_limit: '此前因每日上限停止；该限制已取消，可点击重试 PDF。',
  attempt_interval: '距上次尝试不足 120 秒，请稍后再试。',
  daily_browser_publisher_not_supported: '当前出版商需人工获取主文 PDF 并导入。',
  publisher_article_not_found: '出版商或代理的论文路径返回 404，请核对官方原文入口；重试相同地址通常无法解决。',
  publisher_confirmation_required: '请手动点击 Chaoslib 的人工确认，等 Nature 或 Springer 新标签页打开后，再点击重试 PDF。',
  publisher_tabs_require_human: '发现多个同类出版商标签页，请保留要使用的一个，其余关闭后重试。',
  browser_action_failed: '浏览器操作未完成；请保留已手动打开的出版商页，查看页面状态后重试。',
  publisher_verification_required: '出版商要求验证，请在浏览器完成人工处理后再重试。',
  human_login_required: '请先在日常 Chrome 登录 Chaoslib。',
  select_chaoslib_tab: '连接时请选择已登录的 Chaoslib 标签页。',
  download_interrupted: '任务已超时或服务中断；确认浏览器状态后可手动重试。',
  browser_connection_or_action_timeout: '本次连接或下载等待超时，请检查浏览器后重试。',
};

export default function PaperDownloadControl({ paper, api, post, paused, refresh, debug = false }) {
  const [open, setOpen] = useState(false), [status, setStatus] = useState(null);
  const [error, setError] = useState(''), [submitting, setSubmitting] = useState(false), [restored, setRestored] = useState(false);
  const [browserReady, setBrowserReady] = useState(false);
  const submitted = useRef(''), generation = useRef(0);
  const endpoint = debug ? '/api/paper-downloads/debug' : '/api/paper-downloads';
  const query = new URLSearchParams({ run: paper.run_id, record: paper.record_id });
  const busy = submitting || active.has(status?.phase);

  useEffect(() => {
    if (!open) return;
    const ticket = ++generation.current; let timer;
    async function poll() {
      try {
        const current = await api(endpoint + '?' + query);
        if (ticket !== generation.current) return;
        setStatus(current); setError('');
        if (active.has(current.phase)) timer = setTimeout(poll, 1500);
        else if (submitted.current && submitted.current === current.id) { submitted.current = ''; refresh(); }
      } catch (err) { if (ticket === generation.current) setError(err.message); }
    }
    poll();
    return () => { generation.current++; clearTimeout(timer); };
  }, [open, paper.run_id, paper.record_id, submitting]);

  async function start() {
    if (busy || !browserReady || paused && !restored) return;
    setSubmitting(true); setError('');
    try {
      const result = await post(endpoint, { run_id: paper.run_id, record_id: paper.record_id, retry: ['failed', 'manual_required'].includes(status?.phase), restore_account: paused && restored });
      submitted.current = result.job_id;
      setStatus(current => ({ ...current, phase: result.phase }));
    } catch (err) { setError(err.message); }
    finally { setSubmitting(false); }
  }
  async function save() {
    setSubmitting(true); setError('');
    try {
      const response = await fetch(endpoint + '/file?' + query, { headers: { 'X-Workbench-Token': document.querySelector('meta[name="workbench-token"]')?.content }, signal: AbortSignal.timeout(30000) });
      if (!response.ok) throw new Error((await response.json()).error || 'PDF 保存失败');
      const url = URL.createObjectURL(await response.blob()), anchor = document.createElement('a');
      anchor.href = url; anchor.download = `paper-${paper.record_id.replace(/[^a-zA-Z0-9_-]/g, '').slice(0, 60)}.pdf`; anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) { setError(err.message); }
    finally { setSubmitting(false); }
  }
  if (!debug && !paper.can_download) return <span>{paper.state === 'deferred' ? '未入选全文名单' : paper.state === 'abstract_excluded' ? '摘要未通过' : '完成摘要筛选并入选后可下载'}</span>;
  return <>
    <Button size="small" onClick={() => { setOpen(true); setRestored(false); setBrowserReady(false); }}>{debug ? '下载 PDF（调试）' : '下载 PDF'}</Button>
    <Modal title="获取论文 PDF" open={open} onCancel={() => setOpen(false)} footer={null} destroyOnHidden>
      <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
        <strong>{paper.title}</strong>
        {/^10\.1038\/[A-Za-z0-9._();:-]+$/.test(paper.doi || '') && <a href={'https://www.nature.com/articles/' + paper.doi.split('/')[1]} target="_blank" rel="noreferrer">打开 Nature 官方原文</a>}
        {status?.phase !== 'downloaded' && <PaperBrowserControl api={api} post={post} disabled={busy} onStatus={phase => setBrowserReady(phase === 'ready')} />}
        <div role="status" aria-live="polite"><Tag>{phases[status?.phase] || '读取下载状态'}</Tag></div>
        {error && <Alert type="error" showIcon title={error} action={<Button onClick={() => { setOpen(false); setTimeout(() => setOpen(true), 0); }}>刷新状态</Button>} />}
        {status?.phase === 'downloading' && <Alert type="info" showIcon title="若 Chaoslib 显示人工确认，请手动点击" description="程序最多等待 60 秒接管新开的 Nature / Springer 页面；已手动打开的页面会优先复用。不要重复点击下载。" />}
        {status?.phase === 'waiting_connection' && <>
          <p>在 Playwright 页面确认连接并选择已登录的 Chaoslib 标签页。未弹出时，复制下方地址到日常 Chrome 地址栏；仅本次等待期间有效。</p>
          {status.connect_url && <><Input.TextArea readOnly autoSize={{ minRows: 2, maxRows: 4 }} value={status.connect_url} aria-label="本次浏览器连接地址" /><Button onClick={async () => { try { await navigator.clipboard.writeText(status.connect_url); } catch { setError('复制未完成，请选中上方地址手动复制。'); } }}>复制连接地址</Button></>}
        </>}
        {status?.reason && <><Alert type="warning" showIcon title={reasons[status.reason] || '本次获取未完成，请检查浏览器或人工导入主文。'} /><Collapse size="small" items={[{key:'reason', label:'查看获取原因', children:<code>{status.reason}</code>}]} /></>}
        {paused && status?.phase !== 'downloaded' && !busy && <Checkbox checked={restored} onChange={event => setRestored(event.target.checked)}>我已确认账号恢复，解除本地暂停并仅尝试本篇（保留当日次数）</Checkbox>}
        <Space wrap>
          {status?.phase === 'downloaded' ? <Button type="primary" loading={submitting} onClick={save}>保存已校验 PDF</Button> : <Button type="primary" loading={busy} disabled={!status || !browserReady || Boolean(error) || paused && !restored || busy} onClick={start}>{busy ? phases[status?.phase] || '正在处理' : !browserReady ? '请先连接浏览器' : ['failed', 'manual_required'].includes(status?.phase) ? '重试 PDF' : '开始获取 PDF'}</Button>}
          <Button onClick={() => setOpen(false)}>{busy ? '后台继续' : '关闭'}</Button>
        </Space>
      </Space>
    </Modal>
  </>;
}

export function PaperBrowserControl({ api, post, onStatus, disabled = false }) {
  const [status, setStatus] = useState(null), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [readRevision, setReadRevision] = useState(0);
  const revision = useRef(0);
  useEffect(() => {
    const ticket = ++revision.current; let timer;
    async function poll() {
      try {
        const current = await api('/api/paper-downloads/connection');
        if (ticket !== revision.current) return;
        setStatus(current);
        setError('');
        onStatus?.(current.phase);
        if (current.phase === 'connecting' || busy) timer = setTimeout(poll, 1500);
        else if (current.phase === 'ready') timer = setTimeout(poll, 5000);
      } catch (err) { if (ticket === revision.current) setError(err.message); }
    }
    poll();
    return () => { revision.current++; clearTimeout(timer); };
  }, [busy, readRevision]);
  async function connect() {
    setBusy(true); setError('');
    try { await post('/api/paper-downloads/connect', {}); setStatus({ phase: 'connecting' }); }
    catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }
  return <Space orientation="vertical" size="small" style={{ width: '100%' }}>
    <Space wrap><Tag color={status?.phase === 'ready' ? 'green' : undefined} role="status">{ { ready:'浏览器已就绪', connecting:'等待浏览器确认', error:'连接未完成', disconnected:'浏览器未连接' }[status?.phase] || '读取连接状态' }</Tag><Button disabled={disabled} loading={busy || status?.phase === 'connecting'} onClick={connect}>{status?.phase === 'ready' ? '检查连接' : '连接日常 Chrome'}</Button></Space>
    {error && <Alert type="error" title={error} action={<Button onClick={() => setReadRevision(value => value + 1)}>刷新状态</Button>} />}
    {status?.phase === 'error' && <Alert type="warning" title={reasons[status.reason] || '连接已断开，请重新连接日常 Chrome。'} />}
    {status?.phase === 'connecting' && <span>请在 Chrome 的 Playwright 页面确认一次；本运行台后续下载会复用连接。</span>}
    {status?.connect_url && <Collapse size="small" items={[{key:'manual', label:'未弹出连接页？', children:<><p>仅在自动弹页失败时，将本次地址复制到日常 Chrome 地址栏。</p><Input.TextArea readOnly autoSize={{minRows:2,maxRows:4}} value={status.connect_url} aria-label="备用连接地址" /><Button onClick={async()=>{try {await navigator.clipboard.writeText(status.connect_url);} catch {setError('请选中地址手动复制。');}}}>复制备用地址</Button></>}]} />}
  </Space>;
}


export function PaperDownloadDebug({ items, api, post, paused, refresh }) {
  const [record, setRecord] = useState('');
  const papers = items.filter(item => item.doi);
  const paper = papers.find(item => item.record_id === record) || papers.find(item => item.doi === '10.1038/s41587-026-03348-1') || papers.find(item => item.doi.startsWith('10.1038/')) || papers[0];
  return <section className="selection-progress" aria-label="单篇 PDF 调试">
    <strong>单篇 PDF 调试</strong>
    <div className="business-toolbar" style={{ marginTop: 12 }}>
      <Select aria-label="选择调试论文" showSearch optionFilterProp="label" style={{ width: 'min(700px, 100%)' }} value={paper?.record_id} onChange={setRecord} options={papers.map(item => ({ value: item.record_id, label: item.title + ' · ' + item.doi }))} />
      {paper && <PaperDownloadControl key={paper.record_id} paper={paper} api={api} post={post} paused={paused} refresh={refresh} debug />}
    </div>
    <small>仅手动下载所选一篇，不启动摘要筛选或修改精选名单。</small>
  </section>;
}
