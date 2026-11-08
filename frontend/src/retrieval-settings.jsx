/** 上游检索配置的不可变草稿、查询式预览和显式发布。 */
import React, { useEffect, useState } from 'react';
import { Alert, Button, Form, Input, Modal, Select, Spin, Tag } from 'antd';
import { policyDefinition } from './sources-model';

const labels = {draft:'草稿',published:'当前发布',archived:'历史版本'};
const fields = [['agent','智能体检索词'],['model','模型检索词'],['drug','生物领域检索词'],['review','补充检索词']];
const editValue = definition => ({name:definition.name,scope:definition.scope,notes:definition.notes,...Object.fromEntries(fields.map(([key])=>[key+'_terms',definition[key+'_terms'].join('\n')]))});
export default function RetrievalSettings({ api, post }) {
  const [versions,setVersions] = useState([]), [id,setId] = useState(''), [draft,setDraft] = useState(null);
  const [busy,setBusy] = useState(false), [error,setError] = useState(''), [notice,setNotice] = useState(''), [preview,setPreview] = useState(null), [publishOpen,setPublishOpen] = useState(false);
  const [modal,contextHolder] = Modal.useModal();
  const selected = versions.find(item=>item.id===id);
  const dirty = !!selected && JSON.stringify(draft) !== JSON.stringify(editValue(selected.definition));
  function select(item) {
    setId(item.id);setDraft(editValue(item.definition));setPreview(null);
    const url=new URL(location.href);if(!url.searchParams.get('tab') || url.searchParams.get('tab')==='retrieval'){url.searchParams.set('policy',item.id);history.replaceState(null,'',url);}
  }
  async function load(preferred = new URLSearchParams(location.search).get('policy')) {
    const result=await api('/api/policies');
    const item=result.items.find(item=>item.id===preferred) || result.items.find(item=>item.status==='published') || result.items[0];
    if(!item)throw new Error('检索配置目录为空，请检查配置后重试');
    setVersions(result.items);select(item);
  }
  async function action(operation) {
    setBusy(true);setError('');setNotice('');
    try { await operation(); } catch(e) {setError(e.message);} finally {setBusy(false);}
  }
  async function canLeave() {
    return !dirty || await modal.confirm({title:'放弃未保存的修改？',content:'当前检索配置尚未保存为草稿。',okText:'放弃修改',cancelText:'继续编辑'});
  }
  useEffect(()=>{action(()=>load());},[]);
  useEffect(()=>{const warn=e=>{if(dirty){e.preventDefault();e.returnValue='';}};addEventListener('beforeunload',warn);return()=>removeEventListener('beforeunload',warn);},[dirty]);
  function update(key,value) {setDraft({...draft,[key]:value});setPreview(null);}
  async function save() {await action(async()=>{const result=await post('/api/policies',policyDefinition(selected.definition,draft));await load(result.id);setNotice(`草稿 v${result.version} 已保存，尚未发布。`);});}
  async function publish() {await action(async()=>{const result=await post(`/api/policies/${encodeURIComponent(selected.id)}/publish`,{});setPublishOpen(false);await load(result.id);setNotice(`检索配置 v${result.version} 已发布；历史运行保留原配置。`);});}
  return <section className="retrieval-settings" aria-label="上游检索词">{contextHolder}{error && <Alert type="error" title="操作未完成" description={error} action={!versions.length && <Button onClick={()=>action(()=>load())}>重试</Button>}/>}{notice && <Alert type="success" title={notice}/>}{busy && !draft ? <Spin/> : draft && <>
    <div className="retrieval-toolbar"><label htmlFor="retrieval-version">查看版本</label><Select id="retrieval-version" aria-label="查看检索配置版本" value={id} disabled={busy} options={versions.map(item=>({value:item.id,label:`v${item.version} · ${labels[item.status] || item.status} · ${item.definition.name}`}))} onChange={async value=>{if(await canLeave()){select(versions.find(item=>item.id===value));setNotice('');}}}/><Button disabled={busy} onClick={async()=>{if(await canLeave())action(()=>load());}}>刷新版本</Button><Tag>{dirty?'有未保存修改':labels[selected.status] || selected.status}</Tag></div>
    <Form layout="vertical" onFinish={save} disabled={busy}><Form.Item label="名称" htmlFor="retrieval-name" required><Input id="retrieval-name" value={draft.name} maxLength={120} onChange={e=>update('name',e.target.value)}/></Form.Item><Form.Item label="主题范围" htmlFor="retrieval-scope"><Input.TextArea id="retrieval-scope" rows={2} maxLength={3000} value={draft.scope} onChange={e=>update('scope',e.target.value)}/></Form.Item><div className="retrieval-fields">{fields.map(([key,label])=><Form.Item key={key} label={label+'（每行一个）'} htmlFor={'retrieval-'+key}><Input.TextArea id={'retrieval-'+key} rows={8} value={draft[key+'_terms']} onChange={e=>update(key+'_terms',e.target.value)}/></Form.Item>)}</div><Form.Item label="说明" htmlFor="retrieval-notes"><Input.TextArea id="retrieval-notes" rows={2} maxLength={3000} value={draft.notes} onChange={e=>update('notes',e.target.value)}/></Form.Item><div className="retrieval-toolbar"><Button type="primary" htmlType="submit" disabled={busy || !draft.name.trim()} loading={busy}>保存为新草稿</Button><Button disabled={busy} onClick={()=>action(async()=>{const result=await post('/api/policies/preview',{definition:policyDefinition(selected.definition,draft),title:'',text:''});setPreview(result.queries);setNotice('查询预览已生成，没有发起检索或模型调用。');})}>预览来源查询式</Button><Button disabled={busy || dirty || selected.status!=='draft'} onClick={()=>setPublishOpen(true)}>发布检索配置</Button></div></Form>{preview && <pre className="retrieval-preview" aria-label="来源查询式预览">{JSON.stringify(preview,null,2)}</pre>}<details><summary>用途与版本信息</summary><p>检索词用于生成来源检索式；资料采集后的主题判断使用节点提示词。</p><p>版本：{selected.id} · {selected.content_hash} · {selected.created_at}</p></details>
  </>}<Modal title="发布检索配置" open={publishOpen} onCancel={()=>!busy && setPublishOpen(false)} onOk={publish} confirmLoading={busy} okText="发布此草稿" cancelText="继续检查"><p>发布 v{selected?.version} 后，新运行采用此检索配置，历史运行保留冻结版本。本次不发起采集或模型调用。</p>{error && <Alert type="error" title={error}/>}</Modal></section>;
}
