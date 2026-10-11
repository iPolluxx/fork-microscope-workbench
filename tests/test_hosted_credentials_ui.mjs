import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {HostedClient, submitRunPodCredential} from '../public/fork-microscope/hosted-client.mjs';

const config={enabled:true,apiBase:'https://app.example',firebase:{apiKey:'public-test-config',authDomain:'test.firebaseapp.com',projectId:'test'}};
const user=uid=>({uid,getIdToken:async()=>`fake-user-token-${uid}`});
function setup(fetcher){
 const calls=[],intents=new Map();let count=0;
 const client=new HostedClient(config,{uuid:()=>`request-${++count}`,fetcher:async(url,options)=>{calls.push({url,options});return fetcher(url,options,calls.length);}});
 client.setUser(user('alice'));
 return {client,intents,calls,submit:key=>submitRunPodCredential(client,intents,key)};
}
const ok=()=>new Response(JSON.stringify({connected:true}),{status:200});
const rejected=status=>new Response(JSON.stringify({detail:'Credential rejected'}),{status});

// Fake strings and injected transport only. No account or network calls.
test('rejected key is forgotten; corrected key reaches transport with a fresh request identity',async()=>{
 const h=setup((url,options,n)=>n===1?rejected(422):ok());
 await assert.rejects(h.submit('fake-rejected-key'),error=>error.status===422);
 assert.equal(h.intents.has('runpod-connect'),false);
 assert.equal((await h.submit('fake-corrected-key')).connected,true);
 assert.equal(JSON.parse(h.calls[0].options.body).api_key,'fake-rejected-key');
 assert.equal(JSON.parse(h.calls[1].options.body).api_key,'fake-corrected-key');
 assert.notEqual(h.calls[0].options.headers['Idempotency-Key'],h.calls[1].options.headers['Idempotency-Key']);
 assert.equal(h.intents.size,0);
});

test('same-key uncertain retries reuse exact request body and identity',async()=>{
 for(const status of [null,408,425,429,500,503]){
  const h=setup((url,options,n)=>{if(n>1)return ok();if(status===null)throw Error('lost transport');return rejected(status);});
  await assert.rejects(h.submit('fake-same-key'));
  assert.equal(h.intents.has('runpod-connect'),true);
  await h.submit('fake-same-key');
  assert.equal(h.calls[0].options.headers['Idempotency-Key'],h.calls[1].options.headers['Idempotency-Key']);
  assert.equal(h.calls[0].options.body,h.calls[1].options.body);
  assert.equal(h.intents.size,0);
 }
});

test('changed key after uncertainty never reuses the previous key or identity',async()=>{
 const h=setup((url,options,n)=>{if(n===1)throw Error('response unknown');return ok();});
 await assert.rejects(h.submit('fake-first-key'));
 await h.submit('fake-changed-key');
 assert.notEqual(h.calls[0].options.headers['Idempotency-Key'],h.calls[1].options.headers['Idempotency-Key']);
 assert.equal(JSON.parse(h.calls[1].options.body).api_key,'fake-changed-key');
});

test('definitive permission/conflict/validation rejections drop retained key memory',async()=>{
 for(const status of [400,401,403,404,409,410,422]){
  const h=setup(()=>rejected(status));
  await assert.rejects(h.submit('fake-rejected-key'),error=>error.status===status);
  assert.equal(h.intents.size,0);
 }
});

test('duplicate in-flight submission shares one provider request',async()=>{
 let release;
 const h=setup(()=>new Promise(resolve=>{release=()=>resolve(ok());}));
 const first=h.submit('fake-current-key'),second=h.submit('fake-current-key');
 assert.equal(first,second);
 await Promise.resolve();await Promise.resolve();
 assert.equal(h.calls.length,1);
 release();assert.equal((await first).connected,true);await second;
 assert.equal(h.intents.size,0);
});

test('late rejection of an old key cannot erase a replacement key retry',async()=>{
 const releases=[];
 const h=setup(()=>new Promise(resolve=>releases.push(resolve)));
 const old=h.submit('fake-old-key');
 const replacement=h.submit('fake-new-key');
 await Promise.resolve();await Promise.resolve();
 assert.equal(h.calls.length,2);
 releases[0](rejected(422));await assert.rejects(old);
 assert.equal(h.intents.get('runpod-connect').body.api_key,'fake-new-key');
 releases[1](rejected(503));await assert.rejects(replacement);
 assert.equal(h.intents.get('runpod-connect').body.api_key,'fake-new-key');
});

test('account epoch invalidates prior credential intent even for same-key retry',async()=>{
 const h=setup((url,options,n)=>n===1?rejected(503):ok());
 await assert.rejects(h.submit('fake-key'));
 h.client.setUser(user('bob'));
 await h.submit('fake-key');
 assert.notEqual(h.calls[0].options.headers['Idempotency-Key'],h.calls[1].options.headers['Idempotency-Key']);
 assert.equal(h.calls[1].options.headers.Authorization,'Bearer fake-user-token-bob');
});

test('secure form clears input before submission and scopes completion to current account',async()=>{
 const source=await readFile(new URL('../public/fork-microscope/hosted-ui.mjs',import.meta.url),'utf8');
 const form=source.slice(source.indexOf("$('runpod-form').onsubmit="),source.indexOf("$('runpod-disconnect').onclick="));
 assert(form.indexOf("$('runpod-key').value=''")<form.indexOf('await submitRunPodCredential'));
 assert.match(form,/if\(runpodConnectTicket\)return/);
 assert.match(form,/if\(ticket\.epoch!==accountEpoch\)return/);
 assert.match(form,/if\(runpodConnectTicket===ticket\)/);
 assert.doesNotMatch(form,/localStorage|sessionStorage|console\.|JSON\.stringify/);
 assert.match(source,/userUid=user\?\.uid\|\|null;intents\.clear\(\);runpodConnectTicket=null/);
 assert.match(source,/pagehide',\(\)=>\{intents\.clear\(\);\$\('runpod-key'\)\.value=''/);
});
