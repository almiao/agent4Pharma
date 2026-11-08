/** 同轮论文的比较结论、证据与人工关联；自动获取范围由服务端另行校验。 */
import React from 'react';
import { Button, Empty, Space, Table, Tag } from 'antd';
import PaperMetadata from './paper-metadata';
import ManualPdfImport from './manual-pdf-import';
import OriginalPaper from './original-paper';
import PaperProcessingFailure from './paper-processing-failure.jsx';
import PaperRemake from './paper-remake';
import { paperProductionState } from './paper-production-state.js';
import { comparisonLabel } from './selection-paper-view.js';


export default function SelectionPaperTable({ rows, run, onRead, api, post, refresh, data, filtered }) {
  const cards = new Map((run.comparison_result?.cards || []).map(card => [card.record_id, card]));
  return <Table aria-label="论文精选与比较依据" rowKey="id" dataSource={rows} pagination={false} scroll={{x:1100}}
    locale={{emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={filtered ? '没有符合当前条件的论文' : '本轮暂无论文记录'}/>}} columns={[
    {title:'论文',key:'paper',width:360,render:(_,row)=><><PaperMetadata paper={row.detail} onOpen={()=>onRead(row)}/><Space wrap>{row.detail.local_rank != null && <Tag>本地第 {row.detail.local_rank} 名</Tag>}{row.detail.priority_institutions?.map(name=><Tag key={name}>{name}</Tag>)}{run.authority_journals?.some(name=>name.toLowerCase()===(row.detail.journal || '').toLowerCase()) && <Tag>优先期刊</Tag>}</Space></>},
    {title:'比较结论',key:'decision',width:140,render:(_,row)=><Tag>{comparisonLabel(run, row.detail, cards.get(row.detail.record_id))}</Tag>},
    {title:'新闻点与取舍理由',key:'reason',width:320,render:(_,row)=>{
      const card = cards.get(row.detail.record_id);
      return <>{card?.news_hook && <strong>{card.news_hook}</strong>}<p>{card?.reason || row.detail.reason || '尚无比较理由'}</p>
        {card && <details><summary>原文与机构依据</summary>{['evidence_quote','ai_quote','domain_quote','institution_quote'].filter(key=>card[key]).map(key=><blockquote key={key}>{card[key]}</blockquote>)}{card.verification_question && <details><summary>历史下载前问题</summary><p>{card.verification_question}</p></details>}</details>}</>;
    }},
    {title:'人工推荐',key:'human',width:120,render:(_,row)=><>{row.detail.manual_selected_at && <Tag>人工加入精选</Tag>}{row.detail.human_recommendation ? '已推荐' : '未推荐'}</>},
    {title:'采编状态',key:'production',width:150,render:(_,row)=>{
      const state = paperProductionState(row.detail,data);
      return <><Tag color={state.failed?'red':'default'}>{state.label}</Tag>
      {state.failed && <details><summary>失败详情</summary><PaperProcessingFailure paper={row.detail} data={data}/></details>}</>;
    }},
    {title:'主文与操作',key:'actions',width:190,render:(_,row)=><Space wrap>
      {row.detail.pdf_imported && <OriginalPaper paper={row.detail}/>}
      {(row.detail.can_associate_pdf ?? row.detail.can_download) === true && <ManualPdfImport paper={row.detail} post={post} refresh={refresh}/>}
      {paperProductionState(row.detail,data).href && <Button type="link" href={paperProductionState(row.detail,data).href}>查看制作过程</Button>}
      <PaperRemake paper={row.detail} {...{data,api,post}}/>
      <Button type="link" onClick={()=>onRead(row)}>详情</Button>
    </Space>},
  ]}/>;
}
