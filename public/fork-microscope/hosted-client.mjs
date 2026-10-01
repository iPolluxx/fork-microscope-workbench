/** Hosted requests carry only Firebase user ID tokens. No provider secret persists. */
export const FIREBASE_VERSION='12.3.0';
export const MAX_BUNDLE_BYTES=64*1024*1024;
export function validateHostedConfig(raw){
 if(raw?.enabled!==true)return {enabled:false};
 const url=new URL(raw.apiBase);
 if(url.protocol!=='https:'||url.username||url.password||url.search||url.hash)throw Error('Hosted API must use a configured HTTPS address.');
 if(!raw.firebase?.apiKey||!raw.firebase?.authDomain||!raw.firebase?.projectId)throw Error('Hosted Google sign-in is not configured.');
 return {...raw,apiBase:url.href.replace(/\/$/,'')};
}
export class HostedError extends Error{constructor(message,status){super(message);this.status=status;}}
export class HostedClient{
 constructor(config,{fetcher=fetch,uuid=()=>crypto.randomUUID()}={}){this.config=validateHostedConfig(config);this.fetcher=fetcher;this.uuid=uuid;this.user=null;this.epoch=0;this.pending=new Map();}
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
  const response=await this.fetcher(this.config.apiBase+'/api/hosted/v1'+path,{method,headers,credentials:'omit',cache:'no-store',...(body!==undefined?{body:JSON.stringify(body)}:{})});
  if(epoch!==this.epoch||user!==this.user)throw Error('Account changed. Refresh your workspace.');
  let result;try{result=await response.json();}catch{throw new HostedError('The hosted service did not return a readable response.',response.status);}
  if(!response.ok)throw new HostedError(result.detail||'Hosted request failed.',response.status);
  return result;
 }
 async submit(intent){if(intent.epoch!==this.epoch)throw Error('This request belongs to a previous account.');return this.request(intent.path,intent);}
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
export async function readVerifiedBundle(data,artifactId,{fetcher=fetch,cryptoImpl=crypto,maxBytes=MAX_BUNDLE_BYTES}={}){
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
