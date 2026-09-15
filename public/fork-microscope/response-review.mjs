export function reviewState(base,{dirty=false,reviewed=false,acceptOther=false}={}){
  if(!base)return {canReview:false,canScan:false,message:'Generate an original response first.'};
  if(dirty)return {canReview:false,canScan:false,message:'The prompt or response settings changed. Generate a new original response.'};
  if(base.finish_reason!=='stop')return {canReview:false,canScan:false,message:'This response hit its token limit before finishing. Increase the base token cap or revise your prompt, then generate again.'};
  if(!base.readout)return {canReview:false,canScan:false,message:'This worker needs an update to preview answer matching. Update Fork Microscope on your compute and reconnect.'};
  const other=base.readout.status!=='matched';
  return {canReview:!other||acceptOther,canScan:reviewed&&(!other||acceptOther),other,
    message:other?(base.readout.status==='ambiguous'?`Multiple outcome texts matched: ${base.readout.matched_answers.join(', ')}. This reply is classified as Other.`:'No tracked outcome matched the completed reply. This reply is classified as Other.'):`Matched outcome: ${base.readout.label}. Check that the text expresses that outcome; mentions and rejected alternatives can also match.`};
}
export function fullTracePass(pass,length){
  const end=Math.max(1,length-1);
  // About 16 checkpoints by default; a shorter response uses every token.
  return {...pass,start:0,end,stride:Math.min(128,Math.max(1,Math.ceil(end/15))),offset:0};
}
