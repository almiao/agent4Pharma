/** 准确批准图文的草稿箱交付；不提供群发，读取状态不执行网络发送。 */
import React, {useEffect, useRef, useState} from 'react';
import {Alert, Button, Input, Modal, Radio, Select, Space, Tag} from 'antd';
import './wechat-delivery.css';

const labels = {queued:'等待交付', uploading:'上传素材中', submitting:'正在创建草稿', submitted:'已送入草稿箱', failed:'交付失败', unknown_delivery:'结果未知'};
const active = new Set(['queued','uploading','submitting']);

export default function WechatDelivery({item, post}) {
  const [open,setOpen] = useState(false), [state,setState] = useState(null);
  const [busy,setBusy] = useState(false), [error,setError] = useState('');
  const [mode,setMode] = useState('image'), [cover,setCover] = useState(''), [media,setMedia] = useState('');
  const [absenceReason,setAbsenceReason] = useState('');
  const request = useRef(null);
  useEffect(() => {setOpen(false); setState(null); setCover(''); setMedia(''); request.current=null;}, [item.id]);
  useEffect(() => {
    if (!open) return;
    let cancelled=false, timer;
    async function poll() {
      try {
        const value=await post('/api/production/wechat-state', {package_id:item.id});
        if (cancelled) return;
        setState(value);
        if(value.deliveries.some(row=>row.id===request.current?.id && row.status==='failed')) request.current=null;
        if (value.deliveries.some(row=>active.has(row.status))) timer=setTimeout(poll,1500);
      } catch(err) {if(!cancelled) setError(err.message);}
    }
    poll();
    return () => {cancelled=true; clearTimeout(timer);};
  }, [open,item.id,post,busy]);
  const blocking=state?.deliveries.find(row=>row.request.account_id===state.account_id && row.status!=='failed');
  const validCover=mode==='image' ? !!cover : !!media.trim();
  async function send() {
    setBusy(true); setError('');
    const payload={package_id:item.id, expected_fingerprint:state.fingerprint, account_id:state.account_id,
      cover_sha256:mode==='image'?cover:'', thumb_media_id:mode==='material'?media.trim():''};
    const key=JSON.stringify(payload);
    if(request.current?.key!==key) request.current={key,id:crypto.randomUUID()};
    try {
      const result=await post('/api/production/wechat-draft', {...payload,request_id:request.current.id});
      setState(value=>({...value,deliveries:[...value.deliveries.filter(row=>row.id!==result.id),result]}));
      if(result.status==='failed') request.current=null;
    } catch(err) {setError(err.message);} finally {setBusy(false);}
  }
  async function confirmAbsent(row) {
    setBusy(true);setError('');
    try {
      const result=await post('/api/production/wechat-confirm-absent', {delivery_id:row.id,account_id:state.account_id,confirmed_absent:true,reason:absenceReason.trim()});
      request.current=null;setAbsenceReason('');
      setState(value=>({...value,deliveries:value.deliveries.map(r=>r.id===result.id?result:r)}));
    } catch(err) {setError(err.message);} finally {setBusy(false);}
  }
  return <>
    <Button onClick={()=>{setError('');setOpen(true);}}>送入公众号草稿箱</Button>
    <Modal title="送入公众号草稿箱" open={open} onCancel={()=>setOpen(false)} footer={null} destroyOnHidden>
      {error && <Alert type="error" title={error} showIcon/>}
      {!state ? <p role="status">正在读取交付信息…</p> : <Space orientation="vertical" className="wechat-delivery-field">
        <div className="wechat-delivery-content">目标账号：<strong>{state.account_name}</strong>{state.account_id && <div>{state.account_id}</div>}</div>
        {state.reason && <Alert type="warning" title={state.reason} showIcon/>}
        {!blocking && <>
          <Radio.Group aria-label="封面来源" value={mode} onChange={e=>setMode(e.target.value)} options={[{label:'使用图文中的图片',value:'image'},{label:'已有永久素材',value:'material'}]}/>
          {mode==='image' ? <><label htmlFor="wechat-cover">封面图片</label><Select id="wechat-cover" aria-label="封面图片" className="wechat-delivery-field" value={cover||undefined} placeholder="选择封面图片" onChange={setCover} options={state.images.map(i=>({value:i.sha256,label:i.label}))}/></> : <><label htmlFor="wechat-material">永久封面素材 ID</label><Input id="wechat-material" value={media} onChange={e=>setMedia(e.target.value)} maxLength={256}/><small>使用此公众号已有的永久图片素材。</small></>}
          <p>将当前已批准图文上传到上述账号的草稿箱，不会群发。正文与标题保持此版本。</p>
          <Button type="primary" loading={busy} disabled={!state.available||!validCover||busy} onClick={send}>确认送入草稿箱</Button>
        </>}
        {state.deliveries.map(row=><div key={row.id} role="status" className="wechat-delivery-content">
          <Tag color={row.status==='submitted'?'success':row.status==='failed'?'error':'processing'}>{labels[row.status]}</Tag>
          {row.request.account_id!==state.account_id && <span>其他账号：{row.request.account_id}</span>}
          {row.remote_id && <p>草稿 ID：<code>{row.remote_id}</code></p>}
          {row.error && <Alert type={row.status==='unknown_delivery'?'warning':'error'} title={row.error}/>}
          {row.resolution && <p>{row.resolution}</p>}
          {row.status==='unknown_delivery' && <>
            <p>请到公众号后台核对是否已创建草稿；此版本已阻止重复发送。</p>
            {row.request.account_id===state.account_id && <details><summary>已核对，草稿确实不存在</summary>
              <label htmlFor="wechat-absence">核对记录</label><Input.TextArea id="wechat-absence" value={absenceReason} onChange={e=>setAbsenceReason(e.target.value)} maxLength={500}/>
              <p>误确认可能导致重复草稿。此操作只解除阻断，之后仍需手动交付。</p>
              <Button loading={busy} disabled={absenceReason.trim().length<5||busy} onClick={()=>confirmAbsent(row)}>确认草稿不存在并解除阻断</Button>
            </details>}
          </>}
        </div>)}
        <a href="https://mp.weixin.qq.com/" target="_blank" rel="noreferrer">打开微信公众号后台</a>
      </Space>}
    </Modal>
  </>;
}
