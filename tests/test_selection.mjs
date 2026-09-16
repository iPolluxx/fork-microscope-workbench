// generated: Codex, fork-microscope-revamp-ASTRA-BRIEF.md — evidence navigation regression checks.
import test from 'node:test';
import assert from 'node:assert/strict';
import {reduceSelection,selectionFromURL,selectionURL,validateSelection} from '../public/fork-microscope/selection.mjs';
test('selection survives setup and inspection links without putting private draft text in the URL',()=>{
const source=reduceSelection({}, {investigation_id:'investigation1',run_id:'run1',pass_id:'refinement',checkpoint:132,response_id:'response2',pair:{draw_indices:[2,7]},area:'inspect',token_region:{space:'response',start:132,end_exclusive:136},layers:[0,16]});
const url=selectionURL({...source,prompt:'private text'},'setup');assert.ok(!url.includes('private'));const restored=selectionFromURL(url,source);assert.equal(restored.run_id,'run1');assert.equal(restored.checkpoint,132);assert.deepEqual(restored.pair,{draw_indices:[2,7]});assert.deepEqual(restored.token_region,source.token_region);
});
test('changing evidence source invalidates incompatible path and readout selections',()=>{
const source=reduceSelection({}, {run_id:'a',pass_id:'x',checkpoint:2,pair:{draw_indices:[1,2]},inspection_id:'lens',layers:[1],token_region:{space:'response',start:1,end_exclusive:3}});
const next=reduceSelection(source,{run_id:'b'});assert.equal(next.pair,null);assert.equal(next.inspection_id,null);assert.equal(next.token_region,null);assert.deepEqual(next.layers,[]);
});
test('draw identity includes run, pass, checkpoint and integer index; duplicate pair is rejected',()=>{
const base={run_id:'a',pass_id:'x',checkpoint:2};assert.equal(reduceSelection(base,{continuation:{run_id:'b',pass_id:'x',checkpoint:2,draw_index:1}}).continuation,null);assert.equal(reduceSelection(base,{pair:{draw_indices:[1,1]}}).pair,null);assert.equal(reduceSelection(base,{token_region:{space:'response',start:3,end_exclusive:3}}).token_region,null);
});
test('validation rejects stale comparison IDs without selecting a different pair',()=>{
const s=reduceSelection({}, {run_id:'a',pass_id:'p',checkpoint:2,pair:{draw_indices:[0,8]}});const {selection,reason}=validateSelection(s,{id:'a',records:{p:{branches:[{t:2,draw_indices:[0],observations:[{}]}]}}},[{id:'p'}]);assert.equal(selection.pair,null);assert.match(reason,/unavailable/);
});
