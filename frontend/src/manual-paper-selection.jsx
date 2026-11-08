/** 人工加入精选只保存选择；不改变模型结论、不调用模型。 */
import React, { useState } from 'react';
import { Alert, Button, Space, Tag } from 'antd';

export default function ManualPaperSelection({ paper, run, post, refresh }) {
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [saved, setSaved] = useState(false);
  async function select() {
    setBusy(true); setError('');
    try {
      await post('/api/paper-selections/manual', {run_id:run.id, record_id:paper.record_id});
      setSaved(true); await refresh();
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }
  const ready = run.phase === 'fulltexts' && !run.paused && (!run.processing_stage || run.processing_stage === 'idle');
  return <Space orientation="vertical" size="small">
    {paper.manual_selected_at || saved ? <Tag color="success">人工已加入精选</Tag> : <Button size="small" loading={busy} disabled={!ready || !post} onClick={select}>加入精选</Button>}
    {!ready && <span>本轮筛选完成且未暂停后可加入</span>}
    {error && <Alert type="error" title={error}/>}
  </Space>;
}
