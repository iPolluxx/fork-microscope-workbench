import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import {createSavedImportController, MAX_IMPORT_BYTES} from '../public/fork-microscope/hosted-import-ui.mjs';
const file = text => new Blob([text], {type:'application/json'});
const raw = ' { "schema": "fixture", "payload": {"label":"測定"} }\n';
const receipt = (bytes,hash,synthetic,deduplicated=false) => ({saved_evidence:{server_saved:true,bundle_id:'saved.json',file_sha256:hash,bytes:bytes.byteLength,provenance:{source:'user_file',synthetic,execution_verified:false},deduplicated}});
function setup(handler=async(...args)=>receipt(args[0],args[1],args[3])) {
  const calls=[]; let number=0;
  const client={user:{uid:'alice'},epoch:0,importEvidence:async(...args)=>{calls.push(args);return handler(...args);}};
  const controller=createSavedImportController({getClient:()=>client,cryptoImpl:webcrypto,uuid:()=>String(++number)});
  return {client,calls,controller};
}

test('local file import keeps original UTF-8 bytes and verifies hash size and synthetic provenance',async()=>{
  const h=setup();assert.equal(await h.controller.submit(file(raw),true),true);
  assert.equal(new TextDecoder().decode(h.calls[0][0]),raw);
  assert.match(h.calls[0][1],/^[a-f0-9]{64}$/);assert.equal(h.calls[0][3],true);
  assert.equal(h.controller.state().result.provenance.synthetic,true);
  assert.equal(h.controller.state().result.provenance.execution_verified,false);
});
test('nonempty file bound and valid JSON object checked before authenticated upload',async()=>{
  const h=setup();
  for(const item of [undefined,file(''),file('not JSON'),file('[]'),{size:MAX_IMPORT_BYTES+1,arrayBuffer(){throw Error('must not read');}}])assert.equal(await h.controller.submit(item),false);
  assert.equal(h.calls.length,0);
});
test('uncertain import retries preserve exact checksum request identity and label',async()=>{
  let attempt=0;const h=setup(async(bytes,hash,key,synthetic)=>{if(++attempt===1)throw Error('response unknown');return receipt(bytes,hash,synthetic,true);});
  assert.equal(await h.controller.submit(file(raw),true),false);
  assert.match(h.controller.state().error,/may have completed/);
  assert.equal(await h.controller.submit(file(raw),true),true);
  assert.equal(h.calls[0][2],h.calls[1][2]);assert.equal(h.calls[0][1],h.calls[1][1]);
  assert.equal(h.controller.state().result.deduplicated,true);
  await h.controller.submit(file(raw+' '),true);assert.notEqual(h.calls[1][2],h.calls[2][2]);
  await h.controller.submit(file(raw),false);assert.notEqual(h.calls[1][2],h.calls[3][2]);
});
test('receipt cannot claim success for a changed hash size or label',async()=>{
  for(const change of [{file_sha256:'f'.repeat(64)},{bytes:1},{provenance:{source:'user_file',synthetic:true,execution_verified:false}},{server_saved:false}]){
    const h=setup(async(bytes,hash,key,synthetic)=>({saved_evidence:{...receipt(bytes,hash,synthetic).saved_evidence,...change}}));
    assert.equal(await h.controller.submit(file(raw),false),false);assert.equal(h.controller.state().result,null);assert.match(h.controller.state().error,/did not confirm/);
  }
});
test('duplicate clicks make one upload and account switch discards late receipt',async()=>{
  let release;const h=setup((...args)=>new Promise(resolve=>{release=()=>resolve(receipt(args[0],args[1],args[3]));}));
  const first=h.controller.submit(file(raw));assert.equal(await h.controller.submit(file(raw)),false);
  while(!release)await new Promise(resolve=>setImmediate(resolve));assert.equal(h.calls.length,1);
  h.client.user={uid:'bob'};h.client.epoch++;h.controller.reset();release();assert.equal(await first,false);assert.equal(h.controller.state().result,null);
});
test('sign-out during file reading prevents upload and unsigned user never reads file',async()=>{
  let release;const h=setup();
  const pending=h.controller.submit({size:2,arrayBuffer:()=>new Promise(resolve=>{release=resolve;})});
  h.client.user=null;h.client.epoch++;h.controller.reset();release(new TextEncoder().encode('{}').buffer);
  assert.equal(await pending,false);assert.equal(h.calls.length,0);
  assert.equal(await h.controller.submit({size:2,arrayBuffer(){throw Error('must not read');}}),false);assert.match(h.controller.state().error,/Sign in/);
});
test('quota permission and unavailable errors suggest recovery without compute or credential actions',async()=>{
  for(const [status,expected] of [[429,/request limit/],[403,/signed-in account/],[503,/same file and label/],[500,/same file and label/],[404,/not enabled on this deployment/]]){
    const h=setup(async()=>{throw Object.assign(Error('Import rejected.'),{status});});assert.equal(await h.controller.submit(file(raw)),false);assert.match(h.controller.state().error,expected);
  }
});
test('hosted form is an explicit authenticated upload handoff with no browser persistence of bundle bytes',async()=>{
  const source=await readFile(new URL('../public/fork-microscope/hosted-import-ui.mjs',import.meta.url),'utf8');
  assert.doesNotMatch(source,/localStorage|sessionStorage|console\.|postMessage|btoa/);
  const fragment=await readFile(new URL('../public/fork-microscope/hosted-fragment.html',import.meta.url),'utf8');
  assert.match(fragment,/id="saved-import-file" type="file"/);assert.match(fragment,/This uploads the selected file/);assert.match(fragment,/synthetic test fixture/);
});


test('busy verification 429 stays retryable without falsely claiming quota exhaustion',async()=>{
 const busy=setup(async()=>{throw Object.assign(Error('Evidence is already being verified; retry shortly'),{status:429});});
 await busy.controller.submit(file(raw));assert.match(busy.controller.state().error,/same file shortly/);assert.doesNotMatch(busy.controller.state().error,/quota|retention cleanup/);
 const quota=setup(async()=>{throw Object.assign(Error('Private evidence quota reached'),{status:429});});
 await quota.controller.submit(file(raw));assert.match(quota.controller.state().error,/retention cleanup/);
});
