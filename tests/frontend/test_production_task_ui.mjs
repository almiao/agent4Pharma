import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath,pathToFileURL} from 'node:url';

test('actual task controls and uncommissioned paper stage/savepoint navigation render without execution',async()=>{
  const require=createRequire(new URL('../../frontend/package.json',import.meta.url));
  const {createServer}=await import(pathToFileURL(require.resolve('vite')).href);
  const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
  const server=await createServer({root:fileURLToPath(new URL('../../frontend',import.meta.url)),configFile:false,optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true,hmr:false},appType:'custom'});
  try {
    const {default:Controls}=await server.ssrLoadModule('/src/production-task-controls.jsx');
    const {default:TaskDetail}=await server.ssrLoadModule('/src/production-task-detail.jsx');
    const {PaperFlowTasks}=await server.ssrLoadModule('/src/production-paper-flows.jsx');
    const {default:Board}=await server.ssrLoadModule('/src/production-board.jsx');
    const {default:Outputs,OutputContent}=await server.ssrLoadModule('/src/production-outputs.jsx');
    const {ReaderReview}=await server.ssrLoadModule('/src/production-task-document.jsx');
    const {productionCollections}=await server.ssrLoadModule('/src/production-board-model.js');
    const post=()=>assert.fail('render must never authorize or execute models');
    const render=(component,props)=>renderToStaticMarkup(React.createElement(component,props));
    const task={id:'pitch',status:'blocked',phase:'topic-selection'};
    const usageHtml=render(TaskDetail,{task:{...task,limits:{timeout_seconds:10,max_reads:4},usage:{attempts:[{attempt:1,requests:2,total_tokens:20}]}}});
    assert.ok(usageHtml.includes('2 次用量回报'));
    assert.ok(usageHtml.includes('费用未知'));
    const costHtml=render(TaskDetail,{task:{...task,limits:{timeout_seconds:10,max_reads:4},cost:{amount:'0.439611',partial:true,source:'https://api-docs.deepseek.com/zh-cn/quick_start/pricing/',note:'按当前价估算',pricing_version:'deepseek-2026-11-05'}}});
    assert.ok(costHtml.includes('约 ¥0.4396（部分）') && costHtml.includes('计价依据'));
    assert.ok(render(Controls,{task,post}).includes('重试此阶段'));
    assert.ok(render(Controls,{task,post}).includes('停止'));
    assert.ok(render(Controls,{task:{...task,status:'running'},post}).includes('暂停'));
    assert.ok(render(Controls,{task:{...task,status:'paused'},post}).includes('继续'));
    assert.equal(render(Controls,{task,post,hasSuccessor:true}),'');
    assert.equal(render(Controls,{task:{...task,status:'succeeded'},post}),'');
    const flow={root_task_id:'root',paper_titles:['Exact original paper'],candidate_ids:['c'],task_ids:['root','pitch'],leaf_task_ids:['pitch'],story_ids:[],savepoint_task_ids:['root']};
    const tasks=[{id:'root',phase:'paper-understanding',status:'succeeded'},task];
    const html=render(Board,{collections:productionCollections({paper_flows:[flow],tasks},[]),section:'preparation',query:'original',setQuery:post,navigate:post});
    assert.ok(html.includes('Exact original paper') && html.includes('已保存 1 个阶段'));
    assert.ok(html.includes('主编选题') && html.includes('查看并处理'));
    assert.ok(html.includes('当前环节') && html.includes('处理状态'));
    assert.ok(!html.includes('选择正式候选并排队立项'));
    assert.ok(html.includes('全部产出'));
    const outputHtml=render(Outputs,{tasks:[{id:'writer',result_ref:'draft'}],index:{items:[{id:'draft',kind:'draft',version:'稿件 v1',created_at:'2026-11-05T01:02:03Z'}],intermediates:[{id:'research',task_id:'writer',kind:'writing_research',version:1}]},api:post});
    assert.ok(outputHtml.includes('作者研究底稿 v1') && outputHtml.includes('09:02:03'));
    assert.ok(render(OutputContent,{item:{kind:'draft',id:'old'},record:{artifact:{content:{id:'new'}}}}).includes('任务产物与所选版本不一致'));
    assert.equal(render(OutputContent,{item:null,record:{artifact:{}}}),'');
    const readingHtml=render(ReaderReview,{review:{submission:{decision:'changes_requested',summary:'具体段落存在理解障碍。',appeal_and_flow:'completed',language_naturalness:'completed',issues:[{id:'R-01',dimension:'appeal_and_flow',severity:'required',anchors:['准确原句'],problem:'问题描述',reader_impact:'影响理解',resolution:'处理要求'}]}}});
    assert.ok(readingHtml.includes('新闻吸引力与阅读推进：要求返修'));
    assert.ok(readingHtml.includes('中文自然度：通过'));
    assert.ok(!readingHtml.includes('已评审'));
    const nav=render(PaperFlowTasks,{flow,tasks,selected:'pitch',navigate:post});
    assert.ok(nav.includes('aria-label="论文理解 · 完成 · 尝试 0 · 已保存 · 费用未知"'));
    assert.ok(nav.includes('stage-succeeded') && nav.includes('stage-blocked'));
    assert.ok(!nav.includes('<small>'));
    assert.ok(nav.includes('aria-pressed="true"'));
  } finally {await server.close();}
});


test('author checkpoint is visible before a draft and references remain escaped', async()=>{
  const require=createRequire(new URL('../../frontend/package.json',import.meta.url));
  const {createServer}=await import(pathToFileURL(require.resolve('vite')).href);
  const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
  const server=await createServer({root:fileURLToPath(new URL('../../frontend',import.meta.url)),configFile:false,optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true,hmr:false},appType:'custom'});
  try {
    const {ProductionTaskOutput}=await server.ssrLoadModule('/src/production-task-document.jsx');
    const snapshot={version:2,brief_version:1,research:{question:'What changed?',answer:'A bounded answer <script>unsafe()</script>',findings:[{statement:'A measured result',evidence_id:'paper',source_span:{content_hash:'hash',start:0,end:5}}],background:[],gaps:[{question:'Does this cover patients?',handling:'qualify',reason:'Only the reported assay is described.'}],research_scope:'One bound paper; no external search needed.'}};
    const record={task:{status:'running'},writing_research:snapshot,writing_research_versions:[{...snapshot,version:1},snapshot],writing_research_excerpts:[{evidence_id:'paper',content_hash:'hash',start:0,end:5,text:'Exact source <b>not markup</b>'}],context_json:'{}'};
    const html=renderToStaticMarkup(React.createElement(ProductionTaskOutput,{record,post:()=>assert.fail('read only')}));
    for(const text of ['作者研究底稿','围绕选题的理解','论文事实与证据','未决问题与处置','限定表述','实际调查范围','历史底稿 v1','写作任务仍在进行','Exact source &lt;b&gt;']) assert.ok(html.includes(text),text);
    assert.ok(!html.includes('<script>') && !html.includes('背景调研结果'));
    const draft=renderToStaticMarkup(React.createElement(ProductionTaskOutput,{record:{...record,artifact:{kind:'draft',content:{number:1,brief_version:1,submission:{title:'Final article',body:'Article text'}}}}}));
    assert.ok(draft.includes('Article text') && draft.includes('作者研究底稿'));
    assert.ok(!draft.includes('写作任务仍在进行'));
    const {default:Remake}=await server.ssrLoadModule('/src/paper-remake.jsx');
    const paper={title:'Recommended paper',human_recommendation:{id:'candidate'},continuation_task_id:''};
    const renderAction=(data,selectedPaper=paper)=>renderToStaticMarkup(React.createElement(Remake,{paper:selectedPaper,data,api:()=>assert.fail('read only'),post:()=>assert.fail('no models')}));
    assert.ok(renderAction({production:{started_candidate_ids:[]}}).includes('启动采编 Recommended paper'));
    assert.ok(renderAction({production:null}).includes('制作状态未知 Recommended paper'));
    const legacyPaper={...paper,continuation_task_id:'old-callback'};
    const withoutHistory={production:{tasks:[],paper_flows:[],started_candidate_ids:['other']}};
    assert.ok(renderAction(withoutHistory,legacyPaper).includes('启动采编 Recommended paper'));
    assert.ok(renderAction({production:{...withoutHistory.production,started_candidate_ids:['candidate']}}).includes('重做 Recommended paper'));
    const existing={production:{started_candidate_ids:['candidate'],tasks:[{id:'root',phase:'topic-selection',status:'cancelled'}],paper_flows:[{root_task_id:'root',candidate_ids:['candidate'],task_ids:['root'],leaf_task_ids:['root']}]}};
    assert.ok(renderAction(existing).includes('重做 Recommended paper'));
    assert.ok(!renderAction(existing).includes('启动采编 Recommended paper'));
  } finally {await server.close();}
});


test('the execution rail preserves rework order and directly selects prior attempts',async()=>{
  const require=createRequire(new URL('../../frontend/package.json',import.meta.url));
  const {createServer}=await import(pathToFileURL(require.resolve('vite')).href);
  const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
  const server=await createServer({root:fileURLToPath(new URL('../../frontend',import.meta.url)),configFile:false,optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true,hmr:false},appType:'custom'});
  try {
    const {PaperFlowTasks}=await server.ssrLoadModule('/src/production-paper-flows.jsx');
    const tasks=[
      {id:'write',phase:'draft-writing',activity_label:'初稿撰写',status:'succeeded'},
      {id:'review',phase:'fact-check',status:'succeeded'},
      {id:'revision',phase:'draft-writing',activity_label:'稿件返修',status:'succeeded'},
      {id:'review-again',phase:'fact-check',status:'succeeded'},
      {id:'pub-old',phase:'layout-production',status:'blocked',attempt:1},
      {id:'pub-retry',parent_task_id:'pub-old',phase:'layout-production',status:'running',attempt:1},
    ];
    const flow={root_task_id:'root',task_ids:tasks.map(t=>t.id),paper_titles:['Paper'],candidate_ids:['candidate'],story_ids:[],savepoint_task_ids:[]};
    const render=selected=>renderToStaticMarkup(React.createElement(PaperFlowTasks,{flow,tasks,selected,navigate:()=>assert.fail('read only')}));
    const html=render('pub-retry');
    assert.deepEqual([...html.matchAll(/<span>([^<]+)<small class="stage-cost">/g)].map(match=>match[1]),
      ['初稿撰写','事实核验','稿件返修','事实核验','图文编排','图文编排']);
    assert.ok(!html.includes('<details'));
    assert.match(html,/aria-label="图文编排 · 运行中[^"]*" aria-pressed="true"/);
    const old=render('pub-old');
    assert.match(old,/aria-label="图文编排 · 业务阻断[^"]*" aria-pressed="true"/);
  } finally {await server.close();}
});

test('commissioned candidate archives old research without matching titles or hiding unresolved candidates',async()=>{
  const {productionCollections}=await import('../../frontend/src/production-board-model.js');
  const makeFlow=(id,ids)=>({root_task_id:id,task_ids:[id],leaf_task_ids:[id],story_ids:[],candidate_ids:ids,paper_titles:['Same paper title'],savepoint_task_ids:[]});
  const flows=[makeFlow('old',['same']),makeFlow('different',['other']),makeFlow('partial',['same','unresolved']),makeFlow('unknown',[])];
  const commissioned={...makeFlow('commissioned',['same']),story_ids:['story']};
  const production={tasks:[...flows.map(f=>({id:f.root_task_id,phase:'topic-selection',status:'blocked',created_at:'2026-11-01T00:00:00Z'})),{id:'commissioned',phase:'topic-selection',status:'succeeded',created_at:'2026-11-02T00:00:00Z'}],paper_flows:[...flows,commissioned]};
  const stories=[{id:'story',candidate_ids:['same'],brief:{title:'Current commissioned story'},paperTitles:['Same paper title'],stage:'human',status:'active',statusLabel:'待我审稿',tasks:[]}];
  const collections=productionCollections(production,stories);
  assert.deepEqual(collections.pending.map(r=>r.id),['different','partial','unknown']);
  const archived=collections.history.find(r=>r.id==='old');
  assert.ok(archived && !archived.attention && archived.status.includes('已立项'));
  assert.equal(archived.target.task,'old');
});


test('stage projection follows rework ancestry and revisions while retaining independent parallel branches',async()=>{
  const {productionTaskStages}=await import('../../frontend/src/production-board-model.js');
  const tasks=[
    {id:'write-1',phase:'draft-writing',story_id:'s',expected_revision:1,status:'succeeded'},
    {id:'review-1',phase:'fact-check',parent_task_id:'write-1',status:'succeeded'},
    {id:'write-2',phase:'draft-writing',story_id:'s',expected_revision:3,parent_task_id:'review-1',status:'succeeded'},
    {id:'review-2',phase:'fact-check',parent_task_id:'write-2',status:'queued'},
    {id:'publisher-old',phase:'layout-production',status:'blocked'},
    {id:'publisher-retry',phase:'layout-production',parent_task_id:'publisher-old',status:'running'},
    {id:'publisher-parallel',phase:'layout-production',status:'queued'},
    {id:'stale-legacy',role:'writer',story_id:'s',expected_revision:1,status:'failed'},
    {id:'current-legacy',role:'writer',story_id:'s',expected_revision:3,status:'queued'},
  ];
  const before=JSON.stringify(tasks),groups=productionTaskStages(tasks);
  assert.equal(groups.find(g=>g.phase==='draft-writing').primary.id,'write-2');
  assert.deepEqual(groups.find(g=>g.phase==='fact-check').history.map(t=>t.id),['review-1']);
  const publisher=groups.find(g=>g.phase==='layout-production');
  assert.equal(publisher.primary.id,'publisher-retry');
  assert.deepEqual(publisher.current.map(t=>t.id),['publisher-retry','publisher-parallel']);
  assert.deepEqual(publisher.history.map(t=>t.id),['publisher-old']);
  assert.deepEqual(groups.find(g=>g.phase==='writer').history.map(t=>t.id),['stale-legacy']);
  assert.equal(JSON.stringify(tasks),before);
});


test('task documents render exact input and outputs as readable safe documents', async()=>{
  const require=createRequire(new URL('../../frontend/package.json',import.meta.url));
  const {createServer}=await import(pathToFileURL(require.resolve('vite')).href);
  const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
  const server=await createServer({root:fileURLToPath(new URL('../../frontend',import.meta.url)),configFile:false,optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true,hmr:false},appType:'custom'});
  try {
    const {ProductionTaskInput,ProductionTaskOutput}=await server.ssrLoadModule('/src/production-task-document.jsx');
    const render=(component,record)=>renderToStaticMarkup(React.createElement(component,{record,post:()=>assert.fail('reading does not execute')}));
    const brief={title:'Frozen story',angle:'Evidence',boundary:'Bounded evidence'};
    const source={id:'paper',title:'Bound paper',text:'Exact source <script>bad()</script>',url:'javascript:bad()'};
    const context={story:{brief,brief_version:2,revision:4},evidence:[source],candidates:[{id:'c',summary:'Candidate summary',reason:'Frozen reason',evidence:[source]}]};
    const base={task:{id:'exact-task',status:'succeeded',model:'private-model'},context_json:JSON.stringify(context),reporting_excerpts:[]};
    const input=render(ProductionTaskInput,base);
    assert.ok(input.includes('<article class="task-document"'));
    assert.ok(input.includes('Frozen story') && input.includes('任务书 v2') && input.includes('Bounded evidence'));
    assert.ok(input.includes('资料（1）') && input.includes('Exact source &lt;script&gt;'));
    assert.ok(!input.includes('href="javascript:') && !input.includes('<script>'));
    assert.ok(input.includes('<summary>历史候选依据</summary>') && input.includes('Frozen reason'));
    const selected={...context,story:null,candidates:[{id:'human',summary:source.title,reason:'',evidence:[source],selection_basis:{news_hook:'A news lead',reason:'Relative download value',evidence_quote:'Original quote',institution_quote:'Institute'}}]};
    const selectedInput=render(ProductionTaskInput,{...base,context_json:JSON.stringify(selected)});
    assert.ok(selectedInput.includes('下载前入选依据') && selectedInput.includes('A news lead') && selectedInput.includes('Relative download value'));
    assert.ok(selectedInput.includes('<summary>原文与机构依据</summary>') && selectedInput.includes('Original quote'));
    assert.ok(!selectedInput.includes('<h4>Bound paper</h4>') && !selectedInput.includes('全文待核实') && !selectedInput.includes('人工确认推荐'));
    const draft={submission:{title:'Exact saved draft',body:'## Results\n\nA bounded result.\n\n<script>bad()</script>',summary:'Saved summary',claims:[{claim:'A claim',quote:'Exact quote',evidence_id:'paper'}]},number:3,brief_version:2};
    const output=render(ProductionTaskOutput,{...base,artifact:{kind:'draft',content:draft}});
    assert.ok(output.includes('Exact saved draft') && output.includes('稿件 v3'));
    assert.ok(output.includes('<h3>Results</h3>') && output.includes('引用与依据（1）'));
    assert.ok(!output.includes('exact-task') && !output.includes('private-model') && !output.includes('<script>'));
    const decision=render(ProductionTaskOutput,{...base,artifact:{kind:'decision',content:{brief,decision:'commission',reason:'Exact editorial reason'}}});
    assert.ok(decision.includes('选题决定与任务书') && decision.includes('Exact editorial reason'));
    assert.ok(decision.indexOf('报道角度') < decision.indexOf('立项依据'));
    assert.ok(decision.includes('<summary>立项依据</summary>') && !decision.includes('<details open'));
    const review=render(ProductionTaskOutput,{...base,artifact:{kind:'review',content:{submission:{decision:'changes_requested',summary:'Actual review',issues:[{id:'i',severity:'required',location:'Results',reason:'Needs a baseline',resolution:'Add the bound baseline',draft_quote:'Old statement'}]}}}});
    assert.ok(review.includes('要求返修') && review.includes('Add the bound baseline') && review.includes('Old statement'));
    const missing=render(ProductionTaskOutput,{...base,artifact:null,context_json:JSON.stringify({...context,draft})});
    assert.ok(missing.includes('未读取到此任务已提交的产物') && !missing.includes('Exact saved draft'));
    const invalid=render(ProductionTaskInput,{...base,context_json:'not json'});
    assert.ok(invalid.includes('role="alert"'));
  } finally {await server.close();}
});


test('workspace chrome keeps scope controls and a semantic title without duplicate heading bands',async()=>{
  const require=createRequire(new URL('../../frontend/package.json',import.meta.url));
  const {createServer}=await import(pathToFileURL(require.resolve('vite')).href);
  const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
  const server=await createServer({root:fileURLToPath(new URL('../../frontend',import.meta.url)),configFile:false,optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true,hmr:false},appType:'custom'});
  const previous=globalThis.location;
  try {
    const {default:Workspace}=await server.ssrLoadModule('/src/real-workspace.jsx');
    const render=stage=>{
      globalThis.location=new URL('http://localhost/console?view=workspace&stage='+stage);
      return renderToStaticMarkup(React.createElement(Workspace,{api:()=>assert.fail('SSR is read only'),post:()=>assert.fail('SSR is read only')}));
    };
    const production=render('production');
    assert.ok(production.includes('<h1 class="workspace-page-title">稿件制作</h1>'));
    assert.ok(production.includes('aria-label="刷新当前视图"'));
    assert.ok(production.includes('稿件制作视图') && production.includes('当前任务') && production.includes('已结束'));
    assert.ok(!production.includes('批次情报与选题制作') && !production.includes('真实数据 · 制作范围'));
    assert.ok(!production.includes('class="trace-heading"') && !production.includes('aria-label="情报批次"'));
    assert.ok(render('selection').includes('aria-label="情报批次"'));
    assert.ok(!render('human').includes('aria-label="情报批次"'));
  } finally {
    if(previous===undefined)delete globalThis.location;else globalThis.location=previous;
    await server.close();
  }
});


test('dependency actions honor server capabilities and never start during rendering',async()=>{
  const require=createRequire(new URL('../../frontend/package.json',import.meta.url));
  const {createServer}=await import(pathToFileURL(require.resolve('vite')).href);
  const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
  const server=await createServer({root:fileURLToPath(new URL('../../frontend',import.meta.url)),configFile:false,optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true,hmr:false},appType:'custom'});
  try {
    const {DependencyServiceActions}=await server.ssrLoadModule('/src/background-activity.jsx');
    const render=item=>renderToStaticMarkup(React.createElement(DependencyServiceActions,{item,available:true,busy:'',onAction:()=>assert.fail('read only')}));
    const ready=render({id:'mineru',actions:['start','restart']}).replace(/\s/g,'');
    assert.ok(ready.includes('启动') && ready.includes('重启'));
    assert.ok(!render({id:'model',actions:[]}).includes('<button'));
    assert.ok(!render({id:'mineru',actions:['start'],operation:{status:'running'}}).includes('<button'));
    assert.ok(!render({id:'decider',actions:[],control_note:'external'}).includes('<button'));
  } finally {await server.close();}
});


test('reader report exposes both dimensions, exact anchors and escaped reading impact', async()=>{
  const require=createRequire(new URL('../../frontend/package.json',import.meta.url));
  const {createServer}=await import(pathToFileURL(require.resolve('vite')).href);
  const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
  const server=await createServer({root:fileURLToPath(new URL('../../frontend',import.meta.url)),configFile:false,optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true,hmr:false},appType:'custom'});
  try {
    const {ProductionTaskOutput}=await server.ssrLoadModule('/src/production-task-document.jsx');
    const report={observation:{news_understood:'Understood news',reason_to_read:'Real reason',first_obstacle:'Obstacle'},submission:{decision:'changes_requested',summary:'Specific reading gaps',appeal_and_flow:'completed',language_naturalness:'completed',issues:[{id:'R1',dimension:'language_naturalness',severity:'required',anchors:['Actual <script> text'],problem:'Abstract subject obscures the action',reader_impact:'Reader cannot follow the action',resolution:'Name the known action',example_revision:'Known actor did the action'}],closed_issue_ids:[]}};
    const html=renderToStaticMarkup(React.createElement(ProductionTaskOutput,{record:{task:{phase:'reading-review',status:'succeeded'},context_json:'{}',artifact:{kind:'reader_review',content:report}},post:()=>assert.fail('read only')}));
    for(const text of ['阅读评审报告','新闻吸引力与阅读推进','中文自然度','阅读影响','Actual &lt;script&gt; text','首次阅读观察']) assert.ok(html.includes(text),text);
    assert.ok(!html.includes('<script>') && !html.includes('事实核验通过'));
  } finally { await server.close(); }
});
