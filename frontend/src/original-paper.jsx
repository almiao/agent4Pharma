/** 查看已导入、身份与哈希均通过校验的工作副本，不使用收件目录中的临时文件。 */
import React, { useState } from 'react';
import { Alert, Button } from 'antd';

export default function OriginalPaper({ paper }) {
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  async function open() {
    if (busy) return;
    const preview=window.open('about:blank','_blank');
    setBusy(true);setError('');
    try {
      const query=new URLSearchParams({run:paper.run_id,record:paper.record_id});
      const response=await fetch('/api/paper-downloads/file?'+query,{headers:{'X-Workbench-Token':document.querySelector('meta[name="workbench-token"]')?.content},signal:AbortSignal.timeout(20000)});
      if (!response.ok) throw new Error('原文文件不可用，请重新关联');
      const url=URL.createObjectURL(await response.blob());
      if (preview) {preview.opener=null;preview.location.href=url;}
      else {const link=document.createElement('a');link.href=url;link.download='paper.pdf';link.click();}
      setTimeout(()=>URL.revokeObjectURL(url),60000);
    } catch(err) {if(preview) preview.close();setError(err.message);}
    finally {setBusy(false);}
  }
  return <><Button size="small" type="link" loading={busy} onClick={open}>查看原文</Button>{error && <Alert type="error" title={error} />}</>;
}
