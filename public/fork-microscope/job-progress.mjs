export function duration(seconds){
  seconds=Math.max(0,Math.round(seconds));
  return seconds<60?`${seconds}s`:seconds<3600?`${Math.floor(seconds/60)}m ${seconds%60}s`:`${Math.floor(seconds/3600)}h ${Math.floor(seconds%3600/60)}m`;
}
const fmt=n=>Number(n).toLocaleString('en-US',{maximumFractionDigits:0});
const bytes=n=>n>=2**30?`${(n/2**30).toFixed(2)} GiB`:`${(n/2**20).toFixed(1)} MiB`;
export function jobProgress(job,now=Date.now()/1000){
  if(!job)return '';
  const elapsed=job.started?duration((job.finished??now)-job.started):null;
  const parts=elapsed?[`${elapsed} elapsed`]:[];
  if(job.total>0)parts.push(`${fmt(job.completed??0)}/${fmt(job.total)} ${job.progress_unit??'continuations'}${job.progress_unit==='tokens'?' replayed':' saved'}`);
  if(job.status!=='running')return parts.join(' · ');
  const a=job.activity;
  if(a?.kind==='generation')parts.push(`Generating token ${fmt(a.step)}/${fmt(a.cap)} max${a.batches>1?` · batch ${a.batch}/${a.batches}`:''}`);
  if(a?.kind==='download')parts.push(a.unit==='bytes'?`${bytes(a.completed)}${a.total?` / ${bytes(a.total)}`:''} of weights prepared`:`${fmt(a.completed)}${a.total?` / ${fmt(a.total)}`:''} weight files ready`);
  if(a?.kind==='download'&&a.rate>0&&a.total>a.completed)parts.push(`About ${duration((a.total-a.completed)/a.rate)} to prepare remaining weights at this pace; memory loading follows`);
  if(job.generated_tokens>0&&job.collection_started){
    const seconds=Math.max(1,now-job.collection_started);
    parts.push(`${(job.generated_tokens/seconds).toFixed(1)} saved tokens/s`);
    if(job.completed>=2&&job.total>job.completed)parts.push(`About ${duration(seconds/job.completed*(job.total-job.completed))} left at this run’s pace; lengths vary`);
  }else if(job.action==='load')parts.push('Preparing weights and allocating memory can take time; no reliable finish estimate yet');
  else if(job.total>0&&job.progress_unit!=='tokens')parts.push('Time estimate appears after two continuations finish');
  return parts.join(' · ');
}
