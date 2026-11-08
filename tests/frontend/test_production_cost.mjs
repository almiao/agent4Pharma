import test from 'node:test';
import assert from 'node:assert/strict';
import {productionCostLabel} from '../../frontend/src/production-cost.js';

test('node cost distinguishes unknown, estimated zero and partial reported usage',()=>{
  assert.equal(productionCostLabel(null),'费用未知');
  assert.equal(productionCostLabel({amount:null}),'费用未知');
  assert.equal(productionCostLabel({amount:'0.000000'}),'约 ¥0.0000');
  assert.equal(productionCostLabel({amount:'0.439611',partial:true}),'约 ¥0.4396（部分）');
});
