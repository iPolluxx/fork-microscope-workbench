// Offline browser-state regression checks; imports are replaced with a fake SDK.
import assert from 'node:assert/strict';
import vm from 'node:vm';
let input='';for await(const part of process.stdin)input+=part;
const {script,now}=JSON.parse(input);
async function page(){
 const elements=Object.fromEntries(['status','signin','approve','deny'].map(id=>[id,{disabled:id!=='deny',textContent:'',onclick:null}]));
 const events={};const popups=[],requests=[],locations=[];
 let time=now;
 const context=vm.createContext({document:{getElementById:id=>elements[id]},window:{addEventListener:(name,fn)=>events[name]=fn},Date:{now:()=>time*1000},URL,Error,JSON,
  location:{assign:value=>locations.push(value)},fetch:async(url,options)=>{requests.push({url,...options});return {ok:true,json:async()=>({redirect_url:'https://chatgpt.com/connector_platform_oauth_redirect?error=access_denied'})};}});
 const sdk={getAuth:()=>({}),setPersistence:async()=>{},inMemoryPersistence:{},GoogleAuthProvider:class{},signInWithPopup:()=>new Promise((resolve,reject)=>popups.push({resolve,reject}))};
 const fakeModules={
  'https://www.gstatic.com/firebasejs/12.3.0/firebase-app.js':{initializeApp:()=>({})},
  'https://www.gstatic.com/firebasejs/12.3.0/firebase-auth.js':sdk};
 const module=new vm.SourceTextModule(script,{context,importModuleDynamically:async name=>{
  assert.ok(name in fakeModules,'Unexpected external import');const exports=fakeModules[name];
  const fake=new vm.SyntheticModule(Object.keys(exports),function(){for(const [key,value]of Object.entries(exports))this.setExport(key,value);},{context});
  await fake.link(()=>{});await fake.evaluate();return fake;
 }});
 await module.link(()=>{throw Error('Unexpected static import');});await module.evaluate();
 return {elements,events,popups,requests,locations,setTime:value=>time=value};
}
let cases=0;
{
 const p=await page();assert.equal(p.elements.signin.disabled,false);assert.equal(p.elements.approve.disabled,true);cases++;
 const first=p.elements.signin.onclick();const second=p.elements.signin.onclick();await second;
 assert.equal(p.popups.length,1);assert.equal(p.elements.signin.disabled,true);cases++;
 p.popups[0].reject({code:'auth/popup-closed-by-user',message:'private-detail'});await first;
 assert.match(p.elements.status.textContent,/canceled/);assert.equal(p.elements.signin.disabled,false);assert.equal(p.elements.approve.disabled,true);cases++;
 const retry=p.elements.signin.onclick();assert.equal(p.popups.length,2);p.popups[1].reject({code:'auth/popup-blocked'});await retry;
 assert.match(p.elements.status.textContent,/Allow popups/);assert.equal(p.elements.signin.disabled,false);cases++;
 const login=p.elements.signin.onclick();p.popups[2].resolve({user:{getIdToken:async()=>{throw Error('No consent allowed in test');}}});await login;
 assert.equal(p.elements.approve.disabled,false);assert.equal(p.elements.signin.disabled,false);cases++;
 p.events.pageshow({persisted:true});assert.equal(p.elements.approve.disabled,true);assert.match(p.elements.status.textContent,/Sign in again/);cases++;
 const interrupted=p.elements.signin.onclick();p.events.pageshow({persisted:true});p.popups[3].resolve({user:{}});await interrupted;
 assert.equal(p.elements.approve.disabled,true);assert.equal(p.elements.signin.disabled,false);cases++;
 p.setTime(now+601);await p.elements.signin.onclick();assert.equal(p.popups.length,4);assert.equal(p.elements.signin.disabled,true);assert.match(p.elements.status.textContent,/expired/);cases++;
}
{
 const p=await page();const first=p.elements.deny.onclick();const repeated=p.elements.deny.onclick();await Promise.all([first,repeated]);
 assert.equal(p.requests.length,1);assert.equal(JSON.parse(p.requests[0].body).approve,false);assert.equal(p.requests[0].headers.Authorization,undefined);assert.equal(p.locations.length,1);cases++;
}
{
 const p=await page();const first=p.elements.signin.onclick();p.popups[0].reject({code:'unknown',message:'private-secret'});await first;
 assert.ok(!p.elements.status.textContent.includes('private-secret'));assert.match(p.elements.status.textContent,/could not start/);assert.equal(p.elements.signin.disabled,false);cases++;
}
console.log(JSON.stringify({cases,external_calls:0,real_credentials:0}));
