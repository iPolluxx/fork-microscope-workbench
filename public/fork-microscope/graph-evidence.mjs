// These summaries describe saved outcome evidence; they do not infer a causal fork.
const probability=v=>typeof v==='number'&&Number.isFinite(v)&&v>=0&&v<=1;
const token=v=>Number.isInteger(v)&&v>=0;
export function wilsonInterval(successes,n){
  if(!Number.isInteger(n)||n<=0||!Number.isInteger(successes)||successes<0||successes>n)return null;
  const z=1.959963984540054,p=successes/n,d=1+z*z/n;
  const center=(p+z*z/(2*n))/d,half=z*Math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d;
  return [Math.max(0,center-half),Math.min(1,center+half)];
}
export function passEvidence(pass,record,categoryCount){
  const c=pass.curve??{},positions=c.positions??[],weighted=c.weighted??[];
  const observed=[],metaByToken=new Map((record?.positions??[]).map(p=>[p.t,p]));
  const labelsByToken=new Map();
  if(record?.sampling_design==='position_mixture_v1')for(const b of record.branches??[]){
    if(!labelsByToken.has(b.t))labelsByToken.set(b.t,[]);
    labelsByToken.get(b.t).push(...(b.answers??[]));
  }
  let previous=-1;
  positions.forEach((t,i)=>{
    const values=weighted[i];
    if(!token(t)||t<=previous||!Array.isArray(values)||values.length!==categoryCount||!values.every(probability)||Math.abs(values.reduce((a,b)=>a+b,0)-1)>1e-6)return;
    previous=t;
    const meta=metaByToken.get(t),samples=meta?.samples;
    let counts=null;
    if(record?.sampling_design==='position_mixture_v1'&&Number.isInteger(samples)&&samples>0){
      const labels=labelsByToken.get(t)??[];
      const categories=record.categories??[];
      const candidate=categories.map(label=>labels.filter(x=>x===label).length);
      if(categories.length===categoryCount&&labels.length===samples&&candidate.reduce((a,b)=>a+b,0)===samples&&candidate.every((n,k)=>Math.abs(n/samples-values[k])<1e-6))counts=candidate;
    }
    observed.push({t,values,samples:counts?samples:null,counts});
  });
  const complete=observed.length===positions.length&&observed.length>0;
  const segmentationEnabled=complete&&observed.length>=4&&!!c.parameters&&c.fit_status!=='withheld'&&c.segmentation_enabled!==false;
  const intervals=[];
  for(let i=1;i<observed.length;i++){
    const left=observed[i-1],right=observed[i];
    // Do not bridge a malformed/missing point and invent adjacency.
    if(positions.indexOf(right.t)!==positions.indexOf(left.t)+1)continue;
    const tv=left.values.reduce((s,v,k)=>s+Math.abs(v-right.values[k]),0)/2;
    const segmented=segmentationEnabled&&(c.boundaries??[]).some(b=>b.left===left.t&&b.right===right.t);
    if(tv>1e-9||segmented)intervals.push({left:left.t,right:right.t,tv,segmented,leftValues:left.values,rightValues:right.values});
  }
  intervals.sort((a,b)=>Number(b.segmented)-Number(a.segmented)||b.tv-a.tv||a.left-b.left);
  const constant=complete&&observed.every(p=>p.values.every((v,k)=>Math.abs(v-observed[0].values[k])<1e-9));
  return {observed,intervals,segmentationEnabled,constant,sparse:observed.length<4,invalidPoints:positions.length-observed.length};
}
export function reconstructionPoints(curve,k){
  if(!curve?.parameters||curve.fit_status==='withheld')return [];
  const positions=curve.positions??[],start=positions[0],end=positions.at(-1);
  if(!token(start)||!token(end)||end<start)return [];
  return (curve.support??[]).map((t,i)=>{
    const value=curve.smoothed?.[i]?.[k],low=curve.low?.[i]?.[k],high=curve.high?.[i]?.[k];
    if(!token(t)||t<start||t>end)return null;
    return {t,value:probability(value)?value:null,low:probability(low)&&probability(high)&&low<=high?low:null,high:probability(low)&&probability(high)&&low<=high?high:null};
  }).filter(Boolean);
}
export function suggestedOutcome(passes,categoryCount){
  const totals=Array(categoryCount).fill(0);
  for(const p of passes)for(const row of p.curve?.weighted??[])row?.forEach((v,k)=>{if(k<categoryCount&&probability(v))totals[k]+=v;});
  return Math.max(0,totals.indexOf(Math.max(...totals)));
}
