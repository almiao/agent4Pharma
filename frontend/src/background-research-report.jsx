/** 背景问题与产出单独成文；充分性是角色判断，原文按准确版本与位置展开。 */
import React from 'react';

const sufficiency = {in_progress:'调研中',sufficient:'足以支撑本篇',limited:'限定后可用',insufficient:'仍不足'};
const statuses = {planned:'待调研',answered:'已回答',partial:'部分回答',unresolved:'尚未解决',technical_failure:'技术失败',not_needed:'无需补查'};
const handling = {use:'采用',qualify:'限定使用',omit:'省略',block:'核心阻断'};
const phases = {'topic-selection':'主编选题','draft-writing':'作者研究与写作'};
const safeUrl = value => /^https?:\/\//i.test(value || '') ? value : undefined;
const savedAt = value => `${new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})}（北京时间）`;
const excerptFor = (finding, excerpts) => excerpts.find(item => item.evidence_id === finding.evidence_id && item.content_hash === finding.source_span.content_hash && item.start === finding.source_span.start && item.end === finding.source_span.end);

/** 将真实保存的调研与准确引文导出为独立Markdown，不发请求或执行模型。 */
export function backgroundResearchMarkdown(snapshot, excerpts = [], sources = []) {
  const research = snapshot.research;
  const lines = ['# 背景调研记录',`阶段：${phases[snapshot.phase] || snapshot.phase} · v${snapshot.version}`,`任务：${snapshot.task_id}`,`保存时间：${snapshot.created_at}`, '## 调研范围',research.focus,'## 充分性判断',`${sufficiency[research.sufficiency]}（调研角色自评）`,research.assessment];
  for (const question of research.questions) {
    lines.push(`## ${question.question}`,`用途：${question.purpose === 'decision' ? '选题判断' : '读者理解'}`,`与主线的关系：${question.relevance}`,`状态：${statuses[question.status]} · ${handling[question.handling]}`,question.answer || '尚未形成答案',`处理判断：${question.assessment}`);
    for (const finding of question.findings) {
      const source = sources.find(item => item.id === finding.evidence_id);
      const excerpt = excerptFor(finding, excerpts);
      lines.push(finding.statement,finding.limitation || '',`来源：${source?.title || finding.evidence_id}`,safeUrl(source?.url) || '',source?.read_scope || '来源范围未记录',excerpt ? `原文：\n${excerpt.text}` : '原文片段未提供',`证据：${finding.evidence_id} · ${finding.source_span.content_hash} · ${finding.source_span.start}–${finding.source_span.end}`);
    }
  }
  if (!research.questions.length) lines.push('没有新增背景问题；无需补查的理由见充分性判断。');
  lines.push('## 实际调查范围与停止理由',research.research_scope);
  return lines.filter(Boolean).join('\n\n')+'\n';
}

/** 通过与运行台现有导出一致的Blob下载，不把整份文档塞进导航URL。 */
export function downloadBackgroundResearch(snapshot, excerpts = [], sources = []) {
  const url = URL.createObjectURL(new Blob([backgroundResearchMarkdown(snapshot, excerpts, sources)], {type:'text/markdown;charset=utf-8'}));
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = `background-${snapshot.task_id}-v${snapshot.version}.md`;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url),1000);
}

export function BackgroundReport({snapshot, excerpts = [], sources = []}) {
  const research = snapshot.research;
  return <section aria-label="背景调研文档">
    <p className="document-note">保存于 {savedAt(snapshot.created_at)}{snapshot.brief_version > 0 ? ` · 任务书 v${snapshot.brief_version}` : ''}</p>
    <button type="button" className="background-download" onClick={() => downloadBackgroundResearch(snapshot,excerpts,sources)}>下载调研文档</button>
    <section><h3>调研范围</h3><p>{research.focus}</p></section>
    <section><h3>充分性判断</h3><p className="document-verdict">{sufficiency[research.sufficiency]}</p><p>{research.assessment}</p><p className="document-note">调研角色自评</p></section>
    {research.questions.map(question => <section key={question.id}>
      <h3>{question.question}</h3>
      <p className="document-note">{question.purpose === 'decision' ? '选题判断' : '读者理解'} · {statuses[question.status]} · {handling[question.handling]}</p>
      <p><strong>为什么查：</strong>{question.relevance}</p>
      {question.answer && <p><strong>调研产出：</strong>{question.answer}</p>}
      <p><strong>处理判断：</strong>{question.assessment}</p>
      {!!question.findings.length && <details className="document-sources"><summary>依据与原文（{question.findings.length}）</summary>{question.findings.map((finding,index) => {
        const source = sources.find(item => item.id === finding.evidence_id);
        const excerpt = excerptFor(finding, excerpts);
        return <section key={index}><h4>{source?.title || finding.evidence_id}</h4><p>{finding.statement}</p>{finding.limitation && <p>{finding.limitation}</p>}
          {safeUrl(source?.url) && <a href={source.url} target="_blank" rel="noreferrer">原始出处</a>}
          <p className="document-note">{source?.read_scope || '来源范围未记录'}</p>
          {excerpt ? <blockquote>{excerpt.text}</blockquote> : <p>此页未提供原文片段</p>}
          <details><summary>证据身份与位置</summary><code>{finding.evidence_id}</code><p><code>{finding.source_span.content_hash}</code> · {finding.source_span.start}–{finding.source_span.end}</p></details>
        </section>;
      })}</details>}
    </section>)}
    {!research.questions.length && <p>没有新增背景问题；无需补查的理由见充分性判断。</p>}
    <details><summary>实际调查范围与停止理由</summary><p>{research.research_scope}</p></details>
  </section>;
}

export default function BackgroundResearchHistory({record}) {
  const reports = record.background_research || [];
  if (!reports.length) return null;
  return <article className="task-document" aria-label="背景调研记录">
    <header className="task-document-heading"><h2>背景调研记录</h2></header>
    <div className="task-document-body">{reports.map((snapshot,index) => <details key={`${snapshot.task_id}-${snapshot.version}`} open={index === reports.length-1}>
      <summary>{phases[snapshot.phase] || snapshot.phase} · {sufficiency[snapshot.research.sufficiency]} · v{snapshot.version}</summary>
      <BackgroundReport snapshot={snapshot} excerpts={record.background_research_excerpts} sources={record.background_research_sources}/>
      {(index === reports.length-1 ? record.background_research_versions || [] : []).filter(item => item.task_id !== snapshot.task_id || item.version !== snapshot.version).map(item => <details key={`${item.task_id}-${item.version}`}>
        <summary>历史调研 v{item.version}</summary><BackgroundReport snapshot={item} excerpts={record.background_research_excerpts} sources={record.background_research_sources}/>
      </details>)}
    </details>)}</div>
  </article>;
}
