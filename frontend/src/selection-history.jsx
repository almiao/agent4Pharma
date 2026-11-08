/** 历史按需读取；关闭侧栏时不查询下载历史，不重载情报全池。 */
import React, { useEffect, useState } from 'react';
import { Alert, Button, Drawer, Table } from 'antd';

const date = value => new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});
const phases = {abstracts:'摘要筛选',commercial_review:'整轮比较',fulltexts:'已生成清单'};
export default function SelectionHistory({open,onClose,page,api,onRun}) {
  const [downloads,setDownloads]=useState(null),[error,setError]=useState(''),[version,setVersion]=useState(0);
  useEffect(()=>{
    if (!open || !page?.selected) return;
    let active=true;setDownloads(null);setError('');
    api('/api/paper-selections/downloads?batch='+encodeURIComponent(page.selected)).then(value=>{if(active)setDownloads(value.downloads);}).catch(err=>{if(active)setError(err.message);});
    return ()=>{active=false;};
  },[open,page?.selected,api,version]);
  const run = page?.paper_selections?.[0];
  return <Drawer title="历史与诊断" open={open} onClose={onClose} size={760}>
    <h3>筛选运行</h3>
    <Table rowKey="id" size="small" dataSource={page?.runs || []} pagination={{pageSize:6,showSizeChanger:false}} columns={[
      {title:'创建时间',render:(_,r)=>date(r.created_at)},
      {title:'阶段',render:(_,r)=>phases[r.phase] || r.phase},
      {title:'',render:(_,r)=><Button onClick={()=>{onRun(r.id);onClose();}}>查看此轮</Button>},
    ]}/>
    <details><summary>当前运行身份</summary><p>{page?.paper_selections?.[0]?.id || '尚未建立运行'}</p></details>
    <details><summary>冻结筛选策略</summary><p>策略：{run?.news_selection_version || '历史未记录'} · 本地池上限：{run?.review_pool_limit ?? '未记录'} · 下载清单上限：{run?.shortlist_limit ?? '未记录'}</p><p>暂停期刊：{run?.paused_journals?.join('、') || '无'} · 本轮跳过 {run?.paused_papers?.length ?? 0} 篇</p><p>优先期刊：{run?.authority_journals?.join('、') || '未记录'}</p></details>
    <h3>PDF 下载记录</h3><Button onClick={()=>setVersion(v=>v+1)}>刷新下载记录</Button>
    {error && <Alert type="error" title="下载记录读取失败" description={error}/>}
    <Table rowKey="id" size="small" loading={!downloads && !error} dataSource={downloads || []} locale={{emptyText:error?'读取失败，记录数量未知':'暂无下载记录'}} pagination={{pageSize:6,showSizeChanger:false}} columns={[
      {title:'论文',render:(_,r)=>r.request.title || r.request.doi || r.request.decision_id},
      {title:'结果',render:(_,r)=>({downloaded:'已校验主文',failed:'下载失败',manual_required:'待人工下载',timeout:'超时'})[r.status] || r.status},
      {title:'说明',render:(_,r)=>r.error || r.channel || '—'},
    ]}/>
    <p><a href={'/console?'+new URLSearchParams({view:'workspace',stage:'selection',section:'records',role:'intel',work:'tasks',batch:page?.selected || ''})}>查看历史评估任务</a></p>
  </Drawer>;
}
