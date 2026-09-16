// generated: Codex — fork-microscope-revamp-ASTRA-BRIEF.md, Stage C.
// Data-only browser evidence. This never attaches weights or executes imported code.
import {classifyPreview,validateRule} from './classification.mjs';
const MAX_BYTES=64*1024*1024, ID=/^[a-f0-9]{32}$/;
const privateKeys=new Set(['token','access_token','api_key','authorization','password','worker_url','hub_cache','cache_dir','checkpoint_path','local_path','path','weights_path','worker_job','error','__proto__','prototype','constructor']);
let bundles=[],active=false,initializing,originalFetch;
const same=(a,b)=>canonical(a)===canonical(b);
const fail=message=>{throw new Error(message);};
const array=(v,label)=>Array.isArray(v)?v:fail(`Missing ${label}.`);
const integer=n=>Number.isSafeInteger(n)&&n>=0;
const ids=(v,label)=>{if(!Array.isArray(v)||v.some(x=>!integer(x)))fail(`Invalid ${label} token IDs.`);return v;};
const identifier=v=>ID.test(v??'')?v:fail('Invalid evidence identifier.');
function stringJSON(v){return JSON.stringify(v).replace(/[\u007f-\uffff]/g,c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'));}
function numberJSON(v){
  if(!Number.isFinite(v))fail('Nonfinite number in evidence.');
  if(Number.isInteger(v)){if(!Number.isSafeInteger(v))fail('Evidence contains an integer outside browser precision.');return String(v);}
  let s=Math.abs(v)<1e-4?v.toExponential():String(v);
  return s.replace(/e([+-]?)(\d+)$/,(_,sign,digits)=>'e'+(sign||'+')+digits.padStart(2,'0'));
}
export function canonical(v){
  if(v===null)return 'null';
  if(typeof v==='string')return stringJSON(v);
  if(typeof v==='number')return numberJSON(v);
  if(typeof v==='boolean')return String(v);
  if(Array.isArray(v))return '['+v.map(canonical).join(',')+']';
  if(v&&typeof v==='object')return '{'+Object.keys(v).sort().map(k=>stringJSON(k)+':'+canonical(v[k])).join(',')+'}';
  fail('Unsupported evidence value.');
}
async function sha(text){const value=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(text));return [...new Uint8Array(value)].map(x=>x.toString(16).padStart(2,'0')).join('');}
function safeTree(v,depth=0){
  if(depth>80)fail('Evidence is too deeply nested.');
  if(typeof v==='number')numberJSON(v);
  if(Array.isArray(v)){for(const x of v)safeTree(x,depth+1);}
  else if(v&&typeof v==='object')for(const [k,x] of Object.entries(v)){
    if(privateKeys.has(k.toLowerCase()))fail(`Private or unsafe operational field: ${k}. Re-export using Export investigation.`);
    if(k==='model_id'&&typeof x==='string'&&(/^[~/\\]/.test(x)||/^[A-Za-z]:\\/.test(x)))fail('Private model path in evidence.');
    safeTree(x,depth+1);
  }
}
function probabilityRows(rows,k){for(const row of array(rows,'graph rows'))if(!Array.isArray(row)||row.length!==k||row.some(x=>typeof x!=='number'||x<0||x>1)||Math.abs(row.reduce((a,b)=>a+b,0)-1)>1e-6)fail('Invalid outcome distribution.');}
function validateRun(r){
  identifier(r.id);if(![1,2].includes(r.schema_version))fail('Unsupported saved run version.');
  if(!r.model?.model_id||!r.base_config||!r.settings||!r.records||!Number.isFinite(r.created))fail('Incomplete saved run.');
  const cats=array(r.categories,'outcomes');if(cats.length<2||cats.length>33||new Set(cats).size!==cats.length||cats.some(x=>typeof x!=='string'||!x||x.length>200))fail('Invalid outcomes.');
  const n=r.base?.length;if(!integer(n)||!n||array(r.base?.tokens,'original tokens').length!==n||r.base.tokens.some(t=>typeof t!=='string')||typeof r.base.text!=='string')fail('Original response length mismatch.');
  const passes=array(r.passes,'passes');if(!passes.length||passes.length>16)fail('Invalid pass count.');
  let base;const names=new Set();
  for(const p of passes){
    if(!/^[A-Za-z0-9_-]{1,64}$/.test(p.id)||names.has(p.id)||['manifest','result','dense','replay-verification','import-info'].includes(p.id))fail('Invalid pass ID.');names.add(p.id);
    const record=r.records[p.id],b=record?.base;if(!b||!same(record.categories,cats))fail('Missing pass evidence.');
    ids(b.prompt_ids,'prompt');ids(b.gen_ids,'response');if(!b.prompt_ids.length||b.gen_ids.length!==n||b.base_text!==r.base.text)fail('Pass response differs from graph.');
    if(base&&!same([base.prompt_ids,base.gen_ids],[b.prompt_ids,b.gen_ids]))fail('Passes have different exact responses.');base=b;
    const positions=array(p.curve?.positions,'checkpoints');if(!positions.length||positions.some((t,i)=>!integer(t)||t>=n||(i&&t<=positions[i-1])))fail('Invalid checkpoint ordering.');
    const rows=p.curve.weighted;if(array(rows,'observed proportions').length!==positions.length)fail('Graph/checkpoint length mismatch.');probabilityRows(rows,cats.length);
    const support=array(p.curve.support??[],'fit positions');if(support.some((t,i)=>!integer(t)||t<positions[0]||t>positions.at(-1)||(i&&t<=support[i-1]))||support.length!==(p.curve.smoothed??[]).length)fail('Invalid fitted coordinates.');probabilityRows(p.curve.smoothed??[],cats.length);
    for(const b of p.curve.boundaries??[])if(!positions.some((t,i)=>t===b.left&&positions[i+1]===b.right))fail('Boundary outside adjacent measured positions.');
    const counts=new Map(),seen=new Set();
    for(const branch of array(record.branches,'continuations')){
      if(!positions.includes(branch.t)||!integer(branch.tok_id))fail('Invalid continuation checkpoint or branch token.');
      const observations=array(branch.observations,'continuation records'),labels=array(branch.answers,'continuation labels');
      if(observations.length!==labels.length||array(branch.continuation_ids,'continuation tokens').length!==labels.length)fail('Continuation record length mismatch.');
      for(let i=0;i<labels.length;i++){
        ids(branch.continuation_ids[i],'continuation');if(!cats.includes(labels[i])||observations[i]?.label!==labels[i])fail('Continuation labels disagree.');
        if(record.sampling_design==='position_mixture_v1'){
          const d=branch.draw_indices?.[i],key=branch.t+':'+d;if(!integer(d)||seen.has(key))fail('Duplicate or invalid continuation index.');seen.add(key);
          if(!counts.has(branch.t))counts.set(branch.t,Array(cats.length).fill(0));counts.get(branch.t)[cats.indexOf(labels[i])]++;
        }
      }
    }
    if(record.sampling_design==='position_mixture_v1')positions.forEach((t,i)=>{const c=counts.get(t),total=c?.reduce((a,b)=>a+b,0);if(!total||c.some((n,j)=>Math.abs(n/total-rows[i][j])>1e-8))fail('Graph proportions disagree with recorded continuations.');});
  }
  for(const k of Object.keys(r.records))if(!names.has(k)&&!['dense','replay-verification','import-info'].includes(k))fail('Unexpected evidence record.');
  return r;
}
function sourceResponse(run,pass,draw){
  const rec=run.records[pass];if(!rec)fail('Missing source pass.');
  if(!draw)return rec.base.gen_ids;
  const matches=rec.branches.flatMap(b=>b.t===draw.checkpoint?(b.draw_indices??[]).flatMap((d,i)=>d===draw.draw_index?[rec.base.gen_ids.slice(0,b.t).concat(b.tok_id,b.continuation_ids[i])]:[]):[]);
  if(matches.length!==1)fail('Missing or ambiguous source continuation.');return matches[0];
}
// Python's historical prefix hashes used JSON separators with spaces.
const prefixJSON=v=>Array.isArray(v)?'['+v.map(prefixJSON).join(', ')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>stringJSON(k)+': '+prefixJSON(v[k])).join(', ')+'}':canonical(v);
async function validateArtifact(a,runs){
  identifier(a.id);if(a.status!=='complete')fail('Only completed inspection artifacts can be imported.');
  const q=a.request??{},r=runs.get(q.source_run_id);if(!r||!r.records[q.source_pass_id])fail('Inspection source is missing.');
  if(['model_id','resolved_revision'].some(k=>a.model?.[k]!==r.model[k]))fail('Inspection model differs from its source.');
  if(a.schema==='fork-lens-v1'){
    const arms=new Map();for(const arm of array(a.arms,'lens arms')){
      if(!arm.id||arms.has(arm.id))fail('Invalid lens arm.');arms.set(arm.id,arm);
      ids(arm.input_ids,'lens input');ids(arm.response_ids,'lens response');
      if(!same(arm.prompt_ids,r.records[q.source_pass_id].base.prompt_ids))fail('Lens prompt differs from source.');
      if(arm.source_draw&&!same(arm.response_ids,sourceResponse(r,q.source_pass_id,arm.source_draw)))fail('Lens continuation differs from saved tokens.');
      if(!same(arm.input_ids,[...arm.prompt_ids,...arm.response_ids].slice(0,arm.input_ids.length))||await sha(prefixJSON(arm.input_ids))!==arm.prefix_sha256)fail('Lens prefix checksum mismatch.');
    }
    if(!arms.size)fail('Missing lens arms.');
    for(const c of array(a.cells,'lens cells'))if(!arms.has(c.arm)||!['layer','index','absolute_position','token_id'].every(k=>integer(c[k]))||!q.layers?.includes(c.layer)||c.index<q.start||c.index>q.end||!['tokens','logit_lens_tokens','model_tokens'].every(k=>Array.isArray(c[k])))fail('Invalid lens cell.');
  }else if(a.schema==='fork-activation-patch-v1'){
    for(const [k,lo,hi] of [['samples',2,32],['cont_max',1,512],['seed',0,2147483647],['max_seconds',1,1800]])if(!integer(q[k])||q[k]<lo||q[k]>hi)fail('Invalid patch limits.');
    if(typeof q.temperature!=='number'||q.temperature<.05||q.temperature>2||typeof q.selection_rationale!=='string'||!q.selection_rationale.trim()||q.selection_rationale.length>2000||!Array.isArray(q.layers)||!q.layers.length||q.layers.length>4||q.layers.some(x=>!integer(x))||new Set(q.layers).size!==q.layers.length)fail('Invalid patch request.');
    const base=r.records[q.source_pass_id].base;if(!same([a.source_base?.prompt_ids,a.source_base?.gen_ids],[base.prompt_ids,base.gen_ids])||await sha(prefixJSON({prompt_ids:base.prompt_ids,gen_ids:base.gen_ids}))!==a.source_ids_sha256)fail('Patch source checksum mismatch.');
    for(const name of ['donor','recipient']){
      const p=a.prefixes?.[name],s=q[name];if(!p||!s||!integer(s.position))fail('Missing patch prefix.');
      const response=sourceResponse(r,q.source_pass_id,s.selection?.type==='draw'?s.selection:null),expected=r.records[q.source_pass_id].base.prompt_ids.concat(response.slice(0,s.position+1));
      if(s.position>=response.length||!same(expected,p.input_ids)||await sha(prefixJSON(expected))!==p.prefix_sha256)fail('Patch prefix differs from source.');
    }
    if(same(a.prefixes.donor.input_ids,a.prefixes.recipient.input_ids))fail('Patch sources are identical.');
    if(a.continuations!==3*q.samples||a.max_new_tokens!==3*q.samples*q.cont_max||!same(a.answers,r.base_config.answers))fail('Patch limits or outcome rules differ.');
    const seen=new Map(),counts={baseline:{},identity:{},patched:{}},labels=r.base_config.answers??['A','B','C','D'];
    if(array(a.observations,'patch observations').length!==3*q.samples)fail('Missing patch controls.');
    for(const o of a.observations){const key=o.arm+':'+o.draw;if(!Object.hasOwn(counts,o.arm)||!integer(o.draw)||o.draw>=q.samples||seen.has(key))fail('Invalid patch observation.');seen.set(key,o);
      ids(o.continuation_ids,'patch continuation');if(o.continuation_ids.length>q.cont_max||o.seed!==(q.seed+o.draw)%2147483647||!labels.concat('Other').includes(o.label)||!['eos','length'].includes(o.stop_reason)||typeof o.continuation_text!=='string'||typeof o.full_response_text!=='string'||o.prefill_patch_count!==(o.arm==='baseline'?0:q.layers.length))fail('Invalid patch continuation.');
      counts[o.arm][o.label]=(counts[o.arm][o.label]??0)+1;
    }
    if(!same(a.summary?.counts,counts))fail('Patch summary differs from raw observations.');
  }else if(a.schema==='fork-investigation-v1'){
    if(!['edit','activation','capture'].includes(q.kind))fail('Unsupported saved inspection.');
    const base={...r.records[q.source_pass_id].base,gen_ids:sourceResponse(r,q.source_pass_id,q.source_selection??null)};
    if(!same([a.source_base?.prompt_ids,a.source_base?.gen_ids],[base.prompt_ids,base.gen_ids])||await sha(prefixJSON({prompt_ids:base.prompt_ids,gen_ids:base.gen_ids}))!==a.source_ids_sha256)fail('Inspection source tokens differ.');
    if(q.kind==='edit'){
      if(!integer(q.start)||!integer(q.end)||q.start>=q.end||q.end>base.gen_ids.length)fail('Invalid edit region.');
      ids(a.replacement_ids,'replacement');const arms={control:base.gen_ids.slice(0,q.end),edit:base.gen_ids.slice(0,q.start).concat(a.replacement_ids)};
      if(!same(arms,a.arms)||!integer(q.samples)||q.samples<2||q.samples>128||!integer(q.cont_max)||q.cont_max<1||q.cont_max>4096)fail('Invalid edit prefixes or limits.');
      const seen=new Set();if(array(a.observations,'edit observations').length!==q.samples*2)fail('Incomplete edit samples.');
      for(const o of a.observations){const key=o.arm+':'+o.draw;if(!Object.hasOwn(arms,o.arm)||!integer(o.draw)||o.draw>=q.samples||seen.has(key))fail('Invalid edit sample coordinates.');seen.add(key);ids(o.continuation_ids,'edit continuation');if(o.continuation_ids.length>q.cont_max)fail('Edit exceeds its token limit.');}
    }else{
      if(!Array.isArray(q.positions)||!Array.isArray(q.layers)||!q.positions.length||q.positions.length>16||!q.layers.length||q.layers.length>4||new Set(q.positions).size!==q.positions.length||new Set(q.layers).size!==q.layers.length||q.positions.some(x=>!integer(x)||x>base.gen_ids.length)||q.layers.some(x=>!integer(x)))fail('Invalid capture selection.');
      const rows=array(a.captures,'captures'),seen=new Set();if(rows.length!==q.positions.length*q.layers.length)fail('Incomplete capture rows.');
      for(const row of rows){const key=row.checkpoint+':'+row.layer,prefix=base.prompt_ids.concat(base.gen_ids.slice(0,row.checkpoint));if(!q.positions.includes(row.checkpoint)||!q.layers.includes(row.layer)||seen.has(key)||!same(prefix,row.prefix_ids)||await sha(prefixJSON(prefix))!==row.prefix_sha256||row.absolute_position!==prefix.length-1)fail('Capture prefix provenance mismatch.');seen.add(key);if(!Array.isArray(row.vector)||!row.vector.length||row.vector.some(x=>typeof x!=='number'||!Number.isFinite(x)))fail('Invalid capture vector.');}
    }
  }else fail('Unsupported inspection schema.');
}
export async function validateOfflineBundle(bundle){
  if(new TextEncoder().encode(JSON.stringify(bundle)).length>MAX_BYTES)fail('Choose an evidence bundle under 64 MiB.');
  if(!/^fork-investigation-bundle-v[123]$/.test(bundle?.schema))fail('Use Export investigation to create a portable bundle (v1, v2 or v3).');
  safeTree(bundle);
  if(Object.keys(bundle).some(k=>!['schema','manifest','payload','sha256'].includes(k)))fail('Unexpected bundle fields.');
  const p=bundle.payload,m=bundle.manifest;if(!p||!m||await sha(canonical(p))!==bundle.sha256)fail('Evidence checksum mismatch. The file may be damaged or modified.');
  const kinds=['runs','lenses','patches','responses','edits','captures'];
  if(Object.keys(p).some(k=>!kinds.includes(k)&&k!=='investigation'))fail('Unknown payload collection.');
  const all=new Set(),runs=new Map();
  for(const kind of kinds){const rows=array(p[kind]??[],kind);if(rows.length>100)fail('Too many artifacts.');for(const a of rows){identifier(a.id);if(all.has(a.id))fail('Duplicate artifact ID.');all.add(a.id);}if(m[({lenses:'lens',patches:'patch'}[kind]??kind.slice(0,-1))+'_ids']&&!same(m[({lenses:'lens',patches:'patch'}[kind]??kind.slice(0,-1))+'_ids'],rows.map(a=>a.id)))fail('Manifest/artifact mismatch.');}
  for(const r of p.runs)runs.set(r.id,validateRun(r));
  if(!same(m.run_ids,[...runs.keys()])||!same(m.lens_ids,p.lenses.map(a=>a.id))||(runs.size?!runs.has(m.entry_run_id):m.entry_run_id!==null))fail('Invalid bundle entry.');
  for(const r of runs.values()){
    let parent=r.lineage?.source_run_id;const seen=new Set([r.id]);
    while(parent){if(!runs.has(parent)||seen.has(parent))fail('Missing parent or cyclic lineage.');seen.add(parent);parent=runs.get(parent).lineage?.source_run_id;}
    if(r.lineage?.source_run_id){const parentRun=runs.get(r.lineage.source_run_id),b=r.records[r.passes[0].id].base,pb=parentRun.records[parentRun.passes[0].id].base;if(!same([b.prompt_ids,b.gen_ids],[pb.prompt_ids,pb.gen_ids]))fail('Refinement changed the original exact tokens.');}
  }
  for(const kind of ['lenses','patches','edits','captures'])for(const a of p[kind]??[])await validateArtifact(a,runs);
  for(const r of p.responses??[]){
    if(r.schema!=='fork-response-v1'||r.status!=='complete')fail('Unsupported response version or status.');
    const b=r.base??{};ids(b.prompt_ids,'response prompt');ids(b.gen_ids,'response output');
    if(!b.prompt_ids.length||!b.gen_ids.length||!['stop','length'].includes(b.finish_reason)||typeof b.base_text!=='string'||!r.model?.model_id)fail('Missing response provenance.');
    if(await sha(prefixJSON({prompt_ids:b.prompt_ids,gen_ids:b.gen_ids}))!==r.source_ids_sha256)fail('Response token checksum mismatch.');
    if(r.outcome_rule){const expected=classifyPreview(r.outcome_rule,r.raw_text??b.base_text,b.finish_reason==='stop',r.is_muse??false);expected.rule_id=await sha(prefixJSON(validateRule(r.outcome_rule)));if(!same(expected,r.classification))fail('Response classification differs from saved rules.');}
  }
  if(bundle.schema==='fork-investigation-bundle-v3'){
    if(Object.keys(p).length!==7||!kinds.every(k=>Array.isArray(p[k])))fail('Incomplete v3 payload.');
    const job=p.investigation;if(job?.schema!=='fork-workflow-v2')fail('Missing investigation record.');identifier(job.id);
    for(const kind of kinds){const key=({lenses:'lens',patches:'patch'}[kind]??kind.slice(0,-1))+'_ids';if(!same(m[key],p[kind].map(a=>a.id))||!same(job[kind],p[kind].map(a=>a.id)))fail('Investigation index mismatch.');}
    const responses=new Map(p.responses.map(r=>[r.id,r]));
    if(m.entry_response_id!==job.selected_response_id||(job.selected_response_id!==null&&!responses.has(job.selected_response_id)))fail('Missing selected response.');
    for(const search of job.searches??[])if(!Array.isArray(search.response_ids)||search.response_ids.some(id=>!responses.has(id)))fail('Missing search response.');
    for(const c of job.comparisons??[]){identifier(c.id);const run=runs.get(c.run_id);if(!run||!Array.isArray(c.draw_indices)||c.draw_indices.length!==2||c.draw_indices[0]===c.draw_indices[1])fail('Invalid comparison.');for(const d of c.draw_indices)sourceResponse(run,c.pass_id,{checkpoint:c.checkpoint,draw_index:d});}
    for(const r of runs.values())if(r.source_response_id){const response=responses.get(r.source_response_id);if(!response||['model_id','resolved_revision'].some(k=>response.model[k]!==r.model[k]))fail('Source response missing or model mismatch.');for(const pass of r.passes){const b=r.records[pass.id].base;if(!same([b.prompt_ids,b.gen_ids],[response.base.prompt_ids,response.base.gen_ids]))fail('Scan changed its exact source response.');}}
  }
  return bundle;
}
function database(){return new Promise((resolve,reject)=>{const req=indexedDB.open('fork-microscope-evidence',1);req.onupgradeneeded=()=>req.result.createObjectStore('bundles',{keyPath:'sha256'});req.onsuccess=()=>resolve(req.result);req.onerror=()=>reject(Error('Browser evidence storage is unavailable.'));});}
async function savedBundles(){const db=await database();try{return await new Promise((resolve,reject)=>{const request=db.transaction('bundles').objectStore('bundles').getAll();request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);});}finally{db.close();}}
async function persist(bundle){const db=await database();try{await new Promise((resolve,reject)=>{const tx=db.transaction('bundles','readwrite');tx.objectStore('bundles').put(bundle);tx.oncomplete=resolve;tx.onerror=()=>reject(Error('Browser storage is full or unavailable. Keep your exported file; nothing was overwritten.'));});}finally{db.close();}}
function assertNoConflicts(bundle,existing){const known=new Map();for(const b of existing)for(const kind of ['runs','lenses','patches','responses','edits','captures'])for(const a of b.payload[kind]??[])known.set(a.id,a);for(const kind of ['runs','lenses','patches','responses','edits','captures'])for(const a of bundle.payload[kind]??[])if(known.has(a.id)&&!same(known.get(a.id),a))fail('Different evidence already uses this ID. Existing data was not replaced.');}
export async function installOfflineEvidence(bundle,{persist:save=true}={}){
  await validateOfflineBundle(bundle);
  const existing=save?await savedBundles():bundles;assertNoConflicts(bundle,existing);
  if(save)await persist(bundle);
  bundles=[...existing.filter(b=>b.sha256!==bundle.sha256),bundle];active=true;
  try{globalThis.localStorage?.setItem('fork-evidence-source','browser');}catch{}
  globalThis.dispatchEvent?.(new Event('offline-evidence-change'));
  return {id:bundle.manifest.entry_run_id,run_ids:bundle.manifest.run_ids,lens_ids:bundle.manifest.lens_ids};
}
export function isOfflineEvidence(){return active;}
function recordFor(bundle){
  if(!bundle)return null;
  const existing=bundle.payload.investigation??{},r=bundle.payload.runs[0];
  if(existing.schema==='fork-workflow-v2')return existing;
  return {...existing,id:existing.id??r?.id,schema:'fork-workflow-v2',record_revision:0,status:existing.status??'idle',
    context:{provenance:'legacy_inferred',name:'Saved investigation',question:r?.base_config?.prompt??r?.base_config?.question??'',input:{prompt:r?.base_config?.prompt??r?.base_config?.question??'',mode:r?.base_config?.mode??'chat'},model:r?.model,outcome_rule:{answers:r?.categories?.filter(x=>x!=='Other')??[],method:'text_match'}},
    runs:bundle.manifest.run_ids,lenses:bundle.manifest.lens_ids,responses:[],selected_response_id:null,comparisons:[],searches:[],patches:bundle.manifest.patch_ids??[],edits:[],captures:[],conclusion:'',legacy_inferred:true};
}
export function getOfflineInvestigation(){const run=typeof location!=='undefined'?new URL(location.href).searchParams.get('run'):null;return recordFor(bundles.findLast(b=>b.manifest.run_ids.includes(run))??bundles.at(-1));}
export function getOfflineEntryRunId(){const record=getOfflineInvestigation();return bundles.findLast(b=>recordFor(b)?.id===record?.id)?.manifest.entry_run_id??null;}
export function clearOfflineEvidence(){active=false;try{globalThis.localStorage?.setItem('fork-evidence-source','worker');}catch{}globalThis.dispatchEvent?.(new Event('offline-evidence-change'));}
const reply=(data,status=200)=>new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json'}});
export async function evidenceFetch(input,init={}){
  if(!active)return (originalFetch??globalThis.fetch)(input,init);
  const u=new URL(typeof input==='string'?input:input.url,globalThis.location?.href??'http://localhost/');
  if(!u.pathname.startsWith('/api/live/'))return (originalFetch??globalThis.fetch)(input,init);
  if((init.method??'GET').toUpperCase()!=='GET'){
    if(u.pathname==='/api/live/workflow-update'&&(init.method??'').toUpperCase()==='POST'){
      try{return reply(await updateOfflineMetadata(JSON.parse(init.body)));}catch(e){return reply({error:e.message},400);}
    }
    return reply({error:'Browsing saved browser evidence. Connect compute and transfer this investigation before starting a new operation.'},409);
  }
  const route=u.pathname.slice('/api/live/'.length),id=u.searchParams.get('id'),runs=new Map(),artifacts=new Map();
  for(const b of bundles){for(const r of b.payload.runs)runs.set(r.id,r);for(const kind of ['lenses','patches','edits','captures'])for(const a of b.payload[kind]??[])artifacts.set(a.id,a);}
  if(route==='runs')return reply([...runs.values()].map(r=>({id:r.id,model:r.model.model_id,created:r.created,lineage:r.lineage,passes:r.settings.passes??r.passes,prompt:r.base_config.prompt??r.base_config.question??''})).sort((a,b)=>b.created-a.created));
  if(route==='export'||route==='result')return runs.has(id)?reply(runs.get(id)):reply({error:'Run is not in your imported browser library.'},404);
  if(route==='bundle-export'){const b=bundles.findLast(b=>b.manifest.run_ids.includes(id));return b?reply(b):reply({error:'Investigation not found in browser library.'},404);}
  if(route==='investigations')return reply([...artifacts.values()].map(a=>({id:a.id,schema:a.schema,status:a.status,request:a.request,created:a.created,kind:a.schema==='fork-lens-v1'?'lens':a.request?.kind})));
  if(route==='investigation')return artifacts.has(id)?reply(artifacts.get(id)):reply({error:'Saved inspection is not included in this bundle.'},404);
  if(route==='status')return reply({job:{status:'idle',phase:'Browsing saved evidence'},model:null,base:null,runtime:{cuda_available:false},offline:true});
  if(route.endsWith('-options'))return reply({available:false,installed:false,model:null,profiles:[],reason:'Saved evidence only. Connect compatible compute to run a new inspection.',offline:true});
  if(route==='workflow-list'||route==='workflows')return reply(bundles.map(recordFor).filter(i=>i?.id));
  if(route==='workflow'){const b=bundles.findLast(b=>recordFor(b)?.id===id);return b?reply(recordFor(b)):reply({error:'Investigation is not in the browser library.'},404);}
  if(route==='workflow-export'){const b=bundles.findLast(b=>recordFor(b)?.id===id);return b?reply(b):reply({error:'Investigation is not in the browser library.'},404);}
  if(route==='responses'){const owner=u.searchParams.get('investigation_id');return reply(bundles.filter(b=>!owner||recordFor(b)?.id===owner).flatMap(b=>b.payload.responses??[]));}
  if(route==='response'){const r=bundles.flatMap(b=>b.payload.responses??[]).find(r=>r.id===id);return r?reply(r):reply({error:'Response is not in the browser library.'},404);}
  return reply({error:'This action requires connected compute; saved evidence remains available in Explore.'},409);
}

export async function updateOfflineMetadata(request,{persist:save=true}={}){
  if(!request||Object.keys(request).some(k=>!['id','record_revision','conclusion','comparison'].includes(k)))fail('Only conclusion and comparison notes can be saved without compute.');
  const original=bundles.findLast(b=>recordFor(b)?.id===request.id);if(!original)fail('Investigation is not in your browser library.');
  const job=structuredClone(recordFor(original));if(request.record_revision!==job.record_revision)fail('Investigation changed. Reload before saving.');
  if(Object.hasOwn(request,'conclusion')){if(typeof request.conclusion!=='string'||request.conclusion.length>16000)fail('Conclusion must be text up to 16,000 characters.');job.conclusion=request.conclusion;}
  if(request.comparison){const c=request.comparison;identifier(c.id);const old=job.comparisons.find(x=>x.id===c.id);if(old&&!same(old,c))fail('A different saved comparison uses this ID.');if(!old)job.comparisons.push(c);}
  job.record_revision=(job.record_revision??0)+1;job.updated=Date.now()/1000;
  const payload={investigation:job},manifest={entry_run_id:original.manifest.entry_run_id,entry_response_id:job.selected_response_id??null,minimum_reader_version:3,note:'Saved browser annotations. Checksums verify consistency, not scientific truth.'};
  for(const kind of ['runs','lenses','patches','responses','edits','captures']){payload[kind]=original.payload[kind]??[];job[kind]=payload[kind].map(a=>a.id);manifest[({lenses:'lens',patches:'patch'}[kind]??kind.slice(0,-1))+'_ids']=job[kind];}
  const bundle={schema:'fork-investigation-bundle-v3',payload,manifest,sha256:await sha(canonical(payload))};
  await installOfflineEvidence(bundle,{persist:save});return job;
}

// Explicit data transfer only. Importing on a worker does not load a model or run it.
export async function transferOfflineEvidenceToWorker(){
  if(!active||!originalFetch)fail('Open saved browser evidence before transferring it.');
  const current=getOfflineInvestigation(),bundle=bundles.findLast(b=>recordFor(b)?.id===current?.id);
  if(!bundle)fail('No selected investigation to transfer.');
  const status=await originalFetch('/api/live/status');if(!status.ok)fail('Connect your compute worker before transferring evidence.');
  const response=await originalFetch('/api/live/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(bundle)});
  let result;try{result=await response.json();}catch{fail('The worker returned an unreadable import result. Your browser copy remains saved.');}
  if(!response.ok)fail(result.error||'Worker import failed. Your browser copy remains saved.');
  const investigation_id=bundle.schema==='fork-investigation-bundle-v3'?bundle.payload.investigation.id:null;
  clearOfflineEvidence();
  return {...result,investigation_id,run_id:result.id??bundle.manifest.entry_run_id};
}

export async function initOfflineEvidence(){
  if(initializing)return initializing;
  initializing=(async()=>{
    if(typeof window==='undefined')return;
    originalFetch=window.workerFetch?.bind(window)??window.fetch.bind(window);
    const params=new URL(location.href).searchParams;let preferred='';try{preferred=localStorage.getItem('fork-evidence-source');}catch{}
    if(params.get('demo')==='attendance'){
      const response=await fetch('/demo-attendance.json');if(!response.ok)fail('Demo evidence could not be loaded.');await installOfflineEvidence(await response.json());
    }else if(params.get('evidence')==='local'||preferred==='browser'){
      bundles=(await savedBundles()).sort((a,b)=>(a.payload.investigation?.updated??0)-(b.payload.investigation?.updated??0));for(const b of bundles)await validateOfflineBundle(b);active=Boolean(bundles.length);
    }
    window.workerFetch=evidenceFetch;
  })();
  return initializing;
}
