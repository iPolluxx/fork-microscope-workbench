/* Static dashboard transport. Models and evidence remain on the user's worker. */
(() => {
  const key = 'fork-worker-session-v1';
  const nativeFetch = window.fetch.bind(window);
  let config = null, generation = 0;
  try { config = JSON.parse(sessionStorage.getItem(key) || 'null'); } catch {}
  const local = ['127.0.0.1', 'localhost', '[::1]'].includes(location.hostname);
  function endpoint(value) {
    const url = new URL(value);
    if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password || url.search || url.hash || !['','/'].includes(url.pathname)) throw Error('Use a worker origin, such as https://gpu.example.org, without a path or credentials.');
    if (url.protocol === 'http:' && !['127.0.0.1','localhost','[::1]'].includes(url.hostname)) throw Error('A remote worker must use HTTPS. For a VM over SSH, forward its port to localhost.');
    return url.origin;
  }
  try { if (config) config = {url:endpoint(config.url), token:String(config.token || '')}; } catch { config=null; }
  // Share credentials only with other live, same-origin tabs. Never persist
  // tokens in localStorage or put them in navigation URLs.
  let channel=null, finishHandoff, handoffPending=!config;
  const handoff=config ? Promise.resolve() : new Promise(resolve=>{finishHandoff=resolve;setTimeout(()=>{handoffPending=false;resolve();},400);});
  try {
    channel=new BroadcastChannel('fork-worker-tabs-v1');
    channel.onmessage=({data})=>{
      if(data?.type==='request' && config) channel.postMessage({type:'connection',config});
      if(data?.type==='connection' && !config && data.config){
        try {
          const next={url:endpoint(data.config.url),token:String(data.config.token||'')};
          // Resolve startup before issuing the first evidence request.
          const late=!handoffPending;
          config=next;generation++;handoffPending=false;
          try {sessionStorage.setItem(key,JSON.stringify(config));} catch {}
          finishHandoff?.();draw();
          if(late)window.dispatchEvent(new Event('worker-connection-change'));
        } catch {}
      }
    };
    if(!config)channel.postMessage({type:'request'});
  } catch {finishHandoff?.();}
  window.workerConnection = () => ({url:config?.url || (local ? location.origin : null), connected:!!config || local});
  window.workerFetch = async (input, options={}) => {
    if (typeof input !== 'string' || !input.startsWith('/api/')) return nativeFetch(input, options);
    await handoff;
    const current=generation, origin=config?.url || (local ? location.origin : null);
    if (!origin) throw Error('Connect the worker that holds your saved runs using Connect compute above. Reading saved evidence needs no loaded model or GPU.');
    const headers=new Headers(options.headers);
    if (config?.token) headers.set('Authorization','Bearer '+config.token);
    const response=await nativeFetch(origin+input,{...options,headers,credentials:'omit',referrerPolicy:'no-referrer'});
    if (current!==generation) throw Error('Worker changed. Discarded a response from the previous worker.');
    if(response.status===401)throw Error('Your saved runs have not been deleted. This window needs the worker access token: choose Connect compute and reconnect to the worker holding your evidence.');
    return response;
  };
  function persist(value) {
    config=value;generation++;
    try { if (value) sessionStorage.setItem(key,JSON.stringify(value)); else sessionStorage.removeItem(key); } catch {}
    draw();window.dispatchEvent(new Event('worker-connection-change'));
  }
  let status, button;
  function draw() {
    if (!status) return;
    status.textContent=config ? `Evidence & compute · ${new URL(config.url).host}` : local ? `Local evidence · ${location.host} · model readiness not checked` : 'Evidence source · not connected';
    button.textContent=config ? 'Change machine' : 'Connect a machine';
  }
  function init() {
    const style=document.createElement('style');
    style.textContent=`.worker-strip{display:flex;justify-content:space-between;align-items:center;gap:16px;padding:9px max(16px,3vw);font-size:13px;line-height:1.5;font-family:inherit;background:#101820;color:#c8d7df;border-bottom:1px solid #30424e}.worker-strip button,.worker-dialog button{border:1px solid #688496;background:#1c303d;color:#fff;border-radius:8px;padding:8px 13px;cursor:pointer;font:inherit}.worker-dialog{box-sizing:border-box;width:min(560px,calc(100% - 24px));max-height:90vh;overflow:auto;padding:26px;border:1px solid #66808f;border-radius:16px;background:#101d26;color:#e8eff4;font-size:14px;line-height:1.6;font-family:inherit}.worker-dialog::backdrop{background:#020911d9}.worker-dialog h2{font-size:23px;margin-top:0}.worker-dialog label{display:block;margin:16px 0}.worker-dialog input{box-sizing:border-box;display:block;width:100%;padding:11px;margin-top:5px;background:#071219;color:white;border:1px solid #586c77;border-radius:7px;font:inherit}.worker-dialog .worker-actions{display:flex;gap:8px;flex-wrap:wrap}.worker-dialog [role=status]{white-space:pre-wrap;color:#e8d4a8}.worker-dialog code{overflow-wrap:anywhere}.worker-dialog summary{cursor:pointer}.worker-dialog small{color:#b7c7d0}.worker-dialog .worker-lead{font-size:15px;color:#d3e2ea}.worker-dialog ol{padding-left:22px;margin:14px 0}.worker-dialog li+li{margin-top:8px}.worker-dialog .worker-example{padding:11px 13px;background:#0a1620;border-left:2px solid #7798ad;color:#bdd3df;font-size:12px}`;
    document.head.append(style);
    const bar=document.createElement('div');bar.className='worker-strip';
    status=document.createElement('span');button=document.createElement('button');button.type='button';
    bar.append(status,button);document.body.prepend(bar);
    const dialog=document.createElement('dialog');dialog.className='worker-dialog';dialog.setAttribute('aria-labelledby','worker-title');
    dialog.innerHTML=`<h2 id="worker-title">Connect a machine</h2>
      <p class="worker-lead">Your machine stores the evidence and runs the model. Connecting does not load a model or start paid compute.</p>
      <label>Where will it run?<select data-machine-kind><option value="local">This computer</option><option value="remote">GPU VM or access from my phone</option></select></label>
      <p>In your installed Fork Microscope folder, run:</p><pre style="white-space:pre-wrap;overflow-wrap:anywhere;background:#071219;padding:12px"><code data-command></code></pre><button type="button" data-copy-command>Copy command</button>
      <p data-connection-hint></p>
      <form data-pair-form><label>One-time pairing code<input name="code" autocomplete="off" spellcheck="false" placeholder="FM1.…" required></label>
      <p data-pair-target></p><p data-pair-note role="status" aria-live="polite"></p><button type="submit">Pair this machine</button></form>
      <p><small>One code replaces the URL and password fields. It expires in 10 minutes. Pairing grants access to this worker; keep the code private.</small></p>
      <p><a href="https://github.com/iPolluxx/fork-microscope-workbench/blob/main/docs/GETTING-STARTED.md" target="_blank" rel="noopener noreferrer" style="color:#c8e5fa">Installation and connection guide ↗</a></p>
      <details data-advanced><summary>Advanced: worker URL and access token</summary><form data-manual-form><label>Worker URL<input name="url" type="url" placeholder="http://127.0.0.1:8767" required autocomplete="off"></label><label>Worker access token<input name="token" type="password" autocomplete="off" placeholder="Worker token, not a provider API key"></label><p role="status" aria-live="polite"></p><button type="submit">Test and connect</button></form></details>
      <p><small>Connections stay in this tab’s session storage and are shared with other open tabs of this website. Saved runs stay on the selected machine. Switching machines changes which library you see.</small></p>
      <div class="worker-actions"><button type="button" data-disconnect>Disconnect</button><button type="button" data-close>Close</button></div>
      <details><summary>Connection lifetime and troubleshooting</summary><p>The background worker survives closing your terminal. The computer must stay awake and online. A temporary tunnel address can change after restart: run <code>fork-microscope machine pair</code> and pair again. For a permanent endpoint, use <code>--public-url</code> with your HTTPS setup.</p><p>A hosted website may request local-network permission. If localhost access is blocked, use the dashboard served on the same computer. Allowed website: <code data-origin></code></p><p>Stopping the worker does not stop VM billing. Export your investigation before deleting a rented machine.</p></details>`;
    dialog.querySelector('[data-origin]').textContent=location.origin;
    document.body.append(dialog);
    const form=dialog.querySelector('[data-manual-form]'), note=form.querySelector('[role=status]');
    const pairForm=dialog.querySelector('[data-pair-form]'), pairNote=dialog.querySelector('[data-pair-note]');
    function decodePair(value) {
      if (!/^FM1\.[A-Za-z0-9_-]+$/.test(value) || value.length>2048) throw Error('Paste the complete FM1 pairing code printed by your machine.');
      const encoded=value.slice(4).replace(/-/g,'+').replace(/_/g,'/');
      const data=JSON.parse(atob(encoded.padEnd(Math.ceil(encoded.length/4)*4,'=')));
      if (!data || Object.keys(data).sort().join(',')!=='secret,url' || !/^[A-Za-z0-9_-]{32,128}$/.test(data.secret)) throw Error('Invalid pairing code.');
      return {url:endpoint(data.url),secret:data.secret};
    }
    const kind=dialog.querySelector('[data-machine-kind]');
    function showCommand(){
      dialog.querySelector('[data-command]').textContent=`.venv/bin/fork-microscope machine start --dashboard-origin ${location.origin}${kind.value==='remote'?' --share':''}`;
      dialog.querySelector('[data-connection-hint]').textContent=kind.value==='remote'?'Run this on your VM or evidence computer. Requires cloudflared installed there. It creates a temporary HTTPS tunnel; no VM is provisioned.':'Run this on the same computer as your browser. The worker runs in the background. No GPU is needed to browse saved evidence.';
    }
    kind.onchange=showCommand;showCommand();
    dialog.querySelector('[data-copy-command]').onclick=async()=>{
      try{await navigator.clipboard.writeText(dialog.querySelector('[data-command]').textContent);pairNote.textContent='Command copied.';}catch{pairNote.textContent='Select and copy the displayed command.';}
    };
    pairForm.elements.code.oninput=()=>{
      try{dialog.querySelector('[data-pair-target]').textContent='Connect to '+decodePair(pairForm.elements.code.value.trim()).url;}catch{dialog.querySelector('[data-pair-target]').textContent='';}
    };
    pairForm.onsubmit=async event=>{
      event.preventDefault();const submit=pairForm.querySelector('[type=submit]');submit.disabled=true;
      try{
        const {url,secret}=decodePair(pairForm.elements.code.value.trim());pairNote.textContent='Pairing with '+url+'…';
        const response=await nativeFetch(url+'/api/pair',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({secret}),credentials:'omit',redirect:'error',referrerPolicy:'no-referrer',signal:AbortSignal.timeout(15000)});
        const value=await response.json();if(!response.ok)throw Error(value.error||'Pairing failed.');
        if(typeof value.token!=='string'||!/^[A-Za-z0-9._~-]{32,512}$/.test(value.token))throw Error('Worker returned invalid credentials.');
        const check=await nativeFetch(url+'/api/live/status',{headers:{Authorization:'Bearer '+value.token},credentials:'omit',redirect:'error',referrerPolicy:'no-referrer',signal:AbortSignal.timeout(15000)});
        const status=await check.json();if(!check.ok||!status.job||!status.runtime)throw Error('Pairing succeeded but worker verification failed. Run machine pair to retry.');
        persist({url,token:value.token});pairForm.reset();dialog.close();
      }catch(error){pairNote.textContent=error.message==='Failed to fetch'?'Cannot reach the machine. Check that it is online; for phone access use --share. If its tunnel restarted, generate a new pairing code.':error.message;}
      finally{submit.disabled=false;}
    };
    button.onclick=()=>{form.elements.url.value=config?.url || (local?location.origin:'http://127.0.0.1:8767');form.elements.token.value=config?.token || '';note.textContent='';pairNote.textContent='';dialog.showModal();};
    dialog.querySelector('[data-close]').onclick=()=>dialog.close();
    dialog.querySelector('[data-disconnect]').onclick=()=>{persist(null);dialog.close();};
    form.onsubmit=async event=>{
      event.preventDefault();const submit=form.querySelector('[type=submit]');submit.disabled=true;
      try {
        const url=endpoint(form.elements.url.value.trim()),token=form.elements.token.value.trim();
        note.textContent='Checking worker access…';
        const headers=token?{Authorization:'Bearer '+token}:{};
        const response=await nativeFetch(url+'/api/live/status',{headers,credentials:'omit',referrerPolicy:'no-referrer',signal:AbortSignal.timeout(15000)});
        const value=await response.json();
        if (!response.ok) throw Error(value.error || 'Worker refused this connection.');
        if (!value.job || !value.runtime) throw Error('This is not a compatible Fork Microscope worker.');
        persist({url,token});dialog.close();
      } catch (error) {note.textContent=error.message==='Failed to fetch'?'Cannot reach the worker. Check its URL, HTTPS or SSH tunnel, allowed dashboard origin, and browser local-network permission.':error.message;}
      finally {submit.disabled=false;}
    };
    draw();
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();
