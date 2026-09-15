export function newPass(existing=[]){
  let i=1;while(existing.some(p=>p.id===`pass_${i}`))i++;
  const previous=existing.at(-1);
  return {id:`pass_${i}`,label:`Pass ${i}`,start:previous?.start??0,end:previous?.end??31,stride:previous?.stride??4,offset:previous?((previous.offset+1)%previous.stride):0,samples:previous?.samples??20,seed:previous?.seed??0};
}
export function resultPasses(result){
  if(result.passes)return result.passes;
  return ['first','second'].filter(id=>result[id]).map((id,i)=>({id,label:`Pass ${i+1} (legacy)`,curve:result[id],configuration:{samples:result.settings.samples}}));
}
