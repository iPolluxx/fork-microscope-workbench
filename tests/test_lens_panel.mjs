// generated: Codex — bounded selection and exact saved-cell navigation invariants.
import test from 'node:test';
import assert from 'node:assert/strict';
import {parseLensLayers, lensDraws, lensMatrix} from '../public/fork-microscope/lens-panel.mjs';

test('layer selection rejects ambiguous, duplicate and excessive requests before compute', () => {
  assert.deepEqual(parseLensLayers('0, 8,16'), [0,8,16]);
  for (const invalid of ['', '0,', '1,1', '-1', '1.5', '2e3', 'NaN', 'Infinity', '9007199254740993', '0,1,2,3,4,5,6,7,8']) assert.throws(() => parseLensLayers(invalid));
});

test('saved continuations preserve draw IDs across branch grouping and include forced branch token', () => {
  const record = {branches: [
    {t:10, tok_id:9, draw_indices:[2,0], continuation_ids:[[90,91],[92]], observations:[{label:'yes'},{label:'no'}]},
    {t:10, tok_id:8, draw_indices:[1], continuation_ids:[[93,94,95]], observations:[{label:'yes'}]},
    {t:20, tok_id:7, draw_indices:[0], continuation_ids:[[96]], observations:[{label:'other'}]},
  ]};
  assert.deepEqual(lensDraws(record,10), [
    {checkpoint:10,draw_index:0,label:'no',length:12},
    {checkpoint:10,draw_index:1,label:'yes',length:14},
    {checkpoint:10,draw_index:2,label:'yes',length:13},
  ]);
  assert.deepEqual(lensDraws(record,0), []);
});

test('comparison cells are keyed by actual layer and position without merging control and edit', () => {
  const cells = [
    {arm:'edit',layer:8,index:11,tokens:[{text:'changed'}]},
    {arm:'control',layer:8,index:11,tokens:[{text:'original'}]},
    {arm:'edit',layer:0,index:10,tokens:[{text:'earlier'}]},
  ];
  const grid = lensMatrix(cells,'edit');
  assert.deepEqual(grid.layers,[0,8]); assert.deepEqual(grid.positions,[10,11]);
  assert.equal(grid.byKey.get('11/8'),cells[0]); assert.equal(grid.byKey.get('10/8'),undefined);
  assert.equal(lensMatrix(cells,'control').byKey.get('11/8'),cells[1]);
});

test('outcome pairs exclude capped, ambiguous, incomplete or non-replayable draws', async () => {
  const {completedDraws,outcomePair}=await import('../public/fork-microscope/lens-panel.mjs');
  const branch=(id,label,stop='eos',tokens=[90])=>({t:10,tok_id:8,draw_indices:[id],continuation_ids:[tokens],observations:[{label,stop_reason:stop}]});
  const missing=branch(6,'yes');delete missing.continuation_ids;
  const record={branches:[branch(3,'yes'),branch(1,'no'),branch(2,'yes'),branch(4,'Other'),branch(5,'no','length'),missing,branch(7,'no',null),branch(8,'yes'),branch(8,'no'),branch(9,'no','eos',[null])]};
  const rows=completedDraws(record,10);
  assert.deepEqual(rows.map(r=>r.draw_index),[1,2,3]);
  assert.deepEqual(rows[0].ids,[8,90]);assert.equal(rows[0].length,12);
  assert.deepEqual(outcomePair(rows).map(r=>r.draw_index),[1,2]);
  assert.deepEqual(outcomePair(rows,3,2).map(r=>r.draw_index),[3,1]);
  assert.deepEqual(outcomePair(rows,3,1).map(r=>r.draw_index),[3,1]);
  assert.deepEqual(outcomePair(rows.filter(r=>r.label==='yes')),[]);
  assert.deepEqual(completedDraws(record,11),[]);
});

test('focused inspection follows actual divergence, not simply the checkpoint', async () => {
  const {pairFocus}=await import('../public/fork-microscope/lens-panel.mjs');
  assert.deepEqual(pairFocus([{ids:[4,5,6,7,8]},{ids:[4,5,9,7]}],60),{first:62,start:61,end:63,baselineSpace:'response',baselineIndex:61});
  assert.equal(pairFocus([{ids:[4]},{ids:[9]}],0).baselineSpace,'prompt');
  assert.deepEqual(pairFocus([{ids:[4]},{ids:[4,5]}],2),{first:null,prefixOnly:true});
  assert.equal(pairFocus([],2),null);
});

test('exploration presets and expansions stay bounded and include existing layers', async () => {
  const {explorationLayers,denserLensLayers,expandLensRange}=await import('../public/fork-microscope/lens-panel.mjs');
  const initial=explorationLayers(52),more=denserLensLayers(initial,52);
  assert.equal(initial.length,5); assert.equal(more.length,8);
  assert(initial.every(n=>more.includes(n)));assert(more.every(n=>n>=0&&n<51));
  assert.deepEqual(explorationLayers(2),[0]);
  assert.deepEqual(expandLensRange(1,4,6),{start:0,end:5});
  const bounded=expandLensRange(8,70,100);
  assert(bounded.end-bounded.start+1<=64);
  assert(bounded.start<=8 && bounded.end>=70);
  assert.throws(()=>expandLensRange(0,63,100));
  assert.throws(()=>expandLensRange(0,3,0));
});

test('inspection capabilities retain old-worker native support and never invent NNsight availability', async () => {
  const {inspectionBackends}=await import('../public/fork-microscope/lens-panel.mjs');
  assert.deepEqual(inspectionBackends(null).map(x=>[x.id,x.available]),[['native',true],['nnsight',false]]);
  const state=inspectionBackends({inspection_backends:[{id:'native',available:true},{id:'nnsight',available:false,reason:'Requires optional package'}]});
  assert.equal(state[1].reason,'Requires optional package');
  assert.equal(state[1].available,false);
  assert.equal(inspectionBackends({inspection_backends:[{id:'nnsight',available:'true'}]})[1].available,false);
  assert.equal(inspectionBackends({inspection_backends:[{id:'nnsight',available:true}]})[1].available,true);
});
