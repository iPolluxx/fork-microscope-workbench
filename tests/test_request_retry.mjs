// generated: Codex, fork-microscope-revamp-ASTRA-BRIEF.md — uncertain-response retry IDs.
import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {retryIdentity,retryableRequest} from '../public/fork-microscope/request-retry.mjs';
if(!globalThis.crypto)globalThis.crypto=webcrypto;
const values=new Map(),storage={getItem:k=>values.get(k),setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};
test('same pending operation reuses ID while different settings get a distinct ID',async()=>{const a=await retryIdentity('search',{target:'A'},storage),b=await retryIdentity('search',{target:'A'},storage),c=await retryIdentity('search',{target:'B'},storage);assert.equal(a.id,b.id);assert.notEqual(a.id,c.id);assert.equal(storage.getItem(a.key),a.id);a.ack();assert.notEqual((await retryIdentity('search',{target:'A'},storage)).id,a.id);});
test('uncertain HTTP failure retains request ID and successful acknowledgement permits intentional repeat',async()=>{const seen=[];const api=async(_r,p)=>{seen.push(p.request_id);if(seen.length===1)throw new Error('disconnected');return {ok:true};};await assert.rejects(retryableRequest(api,'scan',{response:'r'}));await retryableRequest(api,'scan',{response:'r'});await retryableRequest(api,'scan',{response:'r'});assert.equal(seen[0],seen[1]);assert.notEqual(seen[1],seen[2]);});

test('a fresh module instance recovers the pending ID from session storage',async()=>{const a=await retryIdentity('create',{name:'persist'},storage);const fresh=await import('../public/fork-microscope/request-retry.mjs?fresh');const b=await fresh.retryIdentity('create',{name:'persist'},storage);assert.equal(a.id,b.id);});
