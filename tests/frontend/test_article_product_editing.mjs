import test from 'node:test';
import assert from 'node:assert/strict';
import {articleLines, addReference, targetText} from '../../frontend/src/article-product-model.js';

test('rendered headings and emoji preserve exact source codepoint positions across CRLF', () => {
  const body = '😀原句\r\n\r\n## 小标题  \r\n第二段';
  const lines = articleLines(body);
  assert.deepEqual(lines.map(x => x.text), ['😀原句','小标题','第二段']);
  for (const line of lines) assert.equal(Array.from(body).slice(line.start,line.start+Array.from(line.text).length).join(''),line.text);
});

test('quotes distinguish repeated text by target and exact range', () => {
  const first = {target:'body',start:0,end:2,text:'重复'};
  const second = {target:'body',start:3,end:5,text:'重复'};
  let refs = addReference([],first);
  assert.equal(addReference(refs,{...first}),refs);
  refs = addReference(refs,second);
  refs = addReference(refs,{...first,target:'title'});
  assert.equal(refs.length,3);
  assert.throws(() => addReference(refs,{target:'body',start:1,end:3,text:'复文'}), /重叠/);
  assert.throws(() => addReference([], {target:'body',start:0,end:4001,text:'😀'.repeat(4001)}), /4000/);
});

test('caption references read the exact product instead of body or previous version', () => {
  const state = {draft:{submission:{title:'标题',body:'正文'}},package:{presentation:{figures:[{selection:{caption:'当前图注'}}]}}};
  assert.equal(targetText(state,'title'),'标题');
  assert.equal(targetText(state,'caption:0'),'当前图注');
  assert.equal(targetText(state,'caption:1'),'');
});
