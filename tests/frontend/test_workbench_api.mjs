import test from 'node:test';
import assert from 'node:assert/strict';
import {createWorkbenchApi} from '../../frontend/src/workbench-api.js';

test('concurrent reads coalesce; completed and failed requests remain refreshable',async()=>{
  const calls=[];
  const {api}=createWorkbenchApi('test',(path)=>new Promise(resolve=>calls.push({path,resolve})));
  const first=api('/a'),same=api('/a'),other=api('/b');
  assert.equal(first,same); assert.equal(calls.length,2);
  calls[0].resolve({ok:true,json:async()=>({value:1})});
  calls[1].resolve({ok:false,status:403,json:async()=>({error:'denied'})});
  assert.deepEqual(await first,{value:1}); await assert.rejects(other,/denied/);
  const next=api('/a'),retry=api('/b'); assert.equal(calls.length,4);
  calls[2].resolve({ok:true,json:async()=>({value:2})});
  calls[3].resolve({ok:true,json:async()=>({})});
  assert.deepEqual(await next,{value:2}); await retry;
});

test('a mutation isolates reads made before, during and after it, and never coalesces commands',async()=>{
  const calls=[];
  const {api,post}=createWorkbenchApi('test',(path,options)=>new Promise(resolve=>calls.push({path,options,resolve})));
  const old=api('/a'),command=post('/save',{value:1}),during=api('/a');
  assert.equal(calls.length,3); assert.equal(calls[1].options.method,'POST');
  assert.equal(calls[1].options.headers['X-Workbench-Token'],'test');
  calls[1].resolve({ok:true,json:async()=>({saved:true})}); await command;
  const current=api('/a'); assert.equal(calls.length,4);
  for(const call of [calls[0],calls[2],calls[3]]) call.resolve({ok:true,json:async()=>({})});
  await Promise.all([old,during,current]);
});
