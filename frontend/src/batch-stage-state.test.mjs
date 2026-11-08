import test from 'node:test';
import assert from 'node:assert/strict';
import { initialEditorial, queueEditorial, approveRelease, simulatePublication, saveRevision } from './editorial-sample.js';
import { stageCollection, editorialProgress, cumulativeHandoff } from './batch-stage-state.js';

test('current occupancy is distinct from cumulative reached collections', () => {
  const state = initialEditorial();
  assert.equal(stageCollection('production', state, 'current').length, 0);
  assert.equal(stageCollection('production', state, 'history').length, 1);
  assert.equal(stageCollection('human', state, 'current').length, 1);
  assert.equal(stageCollection('publication', state, 'history').length, 0);
  assert.equal(stageCollection('coarse', state, 'current').length, 1);
  assert.equal(stageCollection('fulltext', state, 'current').length, 3);
  const revision = queueEditorial(state, 'v2', 'Agent 修订', '调整风格');
  assert.equal(editorialProgress(revision).current, 'human');
  assert.equal(editorialProgress(revision).human, '等待修订 / 核验');
});
test('approval, publication and new revision move occupancy without erasing history', () => {
  const approved = approveRelease(initialEditorial(), 'v2', true);
  assert.equal(stageCollection('human', approved, 'current').length, 0);
  assert.equal(stageCollection('publication', approved, 'current').length, 1);
  assert.equal(stageCollection('human', approved, 'history').length, 1);
  const failed = simulatePublication(approved, 'release-v2', false);
  assert.equal(editorialProgress(failed).current, 'publication');
  const published = simulatePublication(failed, 'release-v2', true);
  assert.equal(stageCollection('publication', published, 'current').length, 0);
  assert.equal(stageCollection('publication', published, 'history').length, 1);
  const revised = saveRevision(published, 'v2', '新标题', published.versions[1].body, '修订');
  assert.equal(editorialProgress(revised).current, 'human');
  assert.equal(stageCollection('publication', revised, 'current').length, 0);
  assert.equal(cumulativeHandoff('publication', revised), '1 篇已批准');
});
