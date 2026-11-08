import test from 'node:test';
import assert from 'node:assert/strict';
import { loadWorkspaceData } from '../../frontend/src/real-workspace-data.js';

test('node reads omit unrelated screening history and retain exact historical ranking selection',async()=>{
  for(const node of ['ingest','triage','overview']) {
    const calls=[];
    await loadWorkspaceData(async path=>{calls.push(path);return {selected:'b'};},{intelligence:true,node,run:'r'});
    const path=calls.find(p=>p.startsWith('/api/rss-triage'));
    const params=new URL(path,'http://localhost').searchParams;
    assert.equal(params.get('batch'),'b');
    assert.equal(params.get('view'),node==='overview'?'rank':node);
    assert.equal(params.get('run'),'r');
  }
});

test('production reads lightweight artifact times and review status without screening', async () => {
  const calls = [];
  const result = await loadWorkspaceData(async path => {
    calls.push(path);
    if (!['/api/production?view=summary','/api/production/human-review','/api/production/artifact-summaries'].includes(path)) throw new Error('unexpected screening read');
    return {items:[]};
  }, {batch:'batch-1',intelligence:false});
  assert.deepEqual(calls.sort(), ['/api/production?view=summary','/api/production/human-review','/api/production/artifact-summaries'].sort());
  assert.deepEqual(result.errors,[]);
  assert.equal(result.data.discovery,undefined);
});

test('intelligence retains exact selected batch and starts its three follow-up reads together', async () => {
  const started = [];
  const releases = [];
  const loading = loadWorkspaceData(async path => {
    started.push(path);
    if (path === '/api/discovery-run') return {selected:'exact-batch'};
    if (/rss-triage|intelligence-tasks|candidates/.test(path)) return new Promise(resolve=>releases.push(resolve));
    return {};
  },{intelligence:true});
  // Wait for the first, finite request phase without timing assumptions.
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(releases.length,3);
  assert.deepEqual(started.filter(path=>path.includes('?batch=')), [
    '/api/rss-triage?batch=exact-batch','/api/intelligence-tasks?batch=exact-batch','/api/candidates?batch=exact-batch',
  ]);
  releases.forEach(resolve=>resolve({items:[]}));
  const result = await loading;
  assert.deepEqual(result.errors,[]);
});

test('failed reads remain null with errors instead of becoming empty successes', async () => {
  const result = await loadWorkspaceData(async path=>{
    if (path==='/api/production?view=summary') throw new Error('read failed');
    return {items:[]};
  });
  assert.equal(result.data.production,null);
  assert.deepEqual(result.errors,['production：read failed']);
});

test('migrated production records load versions and candidate decisions without the RSS pool', async () => {
  const calls=[];
  const result = await loadWorkspaceData(async path=>{calls.push(path);return {};}, {operations:true});
  assert.ok(calls.includes('/api/production/artifact-summaries'));
  assert.ok(calls.includes('/api/candidates'));
  assert.ok(!calls.some(path=>path.includes('rss-triage') || path.includes('discovery-run')));
  assert.deepEqual(result.errors,[]);
});

test('a migrated batch record list retains the exact batch without reading the RSS pool', async () => {
  const calls=[];
  await loadWorkspaceData(async path=>{calls.push(path);return {};}, {operations:true,recordBatch:'frozen-batch'});
  assert.ok(calls.includes('/api/candidates?batch=frozen-batch'));
  assert.ok(calls.includes('/api/discovery-run?batch=frozen-batch'));
  assert.ok(!calls.some(path=>path.includes('rss-triage')));
});

test('paper selection reads its bounded query and lightweight production status without the full pool', async () => {
  const calls=[];
  const page={selected:'b1',batches:['b1'],runs:[],paper_selections:[]};
  const result=await loadWorkspaceData(async path=>{calls.push(path);return page;},{selection:true,intelligence:true,operations:true,batch:'b1',run:'r1'});
  assert.deepEqual(calls,['/api/paper-selections?batch=b1&run=r1','/api/production/paper-status']);
  assert.deepEqual(result.data.discovery,{selected:'b1',batches:['b1']});
  assert.equal(result.data.preselection,page);
  assert.deepEqual(result.errors,[]);
});

test('selection failure stays unknown and does not fall back to a different run or full-pool query', async () => {
  const calls=[];
  const result=await loadWorkspaceData(async path=>{calls.push(path);throw new Error('wrong run');},{selection:true,batch:'b'});
  assert.equal(calls.length,2);
  assert.equal(result.data.preselection,null);
  assert.equal(result.data.discovery,null);
  assert.deepEqual(result.errors,['preselection：wrong run','production：wrong run']);
});

 test('ranking loads a single full ranking and its directory, without discovery articles or artifact bodies',async()=>{
  const calls=[];
  const page={selected:'b',batches:[{batch_id:'b'}],paper_selections:[],runs:[]};
  const result=await loadWorkspaceData(async path=>{calls.push(path);return page;},{node:'rank',intelligence:true,batch:'b',run:'historic'});
  assert.deepEqual(calls,['/api/rss-triage?view=rank&batch=b&run=historic','/api/production/paper-status']);
  assert.deepEqual(result.data.discovery,{selected:'b',batches:['b']});
 });
