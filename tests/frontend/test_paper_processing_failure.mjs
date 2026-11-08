import test from 'node:test';
import assert from 'node:assert/strict';
import { paperProcessingFailure } from '../../frontend/src/paper-processing-failure.js';

test('a lost task reference is recovered only from the explicit manual recommendation chain', () => {
  const paper = {continuation_status:'failed',continuation_error:'自动处理失败：ValueError',human_recommendation:{id:'human-1'}};
  const data = {discovery:{selected:'batch-1'},production:{tasks:[
    {id:'research',phase:'paper-understanding',status:'succeeded'},
    {id:'writing',phase:'draft-writing',status:'queued',parent_task_id:'research',story_id:'story-1'},
    {id:'unrelated',phase:'draft-writing',status:'failed',error:'Other paper failed'},
  ]},artifacts:{task_links:[
    {task_id:'research',candidate_ids:['human-1'],current:true},
    {task_id:'writing',candidate_ids:['human-1'],current:true},
    {task_id:'unrelated',candidate_ids:['other'],current:true},
  ]}};
  const failure = paperProcessingFailure(paper,data);
  assert.match(failure.message,/ValueError.*未保存/);
  assert.equal(failure.task.id,'writing');
  assert.equal(failure.phase,'初稿撰写');
  assert.match(failure.state,/尚未开始/);
  const params = new URL(failure.href,'http://localhost').searchParams;
  assert.equal(params.get('task'),'writing');
  assert.equal(params.get('story'),'story-1');
  assert.equal(params.get('batch'),'batch-1');
  assert.equal(paper.continuation_task_id,undefined);
});

test('an explicit failing task wins and missing or ambiguous links never guess by title', () => {
  const paper = {continuation_status:'failed',continuation_task_id:'t',continuation_error:'明确的失败说明',human_recommendation:{id:'h'}};
  const data = {production:{tasks:[{id:'t',role:'writer',phase:'paper-understanding',status:'failed'}]}};
  const failure = paperProcessingFailure(paper,data);
  assert.equal(failure.message,'明确的失败说明');
  assert.equal(new URL(failure.href,'http://localhost').searchParams.get('task'),'t');
  assert.equal(paperProcessingFailure({...paper,continuation_task_id:''},data).href,'');
  data.production.tasks.push({id:'t2'});
  data.artifacts={task_links:[{task_id:'t',current:true,candidate_ids:['h']},{task_id:'t2',current:true,candidate_ids:['h']}]};
  assert.equal(paperProcessingFailure({...paper,continuation_task_id:''},data).href,'');
  assert.equal(paperProcessingFailure({continuation_status:'succeeded'},data),null);
});

test('a compact shortlist retains an explicit task link without loading all production tasks', () => {
  const failure=paperProcessingFailure({continuation_status:'failed',continuation_task_id:'exact-task',continuation_error:'PDF准备失败'});
  assert.equal(new URL(failure.href,'http://localhost').searchParams.get('task'),'exact-task');
  assert.equal(failure.state,'');
});
