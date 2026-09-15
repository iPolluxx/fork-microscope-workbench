export function plan(config, lastToken) {
  const keys = ['row', 'samples', 'stride', 'shift', 'start', 'end', 'draw_start'];
  if (keys.some(k => !Number.isInteger(config[k]))) throw new Error('Use whole numbers for sampling settings.');
  const {row,samples,stride,shift,start,end,draw_start} = config;
  if (row<0 || row>99) throw new Error('Choose a released question.');
  if(samples<1 || samples>200 || draw_start<0 || draw_start+samples>200) throw new Error('Samples plus draw start must fit within the 200 recorded draws.');
  if(stride<2 || stride>64) throw new Error('Checkpoint spacing must be 2 to 64 tokens.');
  if(shift<1 || shift>=stride) throw new Error('The second-pass shift must be smaller than the spacing.');
  if(start<0 || start>=end || end>lastToken) throw new Error(`Choose a region between token 0 and ${lastToken}.`);
  const first=[];
  for(let t=start;t+shift<=end;t+=stride) first.push(t);
  if(first.length<2) throw new Error('Widen the region to fit at least two checkpoints in each pass.');
  return {first,second:first.map(t=>t+shift),dense:Array.from({length:end-start+1},(_,i)=>start+i)};
}

export function costs(config, lengths, grids) {
  const sum = indices=>config.samples*indices.reduce((total,t)=>total+lengths[t],0);
  const first=sum(grids.first), second=sum(grids.second), dense=sum(grids.dense);
  return {first,second,dense,combined:first+second,saving:dense ? 1-(first+second)/dense : null,
    perPass:grids.first.length,combinedCheckpoints:grids.first.length+grids.second.length,
    denseCheckpoints:grids.dense.length,combinedContinuations:config.samples*(grids.first.length+grids.second.length),
    denseContinuations:config.samples*grids.dense.length};
}

export function gpuEstimate(tokens, throughput, hourlyRate) {
  if(!Number.isFinite(throughput) || throughput<=0) return {hours:null,dollars:null};
  const hours=tokens/throughput/3600;
  return {hours,dollars:Number.isFinite(hourlyRate) && hourlyRate>=0 ? hours*hourlyRate : null};
}
