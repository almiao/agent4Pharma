import test from 'node:test';
import assert from 'node:assert/strict';
import {executionInputPreview,executionNodes,executionStream,publicTraceText,traceRoots} from '../../frontend/src/execution-model.js';
import {fieldDescriptions} from '../../frontend/src/execution-labels.js';

const event=(id,span,status,extra={})=>({id,subject_ref:'s',operation:'anything',status,created_at:'2026-11-03T00:00:00Z',execution:{trace_id:'trace',span_id:span,parent_span_id:'',kind:'tool'},...extra});

test('arbitrary tools merge only by explicit identity and preserve inputs outputs and original keys',()=>{
  const rows=executionNodes([
    event('1','a','started',{input_json:'{"unknown_parameter":3}'}),
    event('2','b','started'),
    event('3','a','succeeded',{output_json:'{"original_result":4}'}),
  ]);
  assert.equal(rows.length,2);
  assert.deepEqual(rows[0].input,{unknown_parameter:3});
  assert.deepEqual(rows[0].output,{original_result:4});
  assert.equal(rows[1].status,'incomplete');
  assert.equal(fieldDescriptions.start_page,'起始页码');
  assert.equal(fieldDescriptions.unknown_parameter,undefined);
});

test('same span names in separate traces never merge or become each others children',()=>{
  const rows=executionNodes([event('1','shared','succeeded'),event('2','shared','succeeded',{execution:{trace_id:'other',span_id:'shared',kind:'model'}})]);
  assert.equal(rows.length,2);
  assert.equal(traceRoots(rows).length,2);
});

test('old MCP ID contract pairs calls but matching names and timestamps do not',()=>{
  const id='a'.repeat(32);
  const rows=executionNodes([
    {id:id+'-start',subject_ref:'s',operation:'read',status:'started'},
    {id:id+'-end',subject_ref:'s',operation:'read',status:'succeeded'},
    {id:'unknown1',operation:'same',status:'started'},
    {id:'unknown2',operation:'same',status:'succeeded'},
  ]);
  assert.equal(rows.length,3);
  assert.equal(rows[0].events.length,2);
  assert.ok(rows[0].legacy);
});

test('unknown parents and cycles remain inspectable without inventing completed states',()=>{
  const rows=executionNodes([
    event('a','a','started',{execution:{trace_id:'trace',span_id:'a',parent_span_id:'b',kind:'model'}}),
    event('b','b','started',{execution:{trace_id:'trace',span_id:'b',parent_span_id:'a',kind:'model'}}),
    event('c','c','started',{execution:{trace_id:'trace',span_id:'c',parent_span_id:'missing',kind:'mcp'}}),
  ]);
  assert.equal(traceRoots(rows).length,2);
  assert.ok(rows.every(row=>row.status==='incomplete'));
});

test('conversation previews use public text once and never walk hidden reasoning or replay state',()=>{
  assert.equal(publicTraceText({content:[{type:'reasoning',text:'hidden'},{type:'text',text:'公开说明'},{type:'tool-call',name:'read'},{type:'text',text:'下一步'}],source:{replayState:{text:'duplicate'}}}),'公开说明\n\n下一步');
  assert.equal(publicTraceText({reasoning:'secret',source:{content:'duplicate'}}),'');
  assert.equal(publicTraceText({final_response:'最终回复'}),'最终回复');
  assert.equal(publicTraceText({text:'文档正文'}),'文档正文');
  assert.equal(executionStream([{name:'model_call',kind:'model',status:'succeeded',input:{messages:[{text:'old-context-only'}]},output:{content:'当前回复'}}],{query:'old-context-only'}).length,0);
});

test('flat stream keeps separate host calls, filters descendants and searches translated names',()=>{
  const nodes=executionNodes([
    event('1','agent','started',{operation:'agent_run',execution:{trace_id:'trace',span_id:'agent',kind:'agent'}}),
    event('2','model','succeeded',{operation:'model_call',output_json:'{"content":[{"type":"text","text":"先读取原文"}]}',execution:{trace_id:'trace',span_id:'model',parent_span_id:'agent',kind:'model'}}),
    event('3','tool','failed',{operation:'read',input_json:'{"resource":"paper.pdf"}',execution:{trace_id:'trace',span_id:'tool',parent_span_id:'model',kind:'tool'}}),
    event('4','host','succeeded',{operation:'read',execution:{trace_id:'trace',span_id:'host',parent_span_id:'agent',kind:'mcp'}}),
    event('5','usage','succeeded',{operation:'token_usage'}),
  ]);
  assert.equal(executionStream(nodes).length,3);
  assert.deepEqual(executionStream(nodes,{failuresOnly:true}).map(n=>n.id),['trace:trace:tool']);
  assert.equal(executionStream(nodes,{kind:'mcp'}).length,1);
  assert.equal(executionStream(nodes,{includeHost:false}).length,2);
  assert.equal(executionStream(nodes,{includeHost:false,kind:'mcp'}).length,1);
  assert.equal(executionStream(nodes,{query:'原文'}).length,1);
  assert.equal(executionStream(nodes,{query:'paper.pdf'}).length,1);
  assert.equal(executionStream(nodes,{query:'读取资料',definitions:{read:'读取资料'}}).length,2);
  assert.equal(nodes.find(n=>n.name==='model_call').children.length,1);
  assert.match(executionInputPreview({resource:'paper.pdf',query:'a\nb',large:{nested:'hidden'}}),/resource: paper.pdf · query: a b/);
});

test('host and agent failures remain in the reading view even when routine host rows are folded',()=>{
  const nodes=executionNodes([event('h','h','failed',{execution:{trace_id:'t',span_id:'h',kind:'mcp'}}),event('a','a','failed',{execution:{trace_id:'t',span_id:'a',kind:'agent'}})]);
  assert.equal(executionStream(nodes,{includeHost:false,failuresOnly:true}).length,2);
});
