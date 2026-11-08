/** 人工图文审稿：准确版本、直接引用、定点修改、可撤销版本及真实执行过程。 */
import React, {useEffect, useMemo, useRef, useState} from 'react';
import {Alert, Button, Drawer, Input, Select, Space, Tag} from 'antd';
import {previewDocument} from './preview-document';
import {addReference, articleLines, selectionReference, targetText} from './article-product-model';
import ArticleEditProcess from './article-edit-process';
import ManualArticleEditor from './manual-article-editor';
import './article-editor.css';

function productDocument(state) {
  if (!state?.package) return '';
  const doc = new DOMParser().parseFromString(previewDocument(state.package.body, 'web'), 'text/html');
  const bind = (node, target, start = 0) => {
    if (!node) return;
    node.dataset.editTarget = target; node.dataset.editStart = String(start);
  };
  const title = doc.querySelector('h1');
  if (title?.textContent === state.draft.submission.title) bind(title, 'title');
  const nodes = [...doc.querySelectorAll('.prose > p, .prose > h2')];
  const lines = articleLines(state.draft.submission.body);
  nodes.forEach((node, i) => { if (lines[i]?.text === node.textContent) bind(node, 'body', lines[i].start); });
  doc.querySelectorAll('figure').forEach((figure, i) => {
    const record = state.package.presentation?.figures[i];
    if (!record) return;
    const caption = figure.querySelector('figcaption');
    if (caption) for (const child of [...caption.childNodes]) {
      if (child.nodeType === 3 && child.textContent === record.selection.caption) {
        const span = doc.createElement('span'); span.textContent = child.textContent;
        bind(span, 'caption:' + i); child.replaceWith(span);
      }
    }
    const img = figure.querySelector('img');
    if (img) { img.dataset.editFigure = String(i); img.tabIndex = 0; img.setAttribute('role','button'); img.setAttribute('aria-label','引用图片：' + record.selection.label); }
  });
  const style = doc.createElement('style');
  style.setAttribute('nonce', document.querySelector('meta[name="style-nonce"]')?.content || '');
  style.textContent = '::highlight(editor-reference){background:#ffe7a3;color:inherit}img[role=button]{cursor:pointer}img[role=button]:focus{outline:3px solid #244b70}article{margin:0 auto}';
  doc.head.appendChild(style);
  return '<!doctype html>' + doc.documentElement.outerHTML;
}

export default function ArticleEditor({item, post, refresh, inline = false, onVersionChange, readEditState = post}) {
  const [open,setOpen] = useState(false), [state,setState] = useState(null);
  const [drawerPackageId,setDrawerPackageId] = useState(item.id), [instruction,setInstruction] = useState('');
  const packageId = inline ? item.id : drawerPackageId;
  const setPackageId = inline ? onVersionChange : setDrawerPackageId;
  const [references,setReferences] = useState([]), [figures,setFigures] = useState([]);
  const [busy,setBusy] = useState(false), [error,setError] = useState(''), [notice,setNotice] = useState('');
  const [manualOpen,setManualOpen] = useState(false);
  const frame = useRef(null), selectionRanges = useRef([]), polling = useRef(0), documentId = useRef(null), requestIdentity = useRef(null);
  const html = useMemo(() => productDocument(state), [state?.package?.id, state?.package?.body]);
  const generating = [...(state?.turns || []),...(state?.executions || [])].some(t => ['queued','running'].includes(t.status));
  const locked = busy || generating || !state?.editable || state?.package?.id !== packageId;
  useEffect(() => { setDrawerPackageId(item.id); }, [item.id]);
  useEffect(() => {
    if (!inline && !open) return;
    const generation = ++polling.current;
    let active = true, timer, delay = inline ? 8000 : 2000;
    async function load() {
      try {
        const data = await readEditState('/api/production/edit-state', {package_id:packageId, product:true, include_document:documentId.current !== packageId});
        if (!active || generation !== polling.current) return;
        setState(previous => ({...previous,...data}));
        documentId.current = packageId;
        if ([...data.turns, ...(data.executions || [])].some(t => ['queued','running'].includes(t.status))) delay = 2000;
        else delay = inline ? 8000 : 2000;
        const descendant = data.packages.find(p => p.edit_parent_id === packageId);
        const ownTurn = data.turns.find(t => t.request.package_id === packageId && t.status === 'succeeded' && t.request.auto_apply);
        const ownEdit = ownTurn && requestIdentity.current?.packageId === packageId;
        if (descendant && ownEdit) {
          await setPackageId(descendant.id);
          if (active) setNotice('修改已保存，可直接人工确认定稿');
          requestIdentity.current = null;
        }
      } catch (e) { if (active) setError(e.message); }
      if (active) timer = setTimeout(load, delay);
    }
    load();
    return () => { active = false; clearTimeout(timer); };
  }, [inline, open, packageId, setPackageId, readEditState]);
  useEffect(() => { setReferences([]); setFigures([]); selectionRanges.current = []; if (inline) setInstruction(''); }, [inline, packageId]);
  async function action(fn) { setBusy(true); setError(''); try { await fn(); } catch(e) { setError(e.message); } finally { setBusy(false); } }
  function selected() {
    if (locked) return;
    const win = frame.current?.contentWindow, selection = win?.getSelection();
    const reference = selectionReference(selection);
    if (!reference) return;
    const text = Array.from(targetText(state, reference.target)).slice(reference.start, reference.end).join('');
    if (!text.trim()) return;
    try {
      const next = addReference(references, {...reference, text}); setReferences(next);
      if (next !== references) {
        selectionRanges.current.push(selection.getRangeAt(0).cloneRange());
      }
      if (win.Highlight && win.CSS?.highlights) {
        win.CSS.highlights.set('editor-reference', new win.Highlight(...selectionRanges.current));
        selection.removeAllRanges();
      }
    } catch(e) { setError(e.message); }
  }
  function bindFrame() {
    const doc = frame.current?.contentDocument;
    if (!doc) return;
    let press;
    doc.onmousedown = event => {
      press = event.button === 0 ? {x:event.clientX, y:event.clientY} : null;
    };
    doc.onmouseup = event => {
      const start = press;
      press = null;
      if (!start || event.button !== 0 || locked || event.target?.dataset?.editFigure !== undefined) return;
      if (!doc.getSelection()?.isCollapsed) {
        selected();
        return;
      }
      if (Math.hypot(event.clientX - start.x, event.clientY - start.y) > 4) return;
      const index = selectionRanges.current.findIndex(range => Array.from(range.getClientRects()).some(rect =>
        event.clientX >= rect.left && event.clientX < rect.right &&
        event.clientY >= rect.top && event.clientY < rect.bottom));
      if (index !== -1) removeReference(index);
    };
    doc.onkeyup = event => { if (event.key !== 'Enter' && event.key !== ' ') selected(); };
    const pick = event => {
      const index = event.target?.dataset?.editFigure;
      if (index !== undefined && !locked) setFigures(old => [...new Set([...old, Number(index)])]);
    };
    doc.onclick = pick;
    doc.onkeydown = event => { if (['Enter',' '].includes(event.key) && event.target?.dataset?.editFigure !== undefined) { event.preventDefault(); pick(event); } };
  }
  useEffect(bindFrame, [references, locked, state?.package?.id]);
  function removeReference(index) {
    setReferences(references.filter((_,i) => i !== index)); selectionRanges.current.splice(index,1);
    const win = frame.current?.contentWindow;
    if (win?.Highlight && win.CSS?.highlights) win.CSS.highlights.set('editor-reference',new win.Highlight(...selectionRanges.current));
  }
  async function send() {
    await action(async () => {
      const payload = {package_id:packageId,draft_id:state.draft.id,instruction,references,figure_indices:figures,auto_apply:true};
      const key = JSON.stringify(payload);
      if (requestIdentity.current?.key !== key) requestIdentity.current = {key,id:crypto.randomUUID(),packageId};
      await post('/api/production/edit-generate', {...payload,request_id:requestIdentity.current.id});
      setInstruction(''); setReferences([]); setFigures([]); selectionRanges.current = [];
      frame.current?.contentWindow?.CSS?.highlights?.delete('editor-reference');
      setState(await post('/api/production/edit-state',{package_id:packageId,product:true}));
    });
  }
  async function restore(target) {
    await action(async () => {
      const product = await post('/api/production/edit-restore',{package_id:packageId,restore_package_id:target});
      await setPackageId(product.id); setNotice('已恢复为新版本，历史记录保留');
    });
  }
  const templates = figures.length ? ['修改图注，使其更容易理解','核对图片与正文是否对应','调整这张图的位置'] : ['表达更自然，保留事实和限定','压缩这段，保留关键信息','用读者能理解的方式解释术语','核对这句话，必要时查阅论文或公开资料','补充必要背景并保留出处'];
  const latest = state?.packages.at(-1);
  const editor = <>
      {error && <Alert type="error" title={error} showIcon closable onClose={() => setError('')}/>}
      {state?.package?.id !== packageId ? <p role="status">正在读取准确图文版本…</p> : <>
        <div className="article-review-toolbar"><Space wrap>
          <Tag>稿件 v{state.draft.number}</Tag><Tag color={state.pending_review?'blue':'green'}>{state.pending_review?'人工修改版':'图文成品'}</Tag>
          {!inline && <Select aria-label="图文版本" disabled={manualOpen} value={packageId} onChange={id => {setPackageId(id);requestIdentity.current=null;setNotice('');}} options={state.packages.map((p,i) => ({value:p.id,label:`图文版本 ${i+1}${p.edit_pending?' · 人工修改':''}`}))}/>}
          {!state.editable && latest && latest.id !== packageId && <Button onClick={() => action(() => setPackageId(latest.id))}>切换到最新图文</Button>}
          <Button disabled={locked || !state.package.edit_parent_id} onClick={() => restore(state.package.edit_parent_id)}>撤销上次修改</Button>
          <Button disabled={locked} onClick={() => setManualOpen(true)}>手动编辑</Button>
        </Space></div>
        {manualOpen && <ManualArticleEditor state={state} post={post} onClose={() => setManualOpen(false)} onSaved={async product => {
          setState(await post('/api/production/edit-state',{package_id:product.id,product:true}));
          await setPackageId(product.id);
          setManualOpen(false);
          setNotice('手动修改已保存，可直接人工确认定稿；未调用模型');
          await refresh();
        }}/>}
        <div className="article-review-layout">
          <section className="article-review-paper" aria-label="当前图文成品"><iframe ref={frame} title="可引用的图文成品" sandbox="allow-same-origin" referrerPolicy="no-referrer" srcDoc={html} onLoad={bindFrame}/></section>
          <aside className="article-review-sidebar" aria-label="人工修改意见">
            {notice && <p role="status">{notice}</p>}
            <div className="article-review-turns">{state.executions?.map(t => <div key={t.task_id}><ArticleEditProcess turn={t}/>{t.error && <p role="alert">{t.error}</p>}</div>)}{state.turns.map(turn => <article key={turn.task_id} className="article-review-turn">
              <strong>修改意见</strong><p>{turn.request.instruction || '改善所引内容的表达'}</p>
              {turn.request.references.map((r,i) => <details key={i}><summary>引用 {i+1}</summary><blockquote>{r.text}</blockquote></details>)}
              <ArticleEditProcess turn={turn}/>
              <p>{turn.result?.message || turn.error || {queued:'已排队',running:'DSH 正在处理'}[turn.status]}</p>
              {turn.result?.options.map((option,i) => <details key={i}><summary>查看修改差异 · {option.changes.length} 处文字 / {option.figures?.length || 0} 处图片</summary><p>{option.explanation}</p>{option.changes.map((c,j) => <section key={j}><small>{{body:'正文',title:'标题'}[c.target] || '图注'}</small><del>{c.text}</del><ins>{c.replacement || '（删除）'}</ins></section>)}{option.figures?.map((f,j) => <p key={j}>图片 {f.index+1}：{{move:'移动',remove:'移除',replace:'替换'}[f.action]}{f.after_paragraph ? `到第 ${f.after_paragraph} 段后` : ''}</p>)}</details>)}
            </article>)}</div>
            <div className="article-review-compose">
              {!references.length && !figures.length && <p className="article-reference-hint">在左侧选中文字或点击图片，直接引用。</p>}
              {!!references.length && <p className="article-reference-hint">点击左侧已高亮的文字可移除引用。</p>}
              {references.map((r,i) => <div className="article-reference" key={`${r.target}:${r.start}`}><div><strong>引用 {i+1} · {r.target==='title'?'标题':r.target==='body'?'正文':'图注'}</strong><Button size="small" aria-label={`移除引用 ${i+1}`} onClick={() => removeReference(i)}>移除</Button></div><blockquote>{r.text}</blockquote></div>)}
              {figures.map(i => <Tag closable key={i} onClose={() => setFigures(figures.filter(n => n !== i))}>图片 {i+1} · {state.package.presentation.figures[i].selection.label}</Tag>)}
              <Space wrap>{templates.map(text => <Button key={text} size="small" disabled={locked} onClick={() => setInstruction(old => old ? old+'；'+text : text)}>{text.split('，')[0]}</Button>)}</Space>
              <label htmlFor="article-edit-instruction">修改意见</label>
              <Input.TextArea id="article-edit-instruction" value={instruction} onChange={e => setInstruction(e.target.value)} rows={4} maxLength={3000} placeholder="说明这些位置要怎么改；未引用时请明确指出位置。" disabled={locked}/>
              <Button type="primary" block disabled={locked || (!instruction.trim() && !references.length && !figures.length)} loading={busy || generating} onClick={send}>交给 DSH 修改（调用模型）</Button>
              {!state.editable && <p>此版本只读，请切换到最新图文后修改。</p>}
            </div>
          </aside>
        </div>
      </>}
  </>;
  if (inline) return <section className="article-review-inline" aria-label="审稿与修改">{editor}</section>;
  return <><Button onClick={() => {setOpen(true);setPackageId(item.id);setError('');}}>人工审稿与修改</Button>
    <Drawer title="图文审稿" open={open} closable={!manualOpen} keyboard={!manualOpen} maskClosable={!manualOpen} onClose={() => {setOpen(false);refresh();}} size="100vw" className="article-review-drawer" destroyOnHidden>
      {editor}
    </Drawer>
  </>;
}
