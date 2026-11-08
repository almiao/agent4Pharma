/** 人工文本编辑：以准确图文为基线，显式保存，不启动模型。 */
import React, {useEffect, useId, useState} from 'react';
import {Alert, Button, Input, InputNumber, Modal, Popconfirm, Space} from 'antd';
import {articleLines} from './article-product-model';

export default function ManualArticleEditor({state, post, onSaved, onClose}) {
  const prefix = useId();
  const [source] = useState(state);
  const [baseline] = useState(() => ({
    package_id:state.package.id, draft_id:state.draft.id,
    title:state.draft.submission.title, body:state.draft.submission.body,
    claim_texts:state.draft.submission.claims.map(c => c.claim),
    figures:(state.package.presentation?.figures || []).map(({selection}) => ({
      caption:selection.caption, after_paragraph:selection.after_paragraph,
    })),
  }));
  const [value, setValue] = useState(baseline), [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const dirty = JSON.stringify(value) !== JSON.stringify(baseline);
  const paragraphs = articleLines(value.body);
  const invalidClaims = value.claim_texts.some(text => text.trim() && !value.body.includes(text));
  const update = (key, next) => setValue(old => ({...old, [key]:next}));
  const updateFigure = (index, key, next) => update('figures', value.figures.map((f,i) => i === index ? {...f,[key]:next} : f));
  useEffect(() => {
    if (!dirty) return;
    const warn = event => {event.preventDefault(); event.returnValue = '';};
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);
  async function save() {
    setBusy(true); setError('');
    try {
      const product = await post('/api/production/edit-manual', value);
      await onSaved(product);
    } catch (e) {setError(e.message);}
    finally {setBusy(false);}
  }
  const valid = value.title.trim() && value.body.trim() && Array.from(value.body).length >= 80 &&
    !invalidClaims && value.claim_texts.some(text => text.trim()) &&
    value.figures.every(f => Number.isInteger(f.after_paragraph) && f.after_paragraph >= 1 && f.after_paragraph <= paragraphs.length);
  return <Modal open title="手动编辑图文" width={1000} closable={false} keyboard={false} mask={{closable:false}}
    footer={<Space wrap>
      {dirty ? <Popconfirm title="放弃尚未保存的修改？" okText="放弃修改" cancelText="继续编辑" onConfirm={onClose} disabled={busy}>
        <Button disabled={busy}>取消</Button>
      </Popconfirm> : <Button onClick={onClose} disabled={busy}>取消</Button>}
      <Button type="primary" loading={busy} disabled={!dirty || !valid} onClick={save}>保存为新版本</Button>
    </Space>}>
    <div className="manual-article-form">
      <p>直接修改标题、正文和图注。保存不调用模型，完成后可直接人工确认定稿。</p>
      {error && <Alert type="error" showIcon title={error} />}
      <label htmlFor={`${prefix}-title`}>标题</label>
      <Input id={`${prefix}-title`} value={value.title} maxLength={300} disabled={busy} onChange={e => update('title',e.target.value)} />
      <label htmlFor={`${prefix}-body`}>正文</label>
      <Input.TextArea id={`${prefix}-body`} value={value.body} rows={16} maxLength={60000} disabled={busy}
        aria-describedby={`${prefix}-body-hint`} onChange={e => update('body',e.target.value)} />
      <small id={`${prefix}-body-hint`}>保留原有段落格式，小标题以 ## 开头。正文至少 80 字符。当前 {paragraphs.length} 段（含小标题）；增删段落后请检查下方图片位置。</small>
      <details open={invalidClaims || undefined}>
        <summary>关键表述与证据关联{invalidClaims ? ' · 请确认改后的正文原句' : ''}</summary>
        <p>改动关键表述后，填入正文中的新原句并确认下方原始证据仍适用；清空可移除关联，至少保留一处。最终内容由你审阅确认。</p>
        {value.claim_texts.map((text,i) => <div className="manual-claim" key={i}>
          <label htmlFor={`${prefix}-claim-${i}`}>关联 {i+1} · 正文原句</label>
          <Input.TextArea id={`${prefix}-claim-${i}`} value={text} rows={2} maxLength={2000} disabled={busy}
            status={text.trim() && !value.body.includes(text) ? 'error' : undefined}
            onChange={e => update('claim_texts',value.claim_texts.map((old,j) => j === i ? e.target.value : old))}/>
          {text.trim() && !value.body.includes(text) && <p role="alert">这句话已不在正文中，请填入新原句，或清空移除关联。</p>}
          <blockquote>{source.draft.submission.claims[i].quote}</blockquote>
        </div>)}
      </details>
      {value.figures.length > 0 && <details><summary>查看段落编号</summary><ol>{paragraphs.map((line,i) => <li key={i}>{line.text}</li>)}</ol></details>}
      {value.figures.map((figure,i) => <fieldset key={i}>
        <legend>图片 {i+1} · {source.package.presentation.figures[i].selection.label}</legend>
        <label htmlFor={`${prefix}-caption-${i}`}>图注</label>
        <Input.TextArea id={`${prefix}-caption-${i}`} value={figure.caption} rows={3} maxLength={500} disabled={busy}
          onChange={e => updateFigure(i,'caption',e.target.value)} />
        <label htmlFor={`${prefix}-position-${i}`}>放在第几段后</label>
        <InputNumber id={`${prefix}-position-${i}`} min={1} max={Math.max(1,paragraphs.length)} precision={0}
          value={figure.after_paragraph} disabled={busy} onChange={next => updateFigure(i,'after_paragraph',next)} />
      </fieldset>)}
      <span role="status">{busy ? '正在保存…' : dirty ? '有尚未保存的修改' : '尚未修改'}</span>
    </div>
  </Modal>;
}
