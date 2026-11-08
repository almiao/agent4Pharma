import test from 'node:test';
import assert from 'node:assert/strict';
import { productionCollections, currentProductionRows } from '../../frontend/src/production-board-model.js';
import { groupStories } from '../../frontend/src/real-workspace-model.js';

test('missing reads stay unknown; research, commissioned and unrelated tasks do not duplicate', () => {
  assert.equal(productionCollections(null, []), null);
  assert.equal(productionCollections({}, null), null);
  const production = { stories: [{ id: 'story', status: 'active' }], tasks: [
    { id: 'root', phase: 'paper-understanding', status: 'succeeded', result_ref: 'research' },
    { id: 'pitch', parent_task_id: 'root', phase: 'topic-selection', status: 'succeeded', result_ref: 'story' },
    { id: 'write', story_id: 'story', parent_task_id: 'pitch', phase: 'draft-writing', status: 'queued' },
    { id: 'orphan', role: 'editor', status: 'failed' },
  ], paper_flows: [{ root_task_id: 'root', task_ids: ['root', 'pitch', 'write'], leaf_task_ids: ['write'],
    story_ids: ['story'], paper_titles: ['Original paper'], candidate_ids: ['candidate'], savepoint_task_ids: ['root', 'pitch'] }] };
  const result = productionCollections(production, groupStories(production, { items: [] }));
  assert.equal(result.pending.length, 0);
  assert.equal(result.active.length, 1);
  assert.equal(result.active[0].phase, '初稿撰写');
  assert.equal(result.active[0].status, '待执行');
  assert.equal(result.active[0].saved, 2);
  assert.match(result.active[0].search, /Original paper/);
  assert.ok(!JSON.stringify(result).includes('orphan'));
  assert.ok(!('unlinked' in result));
});

test('a retry supersedes an old failure while paused research remains actionable before commissioning', () => {
  const production = { tasks: [
    { id: 'root', status: 'failed', phase: 'paper-understanding' },
    { id: 'retry', parent_task_id: 'root', status: 'paused', phase: 'paper-understanding' },
  ], paper_flows: [{ root_task_id: 'root', task_ids: ['root', 'retry'], leaf_task_ids: ['retry'],
    story_ids: [], paper_titles: ['Paper'], candidate_ids: [], savepoint_task_ids: [] }] };
  const result = productionCollections(production, []);
  assert.equal(result.pending[0].status, '已暂停');
  assert.equal(result.pending[0].attention, true);
  assert.equal(result.history.length, 0);
  assert.deepEqual(result.pending[0].target, { story: '', task: 'retry' });
});

test('archived stories and ended uncommissioned flows retain exact records, not fabricated story decisions', () => {
  const production = { tasks: [{ id: 'root', status: 'succeeded', phase: 'topic-selection' }],
    paper_flows: [{ root_task_id: 'root', task_ids: ['root'], leaf_task_ids: ['root'], story_ids: [],
      paper_titles: ['Uncommissioned'], candidate_ids: [], savepoint_task_ids: [] }] };
  const stories = [{ id: 's', brief: { title: 'Deferred' }, paperTitles: [], status: 'deferred', stage: 'production', tasks: [] },
    { id: 'h', paperTitles: [], stage: 'human', status: 'active', statusLabel: '待我审稿', tasks: [] }];
  const result = productionCollections(production, stories);
  assert.equal(result.active.length, 0);
  assert.equal(result.pending.length, 0);
  assert.deepEqual(result.history.map(row => row.status), ['已完成', '选题已暂停', '已移交审稿']);
  assert.deepEqual(result.history.map(row => row.target.story), ['', 's', 'h']);
});

test('old revision failures cannot block a new revision, and parallel actionable leaves open first', () => {
  const old = { id: 'old', story_id: 's', expected_revision: 1, phase: 'draft-writing', status: 'failed' };
  const done = { id: 'done', story_id: 's', expected_revision: 2, phase: 'draft-writing', status: 'succeeded' };
  const queued = { id: 'queued', story_id: 's', expected_revision: 2, phase: 'fact-check', status: 'queued' };
  const production = { tasks: [old, done, queued], paper_flows: [] };
  const result = productionCollections(production, [{ id: 's', revision: 2, status: 'active', stage: 'production',
    paperTitles: [], tasks: [old, done, queued] }]);
  assert.equal(result.active[0].attention, false);
  assert.ok(!result.active[0].status.includes('失败'));
  assert.equal(result.active[0].target.task, 'queued');
});


test('current badge and default collection contain only associated production objects',()=>{
  const collections={active:[{id:'story'}],pending:[{id:'paper'}],history:[{id:'past'}]};
  assert.equal(currentProductionRows(null),null);
  assert.deepEqual(currentProductionRows(collections).map(row=>row.id),['story','paper']);
  assert.equal(currentProductionRows(collections).length,2);
  assert.equal(collections.history.length,1);
});

test('production history opens its actual last production stage and labels handoff separately',()=>{
  const tasks=[{id:'write',phase:'draft-writing',status:'succeeded',result_ref:'draft'},{id:'package',parent_task_id:'write',phase:'layout-production',status:'succeeded',result_ref:'package'}];
  const collections=productionCollections({tasks,paper_flows:[]},[{id:'story',paperTitles:[],brief:{title:'Paper'},tasks,stage:'human',status:'active'}]);
  const row=collections.history[0];
  assert.equal(row.phase,'图文编排');
  assert.equal(row.status,'已移交审稿');
  assert.deepEqual(row.target,{story:'story',task:'package'});
});

test('stopping the last stage ends the production row without cancelling the story or losing saved work',()=>{
  const tasks=[{id:'write',story_id:'s',expected_revision:2,phase:'draft-writing',status:'succeeded',result_ref:'draft'},
    {id:'review',story_id:'s',expected_revision:2,parent_task_id:'write',phase:'fact-check',status:'cancelled'}];
  const story={id:'s',paperTitles:[],revision:2,status:'active',stage:'production',tasks};
  const collections=productionCollections({tasks,paper_flows:[]},[story]);
  assert.equal(collections.active.length,0);
  assert.equal(currentProductionRows(collections).length,0);
  assert.equal(collections.history[0].status,'已停止');
  assert.equal(collections.history[0].saved,1);
  assert.equal(story.status,'active');
  const retry={id:'retry',story_id:'s',expected_revision:2,parent_task_id:'review',phase:'fact-check',status:'queued'};
  const resumed=productionCollections({tasks:[...tasks,retry],paper_flows:[]},[{...story,tasks:[...tasks,retry]}]);
  assert.equal(resumed.active[0].target.task,'retry');
  assert.equal(resumed.history.length,0);
});

test('a stopped branch cannot hide another queued or blocked production branch',()=>{
  for (const status of ['queued','blocked','paused']) {
    const tasks=[{id:'stop',story_id:'s',expected_revision:1,status:'cancelled'},
      {id:'other',story_id:'s',expected_revision:1,status}];
    const collections=productionCollections({tasks,paper_flows:[]},[{id:'s',paperTitles:[],revision:1,status:'active',stage:'production',tasks}]);
    assert.equal(collections.active.length,1);
    assert.equal(collections.history.length,0);
  }
});

test('a fresh remake stays current even when the same paper already has a completed story',()=>{
  const tasks=[{id:'old',status:'blocked',phase:'topic-selection',created_at:'2026-11-01T00:00:00Z'},
    {id:'commissioned',status:'succeeded',phase:'topic-selection',created_at:'2026-11-02T00:00:00Z'},
    {id:'fresh',status:'queued',phase:'paper-understanding',created_at:'2026-11-04T00:00:00Z'}];
  const flow=(root,story_ids=[])=>({root_task_id:root,candidate_ids:['h'],story_ids,task_ids:[root],leaf_task_ids:[root],paper_titles:['Paper'],savepoint_task_ids:[]});
  const collections=productionCollections({tasks,paper_flows:[flow('old'),flow('commissioned',['s']),flow('fresh')]},
    [{id:'s',candidate_ids:['h'],paperTitles:[],tasks:[],status:'completed',stage:'human'}]);
  assert.deepEqual(collections.pending.map(row=>row.id),['fresh']);
  assert.equal(collections.history.find(row=>row.id==='old').status,'已立项 · 历史研究');
});


test('reader revision limit preserves required revision instead of inventing manual editing',()=>{
  const production={stories:[{id:'s',revision:4,status:'deferred',brief:{title:'Needs editor'}}],tasks:[
    {id:'reader',role:'reviewer',phase:'reading-review',story_id:'s',status:'succeeded',result_ref:'reader-review-reader',expected_revision:2}
  ],paper_flows:[]};
  const result=productionCollections(production,groupStories(production,{items:[]}));
  assert.equal(result.active.length,1);
  assert.equal(result.history.length,0);
  assert.equal(result.active[0].status,'要求返修 · 自动修订已停止');
  assert.equal(result.active[0].target.task,'reader');
  assert.equal(result.active[0].attention,true);
});
