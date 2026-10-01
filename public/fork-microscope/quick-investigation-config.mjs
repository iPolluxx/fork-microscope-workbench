import {validateRule} from './classification.mjs';
/** Pure configuration/phase helpers shared by the front door and acceptance tests. */
export const QUICK_DEFAULTS = Object.freeze({stride:16,samples:10,cont_max:512,temperature:1,top_k:5,threshold:.05,max_tokens:512,seed:0,max_seconds:1800,max_samples:500,max_generated_tokens:200000,max_rounds:0,refine_stride:4,refine_samples:20,min_tvd:.25});
export function quickConfig(model,prompt,answers,values={}) {
  if(!model?.model_id||!model.resolved_revision)throw Error('Connect compute and load a model first.');
  if(['main','master','latest'].includes(model.resolved_revision))throw Error('The worker must resolve the model to a pinned revision.');
  prompt=prompt.trim();answers=answers.map(x=>x.trim()).filter(Boolean);
  if(!prompt||prompt.length>16000)throw Error('Enter a prompt of 1–16,000 characters.');
  if(answers.length<2||answers.length>32||new Set(answers.map(x=>x.toLowerCase())).size!==answers.length)throw Error('Enter 2–32 distinct answers to watch for.');
  validateRule({schema:'fork-outcome-rule-v1',method:'text_match',answers});
  const v={...QUICK_DEFAULTS,...values};
  for(const [key,lo,hi] of [['stride',1,128],['samples',5,512],['cont_max',1,4096],['top_k',1,50],['max_tokens',8,4096],['seed',0,2147483647],['max_seconds',1,1e9],['max_samples',1,1e9],['max_generated_tokens',1,1e9],['max_rounds',0,8],['refine_stride',1,128],['refine_samples',5,512]])if(!Number.isInteger(v[key])||v[key]<lo||v[key]>hi)throw Error(`${key} must be a whole number from ${lo} to ${hi}.`);
  for(const [key,lo,hi] of [['temperature',.05,2],['threshold',0,1],['min_tvd',0,1]])if(!Number.isFinite(v[key])||v[key]<lo||v[key]>hi)throw Error(`${key} must be from ${lo} to ${hi}.`);
  if(v.max_generated_tokens<v.max_tokens)throw Error('The token allowance must cover the original response limit.');
  return {model:{model_id:model.model_id,revision:model.resolved_revision,device:model.device||'auto',batch_size:model.batch_size||1},base:{prompt,answers,mode:model.chat_template?'chat':'base',max_tokens:v.max_tokens,seed:v.seed},scan:{start:0,end:null,stride:v.stride,samples:v.samples,cont_max:v.cont_max,temperature:v.temperature,top_k:v.top_k,threshold:v.threshold,seed:v.seed},refinement:{max_rounds:v.max_rounds,stride:v.refine_stride,samples:v.refine_samples,min_tvd:v.min_tvd},lens:null,limits:{max_seconds:v.max_seconds,max_samples:v.max_samples,max_generated_tokens:v.max_generated_tokens}};
}
export function quickAllowance(config){
 const checkpoints=Math.floor((config.base.max_tokens-1)/config.scan.stride)+1;
 const samples=checkpoints*config.scan.samples;
 return {checkpoints,samples,tokens:config.base.max_tokens+samples*config.scan.cont_max,exceeds:samples>config.limits.max_samples||config.base.max_tokens+samples*config.scan.cont_max>config.limits.max_generated_tokens};
}
export function quickPhase(job){
 if(!job)return 0;
 if(job.runs?.length)return job.status==='running'?2:3;
 if(job.pending?.action==='run'||job.steps?.some(s=>s.action==='run'))return 2;
 return 1;
}
