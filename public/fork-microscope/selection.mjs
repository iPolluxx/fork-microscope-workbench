// generated: Codex, fork-microscope-revamp-ASTRA-BRIEF.md — shared evidence location, no evidence storage.
export const EMPTY_SELECTION = Object.freeze({investigation_id:null,area:'explore',response_id:null,run_id:null,pass_id:null,checkpoint:null,continuation:null,pair:null,inspection_id:null,token_region:null,layers:[],return_area:null});
const ids = {investigation_id:'investigation',response_id:'response',run_id:'run',pass_id:'pass',inspection_id:'inspection'};
const integer = x => x !== null && x !== '' && Number.isSafeInteger(Number(x)) && Number(x)>=0 ? Number(x) : null;
export function reduceSelection(previous, patch) {
  const next={...EMPTY_SELECTION,...previous,...patch};
  if(Object.hasOwn(patch,'investigation_id')&&patch.investigation_id!==previous?.investigation_id)for(const k of ['response_id','run_id','pass_id','checkpoint'])if(!Object.hasOwn(patch,k))next[k]=null;
  if(Object.hasOwn(patch,'run_id')&&patch.run_id!==previous?.run_id)for(const k of ['pass_id','checkpoint'])if(!Object.hasOwn(patch,k))next[k]=null;
  const sourceChanged=['investigation_id','run_id','pass_id','checkpoint'].some(k=>Object.hasOwn(patch,k)&&patch[k]!==previous?.[k]);
  if(sourceChanged) for(const k of ['continuation','pair','inspection_id','token_region','layers']) if(!Object.hasOwn(patch,k)) next[k]=EMPTY_SELECTION[k];
  if(!['setup','explore','inspect'].includes(next.area))next.area='explore';
  next.checkpoint=integer(next.checkpoint);
  const pair=next.pair?.draw_indices;
  next.pair=Array.isArray(pair)&&pair.length===2&&pair.every(x=>integer(x)!==null)&&pair[0]!==pair[1]?{draw_indices:pair.map(Number)}:null;
  if(next.continuation) {const c=next.continuation;next.continuation=c.run_id===next.run_id&&c.pass_id===next.pass_id&&c.checkpoint===next.checkpoint&&integer(c.draw_index)!==null?{run_id:c.run_id,pass_id:c.pass_id,checkpoint:c.checkpoint,draw_index:Number(c.draw_index)}:null;}
  if(patch.continuation)next.pair=null;else if(patch.pair)next.continuation=null;
  const r=next.token_region;next.token_region=r&&['response','prompt'].includes(r.space)&&integer(r.start)!==null&&integer(r.end_exclusive)!==null&&r.end_exclusive>r.start?{space:r.space,start:Number(r.start),end_exclusive:Number(r.end_exclusive)}:null;
  next.layers=Array.isArray(next.layers)?[...new Set(next.layers.filter(x=>integer(x)!==null).map(Number))]:[];
  return next;
}
export function selectionFromURL(input, fallback={}) {
  const url=new URL(input,'http://localhost'),p=url.searchParams,patch={};
  for(const [key,param] of Object.entries(ids))if(p.has(param))patch[key]=p.get(param)||null;
  if(p.has('checkpoint'))patch.checkpoint=integer(p.get('checkpoint'));
  if(p.has('pair'))patch.pair={draw_indices:p.get('pair').split(',').map(x=>integer(x))};
  const area=p.get('area')||(url.pathname.endsWith('live.html')?'setup':url.hash==='#inspect'?'inspect':'explore');patch.area=area;
  let next=reduceSelection(fallback,patch);
  if(p.has('draw'))next=reduceSelection(next,{continuation:{run_id:next.run_id,pass_id:next.pass_id,checkpoint:next.checkpoint,draw_index:integer(p.get('draw'))}});
  return next;
}
export function selectionURL(selection, area=selection.area, base='http://localhost/') {
  const url=new URL(area==='setup'?'/live.html':'/observatory.html',base);
  for(const [key,param] of Object.entries(ids))if(selection[key])url.searchParams.set(param,selection[key]);
  if(selection.checkpoint!==null)url.searchParams.set('checkpoint',selection.checkpoint);
  if(selection.pair)url.searchParams.set('pair',selection.pair.draw_indices.join(','));
  if(selection.continuation)url.searchParams.set('draw',selection.continuation.draw_index);
  if(typeof location!=='undefined'){const source=new URL(location.href).searchParams;if(source.has('evidence')||source.has('demo'))url.searchParams.set('evidence','local');}
  url.searchParams.set('area',area);url.hash=area==='inspect'?'inspect':area==='explore'?'scan':'';
  return url.pathname+url.search+url.hash;
}
export function validateSelection(selection, result, passes) {
  let next={...selection},reason='';
  if(next.run_id!==result.id)return {selection:next,reason:'The requested scan is unavailable in this evidence source.'};
  const pass=passes.find(p=>p.id===next.pass_id);
  if(next.pass_id&&!pass){next=reduceSelection(next,{pass_id:null,checkpoint:null});reason='The saved pass is unavailable; choose a pass from this scan.';}
  const record=result.records?.[next.pass_id];
  if(record&&next.checkpoint!==null){
    const rows=(record.branches||[]).filter(b=>b.t===next.checkpoint).flatMap(b=>(b.observations||[]).map((o,i)=>({...o,draw_index:b.draw_indices?.[i]??i})));
    if(next.pair&&!next.pair.draw_indices.every(i=>rows.some(o=>o.draw_index===i))){next.pair=null;reason='The saved comparison is unavailable at this checkpoint.';}
    if(next.continuation&&!rows.some(o=>o.draw_index===next.continuation.draw_index)){next.continuation=null;reason='The saved continuation is unavailable at this checkpoint.';}
  }
  return {selection:next,reason};
}
let state={...EMPTY_SELECTION};
const listeners=new Set();
if(typeof window!=='undefined'){
  let saved={};try{saved=JSON.parse(sessionStorage.getItem('fork-selection-v1')||'{}');}catch{}
  state=selectionFromURL(location.href,saved);
  const restore=()=>{state=selectionFromURL(location.href,state);listeners.forEach(fn=>fn(state,{restored:true}));};
  addEventListener('popstate',restore);addEventListener('hashchange',restore);
}
export const getSelection=()=>state;
export function setSelection(patch,options={}) { // Options intentionally never contain prompt or token evidence.
  state=reduceSelection(state,patch);
  if(typeof window!=='undefined'){
    try{sessionStorage.setItem('fork-selection-v1',JSON.stringify(state));}catch{}
    if(options.url!==false){const url=new URL(location.href);for(const [k,p]of Object.entries(ids)){if(state[k])url.searchParams.set(p,state[k]);else url.searchParams.delete(p);}for(const p of ['checkpoint','pair','draw'])url.searchParams.delete(p);if(state.checkpoint!==null)url.searchParams.set('checkpoint',state.checkpoint);if(state.pair)url.searchParams.set('pair',state.pair.draw_indices.join(','));if(state.continuation)url.searchParams.set('draw',state.continuation.draw_index);url.searchParams.set('area',state.area);if(options.push)window.history.pushState(null,'',url);else window.history.replaceState(null,'',url);}
  }
  listeners.forEach(fn=>fn(state,options));return state;
}
export function subscribeSelection(fn){listeners.add(fn);return()=>listeners.delete(fn);}
export function saveDraft(kind,value){if(typeof sessionStorage==='undefined')return;try{sessionStorage.setItem(`fork-draft:${kind}:${state.investigation_id||''}:${state.run_id||''}:${state.pass_id||''}:${state.checkpoint??''}:${state.continuation?.draw_index??''}`,JSON.stringify(value));}catch{}}
export function readDraft(kind){if(typeof sessionStorage==='undefined')return null;try{return JSON.parse(sessionStorage.getItem(`fork-draft:${kind}:${state.investigation_id||''}:${state.run_id||''}:${state.pass_id||''}:${state.checkpoint??''}:${state.continuation?.draw_index??''}`)||'null');}catch{return null;}}
