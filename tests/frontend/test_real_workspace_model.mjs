import test from 'node:test';
import assert from 'node:assert/strict';
import { canonicalStage, realStages, groupStories, visibleStories } from '../../frontend/src/real-workspace-model.js';

test('seven business nodes merge old fulltext deep links into paper selection', () => {
  assert.equal(realStages.length, 7);
  assert.equal(realStages.find(([id])=>id==='selection')[1], '论文精选');
  assert.ok(!realStages.some(([id])=>id==='fulltext'));
  assert.equal(canonicalStage('fulltext'),'selection');
  assert.equal(canonicalStage('rank'),'rank');
  assert.equal(canonicalStage(null),'overview');
});

test('missing reads remain unknown and approved delivery is separate from human review', () => {
  assert.equal(groupStories({ stories: [] }, null), null);
  const production = { stories: [{ id: 's' }], tasks: [] };
  const packages = { items: [{ story_id: 's', valid: true, approval: null, exported: false }] };
  assert.equal(visibleStories(groupStories(production, packages), 'human').length, 1);
  packages.items[0].approval = { id: 'approval' };
  assert.equal(visibleStories(groupStories(production, packages), 'delivery').length, 1);
  packages.items[0].exported = true;
  assert.equal(groupStories(production, packages)[0].stage, 'complete');
  assert.equal(visibleStories(groupStories(production, packages), 'delivery', true).length, 1);
  packages.items[0].valid = false;
  assert.equal(groupStories(production, packages)[0].stage, 'human');
  assert.equal(groupStories(production, packages)[0].statusLabel, '等待修订 / 核验');
  packages.items[0].valid = true;
  packages.items.push({ story_id: 's', valid: true, approval: null, exported: false });
  assert.equal(groupStories(production, packages)[0].stage, 'human', 'new layout must be reviewed again');
  production.stories[0].status = 'cancelled';
  assert.equal(visibleStories(groupStories(production, packages), 'production').length, 0);
});


test('commissioned stories retain research ancestors and searchable original paper titles', () => {
  const production={stories:[{id:'story'}],tasks:[
    {id:'root',phase:'paper-understanding'},
    {id:'research',parent_task_id:'root'},
    {id:'pitch',parent_task_id:'research',result_ref:'story'},
    {id:'write',parent_task_id:'pitch',story_id:'story'},
    {id:'unrelated'},
  ],paper_flows:[{story_ids:['story'],paper_titles:['Original paper title']}]};
  const story=groupStories(production,{items:[]})[0];
  assert.deepEqual(story.tasks.map(t=>t.id),['root','research','pitch','write']);
  assert.deepEqual(story.paperTitles,['Original paper title']);
});
