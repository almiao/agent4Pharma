/** 定稿图文隔离预览，导出仍交后端校验版本并记录回执。 */
import ArticleEditor from './article-editor';
import WechatDelivery from './wechat-delivery';
import React, { useMemo, useState } from 'react';
import { Alert, Button, Collapse, Dropdown, Modal, Segmented, Space, Tag } from 'antd';

import {previewDocument} from './preview-document';

/** 准确历史渠道包的只读预览，不提供编辑、排队、批准或导出命令。 */
export function ReadOnlyPackagePreview({ item }) {
  const html = useMemo(() => previewDocument(item.body, 'web'), [item.body]);
  return <iframe className="document-package-preview" title="此任务提交的渠道包" sandbox="" referrerPolicy="no-referrer" srcDoc={html}/>;
}

export default function PublicationPreview({ item, post, refresh, reviewMode, onVersionChange, readEditState }) {
  const [variant, setVariant] = useState('web'), [width, setWidth] = useState('mobile');
  const [expanded, setExpanded] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [queued, setQueued] = useState(''), [previewOpen, setPreviewOpen] = useState(false);
  const presentation = item?.presentation;
  const html = useMemo(() => previewDocument(variant === 'wechat' ? presentation?.wechat_html || '' : item?.body || '', variant), [item, variant, presentation]);
  async function download() {
    setError(''); setBusy(true);
    try {
      const result = await post('/api/production/export', { package_id: item.id, variant });
      const url = URL.createObjectURL(new Blob([result.body], { type: result.extension === 'md' ? 'text/markdown;charset=utf-8' : 'text/html;charset=utf-8' }));
      const anchor = document.createElement('a'); anchor.href = url; anchor.download = item.id + '-' + variant + '.' + result.extension; anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      await refresh();
    } catch (err) { setError(err.message); } finally { setBusy(false); }
  }
  async function queueDsh() {
    setError(''); setBusy(true);
    try { const task = await post('/api/production/queue-layout', { package_id: item.id }); setQueued(task.id); await refresh(); }
    catch (err) { setError(err.message); } finally { setBusy(false); }
  }
  async function create() {
    setError(''); setBusy(true);
    try { await post('/api/production/layout', { package_id: item.id, figures: [] }); await refresh(); }
    catch (err) { setError(err.message); } finally { setBusy(false); }
  }
  const frame = <div className="publication-preview-surface"><iframe className={'publication-frame ' + width} title="文章排版预览" sandbox="" referrerPolicy="no-referrer" srcDoc={html} /></div>;
  const reviewOptions = [{key:'figures', label:'排队 DSH 选图'}];
  if (item?.channel !== 'local_html') reviewOptions.push({key:'reading', label:'生成阅读版'});
  return <div className="publication-preview">
    {reviewMode === true && <ArticleEditor item={item} post={post} refresh={refresh} inline onVersionChange={onVersionChange} readEditState={readEditState}/>}
    {reviewMode !== false && <Space wrap className="publication-controls">
      {reviewMode === undefined && <ArticleEditor item={item} post={post} refresh={refresh}/>}
      {reviewMode === true ? <Dropdown trigger={['click']} menu={{items:reviewOptions, onClick:({key}) => key === 'figures' ? queueDsh() : create()}}>
        <Button loading={busy}>更多操作</Button>
      </Dropdown> : <Button onClick={queueDsh} loading={busy}>排队 DSH 选图</Button>}
      {queued && <a href={'/console?view=workspace&stage=production&task='+encodeURIComponent(queued)}>查看选图任务并执行</a>}
    </Space>}
    {error && <Alert type="error" title={error} description={<a href={'/console?view=workspace&stage=human&story='+encodeURIComponent(item.story_id)}>进入人工审稿确认此版本</a>}/> }
    {reviewMode && <details onToggle={event => setPreviewOpen(event.currentTarget.open)}><summary>渠道排版预览</summary>
      {previewOpen && renderPreview()}
    </details>}
    {!reviewMode && renderPreview()}
  </div>;

  function renderPreview() {
    return item?.channel !== 'local_html' ? <>{reviewMode === undefined && <Button onClick={create} loading={busy}>生成阅读版</Button>}{!reviewMode && <Button onClick={download} loading={busy}>下载当前 Markdown</Button>}<p>Markdown 渠道包 · 以下按标题和段落阅读；导出保留准确原文。</p><article className="editorial-article">{item?.body.split('\n').filter(Boolean).map((line,i)=>line.startsWith('# ')?<h1 key={i}>{line.slice(2)}</h1>:line.startsWith('## ')?<h2 key={i}>{line.slice(3)}</h2>:<p key={i}>{line}</p>)}</article></> : <>
      <Space wrap className="publication-controls">
        <Segmented aria-label="渠道预览" value={variant} onChange={setVariant} options={[{ label: '网页阅读版', value: 'web' }, { label: '公众号正文', value: 'wechat', disabled: !presentation }]} />
        <Segmented aria-label="预览宽度" value={width} onChange={setWidth} options={[{label:'手机',value:'mobile'}, {label:'桌面',value:'desktop'}]} />
        {!reviewMode && presentation && <WechatDelivery key={item.id} item={item} post={post}/>}
        <Button type="text" onClick={() => setExpanded(true)}>放大预览</Button>{!reviewMode && <Button onClick={download} loading={busy}>下载当前 HTML</Button>}
      </Space>
      {frame}
      {presentation && <><Space wrap><Tag>{presentation.template_version}</Tag><span>{presentation.figures.length} 张原图 · 制作 {presentation.elapsed_seconds.toFixed(2)} 秒{presentation.selection_task_id ? <> · <a href={'/console?view=workspace&stage=production&task='+encodeURIComponent(presentation.selection_task_id)}>DSH 选图耗时与用量</a></> : <> · 模型调用 {presentation.model_calls} · {presentation.total_tokens ?? '未记录'} token</>}</span></Space>
        <Collapse items={[{key:'sources',label:'配图来源与检查记录',children:<><ul>{presentation.checks.map(x=><li key={x}>{x}</li>)}</ul>{presentation.figures.map(f=><p key={f.locator}><strong>{f.selection.label}</strong><br/>{f.locator} · {f.width} × {f.height}<br/>{f.selection.visual_observation}</p>)}{presentation.warnings.map(x=><p key={x}>{x}</p>)}</>}]} />
      </>}
      <Modal title="文章排版预览" open={expanded} onCancel={()=>setExpanded(false)} footer={null} width={960} destroyOnHidden>{expanded && frame}</Modal>
    </>;
  }
}
