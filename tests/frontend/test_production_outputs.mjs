import test from 'node:test';
import assert from 'node:assert/strict';
import {outputTasks,productionOutputs} from '../../frontend/src/production-outputs-model.js';

test('all saved versions stay visible in the exact remake flow, including stopped tasks',()=>{
  const tasks=[{id:'root',result_ref:'s'},{id:'writer',story_id:'s',parent_task_id:'root',status:'cancelled',result_ref:'d1'},
    {id:'reader',story_id:'s',parent_task_id:'writer',result_ref:'r1'},{id:'other',story_id:'other',result_ref:'d2'}];
  const production={tasks,paper_flows:[{story_ids:['s'],task_ids:['root','writer','reader']},{story_ids:['other'],task_ids:['other']}]};
  const scoped=outputTasks(production,{taskId:'reader'});
  const index={items:[{id:'d1',kind:'draft',created_at:'2026-11-05T01:00:00Z'},{id:'r1',kind:'reader_review'},
    {id:'d2',kind:'draft'}],decisions:[{task_id:'root',decision:{decision:'commission'},created_at:'2026-11-05T00:20:00Z'}],intermediates:[
    {id:'v1',task_id:'writer',kind:'writing_research',version:1},
    {id:'v2',task_id:'writer',kind:'writing_research',version:2},
    {id:'bg',task_id:'root',kind:'background_research',version:1,created_at:'2026-11-05T00:10:00Z'},
    {id:'obs',task_id:'reader',kind:'reader_observation',created_at:'2026-11-05T01:10:00Z'},
    {id:'unrelated',task_id:'other',kind:'background_research',version:1},
  ]};
  assert.deepEqual(productionOutputs(index,scoped).map(row=>row.id),['bg','decision:root','d1','obs','r1','v1','v2']);
  assert.deepEqual(outputTasks({tasks},{storyId:'s'}).map(task=>task.id),['root','writer','reader']);
  assert.equal(productionOutputs(null,scoped),null);
  assert.deepEqual(productionOutputs({items:[],decisions:[],intermediates:[]},scoped),[]);
});
