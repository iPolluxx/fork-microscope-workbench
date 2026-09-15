import assert from 'node:assert/strict';
import {reviewState,fullTracePass} from '../public/fork-microscope/response-review.mjs';
import {jobProgress} from '../public/fork-microscope/job-progress.mjs';
import {saveJSON} from '../public/fork-microscope/download.mjs';

const base={finish_reason:'stop',readout:{status:'matched',label:'DEFER_REPAIR'}};
assert.equal(reviewState(base).canScan,false);
assert.equal(reviewState(base,{reviewed:true}).canScan,true);
assert.equal(reviewState(base,{reviewed:true,dirty:true}).canScan,false);
assert.equal(reviewState({...base,finish_reason:'length'},{reviewed:true,acceptOther:true}).canScan,false);
assert.equal(reviewState({finish_reason:'stop'}).canReview,false);
const ambiguous={...base,readout:{status:'ambiguous',matched_answers:['A','B']}};
assert.equal(reviewState(ambiguous,{reviewed:true}).canScan,false);
assert.equal(reviewState(ambiguous,{reviewed:true,acceptOther:true}).canScan,true);
assert.deepEqual(fullTracePass({samples:5},106),{samples:5,start:0,end:105,stride:7,offset:0});
assert.equal(fullTracePass({},2).stride,1);
assert.equal(fullTracePass({},4096).end,4095);
assert.match(jobProgress({progress_unit:'tokens',completed:64,total:106,status:'running'}),/64\/106 tokens replayed/);
assert.doesNotMatch(jobProgress({progress_unit:'tokens',completed:64,total:106,status:'running'}),/continuations/);
assert.match(jobProgress({started:100,collection_started:100,generated_tokens:20,completed:2,total:10,status:'running',activity:{kind:'generation',step:12,cap:128}},120),/2\/10 continuations saved.*token 12\/128.*About 1m 20s left/);

class Element{
  constructor(){this.dataset={};this.children=[];this.classList={contains:()=>false};}
  setAttribute(){} removeAttribute(){} after(el){this.nextElementSibling=el;}
  replaceChildren(...els){this.children=els;} focus(){}
}
globalThis.document={createElement:()=>new Element()};
globalThis.addEventListener=()=>{};
globalThis.window={};
const anchor=new Element();
await saveJSON(anchor,'evidence.json',()=>({test:'saved evidence'}));
let box=anchor.nextElementSibling,link=box.children[1];
assert.equal(link.download,'evidence.json');
assert.deepEqual(await (await fetch(link.href)).json(),{test:'saved evidence'});
assert.match(box.children[0].textContent,/file is ready/);
link.onclick();assert.match(box.children[0].textContent,/Download requested/);
URL.revokeObjectURL(link.href);
window.showSaveFilePicker=()=>assert.fail('An embedded-browser save dialog must not block export');
const direct=new Element();await saveJSON(direct,'direct.json',()=>({id:42}));
const directLink=direct.nextElementSibling.children[1];
assert.deepEqual(await (await fetch(directLink.href)).json(),{id:42});
URL.revokeObjectURL(directLink.href);
const failed=new Element();await saveJSON(failed,'fail.json',()=>{throw Error('worker offline');});
assert.match(failed.nextElementSibling.textContent,/Could not save: worker offline/);
console.log('Response review, progress units, and download round trips passed.');
