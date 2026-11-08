import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath,pathToFileURL} from 'node:url';

test('background questions, sufficiency, versions and exact sources form a read-only document',async()=>{
  const require=createRequire(new URL('../../frontend/package.json',import.meta.url));
  const {createServer}=await import(pathToFileURL(require.resolve('vite')).href);
  const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
  const server=await createServer({root:fileURLToPath(new URL('../../frontend',import.meta.url)),configFile:false,optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true,hmr:false},appType:'custom'});
  try {
    const {ProductionTaskInput,ProductionTaskOutput}=await server.ssrLoadModule('/src/production-task-document.jsx');
    const {backgroundResearchMarkdown,downloadBackgroundResearch}=await server.ssrLoadModule('/src/background-research-report.jsx');
    const span={content_hash:'hash',start:0,end:5};
    const snapshot={task_id:'editor',phase:'topic-selection',version:2,created_at:'2026-11-04T10:00:00Z',research:{focus:'Why the chosen question needs this background.',sufficiency:'limited',assessment:'Enough if the article retains this limitation.',research_scope:'Only a search excerpt was read; no full paper.',questions:[{id:'q',question:'How should the evidence be understood?',purpose:'explanation',relevance:'Distinguish correlation from a causal conclusion.',status:'partial',answer:'There is supporting context but no causal demonstration.',handling:'qualify',assessment:'Use only to explain the limited association.',findings:[{kind:'background',statement:'A source-backed background statement.',evidence_id:'web',source_span:span,limitation:'Search snippet only.'}]}]}};
    const excerpts=[{evidence_id:'web',...span,text:'Exact source <script>not executable</script>'}];
    const sources=[{id:'web',title:'Original source',url:'https://example.org/source',read_scope:'web_search_snippet'}];
    const record={task:{id:'editor',status:'running'},context_json:'{}',background_research:[snapshot],background_research_versions:[{...snapshot,task_id:'prior-editor',version:1},snapshot],background_research_excerpts:excerpts,background_research_sources:sources};
    const render=record=>renderToStaticMarkup(React.createElement(ProductionTaskOutput,{record,post:()=>assert.fail('must not execute models')}));
    const html=render(record);
    for(const text of ['背景调研记录','为什么查','调研产出','处理判断','充分性判断','限定后可用','调研角色自评','部分回答','历史调研 v1','Original source','Exact source &lt;script&gt;','下载调研文档']) assert.ok(html.includes(text),text);
    assert.ok(!html.includes('<script>') && !html.includes('已完成调研'));
    assert.ok(html.includes('href="https://example.org/source"'));
    const downstream={...record,task:{id:'reviewer',status:'succeeded'},context_json:JSON.stringify({background_research:[snapshot]}),artifact:{kind:'review',content:{submission:{decision:'pass',summary:'准确的核验结论',issues:[]}}}};
    const review=render(downstream);
    assert.ok(review.includes('准确的核验结论'));
    assert.ok(!review.includes('背景调研记录'));
    assert.ok(renderToStaticMarkup(React.createElement(ProductionTaskInput,{record:downstream})).includes('上游背景调研'));
    const md=backgroundResearchMarkdown(snapshot,excerpts,sources);
    for(const text of [snapshot.research.focus,snapshot.research.assessment,snapshot.research.questions[0].relevance,excerpts[0].text,'hash','web_search_snippet']) assert.ok(md.includes(text),text);
    let downloadedBlob,clicked=false;
    const anchor={click(){clicked=true;}};
    const oldDocument=globalThis.document;
    const originalCreate=URL.createObjectURL,originalRevoke=URL.revokeObjectURL;
    try {
      globalThis.document={createElement:tag=>{assert.equal(tag,'a');return anchor;}};
      URL.createObjectURL=blob=>{downloadedBlob=blob;return 'blob:background-export';};
      URL.revokeObjectURL=()=>{};
      downloadBackgroundResearch(snapshot,excerpts,sources);
      assert.ok(clicked);
      assert.equal(anchor.download,'background-editor-v2.md');
      assert.equal(anchor.href,'blob:background-export');
      assert.equal(await downloadedBlob.text(),md);
    } finally {
      globalThis.document=oldDocument;
      URL.createObjectURL=originalCreate;
      URL.revokeObjectURL=originalRevoke;
    }
    const absent=render({task:{status:'succeeded'},context_json:'{}'});
    assert.ok(!absent.includes('背景调研记录'));
    const empty={...snapshot,research:{...snapshot.research,questions:[],sufficiency:'sufficient'}};
    assert.ok(render({...record,background_research:[empty],background_research_versions:[]}).includes('没有新增背景问题'));
  } finally {await server.close();}
});
