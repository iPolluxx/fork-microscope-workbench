// generated: Codex — fork-microscope-revamp-ASTRA-BRIEF.md, Stage C.
// Importing an archive never attaches a model or starts generation.
import {initOfflineEvidence,installOfflineEvidence} from './offline-evidence.mjs';
await initOfflineEvidence();
const input = document.getElementById('evidence-import');
const button = document.getElementById('import-evidence');
const status = document.getElementById('import-status');
button?.addEventListener('click', () => input.click());
input?.addEventListener('change', async () => {
  const file = input.files?.[0];
  if (!file) return;
  button.disabled = true;
  try {
    if (file.size > 64 * 1024 * 1024) throw Error('Choose an evidence export under 64 MiB.');
    status.textContent = 'Validating saved evidence. No model or compute will be started…';
    let value;
    try { value=JSON.parse(await file.text()); } catch { throw Error('This is not valid JSON. Choose the file downloaded with Export investigation.'); }
    if(value.schema?.startsWith('fork-investigation-bundle-')){
      const result=await installOfflineEvidence(value);
      status.textContent=`Saved ${result.run_ids.length} linked scans and ${result.lens_ids.length} lens readouts in this browser. Keep the file as your portable backup. Opening…`;
      location.assign('/observatory.html?evidence=local'+(result.id?'&run='+encodeURIComponent(result.id):'')+(value.payload.investigation?.id?'&investigation='+encodeURIComponent(value.payload.investigation.id):''));
    }else{
      // Legacy standalone run files remain supported by the existing worker importer.
      // Complete portable bundles are the worker-free path and are never uploaded.
      const response=await window.workerFetch('/api/live/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});
      const result=await response.json();
      if(!response.ok)throw Error(result.error||'Could not import this standalone run. Use Export investigation for worker-free browsing.');
      location.assign('/observatory.html?run='+encodeURIComponent(result.id));
    }
  } catch (error) {
    status.textContent = error instanceof TypeError ? 'Cannot reach compute for this standalone run. Import an investigation bundle to browse entirely in this browser.' : error.message;
  } finally {
    button.disabled = false;
    input.value = '';
  }
});
