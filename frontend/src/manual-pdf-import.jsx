/** 人工下载确认：多目录检查、身份匹配、歧义选择和明确导入结果。 */
import React, { useState } from 'react';
import { Alert, Button, Modal, Popover, Radio, Space, Spin, Typography } from 'antd';

export function DownloadDirectory({ api }) {
  const [directories, setDirectories] = useState(null), [warnings, setWarnings] = useState([]), [error, setError] = useState('');
  async function read(open) {
    if (!open) return;
    try { const result = await api('/api/manual-pdfs/directory'); setDirectories(result.directories || [result.directory]); setWarnings(result.warnings || []); setError(''); }
    catch (err) { setError(err.message); }
  }
  return <Popover trigger="click" title="PDF 检查目录" onOpenChange={read} content={<div style={{maxWidth:520}}>{error ? <span>{error}</span> : directories ? directories.map(path=><p key={path}><Typography.Text copyable>{path}</Typography.Text></p>) : <Spin size="small" />}{warnings.map(warning=><p key={warning}>{warning}</p>)}<p>同时检查项目收件、Chrome 下载位置和系统下载目录，每目录检查最近50个完整 PDF。下载完成后点击“关联 PDF”，无需移动或重命名文件。</p></div>}><Button type="text" size="small">检查目录</Button></Popover>;
}

export default function ManualPdfImport({ paper, post, refresh }) {
  const [open, setOpen] = useState(false), [busy, setBusy] = useState(false), [result, setResult] = useState(null), [error, setError] = useState(''), [fileId, setFileId] = useState(''), [startProduction, setStartProduction] = useState(false);
  async function confirm(selected = '', start = startProduction) {
    if (busy) return;
    setStartProduction(start); setOpen(true); setBusy(true); setError(''); setFileId(''); setResult(null);
    try {
      const value = await post('/api/manual-pdfs/confirm', {run_id:paper.run_id,record_id:paper.record_id,file_id:selected,recommend_and_start:start});
      setResult(value);

    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }
  function close() {
    if (busy) return;
    setOpen(false);
    if (result?.status === 'imported') refresh();
  }
  if (paper.human_recommendation) return <Popover title="人工推荐记录" trigger="click" content={<p>{paper.human_recommendation.confirmed_at} · 已确认推荐并交采编</p>}><Button size="small">查看推荐记录</Button></Popover>;
  return <>
    <Button size="small" onClick={()=>{setOpen(true);setResult(null);setError('');}}>{paper.pdf_imported ? '确认推荐并进入采编' : '关联 PDF'}</Button>
    <Modal title="关联主文与人工推荐" open={open} onCancel={close} footer={null}>
      <p><strong>{paper.title}</strong></p><p>仅关联 PDF 会校验并保存主文。确认推荐并启动采编会自动调用模型完成制作链，产生费用。</p>
      {!busy && !result && !error && <Space wrap>{!paper.pdf_imported && <Button onClick={()=>confirm('', false)}>仅关联 PDF</Button>}<Button type="primary" onClick={()=>confirm('', true)}>确认推荐并启动采编（会调用模型）</Button></Space>}
      {busy && <Space><Spin size="small" /><span>正在匹配并校验 PDF…</span></Space>}
      {error && <Alert type="error" showIcon title="关联未完成" description={error} />}
      {!busy && result && <>
        {result.status === 'imported' ? <><Alert type="success" showIcon title={startProduction ? '人工推荐已确认，已交后台采编' : 'PDF 已关联'} description={result.filename} /><Space wrap style={{marginTop:16}}>{startProduction ? <Button href="/console?view=workspace&stage=production">查看稿件制作</Button> : <Button type="primary" onClick={()=>confirm('', true)}>确认推荐并启动采编（会调用模型）</Button>}<Button onClick={close}>关闭</Button></Space></> : <>
          <Alert type="warning" showIcon title={result.status === 'choose_file' ? '发现多个匹配文件，请选择' : result.status === 'validation_failed' ? '文件未通过主文校验' : '尚未找到对应论文的 PDF'} description="系统已检查下列目录。请确认下载完成；每目录匹配最近50个 PDF，多个匹配文件需选择。" />
          {(result.directories || [result.directory]).map(path=><p key={path}><Typography.Text copyable>{path}</Typography.Text></p>)}{result.warnings?.map(warning=><Alert key={warning} type="warning" showIcon title={warning} />)}
          {!!result.candidates?.length && <Radio.Group value={fileId} onChange={event=>setFileId(event.target.value)}><Space orientation="vertical">{result.candidates.map(file=><Radio key={file.file_id} value={file.file_id} disabled={!file.matches}>{file.filename}{file.directory && <small style={{display:'block'}}>{file.directory}</small>}{!file.matches && '（与本篇主文不匹配）'}</Radio>)}</Space></Radio.Group>}
          <Space style={{marginTop:16}}><Button onClick={()=>confirm()}>重新匹配</Button>{result.status === 'choose_file' && <Button type="primary" disabled={!fileId} onClick={()=>confirm(fileId)}>确认所选文件</Button>}</Space>
        </>}
      </>}
      {!busy && error && <Button style={{marginTop:12}} onClick={()=>confirm()}>重试关联</Button>}
    </Modal>
  </>;
}
