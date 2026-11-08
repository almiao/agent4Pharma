/** 展示作者实际保存的文章计划；段号属于骨架，不冒充最终正文定位。 */
import React from 'react';

export default function NewsOutlineReport({outline, excerpts = []}) {
  if (!outline) return null;
  return <section aria-label="新闻骨架">
    <h3>新闻骨架</h3>
    <p><strong>新闻主线：</strong>{outline.mainline}</p>
    <p><strong>整体推进：</strong>{outline.progression}</p>
    {outline.paragraphs.map((paragraph, index) => <section key={index}>
      <h4>计划第{index + 1}段{paragraph.section ? ` · ${paragraph.section}` : ''}</h4>
      <p><strong>读者问题：</strong>{paragraph.question}</p>
      <p><strong>本段内容：</strong>{paragraph.message}</p>
      <p><strong>安排理由：</strong>{paragraph.position_reason}</p>
      {!!paragraph.depends_on.length && <p>承接计划第{paragraph.depends_on.join('、')}段</p>}
      {paragraph.sources.map((source, sourceIndex) => {
        const span = source.source_span;
        const excerpt = excerpts.find(item => item.evidence_id === source.evidence_id && item.content_hash === span.content_hash && item.start === span.start && item.end === span.end);
        return <details key={sourceIndex}><summary>依赖材料 · {source.location}</summary>
          <p>{source.use}</p>
          {excerpt ? <blockquote>{excerpt.text}</blockquote> : <p>此页未提供原文片段</p>}
          <code>{source.evidence_id}</code>
        </details>;
      })}
      {paragraph.visual_need && <p><strong>配图需求：</strong>{paragraph.visual_need}</p>}
    </section>)}
    <p><strong>收束：</strong>{outline.ending}</p>
    {outline.omissions && <p><strong>材料取舍：</strong>{outline.omissions}</p>}
  </section>;
}
