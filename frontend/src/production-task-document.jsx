/** 输入与已提交产物的阅读视图；只用准确快照，不拼接当前版本或将文本解释为 HTML。 */
import React from 'react';
import { ReadOnlyPackagePreview } from './publication-preview';
import WritingResearchHistory, { WritingResearchReport } from './writing-research-report';
import BackgroundResearchHistory from './background-research-report';

const decisionLabels = { commission: '立项', defer: '暂缓 · 待事件', decline: '不立项', request_evidence: '暂缓 · 待补证', pass: '事实核验通过', ready: '阅读品质通过', rebrief_needed: '历史：需重新判断选题', changes_requested: '要求返修', blocked: '历史：阻断' };
const safeUrl = value => /^https?:\/\//i.test(value || '') ? value : undefined;

function parse(value) {
  if (!value) return null;
  try { return JSON.parse(value); } catch { return null; }
}

/** 正文按段落、标题和列表阅读，所有源文保持 React 文本转义。 */
export function DocumentText({ text = '' }) {
  return <div className="task-document-text">{text.split(/\n\s*\n/).filter(Boolean).map((block, index) => {
    const lines = block.split('\n');
    if (lines.every(line => /^[-*] /.test(line))) return <ul key={index}>{lines.map((line, i) => <li key={i}>{line.slice(2)}</li>)}</ul>;
    return <React.Fragment key={index}>{lines.map((line, i) => /^#{1,3} /.test(line)
      ? <h3 key={i}>{line.replace(/^#{1,3} /, '')}</h3>
      : <p key={i}>{line}</p>)}</React.Fragment>;
  })}</div>;
}

function DocumentSheet({ title, type, meta, children }) {
  return <article className="task-document" aria-label={type}>
    <header className="task-document-heading"><span>{type}</span><h2>{title}</h2>{meta && <p>{meta}</p>}</header>
    <div className="task-document-body">{children}</div>
  </article>;
}

function Brief({ brief }) {
  return <>
    <section><h3>报道角度</h3><p>{brief.angle}</p></section>
    {brief.boundary && <section><h3>事实边界</h3><p>{brief.boundary}</p></section>}

  </>;
}

function Draft({ draft }) {
  const body = draft.submission || draft;
  return <>
    {body.summary && <p className="document-lead">{body.summary}</p>}
    <DocumentText text={body.body}/>
    {!!body.limitations?.length && <section><h3>限制与边界</h3><ul>{body.limitations.map((text, index) => <li key={index}>{text}</li>)}</ul></section>}
    {!!body.reader_responses?.length && <details><summary>阅读问题处理记录</summary>{body.reader_responses.map(response => <p key={response.issue_id}>{response.issue_id} · {response.action === 'revised' ? '已修改' : '保留并说明'}：{response.reason}</p>)}</details>}
    {!!body.claims?.length && <details className="document-sources"><summary>引用与依据（{body.claims.length}）</summary>{body.claims.map((claim, index) => <section key={index}><p>{claim.claim}</p><blockquote>{claim.quote}</blockquote>{claim.limitation && <p>{claim.limitation}</p>}<code>{claim.evidence_id}</code></section>)}</details>}
  </>;
}

function Review({ review }) {
  const body = review.submission || review;
  return <><p className={`document-verdict verdict-${body.decision}`}>{decisionLabels[body.decision] || body.decision}</p><p className="document-lead">{body.summary}</p>
    {body.issues?.length ? <section><h3>核验意见</h3>{body.issues.map((issue, index) => <section className="document-review-issue" key={issue.id || index}>
      <h4>{index + 1}. {issue.location}</h4><span className={`document-severity severity-${issue.severity}`}>{({ blocking: '阻断', required: '必改', suggestion: '建议' })[issue.severity] || issue.severity}</span>
      {issue.draft_quote && <blockquote>{issue.draft_quote}</blockquote>}<p>{issue.reason}</p><p><strong>处理要求：</strong>{issue.resolution}</p>
      {issue.source_quote && <details><summary>纠正依据</summary><blockquote>{issue.source_quote}</blockquote><code>{issue.evidence_id}</code></details>}
      {issue.requirement && <p className="document-note">任务要求：{issue.requirement}</p>}
    </section>)}</section> : <p className="document-note">未记录核验问题</p>}
  </>;
}

export function ReaderReview({ review }) {
  const body = review.submission;
  const verdict = dimension => body[dimension] !== 'completed' ? '未完成' : body.issues.some(issue=>issue.dimension === dimension && issue.severity === 'required') ? '要求返修' : '通过';
  return <section aria-label="阅读品质评审">
    <p className={`document-verdict verdict-${body.decision}`}>{decisionLabels[body.decision]}</p>
    <p className="document-lead">{body.summary}</p>
    <p>新闻吸引力与阅读推进：{verdict('appeal_and_flow')} · 中文自然度：{verdict('language_naturalness')}</p>
    {body.issues.map(issue => <section className="document-review-issue" key={issue.id}>
      <h3>{issue.dimension === 'appeal_and_flow' ? '阅读推进' : '表达自然度'} · {issue.severity === 'required' ? '必改' : '建议'}</h3>
      {issue.anchors.map((text, index) => <blockquote key={index}>{text}</blockquote>)}
      <p>{issue.problem}</p><p><strong>阅读影响：</strong>{issue.reader_impact}</p>
      <p><strong>处理要求：</strong>{issue.resolution}</p>
      {issue.example_revision && <details><summary>局部修改示例</summary><p>{issue.example_revision}</p></details>}
    </section>)}
    {!!body.closed_issue_ids?.length && <p>已关闭问题：{body.closed_issue_ids.join('、')}</p>}
    {review.observation && <details><summary>首次阅读观察</summary><p>{review.observation.news_understood}</p><p>{review.observation.reason_to_read}</p><p>{review.observation.first_obstacle}</p></details>}
  </section>;
}

function Evidence({ sources }) {
  return <section className="input-evidence"><h3>资料（{sources.length}）</h3>{sources.map((source, index) => <details key={source.id || index}>
    <summary>{source.title || source.id}</summary>
    <p className="document-note">{source.read_scope || (source.document ? '绑定文档，正文按需读取' : source.fulltext ? '来源声明为全文' : '已保存文本片段')}</p>
    {safeUrl(source.url) && <a href={source.url} target="_blank" rel="noreferrer">原始出处</a>}
    <DocumentText text={source.text}/>{source.document && <p className="document-note">PDF · {source.document.pages ?? '页数未知'} 页</p>}
    <details><summary>版本身份</summary><code>{source.id}</code>{source.document?.sha256 && <p><code>{source.document.sha256}</code></p>}</details>
  </details>)}</section>;
}

function SelectionBasis({ candidates, title }) {
  const current = candidates.filter(candidate => candidate.selection_basis);
  const historical = candidates.filter(candidate => !candidate.selection_basis && candidate.reason);
  return <>
    {!!current.length && <section><h3>下载前入选依据</h3>{current.map(candidate => {
      const basis = candidate.selection_basis;
      return <section key={candidate.id}>
        {candidate.summary !== title && <h4>{candidate.summary}</h4>}
        {basis.news_hook && <p><strong>新闻点：</strong>{basis.news_hook}</p>}
        <p>{basis.reason}</p>
        {(basis.evidence_quote || basis.institution_quote) && <details><summary>原文与机构依据</summary>
          {basis.evidence_quote && <blockquote>{basis.evidence_quote}</blockquote>}
          {basis.institution_quote && <blockquote>{basis.institution_quote}</blockquote>}
        </details>}
      </section>;
    })}</section>}
    {!!historical.length && <details><summary>历史候选依据</summary>{historical.map(candidate => <section key={candidate.id}>
      {candidate.summary !== title && <h4>{candidate.summary}</h4>}<DocumentText text={candidate.reason}/>
    </section>)}</details>}
  </>;
}

export function ProductionTaskInput({ record }) {
  const context = parse(record.context_json);
  if (!context) return <p role="alert">无法读取此任务的冻结输入。</p>;
  const sources = [...(context.evidence || []), ...(context.candidates || []).flatMap(candidate => candidate.evidence || [])];
  // 相同身份且内容一致才折叠，保留身份相同但载荷不同的历史证据。
  const unique = [...new Map(sources.map(source => [JSON.stringify(source), source])).values()];
  const title = context.story?.brief?.title || unique[0]?.title || '制作任务输入';
  return <DocumentSheet title={title} type="冻结输入" meta={context.story ? `任务书 v${context.story.brief_version} · 选题修订 ${context.story.revision}` : undefined}>
    {context.story?.brief && <Brief brief={context.story.brief}/>}
    {context.story?.editorial_reason && <details><summary>立项依据</summary><p>{context.story.editorial_reason}</p></details>}
    {context.writing_research && <details><summary>已保存的作者研究底稿</summary><WritingResearchReport snapshot={context.writing_research} excerpts={record.writing_research_excerpts}/></details>}
    {!!context.background_research?.length && <details><summary>上游背景调研</summary><BackgroundResearchHistory record={{...record,background_research:context.background_research,background_research_versions:[]}}/></details>}
    <SelectionBasis candidates={context.candidates || []} title={title}/>
    {!!unique.length && <Evidence sources={unique}/>}
    {context.draft && <section><h3>输入稿件 · v{context.draft.number}</h3><h4>{context.draft.submission.title}</h4><Draft draft={context.draft}/></section>}
    {context.reader_reviews?.map(review => <section key={review.id}><h3>上游阅读品质报告</h3><ReaderReview review={review}/></section>)}
    {context.reviews?.map(review => <section key={review.id}><h3>上游核验报告</h3><Review review={review}/></section>)}
    {context.article_edit && <section><h3>改稿要求</h3><p>{context.article_edit.instruction}</p>{context.article_edit.references?.map((reference, index) => <blockquote key={index}>{reference.text}</blockquote>)}</section>}
    {context.layout_source_package && <p className="document-note">绑定渠道包：{context.layout_source_package}</p>}
    {!context.story && !unique.length && !context.draft && <p>此历史快照未记录可阅读的资料内容。</p>}
  </DocumentSheet>;
}

export function ProductionTaskOutput({ record, post }) {
  const ownBackground = (record.background_research || []).filter(snapshot => snapshot.task_id === record.task.id);
  return <><TaskPrimaryOutput record={record} post={post} hasBackground={ownBackground.length > 0}/><BackgroundResearchHistory record={{...record,background_research:ownBackground}}/></>;
}

function TaskPrimaryOutput({ record, post, hasBackground }) {
  const task = record.task;
  const artifact = record.artifact;
  const source = parse(record.context_json);
  const title = source?.story?.brief?.title || source?.evidence?.[0]?.title || source?.candidates?.[0]?.evidence?.[0]?.title;
  if (artifact?.kind === 'decision') {
    const decision = artifact.content;
    return <DocumentSheet title={decision.brief?.title || title || '选题决定'} type="选题决定与任务书">
      <p className="document-verdict">{decisionLabels[decision.decision] || decision.decision}</p>
      {decision.brief ? <><Brief brief={decision.brief}/><details><summary>立项依据</summary><p>{decision.reason}</p></details></> : <p className="document-lead">{decision.reason}</p>}
      {decision.revisit_condition && <section><h3>重新查看条件</h3><p>{decision.revisit_condition}</p></section>}
    </DocumentSheet>;
  }
  if (artifact?.kind === 'draft') return <DocumentSheet title={artifact.content.submission.title} type="稿件" meta={`稿件 v${artifact.content.number} · 任务书 v${artifact.content.brief_version}`}><WritingResearchHistory record={record}/><Draft draft={artifact.content}/></DocumentSheet>;
  if (artifact?.kind === 'reader_review') return <DocumentSheet title={title || '稿件阅读品质'} type="阅读评审报告"><ReaderReview review={artifact.content}/></DocumentSheet>;
  if (artifact?.kind === 'review') return <DocumentSheet title={title || '稿件核验'} type="事实核验报告"><Review review={artifact.content}/></DocumentSheet>;
  if (artifact?.kind === 'package') return <DocumentSheet title={title || '排版渠道包'} type="渠道包" meta={artifact.content.channel === 'local_html' ? 'HTML 阅读版' : 'Markdown'}>
    {artifact.content.channel === 'local_html' ? <ReadOnlyPackagePreview item={artifact.content}/> : <DocumentText text={artifact.content.body}/>}
    {artifact.content.release_note && <details><summary>制包记录</summary><p className="document-note">{artifact.content.release_note}</p></details>}
  </DocumentSheet>;
  const editing = parse(task.editing_result_json);
  if (editing) return <DocumentSheet title={title || '稿件修改方案'} type="改稿候选"><p className="document-lead">{editing.message}</p>{editing.options?.map((option, index) => <section key={index}><h3>{option.label}</h3><p>{option.explanation}</p>{option.changes?.map((change, i) => <section className="document-text-change" key={i}><del>{change.text}</del><ins>{change.replacement || '（删除）'}</ins></section>)}</section>)}</DocumentSheet>;
  if (record.writing_research) return <DocumentSheet title={title || '写作研究'} type="作者研究底稿"><WritingResearchHistory record={record}/><p role="status">{task.status === 'running' ? '底稿已保存，写作任务仍在进行。' : '底稿已保存，正文尚未提交。'}</p></DocumentSheet>;
  if (hasBackground) return null;
  return <div className="task-document-empty" role="status">{task.status === 'succeeded' ? '未读取到此任务已提交的产物。' : task.status === 'running' ? '本阶段正在生成产物。' : '本阶段尚未提交产物。'}</div>;
}
