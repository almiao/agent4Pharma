import test from 'node:test';
import assert from 'node:assert/strict';
import { consoleUrl, workspaceRoute } from '../../src/agent4pharma/entrypoints/web/static/console_routes.js';

const params = path => new URL(consoleUrl(path), 'http://localhost').searchParams;
test('retired task links open the exact uncommissioned task in the new workspace', () => {
  const p = params('/console?view=legacy&stage=editorial&work=tasks&object=failed-task&batch=b1');
  assert.equal(p.get('view'), 'workspace');
  assert.equal(p.get('stage'), 'production');
  assert.equal(p.get('task'), 'failed-task');
  assert.equal(p.get('batch'), 'b1');
  assert.equal(p.has('object'), false);
});
test('historical artifacts keep identity, explicit empty collection and multiple source filters', () => {
  const p = params('/review?view=artifacts&artifact=report-v1&story=s1&ids=&source=a&source=b');
  assert.equal(p.get('stage'), 'human');
  assert.equal(p.get('role'), 'review');
  assert.equal(p.get('section'), 'records');
  assert.equal(p.get('object'), 'report-v1');
  assert.equal(p.get('story'), 's1');
  assert.equal(p.get('ids'), '');
  assert.deepEqual(p.getAll('source'), ['a','b']);
});
test('canonical task and record links are stable when copied, refreshed or followed twice', () => {
  for (const path of ['/console?view=legacy','/editorial?view=candidates&candidate=c1','/content?view=tasks&task=t1&story=s1','/console?stage=intel&work=downloads&batch=b1','/publishing?view=artifacts&artifact=p1']) {
    const url = consoleUrl(path);
    assert.equal(consoleUrl(url), url);
    assert.equal(new URL(url,'http://localhost').searchParams.get('view'), 'workspace');
  }
  assert.equal(workspaceRoute('/console?stage=fulltext').stage, 'selection');
});
test('configured sources and prompts remain dedicated pages', () => {
  assert.equal(consoleUrl('/sources?return=%2Fconsole'), '/sources?return=%2Fconsole');
  assert.equal(consoleUrl('/settings?node=triage'), '/settings?node=triage');
});

test('explicit legacy batch scope is retained instead of silently showing the global pool', () => {
  const route = workspaceRoute('/editorial?view=candidates&batch=exact-batch');
  assert.equal(route.scope, 'batch');
  assert.equal(route.batch, 'exact-batch');
  assert.equal(workspaceRoute('/editorial?view=candidates&batch=b&scope=all').scope, 'all');
});


test('production list filters survive exact detail, stage switching, refresh, and return',async()=>{
  const {productionDetailRoute,productionReturnRoute,productionReturnLabel}=await import('../../frontend/src/production-navigation.js');
  const path=route=>'/console?'+new URLSearchParams({view:'workspace',...route});
  for(const section of ['', 'preparation', 'active', 'history']) {
    for(const target of [{story:'story-exact',task:'review-exact'},{story:'',task:'research-exact'}]) {
      const list=workspaceRoute('/console?stage=production&section='+section+'&q=exact&phase=fact-check&state=queued&page=2');
      const detail=workspaceRoute(path({...list,...productionDetailRoute({target},section)}));
      assert.equal(detail.task,target.task);
      const switched=workspaceRoute(path({...detail,task:'next-exact'}));
      const returned=workspaceRoute(path({...switched,...productionReturnRoute(switched)}));
      assert.equal(returned.section,section);
      assert.equal(returned.q,'exact');
      assert.equal(returned.phase,'fact-check');
      assert.equal(returned.state,'queued');
      assert.equal(returned.page,'2');
      assert.equal(returned.story,'');
      assert.equal(returned.task,'');
      assert.equal(productionReturnLabel(detail),({'':'当前任务',preparation:'待立项',active:'制作中',history:'已结束'})[section]);
    }
  }
  assert.equal(productionReturnRoute(workspaceRoute('/console?stage=production&task=legacy')).section,'');
});
