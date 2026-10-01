import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {HostedClient,validateHostedConfig,readVerifiedBundle,assertDownloadCapability,quoteWithinAllowance,readHostedArtifact} from '../public/fork-microscope/hosted-client.mjs';
const config={enabled:true,apiBase:'https://api.example',firebase:{apiKey:'public-firebase-config',authDomain:'project.firebaseapp.com',projectId:'project'}};
const user=uid=>({uid,getIdToken:async()=>`id-token-${uid}`});
test('disabled config never asks for cloud config',()=>assert.deepEqual(validateHostedConfig({enabled:false}),{enabled:false}));
test('reject unsafe API origins',()=>{for(const url of ['http://api.example','https://user:password@api.example','https://api.example?key=bad'])assert.throws(()=>validateHostedConfig({...config,apiBase:url}));});
test('only Firebase user token sent, same approved intent retains retry identity',async()=>{
 const calls=[];let fail=true;const client=new HostedClient(config,{uuid:()=> 'same-intent',fetcher:async(url,options)=>{calls.push({url,options});if(fail){fail=false;throw Error('lost connection');}return new Response(JSON.stringify({id:'session'}));}});client.setUser(user('alice'));
 const intent=client.intent('POST','/sessions',{quote_id:'quote',storage_mode:'device',acknowledge_device_loss:true});
 await assert.rejects(client.submit(intent));assert.equal((await client.submit(intent)).id,'session');
 assert.equal(calls[0].options.headers['Idempotency-Key'],calls[1].options.headers['Idempotency-Key']);assert.equal(calls[0].options.body,calls[1].options.body);assert.equal(calls[0].options.headers.Authorization,'Bearer id-token-alice');assert.equal(calls[0].options.credentials,'omit');
});
test('account switch invalidates pending request before transport',async()=>{let resolve;let calls=0;const client=new HostedClient(config,{fetcher:async()=>{calls++;return new Response('{}');}});client.setUser({uid:'alice',getIdToken:()=>new Promise(r=>resolve=r)});const pending=client.request('/me');client.setUser(user('bob'));resolve('alice-token');await assert.rejects(pending,/Account changed/);assert.equal(calls,0);});
test('old approved intent cannot be replayed for a new account',async()=>{const client=new HostedClient(config);client.setUser(user('alice'));const intent=client.intent('POST','/sessions',{});client.setUser(user('bob'));await assert.rejects(client.submit(intent),/previous account/);});
test('server error detail retained without exposing token',async()=>{const client=new HostedClient(config,{fetcher:async()=>new Response(JSON.stringify({detail:'Invite required'}),{status:403})});client.setUser(user('alice'));await assert.rejects(client.request('/me'),e=>e.status===403&&e.message==='Invite required');});
async function checksum(bytes){return Array.from(new Uint8Array(await webcrypto.subtle.digest('SHA-256',bytes)),n=>n.toString(16).padStart(2,'0')).join('');}
test('bounded artifact download verifies exact bytes before JSON import',async()=>{const bytes=new TextEncoder().encode(JSON.stringify({schema:'bundle',payload:{}}));const capability={url:'https://pod1-8780.proxy.runpod.net/bundles/artifact1',download_token:'one-artifact',sha256:await checksum(bytes),size:bytes.length};let sent;const {bundle}=await readVerifiedBundle(capability,'artifact1',{cryptoImpl:webcrypto,fetcher:async(url,options)=>{sent=options;return new Response(bytes);}});assert.equal(bundle.schema,'bundle');assert.equal(sent.headers.Authorization,'Bearer one-artifact');assert.equal(sent.redirect,'error');});
test('artifact checksum mismatch and byte limit fail closed',async()=>{const bytes=new TextEncoder().encode('{}');const capability={url:'https://pod1-8780.proxy.runpod.net/bundles/artifact1',download_token:'one-artifact',sha256:'0'.repeat(64)};await assert.rejects(readVerifiedBundle(capability,'artifact1',{cryptoImpl:webcrypto,fetcher:async()=>new Response(bytes)}),/checksum mismatch/);await assert.rejects(readVerifiedBundle(capability,'artifact1',{cryptoImpl:webcrypto,maxBytes:1,fetcher:async()=>new Response(bytes)}),/import limit/);});
test('reject artifact token exfiltration origins and paths',()=>{for(const url of ['https://evil.example/bundles/artifact1','https://pod1-8780.proxy.runpod.net/bundles/other','https://pod1-8780.proxy.runpod.net/bundles/artifact1?redirect=evil','http://pod1-8780.proxy.runpod.net/bundles/artifact1'])assert.throws(()=>assertDownloadCapability({url,download_token:'private',sha256:'a'.repeat(64)},'artifact1'));});
test('quote requires unexpired price within the requested estimate allowance',()=>{assert.equal(quoteWithinAllowance({expires_at:200,estimated_usd:.25},.3,100),true);assert.throws(()=>quoteWithinAllowance({expires_at:90,estimated_usd:.25},.3,100),/expired/);assert.throws(()=>quoteWithinAllowance({expires_at:200,estimated_usd:.25},.1,100),/exceeds/);assert.throws(()=>quoteWithinAllowance({expires_at:200,estimated_usd:NaN},.3,100),/usable/);});

test('Drive bytes use authenticated backend relay, never an OAuth token in browser',async()=>{
 const bytes=new TextEncoder().encode('{}'),sha256=await checksum(bytes),calls=[];
 const client=new HostedClient(config,{fetcher:async(url,options)=>{calls.push({url,options});return url.endsWith('/download')?new Response(JSON.stringify({file_ref:'opaque',sha256})):new Response(bytes);}});client.setUser(user('alice'));
 await readHostedArtifact(client,{id:'drive-artifact',storage_mode:'drive'},{cryptoImpl:webcrypto});
 assert.equal(calls[1].url,'https://api.example/api/hosted/v1/artifacts/drive-artifact/content');
 assert.equal(calls[1].options.headers.Authorization,'Bearer id-token-alice');assert.equal(calls[1].options.redirect,'error');
});
