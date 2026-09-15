const offers=new WeakMap();
export async function saveJSON(anchor,filename,load){
  if(anchor.dataset.downloading==='true')return;
  anchor.dataset.downloading='true';anchor.setAttribute('aria-busy','true');
  let box=anchor.nextElementSibling;
  if(!box?.classList.contains('download-status')){box=document.createElement('div');box.className='download-status';box.setAttribute('role','status');anchor.after(box);}
  const previous=offers.get(anchor);if(previous)URL.revokeObjectURL(previous);
  box.replaceChildren();box.textContent='Preparing your file…';
  try{
    const data=await load(),blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'});
    const url=URL.createObjectURL(blob);offers.set(anchor,url);
    const link=document.createElement('a');link.href=url;link.download=filename;link.textContent=`Save ${filename} (${Math.ceil(blob.size/1024).toLocaleString()} KB)`;
    link.onclick=()=>{note.textContent='Download requested. Check your browser’s downloads; this link remains available to retry.';};
    const note=document.createElement('p');note.textContent='Your file is ready. Choose Save below, then check your browser’s downloads before stopping your worker.';
    box.replaceChildren(note,link);link.focus();
    // Keep the URL alive for a retry, until the next export or page unload.
    addEventListener('pagehide',()=>URL.revokeObjectURL(url),{once:true});
  }catch(e){box.textContent=`Could not save: ${e.message}. Your evidence remains on the worker. Try again.`;}
  finally{delete anchor.dataset.downloading;anchor.removeAttribute('aria-busy');}
}
