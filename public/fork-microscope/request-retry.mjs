// generated: Codex, fork-microscope-revamp-ASTRA-BRIEF.md — retain idempotency after an uncertain response.
const pending=new Map();
export async function retryIdentity(route,payload,storage=globalThis.sessionStorage){
 const canonical=JSON.stringify({route,payload});
 const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(canonical));
 const key='fork-request:'+Array.from(new Uint8Array(digest),x=>x.toString(16).padStart(2,'0')).join('');
 let id=pending.get(key);try{id||=storage?.getItem(key);}catch{}
 if(!id)id=crypto.randomUUID().replaceAll('-','');pending.set(key,id);try{storage?.setItem(key,id);}catch{}
 return {id,key,ack(){pending.delete(key);try{storage?.removeItem(key);}catch{}}};
}
export async function retryableRequest(api,route,payload){const identity=await retryIdentity(route,payload);const result=await api(route,{...payload,request_id:identity.id});identity.ack();return result;}
