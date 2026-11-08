/** 人工检索所需的可复制元数据；下载入口与采集来源分开显示。 */
import React from 'react';
import { Typography } from 'antd';

function CopyField({ label, value, fallback = '待补充', copyable = false }) {
  return <span className="paper-copy-field"><span className="paper-field-label">{label}</span>{value ? <Typography.Text copyable={copyable ? {text:value, tooltips:[`复制${label}`, '已复制']} : false}>{value}</Typography.Text> : <span>{fallback}</span>}</span>;
}
export default function PaperMetadata({ paper, onOpen, showSource = false }) {
  return <div className="paper-metadata">
    <div className="paper-copy-title">{onOpen ? <button className="object-title" onClick={onOpen}><strong>{paper.title}</strong></button> : <strong>{paper.title}</strong>}</div>
    <div className="paper-copy-fields"><CopyField label="期刊" value={paper.journal} /><CopyField label="数据库" value={paper.download_database} fallback="待确认" /><CopyField label="DOI" value={paper.doi} copyable /></div>
    {showSource && <div className="paper-copy-fields"><span className="paper-source">采集来源：{ {pubmed:'PubMed',biorxiv:'bioRxiv',arxiv:'arXiv',medrxiv:'medRxiv'}[paper.source_id] || paper.source_id || '未知' }</span></div>}
  </div>;
}
