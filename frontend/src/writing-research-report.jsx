/** 作者研究底稿与正文分开阅读；准确引用按需展开，不把保存点显示为任务完成。 */
import React from 'react';
import NewsOutlineReport from './news-outline-report';

const uses = {main_result:'主结果',explanation:'解释机制',boundary:'限定主张',context:'背景',omit:'不进正文'};
const relations = {original_research:'原始研究',team_explanation:'团队说明',external_commentary:'外部评论'};

function Findings({ items, excerpts }) {
  return items.map((item, index) => {
    const span = item.source_span;
    const excerpt = excerpts.find(e => e.evidence_id === item.evidence_id && e.content_hash === span.content_hash && e.start === span.start && e.end === span.end);
    return <section key={index}><p>{item.statement}</p>{item.limitation && <p className="document-note">{item.limitation}</p>}
      {(item.narrative_use || item.selection_reason) && <p><strong>{uses[item.narrative_use] || '材料取舍'}：</strong>{item.selection_reason || '未记录取舍理由'}</p>}
      {relations[item.source_relation] && <p className="document-note">来源关系：{relations[item.source_relation]}</p>}
      <details><summary>查看出处</summary>{excerpt ? <blockquote>{excerpt.text}</blockquote> : <p>此页未提供原文片段</p>}<code>{item.evidence_id}</code></details>
    </section>;
  });
}

export function WritingResearchReport({ snapshot, excerpts = [] }) {
  if (!snapshot) return null;
  const research = snapshot.research;
  return <section aria-label="作者研究底稿">
    <p className="document-note">底稿 v{snapshot.version} · 任务书 v{snapshot.brief_version}</p>
    <section><h3>围绕选题的理解</h3><p><strong>中心问题：</strong>{research.question}</p><p>{research.answer}</p></section>
    <NewsOutlineReport outline={research.outline} excerpts={excerpts}/>
    {research.claim_check && <section><h3>核心主张与边界核对</h3><p>{research.claim_check}</p></section>}
    <section><h3>论文事实与证据</h3><Findings items={research.findings} excerpts={excerpts}/></section>
    {!!research.background.length && <section><h3>背景调研结果</h3><Findings items={research.background} excerpts={excerpts}/></section>}
    {!!research.gaps.length && <section><h3>未决问题与处置</h3>{research.gaps.map((gap, index) => <section key={index}><h4>{gap.question}</h4><p><strong>{({omit:'省略相关内容',qualify:'限定表述',block:'核心阻断'})[gap.handling]}：</strong>{gap.reason}</p></section>)}</section>}
    <details><summary>实际调查范围</summary><p>{research.research_scope}</p></details>
  </section>;
}

export default function WritingResearchHistory({ record }) {
  if (!record.writing_research) return null;
  const snapshot = record.writing_research;
  const past = (record.writing_research_versions || []).filter(item => item.version !== snapshot.version);
  return <details className="document-sources" open={!record.artifact}>
    <summary>作者研究底稿 · v{snapshot.version}</summary>
    <WritingResearchReport snapshot={snapshot} excerpts={record.writing_research_excerpts}/>
    {past.map(item => <details key={item.version}><summary>历史底稿 v{item.version}</summary><WritingResearchReport snapshot={item} excerpts={record.writing_research_excerpts}/></details>)}
  </details>;
}
