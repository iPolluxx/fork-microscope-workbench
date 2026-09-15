import test from 'node:test';
import assert from 'node:assert/strict';
import {passEvidence,wilsonInterval,reconstructionPoints,suggestedOutcome} from '../public/fork-microscope/graph-evidence.mjs';
const make=(values,extra={})=>({curve:{positions:values.map((_,i)=>i*8),weighted:values,parameters:{pen:64},boundaries:[],...extra}});
test('three constant observations cannot acquire a fork from a bend or stale boundary',()=>{
  const p=make([[0,1],[0,1],[0,1]],{smoothed:[[.1,.9],[.4,.6],[.1,.9]],boundaries:[{left:0,right:8}],segmentation_enabled:true});
  const e=passEvidence(p,null,2);assert.equal(e.constant,true);assert.equal(e.sparse,true);assert.equal(e.segmentationEnabled,false);assert.deepEqual(e.intervals,[]);
  assert.equal(suggestedOutcome([p],2),1);
});
test('descriptive changes and enabled fitted intervals remain distinct',()=>{
  const p=make([[1,0],[.5,.5],[.5,.5],[0,1]],{boundaries:[{left:8,right:16},{left:-1,right:0}]});
  let e=passEvidence(p,null,2);assert.equal(e.intervals[0].segmented,true);assert.equal(e.intervals[0].tv,0);assert.equal(e.intervals[1].tv,.5);
  p.curve.segmentation_enabled=false;e=passEvidence(p,null,2);assert.equal(e.intervals.length,2);assert.ok(e.intervals.every(i=>!i.segmented));
  p.curve.fit_status='withheld';p.curve.segmentation_enabled=true;assert.equal(passEvidence(p,null,2).segmentationEnabled,false);
});
test('invalid points cannot manufacture intervals or enable segmentation',()=>{
  const p=make([[1,0],[NaN,0],[0,1],[0,1]]);let e=passEvidence(p,null,2);assert.equal(e.invalidPoints,1);assert.equal(e.segmentationEnabled,false);assert.deepEqual(e.intervals,[]);
  assert.deepEqual(passEvidence({},null,2).observed,[]);
  for(const bad of [[Infinity,0],[null,1],[1,1],[-1,2]])assert.equal(passEvidence(make([bad]),null,2).observed.length,0);
});
test('point intervals require verified mixture counts and include uncertainty at 5/5',()=>{
  const p=make([[0,1]]),r={sampling_design:'position_mixture_v1',categories:['yes','no'],positions:[{t:0,samples:5}],branches:[{t:0,answers:Array(5).fill('no')}]};
  const point=passEvidence(p,r,2).observed[0];assert.deepEqual(point.counts,[0,5]);const [low,high]=wilsonInterval(point.counts[1],point.samples);assert.ok(low>.56&&low<.57);assert.ok(high>.999);
  r.sampling_design='legacy';assert.equal(passEvidence(p,r,2).observed[0].counts,null);
  r.sampling_design='position_mixture_v1';r.branches[0].answers[0]='yes';assert.equal(passEvidence(p,r,2).observed[0].counts,null);
  assert.equal(wilsonInterval(1,0),null);assert.equal(wilsonInterval(1.5,5),null);
});
test('nonfinite reconstruction values become gaps, never forks or extrapolation',()=>{
  const c={positions:[0,2],parameters:{},support:[-1,0,1,2,3],smoothed:[[.5],[.2],[NaN],[.8],[.5]],low:[[0],[.1],[null],[.9],[0]],high:[[1],[.4],[.6],[.7],[1]]};
  const points=reconstructionPoints(c,0);assert.equal(points.length,3);assert.equal(points[1].value,null);assert.equal(points[1].low,null);assert.equal(points[2].high,null);
  assert.deepEqual(reconstructionPoints({...c,fit_status:'withheld'},0),[]);
});
