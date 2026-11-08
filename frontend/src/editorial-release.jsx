/** 人工定稿和发布演练；只有明确批准的完整发布包可以进入发布。 */
import React, { useState } from 'react';
import { approvalBlocker, approveRelease, simulatePublication } from './editorial-sample';

export function HumanApproval({ state, version, act, onPublish }) {
  const [checked, setChecked] = useState(false);
  const blocker = approvalBlocker(state, version.id);
  const approved = state.releases?.find(r => r.package.version === version.id);
  return <section className="human-approval">
    <h3>定稿 · release-{version.id}</h3>
    <p>标题、正文、排版 article-layout-v1 · 引用 paper-v1 · 本篇无配图与图注</p>
    {approved ? <><p>此发布包已获人工批准。后续修改需生成新版本并重新审稿。</p><button onClick={onPublish}>查看发布状态</button></> : <>
      <label><input type="checkbox" checked={checked} onChange={e => setChecked(e.target.checked)}/>我已核对事实与引用，并确认标题、正文、配图及最终排版（本地演练）</label>
      {version.review === '待核验' && <p>此版本没有 Agent 核验通过记录；本次确认将记录为人工核对，不冒充模型核验。</p>}
      {blocker && <p role="status">{blocker}</p>}
      <button className="trace-primary" disabled={!checked || !!blocker} onClick={() => { if (act(() => approveRelease(state, version.id, checked), '人工批准已记录，直接进入发布。')) onPublish(); }}>确认并发布（样例）</button>
    </>}
  </section>;
}

export function Publication({ state, act, onReview, renderArticle }) {
  const records = state.releases || [];
  return <section className="editorial-workspace"><div className="editorial-toolbar"><div><h2>发布</h2><span>人工定稿 → 直接发布 · 本地演练，不发送外部请求</span></div><button onClick={onReview}>返回人工审稿</button></div>
    {!records.length && <div className="trace-empty">等待人工审稿通过，尚无已批准的发布包。</div>}
    {[...records].reverse().map(r => <section className="human-approval" key={r.package.id}>
      <h3>{r.package.id} · {r.status}</h3><p>{r.approval.actor} · {new Date(r.approval.at).toLocaleString()} · {r.approval.factCheck}</p>
      <p>批准内容：{r.package.title} · {r.package.layout} · 引用 {r.package.references.join('、')} · 无配图</p>
      <details><summary>查看已批准的完整发布内容</summary>{renderArticle(r.package)}</details>
      {r.package.version !== state.versions.at(-1).id && <p>已有更新稿件；此批准仅适用于历史版本，不能用于新稿或继续发送。</p>}
      {r.status !== '发布成功（模拟）' && <div className="editorial-actions"><button disabled={r.package.version !== state.versions.at(-1).id || !!approvalBlocker(state, r.package.version)} onClick={() => act(() => simulatePublication(state, r.package.id, true), '模拟发布成功；未向任何外部平台发送。')}>{r.status === '发布失败（模拟）' ? '重试发布（模拟成功）' : '模拟发布成功'}</button><button disabled={r.package.version !== state.versions.at(-1).id || !!approvalBlocker(state, r.package.version)} onClick={() => act(() => simulatePublication(state, r.package.id, false), '模拟网络失败；重试仍使用相同批准内容。')}>模拟发布失败</button></div>}
      {!!approvalBlocker(state, r.package.version) && <p>{approvalBlocker(state, r.package.version)}</p>}
      <ol>{r.attempts.map((a, i) => <li key={i}>第 {i + 1} 次发布 · {a.result} · {new Date(a.at).toLocaleString()}</li>)}</ol>
    </section>)}
  </section>;
}
