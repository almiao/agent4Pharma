import test from 'node:test';
import assert from 'node:assert/strict';
import { papers, collection, reviewInput, reviewOutput, outcome } from './batch-observation-sample.js';
test('all batch counts resolve to exact collections, failures remain distinct',()=>{
 assert.equal(papers.length,14);
 assert.equal(papers.filter(p=>p.coarse==='passed').length,12);
 assert.equal(papers.filter(p=>p.coarse==='excluded').length,1);
 assert.equal(papers.filter(p=>p.coarse==='failed').length,1);
 assert.equal(collection('rank').length,12);
 assert.equal(collection('review').length,10);
 assert.equal(collection('fulltext').length,5);
 assert.equal(collection('fulltext').filter(p=>outcome(p,'fulltext')==='待人工下载').length,3);
 assert.equal(collection('production').length,1);
});
test('one commercial run compares exactly the frozen top ten and selects a subset',()=>{
 const input=reviewInput(),output=reviewOutput();
 assert.equal(new Set(input.map(p=>p.paper_id)).size,10);
 assert.equal(output.decisions.length,10);
 assert.equal(output.selected_ids.length,5);
 assert.ok(output.selected_ids.every(id=>input.some(p=>p.paper_id===id)));
 assert.ok(input.every(p=>p.local_rank<=10));
 const titleOnly=input.find(p=>p.information_status==='title_only');
 assert.equal(titleOnly.text,null);
 assert.ok(output.selected_ids.includes(titleOnly.paper_id));
});
test('formal recommendations require associated fulltext; rejected final decision retains abstract selection',()=>{
 for(const p of papers.filter(p=>p.final)) {assert.equal(p.pdf,'associated');assert.equal(p.selected,true);}
 const excluded=papers.find(p=>p.final==='exclude');
 assert.equal(outcome(excluded,'review'),'摘要入选');
 assert.equal(outcome(excluded,'fulltext'),'全文终判排除');
 assert.ok(papers.filter(p=>p.story).every(p=>p.final==='recommend'));
 assert.ok(papers.filter(p=>p.pdf==='waiting').every(p=>p.final===null));
});
