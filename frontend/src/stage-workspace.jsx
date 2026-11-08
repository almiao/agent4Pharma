/** 业务对象列表、就近详情与真实用例入口；跨版本权限仍由服务端校验。 */
import React, { useDeferredValue, useEffect, useRef, useState } from 'react';
import GeneratedTime from './generated-time';
import { Alert, Button, Collapse, Descriptions, Drawer, Dropdown, Empty, Input, Modal, Select, Space, Spin, Table, Tabs, Tag } from 'antd';
import { ArrowLeft, ArrowRight, FileText, Search } from 'lucide-react';
import { filterCollection, intelligenceGroup, intelligenceGroups, labels, tabCollection, workspaces } from './workspace-model';
import { nodeTabs, stageRows } from './intelligence-node-model';
import { initialLimits, LimitsFields } from './production-limits';
import { restoreCollection } from './collection-scope';
import PaperMetadata from './paper-metadata';
import SelectedPaperList from './selected-paper-list';
import { comparisonLabel, compareSelectionRows, selectionSortOptions } from './selection-paper-view.js';
import ManualPdfImport, { DownloadDirectory } from './manual-pdf-import';
import OriginalPaper from './original-paper';
import PaperProcessingFailure from './paper-processing-failure.jsx';
import ProductionTaskDetail from './production-task-detail';
import PublicationPreview from './publication-preview';
import { ReaderReview } from './production-task-document';

function TextBody({ text }) { return <div className="readable-body">{text || '尚无已保存内容'}</div>; }
function Evidence({ items = [] }) {
  return <Collapse items={items.map((item, index) => ({ key: item.id || index, label: item.title || item.id, children: <><p>{item.origin} · {item.fulltext === true ? '已声明全文' : item.status || '文本快照'}</p>{item.url && <a href={/^https?:\/\//i.test(item.url) ? item.url : undefined} target="_blank" rel="noreferrer">查看原始出处</a>}<TextBody text={item.text} /></> }))} />;
}

export default function StageWorkspace({ stageId, model, data, api, post, workLink, refresh, initialTab, nodeId, embedded = false, tabKeys, suppressNavigation = false, polling = true }) {
  const baseSpec = tabKeys ? {...workspaces[stageId],tabs:workspaces[stageId].tabs.filter(tab=>tabKeys.includes(tab.key))} : workspaces[stageId];
  const scopedTabs = suppressNavigation && nodeId === 'selection' ? ['selection'] : nodeTabs[nodeId];
  const spec = scopedTabs ? { ...baseSpec, title: '', tabs: scopedTabs.map(t => typeof t === 'string' ? { ...baseSpec.tabs.find(tab => tab.key === t), label: ({selection:'推荐清单'})[t] || baseSpec.tabs.find(tab => tab.key === t).label } : t) } : baseSpec;
  const query = new URLSearchParams(location.search);
  const [tabKey, setTabKey] = useState(spec.tabs.some(t => t.key === query.get('work')) ? query.get('work') : initialTab || spec.tabs[0].key);
  const [search, setSearch] = useState(query.get('q') || ''), [status, setStatus] = useState(query.get('state') || '');
  const [selected, setSelected] = useState(null), [detail, setDetail] = useState(null), [error, setError] = useState('');
  const [reading, setReading] = useState(false), [action, setAction] = useState(null), [saving, setSaving] = useState(false), [feedback, setFeedback] = useState('');
  const [page, setPage] = useState(Number(query.get('page')) || 1);
  const [candidateIds, setCandidateIds] = useState([]), [limits, setLimits] = useState(initialLimits);
  const [sourceFilter, setSourceFilter] = useState(query.getAll('source')), [category, setCategory] = useState(query.get('category') || '');
  const [selectionVerdict, setSelectionVerdict] = useState('');
  const [selectionSort, setSelectionSort] = useState('default');
  const [contextStoryId, setContextStoryId] = useState('');
  const generation = useRef(0), deferredSearch = useDeferredValue(search);
  const tab = spec.tabs.find(t => t.key === tabKey) || spec.tabs[0];
  const compactSelection = stageId === 'intel' && tab.key === 'selection';
  const selectionRun = data.preselection?.paper_selections?.at(-1);
  const showSelectionControls = nodeId === 'selection' && compactSelection && !!selectionRun;
  const paperListing = stageId === 'intel' && ['selection', 'abstracts'].includes(tab.key);
  const DetailContainer = paperListing ? Drawer : 'aside';
  const hasFilters = !!(search || status || sourceFilter.length || category || selectionVerdict);
  const complete = tabCollection(model, tab.metricStage || stageId, nodeId === 'selection' ? {...tab, includeComparison:true} : tab, data);
  if (nodeId) complete.rows = stageRows(complete.rows, nodeId);
  // 已交接任务不在当前队列；准确链接仍可读取当前范围内的历史执行记录。
  const linkedTask = tab.key === 'tasks' && data.production?.tasks.find(item => item.id === query.get('object') && item.role === stageId);
  if (linkedTask && !complete.rows.some(row => row.id === linkedTask.id)) {
    complete.rows.push({ id: linkedTask.id, key: 'task:' + linkedTask.id, kind: 'task', title: linkedTask.phase || linkedTask.id,
      story: linkedTask.story_id, status: linkedTask.status, displayStatus: labels[linkedTask.status] || linkedTask.status, detail: linkedTask });
  }
  const scope = restoreCollection(query, sessionStorage);
  const permitted = scope.ids;
  const collection = { ...complete, unknown: complete.unknown || scope.missing, rows: complete.rows.filter(row => !permitted || permitted.has(row.id)) };
  const collectionLoading = collection.unknown && !scope.missing && !!data.pendingReads?.length;
  const priority = stageId === 'intel' ? ['正式推荐', '处理失败', '技术失败', '运行中', '待处理', '未通过'] : ['业务阻断', '技术失败', '要求修改', '待处理', '排队', '运行中'];
  const rank = label => priority.includes(label) ? priority.indexOf(label) : priority.length;
  const selectionLabels = new Map();
  if (showSelectionControls) {
    const cards = new Map((selectionRun.comparison_result?.cards || []).map(card => [card.record_id, card]));
    for (const row of collection.rows) {
      selectionLabels.set(row.id, comparisonLabel(selectionRun, row.detail, cards.get(row.detail.record_id)));
    }
  }
  const verdictOptions = [...new Set(selectionLabels.values())];
  if (selectionVerdict && !verdictOptions.includes(selectionVerdict)) verdictOptions.push(selectionVerdict);
  const rows = filterCollection(collection.rows, { search: deferredSearch, status }).filter(row =>
    (!sourceFilter.length || sourceFilter.includes(row.source || row.detail?.source_id)) &&
    (!category || (row.category || row.detail?.category) === category) &&
    (!showSelectionControls || !selectionVerdict || selectionLabels.get(row.id) === selectionVerdict)
  ).sort((a, b) => {
    if (showSelectionControls) {
      const order = compareSelectionRows(a, b, selectionSort);
      if (order) return order;
    }
    return ['selection', 'abstracts'].includes(tab.key)
      ? (a.rank ?? Number.MAX_SAFE_INTEGER) - (b.rank ?? Number.MAX_SAFE_INTEGER)
      : rank(a.displayStatus) - rank(b.displayStatus);
  });
  const statuses = [...new Set(collection.rows.map(row => row.displayStatus))].sort((a, b) => rank(a) - rank(b));
  const selectionProgress = selectionRun?.progress;
  const continuingPaper = selectionRun?.items?.some(item=>['queued','analyzing','editorial','content','review','publishing'].includes(item.continuation_status));
  const refreshCurrent = useRef(refresh); refreshCurrent.current = refresh;
  useEffect(() => {
    if (!polling || stageId !== 'intel' || !['selection', 'abstracts'].includes(tabKey) || (!['abstracts', 'commercial_review'].includes(selectionRun?.phase) && !continuingPaper) || selectionRun?.paused) return;
    const timer = setInterval(() => { if (!document.hidden) refreshCurrent.current(); }, 15000);
    return () => clearInterval(timer);
  }, [stageId, tabKey, selectionRun?.phase, selectionRun?.paused, continuingPaper, polling]);
  const accountPaused = data.preselection?.download_account?.reason === 'blocked_automation';
  const task = detail?.task;
  const storyId = selected?.story || task?.story_id || detail?.story_id || (selected?.kind === 'story' ? selected.id : '') || query.get('story') || contextStoryId;
  const story = data.production?.stories?.find(s => s.id === storyId);
  function locate(values, replace = false) {
    const url = new URL(location.href);
    for (const [key, value] of Object.entries(values)) { url.searchParams.delete(key); if (Array.isArray(value)) value.forEach(item => url.searchParams.append(key, item)); else if (value) url.searchParams.set(key, value); }
    history[replace ? 'replaceState' : 'pushState'](null, '', url);
  }
  function switchTab(value) {
    generation.current++; setTabKey(value); setSearch(''); setStatus(''); setPage(1); setSelected(null); setDetail(null); setError(''); setReading(false); setCandidateIds([]); setSourceFilter([]); setCategory('');
    locate({ work: value, q: '', state: '', object: '', page: '', ids: '', collection: '', source: '', category: '' });
  }
  async function read(row, write = true) {
    const ticket = ++generation.current; setSelected(row); setReading(true); setError(''); setDetail(null); if (write) setFeedback('');
    if (write) locate({ object: row.id });
    try {
      const href = new URL(row.href || spec.base, location.origin), params = href.searchParams;
      let payload;
      if (tab.key === 'decisions') payload = row.detail;
      else if (row.kind === 'task') {
        payload = await api('/api/production/tasks/' + encodeURIComponent(row.id));
        if (payload.task.role !== stageId) throw new Error('任务属于另一工序，不能在当前工序执行');
      }
      else if (nodeId === 'triage' && row.kind === 'article') payload = await api('/api/discovery-run/article?' + new URLSearchParams({ batch: data.discovery?.selected || '', record: row.id }));
      else if (row.kind === 'intelligence' || stageId === 'intel' && tab.key !== 'records' && (params.get('task') || row.detail?.intelligence_task_id)) payload = await api('/api/intelligence-tasks/' + encodeURIComponent(params.get('task') || row.detail?.intelligence_task_id || row.id));
      else if (['draft', 'review', 'reader_review', 'package'].includes(row.kind)) {
        const artifacts = await api('/api/production/artifacts');
        payload = artifacts[{ draft: 'drafts', review: 'reviews', reader_review: 'reader_reviews', package: 'packages' }[row.kind]].find(a => a.id === row.id);
        if (!payload) throw new Error('未读取到这个准确版本，请刷新列表');
      } else if (stageId === 'intel' && ['article', 'run-article'].includes(row.kind)) payload = await api('/api/discovery-run/article?' + new URLSearchParams({ batch: data.discovery?.selected || '', record: row.id }));
      else if (row.kind === 'candidate') payload = data.candidates?.items.find(c => c.id === row.id) || row.detail || row;
      else if (row.kind === 'story') payload = data.production?.stories?.find(s => s.id === row.id) || row;
      else payload = row.detail || row;
      if (row.kind === 'decision') payload = { ...row.detail, kind: 'triage' };
      if (ticket === generation.current) { setDetail(payload); setContextStoryId(row.story || payload.task?.story_id || payload.story_id || (row.kind === 'story' ? row.id : '')); }
    } catch (err) { if (ticket === generation.current) setError(err.message); }
    finally { if (ticket === generation.current) setReading(false); }
  }
  useEffect(() => {
    const id = new URLSearchParams(location.search).get('object');
    const row = collection.rows.find(r => r.id === id);
    if (row) read(row, false); else { setSelected(null); setDetail(null); setReading(false); }
    return () => { generation.current++; };
  }, [stageId, tabKey, model, selectionRun?.revision]);
  useEffect(() => {
    function restore() {
      const next = new URLSearchParams(location.search);
      setTabKey(spec.tabs.some(t => t.key === next.get('work')) ? next.get('work') : initialTab || spec.tabs[0].key);
      setSearch(next.get('q') || ''); setStatus(next.get('state') || ''); setPage(Number(next.get('page')) || 1); setSourceFilter(next.getAll('source')); setCategory(next.get('category') || '');
      const row = collection.rows.find(r => r.id === next.get('object'));
      if (row) read(row, false); else { generation.current++; setSelected(null); setDetail(null); setReading(false); }
    }
    window.addEventListener('popstate', restore); return () => window.removeEventListener('popstate', restore);
  }, [collection.rows.map(r => r.id).join('|'), tabKey]);

  function begin(kind) { setAction(kind);  setError(''); }
  async function submitAction() {
    setSaving(true); setError('');
    try {
      if (action === 'run') await post('/api/production/run', { task_id: task.id });
      if (action === 'start') await post('/api/production/start', { candidate_ids: candidateIds.length ? candidateIds : [selected.id], limits });
      if (action === 'resume') await post('/api/production/resume', { story_id: storyId, role: 'reviewer', limits });
      if (action === 'export') {
        const result = await post('/api/production/export', { package_id: selected.id });
        const url = URL.createObjectURL(new Blob([result.body], { type: 'text/plain;charset=utf-8' }));
        const anchor = document.createElement('a'); anchor.href = url; anchor.download = `${selected.id}.${result.extension}`; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
      }
      setAction(null); setFeedback(action === 'run' ? '已受理本次执行，刷新后查看实际状态。' : action === 'export' ? '已取得本地包并记录导出回执。' : '已进入自动制作队列。');
      await refresh();
    } catch (err) { setError(err.message); }
    finally { setSaving(false); }
  }

  const submission = detail?.submission, result = detail?.result, card = detail?.card;
  const detailsTabs = [
    { key: 'result', label: selected?.kind === 'draft' ? '稿件正文' : ['review','reader_review'].includes(selected?.kind) ? '问题与要求' : selected?.kind === 'package' ? '包内容' : '结果与下一步', children: <>
      {result && <>{result.exclusion_reason && <Tag>{ { insufficient_evidence: '证据不足 · 已排除', out_of_scope: '主题不相关 · 已排除', low_editorial_value: '推荐价值不足 · 已排除', duplicate: '重复 · 已排除' }[result.exclusion_reason] }</Tag>}<h3>{result.summary}</h3><p>{result.reason}</p>{result.attention_reason && <Alert type="info" title="关注理由" description={result.attention_reason} />}</>}
      {detail?.kind === 'paper-selection' && <>
        <PaperMetadata paper={detail} showSource /><PaperProcessingFailure paper={detail} data={data}/>
        {detail.pdf_imported && <OriginalPaper paper={detail} />}
        {(detail.can_associate_pdf ?? detail.can_download) && <ManualPdfImport paper={detail} post={post} refresh={refresh} />}
        <Descriptions column={1} size="small" items={[{ key: 'journal', label: '期刊／载体', children: detail.journal || '未知' }, { key: 'rank', label: '最终排序', children: detail.rank || '尚未生成' }, { key: 'local-rank', label: '本地排序', children: detail.local_rank || '尚未排序' }, { key: 'doi', label: 'DOI', children: detail.doi || '未知' }]} />
        <p>{detail.reason}</p>
        {detail.fulltext_reason && <Collapse items={[{key:'fulltext-result',label:'全文判断',children:<p>{detail.fulltext_reason}</p>}]} />}
        {detail.todo && <Alert type="warning" showIcon title="人工待办" description={detail.todo} />}
        <Space wrap>{detail.url && <Button href={/^https?:\/\//i.test(detail.url) ? detail.url : undefined} target="_blank" rel="noreferrer">打开原始出处</Button>}{detail.abstract_task_id && <Button onClick={async () => { try { setDetail(await api('/api/intelligence-tasks/' + encodeURIComponent(detail.abstract_task_id))); } catch (err) { setError(err.message); } }}>查看摘要判断</Button>}</Space>

      </>}
      {card && <><h3>{card.summary}</h3><p>{card.attention_reason || card.reason}</p></>}
      {detail?.kind === 'triage' && <><h3>粗筛依据</h3><Tag>{ { analyze: '进入详情分析', exclude: '不进入', needs_evidence: '历史待补证' }[detail.route] || detail.route }</Tag><p>{detail.routing_reason || detail.result?.reason}</p><TextBody text={detail.input.abstract} /><Collapse items={[{ key: 'decision', label: '原始判别与冻结配置', children: <pre>{JSON.stringify(detail, null, 2)}</pre> }]} /></>}
      {typeof detail?.decision === 'string' && <><Tag>{labels[detail.decision] || detail.decision}</Tag><h3>编辑取舍依据</h3><p>{detail.reason}</p>{detail.revisit_condition && <><h3>重新查看条件</h3><p>{detail.revisit_condition}</p></>}</>}
      {nodeId === 'triage' && selected && <><p>{selected.displayStatus}</p>{selected.error && <Alert type="error" title={selected.error} />}<p>{data.preselection?.decisions?.find(d=>d.id===selected.detail?.triage_id)?.routing_reason || (selected.detail?.triage_id ? '本页未读取到对应判断详情' : '')}</p><details><summary>初筛记录与冻结身份</summary><pre>{JSON.stringify(data.preselection?.decisions?.find(d=>d.id===selected.detail?.triage_id) || {triage_id:selected.detail?.triage_id,method:selected.triage_method},null,2)}</pre></details></>}
      {detail?.availability && <><Tag>{ { fulltext: '已取得全文', partial: '片段', abstract_only: '仅摘要', link_only: '仅链接', restricted: '访问受限', unavailable: '未取得文本' }[detail.availability] }</Tag>{detail.read_error && <Alert type="warning" title={detail.read_error} />}{detail.url && <p><a href={/^https?:\/\//i.test(detail.url) ? detail.url : undefined} target="_blank" rel="noreferrer">打开原始出处</a></p>}{detail.abstract && <><h3>已归档摘要</h3><TextBody text={detail.abstract} /></>}{detail.body && <><h3>已归档正文</h3><TextBody text={detail.body} /></>}{detail.next_offset != null && <Button onClick={async () => {
        try { const next = await api('/api/discovery-run/article?' + new URLSearchParams({ batch: data.discovery.selected, record: selected.id, offset: detail.next_offset })); setDetail(current => current?.source_record_id === next.source_record_id ? { ...next, body: current.body + '\n' + next.body } : current); }
        catch (err) { setError(err.message); }
      }}>继续阅读归档文本</Button>}</>}
      {selected?.kind === 'reader_review' && detail && <ReaderReview review={detail}/>}
      {submission && selected?.kind !== 'reader_review' && <><p>{submission.summary}</p>{submission.body && <TextBody text={submission.body} />}{submission.issues?.map(issue => <section className="review-issue" key={issue.id}><Tag color={issue.severity === 'blocking' ? 'red' : 'gold'}>{ { blocking: '阻断', required: '必须修改', suggestion: '建议' }[issue.severity] }</Tag><h3>{issue.location}</h3><p>{issue.reason}</p><strong>解决条件</strong><p>{issue.resolution}</p></section>)}{submission.issues?.length === 0 && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本报告没有问题条目" />}</>}
      {detail?.brief && <>{detail.brief.title && <h3>{detail.brief.title}</h3>}<Descriptions column={1} size="small" items={[
        { key: 'angle', label: '报道角度', children: detail.brief.angle },
        ...(detail.brief.boundary ? [{ key: 'boundary', label: '事实边界', children: detail.brief.boundary }] : []),
      ]} /></>}
      {selected?.kind === 'package' && <PublicationPreview key={detail?.id} item={detail} post={post} refresh={refresh} />}
      {task && <><Descriptions column={1} size="small" items={[{ key: 'status', label: '任务状态', children: labels[task.status] || task.status }, { key: 'model', label: '冻结模型', children: task.model }, { key: 'attempt', label: '尝试', children: `${task.attempt} / ${task.limits.max_attempts}` }]} />{task.error && <Alert type="error" title="任务未完成" description={task.error} />}{!task.result_ref && <p className="detail-empty-result">尚未形成已提交产物。查看任务状态与阻断原因，不代表零结果。</p>}{task.result_ref && <p>产物引用：{task.result_ref}</p>}<ProductionTaskDetail key={task.id} task={task} post={post} /></>}
      {!submission && !result && !card && !detail?.brief && !task && selected && detail?.kind !== 'paper-selection' && <><p>{nodeId === 'ingest' ? selected.source : selected.subtitle}</p>{detail?.text && <TextBody text={detail.text} />}</>}
    </> },
    { key: 'evidence', label: '依据与输入', children: <>{submission?.claims?.map((claim, i) => <section className="claim-evidence" key={i}><strong>{claim.claim}</strong><blockquote>{claim.quote}</blockquote><small>{claim.evidence_id}</small>{claim.limitation && <p>{claim.limitation}</p>}</section>)}<Evidence items={detail?.evidence || [...(detail?.request?.evidence || []), ...(detail?.supplemental_evidence || [])]} />{task && <Collapse items={[{ key: 'input', label: '冻结任务输入', children: <><TextBody text={detail.context_json} /><Collapse items={[{ key: 'prompt', label: '本任务冻结提示词', children: <TextBody text={detail.prompt} /> }]} /></> }]} />}</> },
    { key: 'version', label: '版本与追溯', children: <><Descriptions column={1} size="small" items={['id', 'story_id', 'draft_id', 'review_id', 'content_hash', 'draft_hash', 'brief_version', 'number', 'created_at'].filter(key => detail?.[key] != null).map(key => ({ key, label: { id: '身份', story_id: '选题', draft_id: '稿件版本', review_id: '核验报告', content_hash: '内容指纹', draft_hash: '稿件指纹', brief_version: '任务书版本', number: '稿件版本号', created_at: '创建时间' }[key], children: String(detail[key]) }))} /><Collapse items={[{ key: 'raw', label: '完整留档数据', children: <pre>{JSON.stringify(detail, null, 2)}</pre> }]} /></> },
  ];
  return <section className={`business-workspace ${stageId === 'intel' ? 'paper-workspace' : stageId === 'publishing' ? 'publication-workspace' : ''}`}>
    {!nodeId && !embedded && <header className="business-heading"><div><h1>{spec.title}</h1>{stageId !== 'intel' && <span>{spec.focus}</span>}</div><a className="back-overview" href={(() => {
      const back = new URL(query.get('return') || '/console', location.origin);
      if (back.origin === location.origin && ['/', '/console'].includes(back.pathname)) return back.pathname + back.search;
      return '/console';
    })()}><ArrowLeft size={14} />返回运行</a></header>}
    {!suppressNavigation && (!nodeId || spec.tabs.length > 1) && <div className="workspace-navigation">
      <Tabs className="object-tabs" activeKey={tab.key} onChange={switchTab} items={(stageId === 'intel' && !nodeId ? intelligenceGroup(tab.key).tabs.map(key => spec.tabs.find(item => item.key === key)) : spec.tabs).map(item => { const c = tabCollection(model, item.metricStage || stageId, item, data); if (nodeId) c.rows = stageRows(c.rows, nodeId); return { key: item.key, label: <span>{item.label}<span className="object-count">{c.unknown ? '—' : c.rows.length}</span></span> }; })} />
      {stageId === 'intel' && !nodeId && <Dropdown trigger={['click']} menu={{items: intelligenceGroups.filter(group => group.key !== intelligenceGroup(tab.key).key).map(group => ({key: group.key, type:'group', label:group.label, children: group.tabs.map(key => ({key, label: spec.tabs.find(item => item.key === key).label}))})), onClick:({key})=>switchTab(key)}}><Button type="text">处理记录</Button></Dropdown>}
    </div>}
    {!selected && story && ['content', 'editorial'].includes(stageId) && <div className="story-context"><strong>{story.brief.title || story.paperTitles?.join(' / ') || '立项任务书'}</strong><span>修订 {story.revision} · 任务书 v{story.brief_version}</span></div>}

    {compactSelection && <div className="shortlist-toolbar"><span>{collection.unknown ? collectionLoading ? '正在读取精选论文' : '精选论文读取失败' : nodeId === 'selection' ? `${collection.rows.length} 篇论文 · 建议下载 ${selectionRun?.phase === 'fulltexts' ? collection.rows.filter(row=>row.detail?.can_download).length : '未形成'} · 人工已推荐 ${collection.rows.filter(row=>row.detail?.human_recommendation).length}` : `${collection.rows.length} 篇建议下载`}</span><Space><DownloadDirectory api={api} />{nodeId !== 'selection' && <Collapse ghost size="small" items={[{key:'summary', label:'筛选记录', children:<div className="shortlist-selection-summary">摘要完成 {selectionProgress?.completed ?? '—'} / {selectionProgress?.total ?? '—'} 篇 · 商业复核 {selectionRun?.review_progress?.completed ?? '—'} / {selectionRun?.review_progress?.total ?? '—'} 篇<Button type="link" onClick={()=>nodeId ? location.assign('/console?' + new URLSearchParams({view:'workspace',stage:'selection',batch:data.discovery?.selected || ''})) : switchTab('abstracts')}>查看完整记录</Button></div>}]} />}<Button type="primary" href="https://www.chaoslib.com/" target="_blank" rel="noreferrer">打开 Chaoslib</Button></Space></div>}

    {!(nodeId !== 'selection' && stageId === 'intel' && tab.key === 'selection' && selectionRun && !collection.unknown && !collection.rows.length) && <>
    {!compactSelection && !collection.unknown && <div className="business-statuses" aria-label={tab.statusLabel + '快捷筛选'}><Button type={!status ? 'primary' : 'default'} onClick={() => { setStatus(''); setPage(1); locate({ state: '', page: '' }); }}>全部 <b>{collection.rows.length}</b></Button>{statuses.map(value => <Button key={value} type={status === value ? 'primary' : 'default'} onClick={() => { setStatus(value); setPage(1); locate({ state: value, page: '' }); }}>{value} <b>{collection.rows.filter(row => row.displayStatus === value).length}</b></Button>)}</div>}
    {(!compactSelection || nodeId === 'selection' || hasFilters) && <div className="business-toolbar">
      <Input aria-label="搜索当前列表" prefix={<Search size={16} />} placeholder={paperListing ? '搜索标题、期刊或 DOI' : `搜索${tab.object}标题或编号`} value={search} allowClear onChange={e => { setSearch(e.target.value); setPage(1); locate({ q: e.target.value, page: '' }, true); }} />
      {!paperListing && <Select aria-label={tab.statusLabel} placeholder={tab.statusLabel} value={status || undefined} allowClear options={statuses.map(value => ({ label: value, value }))} onChange={value => { setStatus(value || ''); setPage(1); locate({ state: value, page: '' }); }} />}
      {showSelectionControls && !collection.unknown && <>
        <Select aria-label="按比较结论筛选" placeholder="全部比较结论" allowClear value={selectionVerdict || undefined}
          options={verdictOptions.map(value => ({ label: value, value }))} onChange={value => setSelectionVerdict(value || '')} />
        <Select aria-label="论文排序" value={selectionSort} options={selectionSortOptions} onChange={setSelectionSort} />
      </>}
      {(!paperListing || hasFilters || selectionSort !== 'default') && <Button onClick={() => {
        setSearch(''); setStatus(''); setSourceFilter([]); setCategory(''); setPage(1);
        setSelectionVerdict(''); setSelectionSort('default');
        locate({ q: '', state: '', source: '', category: '', page: '' });
      }}>重置</Button>}
      {stageId === 'editorial' && tab.key === 'candidates' && <Button type="primary" disabled={!candidateIds.length || candidateIds.length > 20} onClick={() => begin('start')}>排队主编{candidateIds.length ? `（${candidateIds.length}）` : ''}</Button>}
      <span className="result-count" role={showSelectionControls ? 'status' : undefined}>{collection.unknown ? '读取未知' : `${rows.length} / ${collection.rows.length} ${tab.object}`}</span>
    </div>}
    {stageId === 'intel' && !compactSelection && ['abstracts', 'results', 'records', 'triage', 'downloads'].includes(tab.key) && <details className="paper-source-filter" open={!!(sourceFilter.length || category)}><summary>按来源筛选{sourceFilter.length ? `（已选 ${sourceFilter.length}）` : ''}</summary><div className="business-toolbar">
      <Select mode="multiple" aria-label="来源筛选" placeholder="来源（可多选）" value={sourceFilter} allowClear options={(data.discovery?.sources || []).map(source => ({ value: source.source_id, label: source.name || source.source_id }))} onChange={value => { setSourceFilter(value); setPage(1); locate({ source: value, page: '' }); }} />
      {tab.key === 'records' && <Select aria-label="资料类型" placeholder="资料类型" allowClear value={category || undefined} options={[...new Set(collection.rows.map(row => row.category).filter(Boolean))].map(value => ({ value, label: { journal: '期刊', preprint: '预印本', media: '媒体', organization: '机构', unknown: '未分类' }[value] || value }))} onChange={value => { setCategory(value || ''); setPage(1); locate({ category: value, page: '' }); }} />}
    </div></details>}
    {stageId === 'intel' && tab.key === 'downloads' && accountPaused && <Alert type="warning" showIcon title="Chaoslib 账号已暂停" description="此前自动化访问受阻，本地暂停仍保留。日常浏览器可登录也不会自动解除暂停；人工确认恢复后才可继续下载。" />}
    {collection.unknown ? collectionLoading ? <div className="collection-loading" role="status"><Spin size="small" /><span>正在读取论文清单…</span></div> : <Alert showIcon type="error" title={scope.missing ? "原集合的会话引用已失效" : "当前对象集合未成功读取"} description={scope.missing ? "请返回运行总览，重新展开该集合。此处不会自动扩大到全库。" : "此处无法确认对象数量，请重试；不能按空集合处理。"} action={<Button onClick={refresh}>重试</Button>} /> : <div className={`business-split ${paperListing ? 'shortlist-layout' : selected ? 'has-selection' : ''}`}><div className="business-list">{compactSelection ? <SelectedPaperList run={nodeId === 'selection' ? selectionRun : undefined} rows={rows} filtered={hasFilters} onRead={read} api={api} post={post} refresh={refresh} data={data} /> : <Table size="middle" rowKey="key" dataSource={rows} rowSelection={stageId === 'editorial' && tab.key === 'candidates' ? { selectedRowKeys: candidateIds.map(id => 'candidate:' + id), onChange: (_, items) => setCandidateIds(items.map(row => row.id)), getCheckboxProps: row => ({ disabled: row.group !== '待处理' }) } : undefined} pagination={{ current: page, pageSize: 15, showSizeChanger: false, onChange: value => { setPage(value); locate({ page: value }); } }} rowClassName={row => row.id === selected?.id ? 'selected-row' : ''} locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={<><strong>{search || status ? '没有匹配对象' : tab.key === 'selection' && selectionRun?.phase === 'fulltexts' ? '本轮没有入选论文' : tab.empty}</strong><p>{search || status ? '调整筛选，或点击重置查看当前集合。' : tab.next}</p></>} /> }} columns={[
      { title: tab.object, key: 'title', render: (_, row) => row.kind === 'paper-selection' ? <PaperMetadata paper={row.detail} onOpen={() => read(row)} /> : <button className="object-title" onClick={() => read(row)}><strong>{row.title}</strong><span>{row.kind === 'paper-selection' ? row.detail?.journal || '期刊信息待补充' : row.id}</span></button> },
      { title: tab.statusLabel, key: 'status', width: 115, render: (_, row) => <Tag color={/失败|阻断|失效/.test(row.displayStatus) ? 'red' : /修改|待/.test(row.displayStatus) ? 'gold' : 'default'}>{row.displayStatus}</Tag> },
      ...(['drafts', 'history'].includes(tab.key) && stageId === 'content' ? [{ title: '生成时间（北京时间）', key: 'generated', width: 190, render: (_, row) => <GeneratedTime value={row.detail?.created_at || row.created_at}/> }] : []),

      { title: '查看', key: 'open', width: 65, render: (_, row) => <Button type="link" size="small" onClick={() => read(row)}>详情</Button> },
    ]} />}</div><DetailContainer {...(paperListing ? {open:!!selected, title:'论文详情', size:680, onClose:()=>{generation.current++;setSelected(null);setDetail(null);locate({object:''});}} : {className:'business-detail','aria-label':'对象详情'})}>{!selected ? <div className="selection-empty"><FileText size={30} /><h3>选择一个{tab.object}</h3><p>{stageId === 'review' ? '先核对稿件版本，再查看问题与解决要求。' : stageId === 'publishing' ? '先确认包是否有效，再预览内容与导出。' : stageId === 'editorial' ? '查看关注理由、编辑决定与任务书。' : stageId === 'content' ? '查看任务进展、稿件与证据。' : nodeId === 'ingest' ? '阅读归档原文、摘要与出处。' : '查看筛选原因、原始资料与证据。'}</p></div> : <>
      {!paperListing && <div className="detail-heading"><div><Tag>{selected.displayStatus}</Tag><h2>{selected.title}</h2></div><Button type="text" onClick={() => { generation.current++; setSelected(null); setDetail(null); setReading(false); locate({ object: '' }); }}>收起</Button></div>}
      {reading ? <Spin /> : <>{error && <Alert type="error" showIcon title="读取或操作未完成" description={error} />}{feedback && <Alert type="success" title={feedback} />}{detail && <Tabs items={detailsTabs} />}
      <div className="object-actions"><Space wrap>
        {stageId === 'editorial' && selected.kind === 'candidate' && selected.group === '待处理' && <Button type="primary" onClick={() => begin('start')}>排队主编</Button>}
        {task && ['queued', 'failed'].includes(task.status) && <Button type="primary" disabled={!data.production?.model_available} onClick={() => begin('run')}>执行当前任务</Button>}
        {stageId === 'review' && story && tab.key !== 'history' && <Button onClick={() => begin('resume')}>发起独立复议</Button>}
        {stageId === 'publishing' && tab.key === 'packages' && <Button type="primary" onClick={() => begin('export')}>导出当前包</Button>}
      </Space>{task && ['queued', 'failed'].includes(task.status) && !data.production?.model_available && <p>模型凭证不可用，当前不能执行；可继续查看任务和已有产物。</p>}</div></>}
    </>}</DetailContainer></div>}
    {!compactSelection && !nodeId && <details className="workspace-scope"><summary>列表范围与口径</summary><p>{query.get('scope') === 'all' ? '当前已读取的制作记录；当前任务、当前产物与历史版本分开呈现。' : '受顶部批次或选题范围约束；缺少关联不表示从未运行。'}</p><p>筛选作用于已读取的完整对象集合，再分页。候选未绑定批次时最多读取最近 200 项；评估无批次时最多读取最近 100 次。</p></details>}
    </>}
    <Modal title={{ start: '排队候选的主编任务', run: '授权执行当前任务', resume: '发起独立编辑复议', export: '导出准确版本的本地包' }[action]} open={!!action} onCancel={() => !saving && setAction(null)} footer={<Button type="primary" loading={saving} onClick={submitAction}>{{ start: '确认排队', run: '开始制作', resume: '确认排队复议', export: '校验并导出' }[action]}</Button>} mask={false}>
      <p>{action === 'start' && candidateIds.length ? `已选 ${candidateIds.length} 个正式候选` : selected?.title}</p><p>{action === 'run' ? '从当前阶段自动推进制作链，沿用冻结输入与任务预算，会调用模型。' : action === 'export' ? '后端将重新检查任务书、稿件、审核与渠道包的版本有效性。本操作生成本地下载及回执，不对外发布。' : '此操作会自动调用模型推进制作，会产生费用。'}</p>
      {['start', 'resume'].includes(action) && <LimitsFields value={limits} onChange={setLimits} />}{error && <Alert type="error" title={error} />}
    </Modal>
  </section>;
}
