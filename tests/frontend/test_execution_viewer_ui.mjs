import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath,pathToFileURL} from 'node:url';

test('reading view exposes public replies and failures, keeps raw payloads lazy and escapes tool text',async()=>{
  const require=createRequire(new URL('../../frontend/package.json',import.meta.url));
  const {createServer}=await import(pathToFileURL(require.resolve('vite')).href);
  const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
  const server=await createServer({root:fileURLToPath(new URL('../../frontend',import.meta.url)),configFile:false,optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true,hmr:false},appType:'custom'});
  try {
    const {default:Viewer}=await server.ssrLoadModule('/src/execution-viewer.jsx');
    const event=(id,kind,operation,status,output)=>({id,operation,status,output_json:JSON.stringify(output),execution:{trace_id:'trace',span_id:id,kind}});
    const events=[
      event('m','model','model_call','succeeded',{content:[{type:'text',text:'已读正文，接下来核对实验。'},{type:'reasoning',text:'DO_NOT_SHOW_REASONING'}],source:{text:'DO_NOT_DUPLICATE'}}),
      event('h','mcp','host_success','succeeded',{text:'HOST_PAYLOAD_LAZY'}),
      event('f','tool','read','failed',{content:[{type:'text',text:'<script>alert(1)</script>'}]}),
    ];
    const html=renderToStaticMarkup(React.createElement(Viewer,{events,agent:{status:'succeeded',model:'test-model'}}));
    assert.ok(html.includes('已读正文，接下来核对实验。'));
    assert.ok(!html.includes('DO_NOT_SHOW_REASONING') && !html.includes('DO_NOT_DUPLICATE'));
    assert.ok(!html.includes('HOST_PAYLOAD_LAZY') && !html.includes('host_success'));
    assert.ok(html.includes('&lt;script&gt;alert(1)&lt;/script&gt;') && !html.includes('<script>'));
    assert.ok(html.includes('异常 1') && html.includes('执行类型') && html.includes('调用树'));
    assert.ok(html.includes('原始键和值') && !html.includes('execution-children'));
    const {default:Text}=await server.ssrLoadModule('/src/execution-text.jsx');
    const prose=renderToStaticMarkup(React.createElement(Text,{text:'## 已完成\n\n**重点**与`原始字段`\n\n- [原文](https://example.com/paper)\n- [危险](javascript:alert(1))\n\n<script>unsafe</script>\n\n```json\n{"a":1}\n```'}));
    assert.ok(prose.includes('<strong>重点</strong>') && prose.includes('<ul>') && prose.includes('<pre'));
    assert.ok(prose.includes('href="https://example.com/paper"') && !prose.includes('href="javascript:'));
    assert.ok(prose.includes('&lt;script&gt;') && !prose.includes('<script>'));
    const link=renderToStaticMarkup(React.createElement(Text,{text:'[论文](https://example.com/S0003-4967(24)01547-4/fulltext)'}));
    assert.ok(link.includes('href="https://example.com/S0003-4967(24)01547-4/fulltext"'));
  } finally {await server.close();}
});
