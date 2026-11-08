import assert from 'node:assert/strict';
import test from 'node:test';
import { tabCollection, filterCollection, workspaces } from '../../frontend/src/workspace-model.js';
import { consoleUrl } from '../../src/agent4pharma/entrypoints/web/static/console_routes.js';
import { storeCollection, restoreCollection } from '../../frontend/src/collection-scope.js';

const row = (id, status, extra = {}) => ({ id, kind: 'task', status, title: `对象 ${id}`, ...extra });
function model(metrics, dimensions = []) {
  return { metrics: new Map(metrics.map(metric => [metric.id, metric])), stages: [{ id: 'editorial', dimensions }] };
}

test('filter the complete collection before pagination; retain failure and blocked separately', () => {
  const rows = Array.from({ length: 250 }, (_, i) => row(String(i), i === 240 ? 'failed' : 'queued'));
  rows.push(row('blocked', 'blocked'));
  const result = tabCollection(model([{ id: 'content-tasks', rows }]), 'content', workspaces.content.tabs[0]);
  assert.equal(result.rows.length, 251);
  assert.equal(filterCollection(result.rows, { status: '技术失败' })[0].id, '240');
  assert.equal(filterCollection(result.rows, { status: '业务阻断' })[0].id, 'blocked');
  assert.equal(filterCollection(result.rows, { search: '240' }).length, 1);
});

test('unknown input never becomes a legitimate empty set', () => {
  const tab = workspaces.review.tabs[0];
  assert.equal(tabCollection(model([]), 'review', tab).unknown, true);
  const metrics = tab.ids.map(id => ({ id, rows: [] }));
  assert.equal(tabCollection(model(metrics), 'review', tab).unknown, false);
  metrics[0].rows = null;
  assert.equal(tabCollection(model(metrics), 'review', tab).unknown, true);
});

test('candidate processing status is owned by the decision partition', () => {
  const pending = { id: 'pending', label: '待处理', rows: [row('c1', 'stored', { kind: 'candidate' })] };
  const decided = { id: 'decided', label: '已有编辑决定', rows: [row('c2', 'stored', { kind: 'candidate' })] };
  const m = model([pending, decided], [{ title: '候选处理情况', metrics: [pending, decided] }]);
  const result = tabCollection(m, 'editorial', workspaces.editorial.tabs[0]);
  assert.deepEqual(result.rows.map(r => r.displayStatus), ['待处理', '已有编辑决定']);
});

test('keep current reports apart from history; package validity is not export status', () => {
  const metrics = [
    { id: 'review-changes', label: '修改', rows: [row('r1', 'changes_requested', { kind: 'review' })] },
    { id: 'review-blocked', rows: [] }, { id: 'reviews-pass', rows: [] },
    { id: 'reviews-history', rows: [row('old', 'pass', { kind: 'review' })] },
    { id: 'packages', rows: [row('p1', 'local_preview', { kind: 'package', detail: { validity: 'valid' } })] },
  ];
  const m = model(metrics);
  assert.deepEqual(tabCollection(m, 'review', workspaces.review.tabs[0]).rows.map(r => r.id), ['r1']);
  assert.deepEqual(tabCollection(m, 'review', workspaces.review.tabs[2]).rows.map(r => r.id), ['old']);
  assert.equal(tabCollection(m, 'publishing', workspaces.publishing.tabs[0]).rows[0].displayStatus, '有效');
});

test('collection links use the sole console and preserve exact empty IDs and frozen identity', () => {
  const empty = new URL(consoleUrl('/content?view=tasks&ids='), 'http://localhost');
  assert.equal(empty.pathname, '/console');
  assert.equal(empty.searchParams.get('stage'), 'production');
  assert.equal(empty.searchParams.get('role'), 'content');
  assert.equal(empty.searchParams.get('view'), 'workspace');
  assert.equal(empty.searchParams.get('work'), 'tasks');
  assert.equal(empty.searchParams.has('ids'), true);
  assert.equal(empty.searchParams.get('ids'), '');
  const historical = new URL(consoleUrl('/review?view=artifacts&artifact=r1&story=s1', 'reviews-history'), 'http://localhost');
  assert.equal(historical.searchParams.get('work'), 'history');
  assert.equal(historical.searchParams.get('object'), 'r1');
  assert.equal(historical.searchParams.get('story'), 's1');
  assert.equal(historical.searchParams.has('scope'), false);
});

test('coarse decisions and download attempts remain distinct and unknown stays unknown', () => {
  const triage = workspaces.intel.tabs.find(t => t.key === 'triage');
  const download = workspaces.intel.tabs.find(t => t.key === 'downloads');
  assert.equal(tabCollection(model([]), 'intel', triage).unknown, true);
  const data = { preselection: { decisions: [{ id: 'd1', route: 'exclude', input: { title: 'Title', source_id: 'src1' } }], downloads: [] } };
  assert.equal(tabCollection(model([]), 'intel', triage, data).rows[0].displayStatus, '不进入');
  assert.equal(tabCollection(model([]), 'intel', download, data).rows.length, 0);
  assert.equal(tabCollection(model([]), 'intel', download, data).unknown, false);
});

test('large collection links round-trip exact IDs without oversized request URLs', () => {
  const entries = new Map();
  const storage = { setItem: (key, value) => entries.set(key, value), getItem: key => entries.get(key) || null };
  const ids = Array.from({ length: 3000 }, (_, i) => 'record-long-identity-' + i);
  const url = storeCollection(new URL('http://localhost/console?stage=intel&ids=' + ids.join(',')), storage);
  assert.ok(url.href.length < 200);
  assert.deepEqual([...restoreCollection(url.searchParams, storage).ids], ids);
  entries.clear();
  const missing = restoreCollection(url.searchParams, storage);
  assert.equal(missing.missing, true);
  assert.equal(missing.ids.size, 0);
  const empty = restoreCollection(new URLSearchParams('ids='), storage);
  assert.equal(empty.missing, false);
  assert.equal(empty.ids.size, 0);
});


test('selection preserves rank and manual download work without promoting abstract decisions', () => {
  const tab = workspaces.intel.tabs.find(t => t.key === 'abstracts');
  const data = { preselection: { paper_selections: [{ items: [
    { id: 'run:r1', title: 'Paper one', state: 'manual_required', rank: 1, journal: 'Cell', todo: 'Authenticate' },
    { id: 'run:r2', title: 'Paper two', state: 'deferred', rank: 6 },
  ] }] } };
  const result = tabCollection(model([]), 'intel', tab, data);
  assert.equal(result.unknown, false);
  assert.deepEqual(result.rows.map(r => r.displayStatus), ['需人工处理', '暂不下载']);
  assert.equal(result.rows[0].rank, 1);
  assert.equal(result.rows[0].detail.todo, 'Authenticate');
  assert.equal(tabCollection(model([]), 'intel', tab, {}).unknown, true);
});


test('shortlist has a permanent entry and only contains download-eligible frozen papers', () => {
  const tab = workspaces.intel.tabs[0];
  assert.equal(tab.key, 'selection');
  assert.equal(tab.label, '精选论文');
  const input = { preselection: { paper_selections: [{ items: [
    { id: 'a', title: 'Pending', state: 'awaiting_abstract', can_download: false },
    { id: 'b', title: 'Passed', state: 'eligible', can_download: false },
    { id: 'c', title: 'Selected', state: 'shortlisted', rank: 1, can_download: true },
    { id: 'd', title: 'Deferred', state: 'deferred', rank: 6, can_download: false },
  ] }] } };
  assert.deepEqual(tabCollection(model([]), 'intel', tab, input).rows.map(row => row.id), ['c']);
  input.preselection.paper_selections[0].items = input.preselection.paper_selections[0].items.slice(0, 2);
  assert.equal(tabCollection(model([]), 'intel', tab, input).rows.length, 0);
  assert.equal(tabCollection(model([]), 'intel', tab, {}).unknown, true);
});


test('intelligence navigation separates paper workflow from evidence and diagnostics', async () => {
  const { intelligenceGroups, intelligenceGroup } = await import('../../frontend/src/workspace-model.js');
  const keys = intelligenceGroups.flatMap(group => group.tabs);
  assert.equal(new Set(keys).size, keys.length);
  assert.deepEqual([...keys].sort(), workspaces.intel.tabs.map(tab => tab.key).sort());
  assert.deepEqual(intelligenceGroup('selection').tabs, ['selection', 'abstracts']);
  assert.equal(intelligenceGroup('history').key, 'activity');
  assert.equal(intelligenceGroup('records').key, 'sources');
});
