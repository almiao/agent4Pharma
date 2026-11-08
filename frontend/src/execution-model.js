/** 通用只读追踪投影：按明确span身份归并生命周期，保留未关联历史和缺失状态。 */
export function parseTrace(value) {
  if (typeof value!=='string') return value;
  if (!value) return undefined;
  try {return JSON.parse(value);} catch {return value;}
}

export function executionNodes(events=[]) {
  const nodes=new Map();
  for(const event of events) {
    const identity=event.execution;
    // 仅兼容本项目已核对的旧MCP记录ID契约，不按工具名/参数/时间配对。
    const legacy=event.id?.match(/^([a-f0-9]{32})-(start|end)$/);
    const id=identity ? `trace:${identity.trace_id}:${identity.span_id}` : legacy ? `legacy:${event.subject_ref}:${event.operation}:${legacy[1]}` : `event:${event.id}`;
    const parent=identity?.parent_span_id ? `trace:${identity.trace_id}:${identity.parent_span_id}` : '';
    const node=nodes.get(id) || {id,parent,kind:identity?.kind || (legacy?'mcp':'event'),name:event.operation,events:[],legacy:!identity};
    node.events.push(event);
    node.identity=identity || node.identity;
    if(event.operation!=='tool_result')node.name=event.operation;
    if(event.input_json)node.input=parseTrace(event.input_json);
    if(event.output_json)node.output=parseTrace(event.output_json);
    if(event.detail)node.detail=event.detail;
    if(event.status==='started' && !node.started)node.started=event.created_at;
    if(event.status!=='started')node.ended=event.created_at;
    node.status=event.status;
    node.duration=identity?.duration_ms ?? node.duration;
    nodes.set(id,node);
  }
  const rows=[...nodes.values()];
  const children=new Map();
  for(const node of rows) {
    if(node.parent && node.parent!==node.id) {
      if(!children.has(node.parent))children.set(node.parent,[]);
      children.get(node.parent).push(node);
    }
  }
  for(const node of rows) {
    if(node.status==='started')node.status='incomplete';
    if(node.duration==null && node.started && node.ended) {
      const duration=Date.parse(node.ended)-Date.parse(node.started);
      if(Number.isFinite(duration)&&duration>=0)node.duration=duration;
    }
    node.children=children.get(node.id) || [];
  }
  return rows;
}

export function traceRoots(nodes) {
  const ids=new Set(nodes.map(node=>node.id));
  const roots=nodes.filter(node=>!node.parent || !ids.has(node.parent) || node.parent===node.id);
  const visited=new Set();
  function mark(node) {if(visited.has(node.id))return;visited.add(node.id);node.children.forEach(mark);}
  roots.forEach(mark);
  for(const node of nodes)if(!visited.has(node.id)){roots.push(node);mark(node);}
  return roots;
}

/** 只提取公开文本块；不遍历source/replayState，也不显示隐藏推理。 */
export function publicTraceText(value) {
  if (typeof value === 'string') return value;
  if (!value || typeof value !== 'object') return '';
  if (typeof value.final_response === 'string') return value.final_response;
  if (typeof value.content === 'string') return value.content;
  if (Array.isArray(value.content)) return value.content
    .filter(block => ['text','output_text'].includes(block?.type) && typeof block.text === 'string')
    .map(block => block.text).join('\n\n');
  return typeof value.text === 'string' ? value.text : '';
}

/** 从实际参数生成短预览；仅展示，不修改名称、参数或关联身份。 */
export function executionInputPreview(input,definitions={}) {
  if (typeof input === 'string') return input.replace(/\s+/g,' ').slice(0,180);
  if (!input || typeof input !== 'object') return '';
  return Object.entries(input).filter(([,value])=>['string','number','boolean'].includes(typeof value))
    .slice(0,3).map(([key,value])=>{
      const text=String(value).replace(/\s+/g,' ');
      return `${definitions[key] || key}: ${/^[a-f0-9]{32,}$/i.test(text)?text.slice(0,10)+'…':text}`;
    }).join(' · ').slice(0,180);
}

/** 过滤为扁平事件流，每个span只出现一次；不改变原有调用树。 */
export function executionStream(nodes, {kind='',query='',failuresOnly=false,definitions={},includeHost=true}={}) {
  const search=query.trim().toLowerCase();
  return nodes.filter(node=>node.name!=='token_usage' && (node.kind!=='agent' || ['failed','blocked'].includes(node.status)))
    .filter(node=>includeHost || kind==='mcp' || node.kind!=='mcp' || ['failed','blocked'].includes(node.status))
    .filter(node=>(!kind || node.kind===kind) && (!failuresOnly || ['failed','blocked'].includes(node.status)))
    .filter(node=>!search || `${node.name} ${definitions[node.name] || definitions[node.name?.split('__').at(-1)] || ''} ${node.detail || ''} ${node.kind==='model'?'':JSON.stringify(node.input) || ''} ${publicTraceText(node.output)}`.toLowerCase().includes(search));
}
