import test from 'node:test';
import assert from 'node:assert/strict';
import { stageRows, comparisonPool, localStatus } from '../../frontend/src/intelligence-node-model.js';
import { compareSelectionRows, comparisonLabel } from '../../frontend/src/selection-paper-view.js';

test('精选默认先显示建议下载，显式本地排序仍按名次，人工标记不覆盖模型结论', () => {
  const deferred={detail:{local_rank:1,can_download:false}};
  const recommended={detail:{local_rank:5,can_download:true}};
  assert.ok(compareSelectionRows(recommended,deferred,'default') < 0);
  assert.ok(compareSelectionRows(recommended,deferred,'local-rank') > 0);
  assert.equal(comparisonLabel({comparison_result:{selected_ids:[]}}, {manual_selected_at:'now'}, {verdict:'exclude'}), '不推荐');
});

test('采集和初筛不使用后续终判状态，失败与直送分别呈现', () => {
  const rows = [{id:'a',displayStatus:'正式推荐',state:'selected',route:'analyze',detail:{availability:'abstract_only'}},
    {id:'b',state:'awaiting_triage',error:'timeout'},
    {id:'c',state:'selected',route:'analyze',triage_method:'semantic_passthrough'}];
  assert.deepEqual(stageRows(rows,'ingest').map(r=>r.displayStatus), ['仅摘要','已归档','已归档']);
  assert.deepEqual(stageRows(rows,'triage').map(r=>r.displayStatus), ['初筛通过','技术失败','直送后续（未粗筛）']);
  assert.equal(rows[0].displayStatus,'正式推荐');
});

test('整轮比较优先读取冻结输入，不受后续全文状态影响；合法空池保持空', () => {
  const items=[{record_id:'a',local_rank:1,state:'complete'}, {record_id:'b',local_rank:2}, {record_id:'c',local_rank:11}];
  assert.deepEqual(comparisonPool({items,comparison_record_ids:['b']}).map(i=>i.record_id),['b']);
  assert.deepEqual(comparisonPool({items,comparison_record_ids:[]}),[]);
  assert.deepEqual(comparisonPool({items,review_pool_limit:10}).map(i=>i.record_id),['a','b']);
  assert.equal(localStatus(items[0]),'已排序');
  assert.equal(localStatus({state:'complete',local_route:'exclude'}),'本地未通过');
  assert.equal(localStatus({state:'local_failed'}),'技术失败');
});

test('全文执行列表排除摘要阶段，不删除全文失败和未知阶段的历史记录', () => {
  const rows = [
    {id:'abstract',detail:{stage:'abstract'}},
    {id:'nested',detail:{request:{stage:'abstract'}}},
    {id:'failed',displayStatus:'技术失败',detail:{stage:'fulltext'}},
    {id:'legacy',detail:{}},
  ];
  assert.deepEqual(stageRows(rows,'fulltext').map(r=>r.id), ['failed','legacy']);
});
