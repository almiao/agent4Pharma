import test from 'node:test';
import assert from 'node:assert/strict';
import { summarizeActivity } from '../../frontend/src/activity-summary.js';

test('historical failures are not current background jobs', () => {
  const rows = [...Array.from({length:444}, (_, i) => ({id:i,status:'attention'})),
    {id:'paused',status:'paused'}, {id:'done',status:'complete'}];
  const summary = summarizeActivity(rows);
  assert.equal(summary.running,0);
  assert.equal(summary.current.length,1);
  assert.equal(summary.paused,1);
  assert.equal(summary.history.length,445);
});
test('actual running and queued jobs have independent counts', () => {
  const summary = summarizeActivity([{status:'running'},{status:'queued'},{status:'unknown'}]);
  assert.equal(summary.running,1);
  assert.equal(summary.queued,1);
  assert.equal(summary.uncertain,1);
});
