import test from 'node:test';
import assert from 'node:assert/strict';
import {plan,costs,gpuEstimate} from '../public/fork-microscope/math.mjs';
const base={row:39,samples:30,stride:4,shift:1,start:0,end:343,draw_start:0};
test('two offset passes use half the checkpoints in an even four-token grid',()=>{
  const p=plan(base,343),c=costs(base,Array(344).fill(100),p);
  assert.equal(c.combinedCheckpoints,172);assert.equal(c.saving,.5);
  assert.equal(c.combined,516000);assert.equal(c.dense,1032000);
});
test('invalid inputs cannot produce plausible budgets',()=>{
  for(const patch of [{samples:NaN},{samples:30.1},{draw_start:180},{shift:4},{end:900},{end:1}])assert.throws(()=>plan({...base,...patch},343));
});
test('time and dollars require explicit assumptions and consistent units',()=>{
  assert.deepEqual(gpuEstimate(360000,100,2),{hours:1,dollars:2});
  assert.deepEqual(gpuEstimate(360000,100,null),{hours:1,dollars:null});
  assert.deepEqual(gpuEstimate(360000,null,2),{hours:null,dollars:null});
  assert.deepEqual(gpuEstimate(360000,0,2),{hours:null,dollars:null});
  assert.deepEqual(gpuEstimate(360000,100,0),{hours:1,dollars:0});
});

import {newPass,resultPasses} from '../public/fork-microscope/passes.mjs';
test('passes start with one and additional passes have independent settings',()=>{
  const p=newPass();assert.equal(p.offset,0);assert.equal(p.id,'pass_1');
  const q=newPass([p]);assert.equal(q.id,'pass_2');assert.equal(q.offset,1);
  q.samples=50;assert.equal(p.samples,20);
  const r=newPass([q]);assert.notEqual(r.id,q.id);
});
test('saved legacy and current passes are both readable',()=>{
  assert.equal(resultPasses({passes:[{id:'custom'}]})[0].id,'custom');
  assert.deepEqual(resultPasses({first:{},second:{},settings:{samples:5}}).map(p=>p.id),['first','second']);
});
