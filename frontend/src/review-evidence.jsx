/** 审稿依据按明确来源归组，保留每条原始证据及其定位。 */
import React from 'react';

export default function ReviewEvidence({ evidence = [] }) {
  const sources = new Map();
  for (const item of evidence) {
    const key = item.document?.sha256 ? 'document:' + item.document.sha256
      : item.url ? 'url:' + item.url : 'evidence:' + item.id;
    if (!sources.has(key)) sources.set(key, []);
    sources.get(key).push(item);
  }
  if (!sources.size) return <p>此版本未记录证据材料。</p>;
  return [...sources].map(([key, excerpts]) => <details key={key}>
    <summary>{excerpts[0].title || '证据材料'}{excerpts.length > 1 && ` · ${excerpts.length} 个片段`}</summary>
    {excerpts.map(item => <div key={item.id}>
      <p>{[item.origin, item.fulltext ? '原文来源' : '部分材料', item.read_scope, ...(item.locators || [])].filter(Boolean).join(' · ')}</p>
      <p className="trace-text">{item.text}</p>
    </div>)}
  </details>);
}
