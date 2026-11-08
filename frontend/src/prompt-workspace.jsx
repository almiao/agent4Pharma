/** 节点提示词配置：版本、草稿与发布操作均调用真实服务。 */
import React, { useEffect, useState } from 'react';
import { Alert, Button, Collapse, Descriptions, Form, Input, Modal, Select, Spin, Table, Tag, Tabs } from 'antd';
import './prompt-workspace.css';

const stateLabels = { builtin: '随包默认', published: '当前发布', draft: '草稿', archived: '历史版本' };
const versionLabel = value => `${value.version ? 'v' + value.version : '默认'} · ${stateLabels[value.status]}`;
export default function PromptWorkspace({ api, post }) {
  const [nodes, setNodes] = useState([]), [key, setKey] = useState(new URLSearchParams(location.search).get('node') || 'triage');
  const [selectedId, setSelectedId] = useState(''), [draft, setDraft] = useState({ instructions: '', examples: '', note: '' });
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [publishOpen, setPublishOpen] = useState(false), [modal, contextHolder] = Modal.useModal();
  const node = nodes.find(item => item.key === key) || nodes[0];
  const isStandard = node?.kind === 'standard';
  const resourceStandard = isStandard && node?.key !== 'screening_scope';
  const selectionPrompt = ['news_ranking', 'news_comparison'].includes(node?.key);
  const publicationEffect = selectionPrompt
    ? '按批次启动时采用此版本并建立新运行；指定已有运行继续使用冻结提示词。'
    : node?.key === 'audience'
    ? '新的制作链和筛选运行采用此版本，已有任务链沿用冻结规范。'
    : resourceStandard ? '新的制作链采用此版本，已有任务链沿用冻结规范。'
    : '新建任务采用此版本，已有任务继续使用冻结版本。';
  const category = isStandard ? 'standard' : 'prompt';
  const selected = node?.versions.find(item => item.id === selectedId);
  const current = node?.versions.find(item => item.id === node.current_id);
  const scopeVersion = nodes.find(item => item.key === 'screening_scope')?.versions.find(v => v.id === selected?.shared_scope_ref);
  const dirty = !!selected && ['instructions', 'examples', 'note'].some(field => draft[field] !== selected[field]);
  const valid = draft.instructions.trim().length >= 40 && draft.instructions.length <= 24000 && draft.examples.length <= 12000 && (!resourceStandard || draft.instructions.length + (draft.examples ? draft.examples.length + '\n\n参考示例：\n'.length : 0) <= 23000);
  function selectVersion(value) {
    setSelectedId(value.id);
    setDraft({ instructions: value.instructions, examples: value.examples, note: value.note });
  }
  async function load(preferredKey = key, preferredId = '') {
    const data = await api('/api/prompts');
    const item = data.items.find(item => item.key === preferredKey) || data.items[0];
    if (!item) throw new Error('提示词目录为空，请检查配置后重试');
    setNodes(data.items); setKey(item.key);
    selectVersion(item.versions.find(value => value.id === (preferredId || item.current_id)) || item.versions[0]);
  }
  async function action(operation) {
    setBusy(true); setError(''); setNotice('');
    try { await operation(); } catch (error) { setError(error.message); } finally { setBusy(false); }
  }
  async function canLeave() {
    return !dirty || await modal.confirm({ title: '放弃未保存的修改？', content: '当前编辑尚未保存为草稿。', okText: '放弃修改', cancelText: '继续编辑' });
  }
  useEffect(() => { action(() => load()); }, []);
  useEffect(() => {
    const warn = event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } };
    window.addEventListener('beforeunload', warn); return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);
  async function changeNode(item) {
    if (busy || item.key === node.key || !await canLeave()) return;
    setKey(item.key); selectVersion(item.versions.find(value => value.id === item.current_id)); setError(''); setNotice('');
    const url = new URL(location.href); url.searchParams.set('node', item.key); history.replaceState(null, '', url);
  }
  async function save() {
    await action(async () => {
      const result = await post(`/api/prompts/${node.key}`, draft);
      await load(node.key, result.id); setNotice(`草稿 v${result.version} 已保存，尚未发布。`);
    });
  }
  async function publish() {
    await action(async () => {
      await post(`/api/prompts/${node.key}/publish`, { version_id: selected.id, expected_current: node.current_id, expected_related: node.expected_related || {} });
      setPublishOpen(false); await load(node.key); setNotice(node.key === 'screening_scope' ? '共用标准已发布，资料预选已同步生成新版本；历史快照保留。' : `${isStandard ? '规范' : ''}已发布；${publicationEffect}`);
    });
  }
  return <section className="prompt-workspace">
    {contextHolder}
    <header className="business-heading"><h1>规范与提示词</h1><Button disabled={busy} onClick={async () => { if (await canLeave()) action(() => load()); }}>刷新版本</Button></header>
    {error && <Alert type="error" showIcon title="操作未完成" description={error} action={!nodes.length && <Button onClick={() => action(() => load())}>重试</Button>} />}
    {notice && <Alert type="success" showIcon title={notice} />}
    {!nodes.length && busy ? <Spin /> : <div className="prompt-layout">
      <div className="prompt-nodes"><Tabs activeKey={category} onChange={value => { const item = nodes.find(n => (n.kind === 'standard' ? 'standard' : 'prompt') === value); if (item) changeNode(item); }} items={[{ key: 'standard', label: '公共规范' }, { key: 'prompt', label: '节点提示词' }]} /><Table rowKey="key" size="middle" pagination={false} dataSource={nodes.filter(item => (item.kind === 'standard' ? 'standard' : 'prompt') === category)} rowClassName={item => item.key === node?.key ? 'selected-row' : ''} columns={[
        { title: isStandard ? '规范 / 用途' : '业务节点 / 提示词用途', key: 'node', render: (_, item) => <button className="object-title" disabled={busy} onClick={() => changeNode(item)} aria-current={item.key === node?.key ? 'true' : undefined}><strong>{item.title}</strong><span>{item.purpose}</span></button> },
        { title: '生效版本', key: 'version', render: (_, item) => { const version = item.versions.find(v => v.id === item.current_id); return <Tag color="green">{version?.version ? 'v' + version.version : '默认'}</Tag>; } },
      ]} /></div>
      {node && selected && <div className="prompt-editor" aria-busy={busy}>
        <div className="prompt-editor-heading"><h2>{node.title}</h2><Select aria-label={isStandard ? '查看规范版本' : '查看提示词版本'} value={selected.id} disabled={busy} options={node.versions.map(value => ({ value: value.id, label: versionLabel(value) }))} onChange={async id => { if (await canLeave()) { selectVersion(node.versions.find(value => value.id === id)); setNotice(''); } }} /></div>
        <Descriptions size="small" column={1} items={[{ key: 'model', label: '调用模型', children: node.model }, { key: 'inputs', label: isStandard ? '适用环节' : '任务输入', children: node.inputs }, ...(selected.shared_scope_ref ? [{ key: 'scope', label: '共用主题标准', children: <a href="/settings?node=screening_scope">{scopeVersion ? 'v' + scopeVersion.version : selected.shared_scope_ref} · AI × Biology/Pharma</a> }] : [])]} />
        {!!node.exposure?.length && <Collapse items={[{ key: 'exposure', label: '各环节如何使用', children: <Descriptions size="small" column={1} items={node.exposure.map(item => ({ key: item.stage, label: item.stage, children: item.usage }))} /> }]} />}
        <Form layout="vertical" onFinish={save}>
          <div className="prompt-actions"><Button htmlType="submit" type="primary" loading={busy} disabled={!valid}>保存为新草稿</Button><Button disabled={busy || dirty || selected.status !== 'draft'} onClick={() => setPublishOpen(true)}>对比并发布</Button><Tag>{dirty ? '有未保存修改' : stateLabels[selected.status]}</Tag></div>
          <Form.Item label={isStandard ? "规范正文" : selected.shared_scope_ref ? "阶段指令" : "提示词指令"} htmlFor="prompt-instructions" validateStatus={!valid ? 'error' : undefined} help={!valid ? (resourceStandard ? '正文需至少40字符，正文与示例合计最多23000字符；示例最多12000字符。' : '指令需为40–24000字符；示例最多12000字符。') : undefined}>
            <Input.TextArea id="prompt-instructions" rows={16} value={draft.instructions} disabled={busy} onChange={event => setDraft({ ...draft, instructions: event.target.value })} />
          </Form.Item>
          <Form.Item label="参考示例（可选）" htmlFor="prompt-examples"><Input.TextArea id="prompt-examples" rows={5} value={draft.examples} disabled={busy} onChange={event => setDraft({ ...draft, examples: event.target.value })} /></Form.Item>
          <Form.Item label="版本备注" htmlFor="prompt-note"><Input id="prompt-note" maxLength={1000} value={draft.note} disabled={busy} onChange={event => setDraft({ ...draft, note: event.target.value })} /></Form.Item>

        </Form>
        <Collapse items={[
          { key: 'effective', label: isStandard ? '此版本完整规范（已保存）' : '此版本完整提示词（已保存）', children: <pre className="prompt-effective">{selected.effective_instructions}</pre> },
          { key: 'compare', label: '与当前版本对比', children: <div className="prompt-comparison"><section><h3>当前生效</h3><pre>{current.instructions}{current.examples && '\n\n参考示例：\n' + current.examples}</pre></section><section><h3>当前编辑</h3><pre>{draft.instructions}{draft.examples && '\n\n参考示例：\n' + draft.examples}</pre></section></div> },
          { key: 'constraints', label: '程序约束与版本追溯', children: <><ul>{node.constraints.map(text => <li key={text}>{text}</li>)}</ul><Descriptions column={1} size="small" items={[{ key: 'id', label: '版本身份', children: selected.id }, { key: 'hash', label: '内容指纹', children: selected.content_hash }, { key: 'time', label: '保存时间', children: selected.created_at || '随包默认或历史准则未记录' }]} /></> },
        ]} />
      </div>}
    </div>}
    <Modal title={`发布${node?.title || ''}${isStandard ? '' : '提示词'}`} open={publishOpen} onCancel={() => !busy && setPublishOpen(false)} onOk={publish} confirmLoading={busy} okText="发布此草稿" cancelText="继续检查" width={960}>
      {node?.key === 'screening_scope' && <Alert type="info" title="同步更新资料预选" description="发布共用标准将同步生成资料预选新版本，并保留阶段指令。首次启用使用预选专用模板。" />}
      <p>发布 {selected && versionLabel(selected)} 后，{publicationEffect}本次不调用模型。</p>
      <div className="prompt-comparison"><section><h3>当前生效</h3><pre>{current?.instructions}{current?.examples && '\n\n参考示例：\n' + current.examples}</pre></section><section><h3>待发布草稿</h3><pre>{selected?.instructions}{selected?.examples && '\n\n参考示例：\n' + selected.examples}</pre></section></div>
      {error && <Alert type="error" title={error} />}
    </Modal>
  </section>;
}
