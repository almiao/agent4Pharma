/** 个位数精选论文的阅读清单，保留检索与挑选所需信息。 */
import React from 'react';
import { Button, Empty, Tag } from 'antd';
import SelectionPaperTable from './selection-paper-table';
import PaperMetadata from './paper-metadata';
import ManualPdfImport from './manual-pdf-import';
import OriginalPaper from './original-paper';
import PaperProcessingFailure from './paper-processing-failure.jsx';
import PaperRemake from './paper-remake';
import { paperProductionState } from './paper-production-state.js';

export default function SelectedPaperList({ rows, onRead, api, post, refresh, data, run, filtered }) {
  if (run) return <SelectionPaperTable {...{rows, run, onRead, api, post, refresh, data, filtered}}/>;
  if (!rows.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前范围没有入选论文" />;
  return <ol className="shortlist-sheet" aria-label="精选论文清单">
    {rows.map(row => <li className="shortlist-paper" key={row.id}>
      <span className="shortlist-rank" aria-label={`精选排名 ${row.detail.rank}`}>{String(row.detail.rank).padStart(2, '0')}</span>
      <div className="shortlist-body"><PaperMetadata paper={row.detail} />{row.detail.reason && <p className="shortlist-reason">{row.detail.reason}</p>}<PaperProcessingFailure paper={row.detail} data={data}/></div>
      <div className="shortlist-actions"><Tag>{row.displayStatus === '待获取全文' ? '待下载' : row.displayStatus}</Tag>{row.detail.pdf_imported ? <><OriginalPaper paper={row.detail} />{!row.detail.human_recommendation && <ManualPdfImport paper={row.detail} post={post} refresh={refresh} />}</> : <ManualPdfImport paper={row.detail} post={post} refresh={refresh} />}{paperProductionState(row.detail,data).href && <Button type="link" href={paperProductionState(row.detail,data).href}>查看制作过程</Button>}<PaperRemake paper={row.detail} {...{data,api,post}}/><Button type="link" onClick={() => onRead(row)}>查看详情</Button></div>
    </li>)}
  </ol>;
}
