import test from 'node:test';
import assert from 'node:assert/strict';
import {quickConfig,quickAllowance,quickPhase} from '../public/fork-microscope/quick-investigation-config.mjs';
const model={model_id:'fixture/model',resolved_revision:'abc123',device:'cuda',batch_size:2,chat_template:true};
test('front door pins loaded model and bounds a full trace scan without automatic refinement',()=>{
 const c=quickConfig(model,' A question ',['CHOICE=A','CHOICE=B']);
 assert.equal(c.model.revision,'abc123');assert.equal(c.base.prompt,'A question');assert.equal(c.scan.end,null);assert.equal(c.refinement.max_rounds,0);assert.equal(c.lens,null);
 assert.deepEqual(quickAllowance(c),{checkpoints:32,samples:320,tokens:164352,exceeds:false});
});
test('invalid and ambiguous labels or unavailable model fail before work',()=>{
 for(const labels of [['A','a'],['Other','B'],['A','a '.repeat(201)]])assert.throws(()=>quickConfig(model,'p',labels));
 assert.throws(()=>quickConfig(null,'p',['A','B']),/load a model/);
 assert.throws(()=>quickConfig({...model,resolved_revision:'main'},'p',['A','B']),/pinned/);
 for(const values of [{stride:0},{samples:2},{temperature:NaN},{max_seconds:-1},{cont_max:2.5}])assert.throws(()=>quickConfig(model,'p',['A','B'],values));
});
test('smaller resource cap is visible rather than a false completion estimate',()=>{
 const c=quickConfig(model,'p',['A','B'],{max_samples:100});assert.equal(quickAllowance(c).exceeds,true);
});
test('progress separates generation sampling and terminal saved evidence',()=>{
 assert.equal(quickPhase(null),0);assert.equal(quickPhase({status:'running',steps:[{action:'base'}]}),1);
 assert.equal(quickPhase({status:'running',pending:{action:'run'}}),2);
 assert.equal(quickPhase({status:'running',runs:['one']}),2);assert.equal(quickPhase({status:'complete',runs:['one']}),3);
});
