import test from 'node:test';
import assert from 'node:assert/strict';
import { initialEditorial, saveRevision, queueEditorial, approveRelease, simulatePublication } from './editorial-sample.js';

test('manual changes create an immutable child with no inherited approval', () => {
  const original = initialEditorial();
  const next = saveRevision(original, 'v2', '新标题', original.versions[1].body, '优化标题');
  assert.equal(original.versions.length, 2);
  assert.equal(next.versions[2].parent, 'v2');
  assert.equal(next.versions[2].review, '待核验');
  assert.equal(next.versions[1].review, '样例核验通过');
  assert.throws(() => saveRevision(original, 'v2', '', '正文', '修改'));
});

test('approval freezes full release package and retries preserve it', () => {
  const state = initialEditorial();
  assert.throws(() => approveRelease(state, 'v2', false));
  const approved = approveRelease(state, 'v2', true);
  assert.equal(approved.releases[0].package.layout, 'article-layout-v1');
  assert.deepEqual(approved.releases[0].package.references, ['paper-v1']);
  assert.equal(approved.releases[0].status, '待发布');
  const failed = simulatePublication(approved, 'release-v2', false);
  const success = simulatePublication(failed, 'release-v2', true);
  assert.deepEqual(success.releases[0].package, approved.releases[0].package);
  assert.equal(success.releases[0].attempts.length, 2);
  assert.throws(() => simulatePublication(success, 'release-v2', true));
  assert.throws(() => approveRelease(approved, 'v2', true));
});

test('new revision cannot reuse approval; pending work blocks approval and sending', () => {
  const approved = approveRelease(initialEditorial(), 'v2', true);
  const revised = saveRevision(approved, 'v2', '新标题', approved.versions[1].body, '改标题');
  assert.throws(() => simulatePublication(revised, 'release-v2', true));
  assert.throws(() => approveRelease(revised, 'v2', true));
  assert.equal(revised.releases.length, 1);
  const pending = queueEditorial(revised, 'v3', '事实核验', '核验标题');
  assert.throws(() => approveRelease(pending, 'v3', true));
  assert.throws(() => approveRelease({ ...revised, buffer: { base: 'v3' } }, 'v3', true));
  assert.throws(() => approveRelease({ ...revised, comments: [{ version: 'v3', resolved: false }] }, 'v3', true));
  const newApproval = approveRelease(revised, 'v3', true);
  assert.equal(newApproval.releases[1].approval.factCheck, '人工核对确认');
  assert.equal(newApproval.versions[2].review, '待核验');
});
test('jobs freeze version, unresolved feedback and configuration without implying execution', () => {
  const state = initialEditorial();
  state.comments.push({ id: 'C-1', version: 'v2', text: '收紧措辞', resolved: false });
  const next = queueEditorial(state, 'v2', 'Agent 修订', '提高可读性');
  state.config.tools.pop(); state.config.skill = 'changed'; state.comments[0].text = 'changed';
  assert.equal(next.jobs[0].status, '待执行');
  assert.equal(next.jobs[0].comments[0].text, '收紧措辞');
  assert.ok(next.jobs[0].config.tools.includes('submit_draft'));
  assert.notEqual(next.jobs[0].config.skill, 'changed');
  assert.throws(() => queueEditorial(next, 'v2', 'Agent 修订', '重复'));
  assert.throws(() => queueEditorial(state, 'v2', 'Agent 修订', '缺少提交工具'));
});
