// generated: Codex — fork-microscope-revamp-ASTRA-BRIEF.md, Stage C.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {validateOfflineBundle,canonical,installOfflineEvidence,evidenceFetch,clearOfflineEvidence} from '../public/fork-microscope/offline-evidence.mjs';
const fixture=JSON.parse(readFileSync(new URL('../public/fork-microscope/demo-attendance.json',import.meta.url)));
const copy=()=>structuredClone(fixture);
const seal=b=>{b.sha256=createHash('sha256').update(canonical(b.payload)).digest('hex');return b;};
test('real Python-exported attendance family validates without compute',async()=>{
  await validateOfflineBundle(fixture);assert.equal(fixture.payload.runs.length,3);assert.equal(fixture.payload.lenses.length,2);
});
test('browser rejects tampering before installing any data',async()=>{
  const b=copy();b.payload.runs[0].base.text+=' changed';await assert.rejects(validateOfflineBundle(b),/checksum/);
});
test('valid checksum does not bypass graph/raw-count validation',async()=>{
  const b=copy(),r=b.payload.runs[0],row=r.passes[0].curve.weighted[0];row[0]=.42;row[1]=.58;row[2]=0;
  await assert.rejects(validateOfflineBundle(seal(b)),/disagree/);
});
test('missing parents and duplicate IDs are rejected even when resealed',async()=>{
  const b=copy();b.payload.runs.shift();b.manifest.run_ids.shift();await assert.rejects(validateOfflineBundle(seal(b)),/parent/);
  const c=copy();c.payload.runs.push(c.payload.runs[0]);c.manifest.run_ids.push(c.payload.runs[0].id);await assert.rejects(validateOfflineBundle(seal(c)),/Duplicate/);
});
test('saved lens readouts must preserve exact continuation tokens',async()=>{
  const b=copy(),arm=b.payload.lenses[0].arms.find(a=>a.source_draw);assert.ok(arm);arm.response_ids[0]++;
  await assert.rejects(validateOfflineBundle(seal(b)),/differs/);
});
test('credentials and unsafe object keys are rejected',async()=>{
  const b=copy();b.payload.investigation.api_key='not-a-real-key';await assert.rejects(validateOfflineBundle(seal(b)),/Private/);
  const c=copy();c.payload.investigation=JSON.parse('{"__proto__":{"polluted":true}}');await assert.rejects(validateOfflineBundle(seal(c)),/unsafe/);assert.equal({}.polluted,undefined);
});
test('browser provider serves complete family and prevents compute mutations',async()=>{
  await installOfflineEvidence(fixture,{persist:false});
  const catalog=await (await evidenceFetch('/api/live/runs')).json();assert.equal(catalog.length,3);
  const r=await (await evidenceFetch('/api/live/export?id='+fixture.manifest.entry_run_id)).json();assert.deepEqual(r,fixture.payload.runs.at(-1));
  const lenses=await (await evidenceFetch('/api/live/investigations')).json();assert.equal(lenses.length,2);
  const lens=await (await evidenceFetch('/api/live/investigation?id='+lenses[0].id)).json();assert.ok(lens.cells.length);
  const blocked=await evidenceFetch('/api/live/lens',{method:'POST',body:'{}'});assert.equal(blocked.status,409);
  const output=await (await evidenceFetch('/api/live/bundle-export?id='+r.id)).json();assert.deepEqual(output,fixture);
  clearOfflineEvidence();
});
test('conflicting artifacts never replace already imported evidence',async()=>{
  await installOfflineEvidence(fixture,{persist:false});const b=copy();b.payload.runs[0].caveats.push('Changed evidence');seal(b);
  await assert.rejects(installOfflineEvidence(b,{persist:false}),/already uses this ID/);clearOfflineEvidence();
});
test('browser conclusions create a portable v3 version without changing model evidence',async()=>{
 const {updateOfflineMetadata,getOfflineInvestigation}=await import('../public/fork-microscope/offline-evidence.mjs');
 await installOfflineEvidence(fixture,{persist:false});const job=getOfflineInvestigation();
 const updated=await updateOfflineMetadata({id:job.id,record_revision:job.record_revision,conclusion:'This is an exploratory observation, not a causal finding.'},{persist:false});
 assert.equal(updated.record_revision,1);assert.ok(updated.conclusion.startsWith('This is'));
 const result=await(await evidenceFetch('/api/live/workflow-export?id='+job.id)).json();await validateOfflineBundle(result);
 assert.deepEqual(result.payload.runs,fixture.payload.runs);assert.equal(result.schema,'fork-investigation-bundle-v3');
 await assert.rejects(updateOfflineMetadata({id:job.id,record_revision:0,conclusion:'Stale'},{persist:false}),/changed/);
 clearOfflineEvidence();
});
test('complete synthetic v3 fixture includes edits captures patches and comparisons',async()=>{
 const b=JSON.parse(readFileSync(new URL('./fixtures/investigation-v3.json',import.meta.url)));await validateOfflineBundle(b);
 for(const kind of ['edits','captures','patches'])assert.ok(b.payload[kind].length,kind);
 assert.ok(b.payload.investigation.comparisons.length);
 const bad=structuredClone(b);bad.payload.captures[0].captures[0].prefix_ids[0]++;
 await assert.rejects(validateOfflineBundle(seal(bad)),/prefix/);
 const missing=structuredClone(b);missing.payload.edits=[];
 await assert.rejects(validateOfflineBundle(seal(missing)),/mismatch/);
});
