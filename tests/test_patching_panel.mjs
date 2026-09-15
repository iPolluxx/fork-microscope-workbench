import assert from 'node:assert/strict';
import {patchSources,patchDefaultPair} from '../public/fork-microscope/patching-panel.mjs';
const record={base:{gen_ids:[1,2,3,4,5]},branches:[
  {t:1,tok_id:2,draw_indices:[0,1],continuation_ids:[[3,4],[6,7]],observations:[{label:'A',stop_reason:'eos'},{label:'B',stop_reason:'eos'}]},
  {t:2,tok_id:3,draw_indices:[5],continuation_ids:[[9]],observations:[{label:'B',stop_reason:'length'}]}
]};
assert.deepEqual(patchSources(record,1).map(x=>x.value),['original','draw:0','draw:1']);
assert.deepEqual(patchDefaultPair(record,1,[0,1]),{donor:'draw:0',recipient:'draw:1',position:2});
assert.equal(patchDefaultPair(record,2,[]),null);
const dup=structuredClone(record);dup.branches.push(structuredClone(record.branches[0]));
assert.deepEqual(patchSources(dup,1).map(x=>x.value),['original']);
const common=structuredClone(record);common.branches[0].continuation_ids[1]=[3,4];
assert.equal(patchDefaultPair(common,1,[0,1]),null);
console.log('Patching selection helpers passed (saved synthetic IDs, no model execution).');
