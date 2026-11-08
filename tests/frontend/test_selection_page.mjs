import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';

test('selection includes comparison and manual choices with explicit PDF association permission', async () => {
  const require = createRequire(new URL('../../frontend/package.json', import.meta.url));
  const {createServer} = await import(pathToFileURL(require.resolve('vite')).href);
  const React = require('react');
  const {renderToStaticMarkup} = require('react-dom/server');
  const root = fileURLToPath(new URL('../../frontend', import.meta.url));
  const server = await createServer({root,configFile:false,optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true,hmr:false},appType:'custom'});
  const previousLocation=globalThis.location, previousStorage=globalThis.sessionStorage;
  globalThis.location = new URL('http://localhost/console?view=workspace&stage=selection');
  globalThis.sessionStorage = {getItem:()=>null};
  try {
    const {default:Nodes} = await server.ssrLoadModule('/src/intelligence-nodes.jsx');
    const row={id:'run:r1',run_id:'run',record_id:'r1',rank:1,title:'Example paper',doi:'10.1234/example',can_download:true,state:'shortlisted',continuation_status:'failed',continuation_task_id:'task-exact',continuation_error:'PDF preparation failed'};
    const deferred={id:'run:r2',run_id:'run',record_id:'r2',local_rank:2,title:'Deferred paper',can_download:false,can_associate_pdf:true,state:'deferred'};
    const outside={id:'run:r3',record_id:'r3',title:'Outside comparison',can_download:false};
    const data={discovery:{selected:'batch'},preselection:{selected:'batch',runs:[],paper_selections:[{id:'run',phase:'fulltexts',comparison_record_ids:['r1','r2'],comparison_result:{selected_ids:['r1'],cards:[{record_id:'r1',verdict:'recommend',reason:'Strong evidence'},{record_id:'r2',verdict:'defer',reason:'Needs validation',evidence_quote:'Frozen evidence'}]},items:[row,deferred,outside]}]}};
    data.production={tasks:[],paper_flows:[]};
    const html=renderToStaticMarkup(React.createElement(Nodes,{nodeId:'selection',data,model:null,api:()=>assert.fail('render must not fetch history'),post:()=>assert.fail('render must not execute'),refresh:()=>{},navigate:()=>{},workLink:x=>x}));
    assert.equal((html.match(/role="tab"/g) || []).length,0);
    assert.ok(html.includes('比较结论') && html.includes('新闻点与取舍理由'));
    assert.ok(html.includes('Deferred paper') && html.includes('Needs validation') && html.includes('Frozen evidence'));
    assert.ok(!html.includes('Outside comparison'));
    assert.equal((html.match(/>关联 PDF</g) || []).length,2);
    assert.ok(html.includes('复制DOI'));
    assert.ok(html.indexOf('Example paper') < html.indexOf('Deferred paper'));
    assert.ok(!html.includes('推荐清单') && !html.includes('处理记录') && !html.includes('论文精选运行'));
    assert.ok(html.includes('task=task-exact'));
    assert.ok(html.includes('PDF preparation failed'));
    for (const suffix of ['&panel=comparison','&panel=history']) {
      globalThis.location=new URL('http://localhost/console?view=workspace&stage=selection'+suffix);
      const legacy=renderToStaticMarkup(React.createElement(Nodes,{nodeId:'selection',data,model:null,api:()=>assert.fail('render must not fetch'),refresh:()=>{},workLink:x=>x}));
      assert.ok(legacy.includes('Deferred paper') && legacy.includes('比较结论'));
    }
    data.preselection.paper_selections[0].phase='commercial_review';
    data.preselection.paper_selections[0].processing_stage='idle';
    data.preselection.paper_selections[0].progress={completed:139,total:139,failed:10};
    data.preselection.paper_selections[0].comparison_result=null;
    data.preselection.paper_selections[0].comparison_error='Comparison timeout';
    const failed=renderToStaticMarkup(React.createElement(Nodes,{nodeId:'selection',data,model:null,post:()=>assert.fail('render must not retry'),refresh:()=>{},workLink:x=>x}));
    assert.ok(failed.includes('本轮筛选失败') && failed.includes('Comparison timeout'));
    assert.ok(!failed.includes('筛选进行中'));
    assert.ok(failed.includes('建议下载 未形成'));
    assert.ok(!failed.includes('Strong evidence'));
    assert.ok(failed.includes('重试本轮比较（调用模型）'));
    for (const item of data.preselection.paper_selections[0].items) {
      item.local_route='analyze'; item.local_margin=1; item.local_rank=1;
    }
    const ranking=renderToStaticMarkup(React.createElement(Nodes,{nodeId:'rank',data,model:null,refresh:()=>{},workLink:x=>x}));
    assert.ok(ranking.includes('本地判别与排序已完成'));
    assert.ok(!ranking.includes('Comparison timeout'));


  } finally {
    globalThis.location=previousLocation;globalThis.sessionStorage=previousStorage;
    await server.close();
  }
});
