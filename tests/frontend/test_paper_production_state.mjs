import test from 'node:test';
import assert from 'node:assert/strict';
import {paperProductionState,latestPaperFlow} from '../../frontend/src/paper-production-state.js';
import {paperProcessingFailure} from '../../frontend/src/paper-processing-failure.js';

test('a remake supersedes stale continuation failures only for the same explicit recommendation',()=>{
  const paper={human_recommendation:{id:'h'},continuation_status:'failed',continuation_task_id:'old',continuation_error:'旧容量限制'};
  const production={paper_flows:[
    {root_task_id:'old',candidate_ids:['h'],leaf_task_ids:['old']},
    {root_task_id:'new',candidate_ids:['h'],leaf_task_ids:['new']},
    {root_task_id:'other',candidate_ids:['other-h'],leaf_task_ids:['other']},
  ],tasks:[
    {id:'old',created_at:'2026-11-02T00:00:00Z',status:'blocked',phase:'topic-selection'},
    {id:'new',created_at:'2026-11-04T00:00:00Z',status:'queued',phase:'paper-understanding'},
    {id:'other',created_at:'2026-11-05T00:00:00Z',status:'failed'},
  ]};
  const state=paperProductionState(paper,{production});
  assert.equal(state.task.id,'new');
  assert.match(state.label,/论文理解 · 待执行/);
  assert.equal(state.failed,false);
  assert.equal(new URL(state.href,'http://localhost').searchParams.get('task'),'new');
  assert.equal(paperProcessingFailure(paper,{production}),null);
  production.tasks[1].status='failed';production.tasks[1].error='新的真实失败';
  assert.equal(paperProcessingFailure(paper,{production}).message,'新的真实失败');
  assert.equal(paper.continuation_error,'旧容量限制');
});

test('ambiguous root chronology never guesses latest from array order or titles',()=>{
  const production={paper_flows:[{root_task_id:'a',candidate_ids:['h']},{root_task_id:'b',candidate_ids:['h']}],tasks:[{id:'a'},{id:'b'}]};
  assert.equal(latestPaperFlow('h',production),null);
  for(const task of production.tasks) task.created_at='2026-11-04T00:00:00Z';
  assert.equal(latestPaperFlow('h',production),null);
});
