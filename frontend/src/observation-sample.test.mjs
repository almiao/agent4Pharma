import test from 'node:test';
import assert from 'node:assert/strict';
import { tasks, artifacts, edges } from './observation-sample.js';

test('every handoff consumes its upstream output and points to an existing task', () => {
  const seen = new Set();
  for (const task of tasks) {
    assert.ok(!seen.has(task.id));
    if (task.parent) assert.ok(seen.has(task.parent), 'parent precedes child');
    seen.add(task.id);
    for (const id of [...task.inputs, ...task.outputs]) assert.ok(artifacts[id]);
    for (const event of task.events) {
      if (event.next) assert.equal(tasks.find(t => t.id === event.next)?.parent, task.id);
      if (event.parentCall) assert.ok(task.events.some(e => e.id === event.parentCall && e.kind === 'model'));
    }
  }
  assert.equal(edges.length, tasks.length - 1);
  for (const edge of edges) assert.ok(edge.artifacts.length > 0 || edge.to === 'publish');
});
test('rejected submission creates no draft; correction stays inside same execution', () => {
  const write = tasks.find(t => t.id === 'write1');
  const rejected = write.events.find(e => e.status === 'rejected');
  assert.equal(rejected.output.draft_created, false);
  assert.equal(rejected.outputArtifact, undefined);
  assert.ok(write.events.findIndex(e => e.id === 'w1-submit-ok') > write.events.indexOf(rejected));
});
test('rework preserves v1 and the passing review consumes v2', () => {
  assert.equal(tasks.find(t => t.id === 'write2').parent, 'review1');
  assert.ok(tasks.find(t => t.id === 'write2').inputs.includes('draft1'));
  assert.ok(tasks.find(t => t.id === 'review2').inputs.includes('draft2'));
  assert.ok(!tasks.find(t => t.id === 'publish').inputs.includes('draft1'));
  for (const artifact of Object.values(artifacts)) {
    if (artifact.producer) assert.ok(tasks.find(t => t.id === artifact.producer).events.some(e => e.id === artifact.event));
  }
  assert.equal(tasks.at(-1).events.length, 0);
});

test('layout precedes human approval and publication has no automatic input before approval', () => {
  assert.equal(taskBy('layout').parent, 'review2');
  assert.equal(taskBy('human').parent, 'layout');
  assert.equal(taskBy('publish').parent, 'human');
  assert.deepEqual(taskBy('publish').inputs, []);
  assert.ok(taskBy('human').inputs.includes('release2'));
});
function taskBy(id) { return tasks.find(t => t.id === id); }
