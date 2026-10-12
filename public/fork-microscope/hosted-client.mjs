/** Hosted requests carry only Firebase user ID tokens. No provider secret persists. */
export const FIREBASE_VERSION='12.3.0';
export const MAX_BUNDLE_BYTES=64*1024*1024;
export const MAX_PILOT_IMPORT_BYTES=16*1024*1024;
export function validateHostedConfig(raw){
 if(raw?.enabled!==true)return {enabled:false};
 const url=new URL(raw.apiBase);
 if(url.protocol!=='https:'||url.username||url.password||url.search||url.hash)throw Error('Hosted API must use a configured HTTPS address.');
 if(typeof location!=='undefined' && url.origin!==location.origin)throw Error('Host the API behind this website’s /api proxy so secure Google Drive consent works on phones.');
 if(!raw.firebase?.apiKey||!raw.firebase?.authDomain||!raw.firebase?.projectId)throw Error('Hosted Google sign-in is not configured.');
 return {...raw,apiBase:url.href.replace(/\/$/,'')};
}
export class HostedError extends Error{constructor(message,status){super(message);this.status=status;}}
export class HostedClient{
 constructor(config,{fetcher=(...args)=>globalThis.fetch(...args),uuid=()=>crypto.randomUUID()}={}){this.config=validateHostedConfig(config);this.fetcher=fetcher;this.uuid=uuid;this.user=null;this.epoch=0;this.pending=new Map();}
 setUser(user){if(this.user?.uid!==user?.uid){this.epoch++;this.pending.clear();}this.user=user;}
 intent(method,path,body={}){return {method,path,body:structuredClone(body),key:this.uuid(),epoch:this.epoch};}
 async request(path,{method='GET',body,key,epoch=this.epoch}={}){
  if(!this.config.enabled)throw Error('Hosted compute is not enabled on this site.');
  const user=this.user;if(!user)throw new HostedError('Sign in to your invited workspace.',401);
  if(!path.startsWith('/')||path.startsWith('//'))throw Error('Invalid API path.');
  const token=await user.getIdToken();
  if(epoch!==this.epoch||user!==this.user)throw Error('Account changed. Refresh your workspace.');
  const headers={Authorization:'Bearer '+token};if(body!==undefined)headers['Content-Type']='application/json';
  if(method!=='GET'){if(!key)throw Error('Mutation requires an idempotency key.');headers['Idempotency-Key']=key;}
  const response=await this.fetcher(this.config.apiBase+'/api/hosted/v1'+path,{method,headers,credentials:'same-origin',cache:'no-store',...(body!==undefined?{body:JSON.stringify(body)}:{})});
  if(epoch!==this.epoch||user!==this.user)throw Error('Account changed. Refresh your workspace.');
  let result;try{result=await response.json();}catch{throw new HostedError('The hosted service did not return a readable response.',response.status);}
  if(!response.ok)throw new HostedError(result.detail||'Hosted request failed.',response.status);
  return result;
 }
 async submit(intent){if(intent.epoch!==this.epoch)throw Error('This request belongs to a previous account.');return this.request(intent.path,intent);}
 async importEvidence(body,expectedSha256,idempotencyKey,synthetic=false){
  if(!this.config.enabled)throw Error('Hosted evidence is not enabled on this site.');
  if(!(body instanceof Uint8Array || body instanceof ArrayBuffer))throw Error('Choose the original JSON file bytes.');
  const bytes=new Uint8Array(body instanceof Uint8Array?body:body.slice(0));
  if(!bytes.byteLength||bytes.byteLength>MAX_PILOT_IMPORT_BYTES)throw Error('Choose a native investigation JSON file of at most 16 MiB.');
  if(!/^[a-f0-9]{64}$/.test(expectedSha256)||!/^[A-Za-z0-9_.:-]{1,128}$/.test(idempotencyKey)||typeof synthetic!=='boolean')throw Error('Invalid file import identity.');
  const epoch=this.epoch,user=this.user;if(!user)throw new HostedError('Sign in before importing saved evidence.',401);
  const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),n=>n.toString(16).padStart(2,'0')).join('');
  if(digest!==expectedSha256)throw Error('File checksum changed; choose the original file again.');
  const token=await user.getIdToken();
  if(epoch!==this.epoch||user!==this.user)throw Error('Account changed. Nothing was submitted from this page.');
  const response=await this.fetcher(this.config.apiBase+'/api/hosted/v1/evidence/import',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json','Idempotency-Key':idempotencyKey,'X-Fork-Evidence-SHA256':expectedSha256,'X-Fork-Evidence-Synthetic':String(synthetic)},credentials:'same-origin',cache:'no-store',redirect:'error',body:bytes});
  if(epoch!==this.epoch||user!==this.user)throw Error('Account changed. Refresh your workspace to check its library.');
  let result;try{result=await response.json();}catch{throw new HostedError('Import could not be confirmed; retry the same file.',response.status);}
  if(!response.ok)throw new HostedError(result.detail||'Import could not be confirmed; retry the same file.',response.status);
  if(result?.saved_evidence?.file_sha256!==expectedSha256||result.saved_evidence.bytes!==bytes.byteLength||result.saved_evidence.server_saved!==true)throw Error('Saved evidence identity was not verified. Refresh the library before retrying.');
  return result;
 }
}
// Credential retries stay only in this page's memory. A corrected key is a new
// request; an uncertain result retains the same identity only for the same key.
const credentialSubmissions=new WeakMap();
export function submitRunPodCredential(client,intents,apiKey){
 const name='runpod-connect',method='PUT',path='/connections/runpod';
 let intent=intents.get(name);
 if(!intent||intent.epoch!==client.epoch||intent.method!==method||intent.path!==path||intent.body?.api_key!==apiKey){
  intent=client.intent(method,path,{api_key:apiKey});intents.set(name,intent);
 }
 if(credentialSubmissions.has(intent))return credentialSubmissions.get(intent);
 const forget=()=>{if(intents.get(name)===intent)intents.delete(name);};
 const pending=(async()=>{
  try{const result=await client.submit(intent);forget();return result;}
  catch(error){
   // Timeouts, too-early/rate-limit replies and server/transport errors can be
   // uncertain. A definitive credential rejection must not retain the old key.
   if(Number.isInteger(error?.status)&&error.status>=400&&error.status<500&&![408,425,429].includes(error.status))forget();
   throw error;
  }finally{credentialSubmissions.delete(intent);}
 })();
 credentialSubmissions.set(intent,pending);return pending;
}
export function assertDownloadCapability(data,artifactId){
 if(typeof data.url!=='string'||typeof data.download_token!=='string'||!data.download_token)throw Error('Evidence download is unavailable.');
 const url=new URL(data.url);
 if(url.protocol!=='https:'||url.username||url.password||url.search||url.hash)throw Error('Unsafe evidence download address.');
 const proxy=/^[a-zA-Z0-9]+-8780\.proxy\.runpod\.net$/.test(url.hostname);
 if(!(proxy&&url.pathname==='/bundles/'+encodeURIComponent(artifactId)))throw Error('Evidence address is outside the configured delivery service.');
 if(!/^[a-f0-9]{64}$/.test(data.sha256))throw Error('Missing evidence checksum.');
 return url;
}
export async function readVerifiedBundle(data,artifactId,{fetcher=(...args)=>globalThis.fetch(...args),cryptoImpl=crypto,maxBytes=MAX_BUNDLE_BYTES}={}){
 const url=assertDownloadCapability(data,artifactId);
 const response=await fetcher(url.href,{headers:{Authorization:'Bearer '+data.download_token},credentials:'omit',cache:'no-store',redirect:'error'});
 return readVerifiedResponse(response,data,{cryptoImpl,maxBytes});
}
export async function readVerifiedResponse(response,data,{cryptoImpl=crypto,maxBytes=MAX_BUNDLE_BYTES}={}){
 if(!/^[a-f0-9]{64}$/.test(data.sha256))throw Error('Missing evidence checksum.');
 if(!response.ok)throw new HostedError('Saved evidence could not be downloaded. Keep the session open and retry.',response.status);
 const declared=Number(response.headers.get('Content-Length'));if(declared>maxBytes)throw Error('Evidence exceeds the browser import limit.');
 const reader=response.body?.getReader();let bytes;
 if(reader){let size=0;const parts=[];try{while(true){const {value,done}=await reader.read();if(done)break;size+=value.byteLength;if(size>maxBytes){await reader.cancel();throw Error('Evidence exceeds the browser import limit.');}parts.push(value);}bytes=new Uint8Array(size);let offset=0;for(const part of parts){bytes.set(part,offset);offset+=part.byteLength;}}finally{reader.releaseLock();}}
 else {bytes=new Uint8Array(await response.arrayBuffer());if(bytes.byteLength>maxBytes)throw Error('Evidence exceeds the browser import limit.');}
 if(data.size!==undefined&&bytes.byteLength!==data.size)throw Error('Evidence size does not match the saved artifact.');
 const hash=new Uint8Array(await cryptoImpl.subtle.digest('SHA-256',bytes));const checksum=Array.from(hash,n=>n.toString(16).padStart(2,'0')).join('');
 if(checksum!==data.sha256)throw Error('Evidence checksum mismatch. Nothing was imported.');
 let bundle;try{bundle=JSON.parse(new TextDecoder().decode(bytes));}catch{throw Error('Saved evidence is not valid JSON.');}
 return {bundle,bytes};
}
export function quoteWithinAllowance(quote,maxUsd,now=Date.now()/1000){
 if(!Number.isFinite(maxUsd)||maxUsd<=0)throw Error('Enter a positive USD allowance.');
 if(!Number.isFinite(quote.estimated_usd)||quote.estimated_usd<0)throw Error('The provider did not return a usable price estimate.');
 if(quote.expires_at<=now)throw Error('This quote expired. Request a fresh quote before approval.');
 if(quote.estimated_usd>maxUsd)throw Error('The estimate exceeds your allowance. Reduce session duration or increase the allowance.');
 return true;
}

export async function readHostedArtifact(client,artifact,{cryptoImpl=crypto}={}){
 const epoch=client.epoch,user=client.user;
 const metadata=await client.request('/artifacts/'+encodeURIComponent(artifact.id)+'/download');
 if(artifact.storage_mode!=='drive')return readVerifiedBundle(metadata,artifact.id,{fetcher:client.fetcher,cryptoImpl});
 const token=await user.getIdToken();if(epoch!==client.epoch||user!==client.user)throw Error('Account changed. Refresh your workspace.');
 const response=await client.fetcher(client.config.apiBase+'/api/hosted/v1/artifacts/'+encodeURIComponent(artifact.id)+'/content',{headers:{Authorization:'Bearer '+token},credentials:'omit',cache:'no-store',redirect:'error'});
 if(epoch!==client.epoch||user!==client.user)throw Error('Account changed. Refresh your workspace.');
 const result=await readVerifiedResponse(response,metadata,{cryptoImpl});if(epoch!==client.epoch||user!==client.user)throw Error('Account changed. Refresh your workspace.');return result;
}
