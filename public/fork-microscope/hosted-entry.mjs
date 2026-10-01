// Additive entry point: self-hosted/offline users retain the current local workflow.
try {
  const response=await fetch('/hosted-config.json',{cache:'no-store',credentials:'omit'});
  if(response.ok && (await response.json()).enabled===true)document.getElementById('hosted-entry').hidden=false;
} catch { /* Configuration absent: local workflow remains usable. */ }
