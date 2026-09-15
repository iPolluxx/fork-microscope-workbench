/** Display only reported hardware facts; GPU presence is not model-fit validation. */
export function computeReadiness(connected, runtime, device = 'auto', model = null) {
  if (!connected) return {kind:'offline', blocked:false, title:'Compute disconnected', summary:'No reachable compute runtime.', message:'Connect a machine using the connection control above. You can prepare your prompt while disconnected.'};
  if (typeof runtime?.cuda_available !== 'boolean') return {kind:'unknown', blocked:false, title:'Hardware not reported', summary:'The runtime is reachable; its hardware is unverified.', message:'Check the machine’s GPU and memory before loading a large model. No execution-time estimate is available.'};
  const memory = Number.isFinite(runtime.system_memory_gb) ? ` · ${runtime.system_memory_gb.toFixed(1)} GB system memory` : '';
  const summary = runtime.cuda_available ? `CUDA GPU: ${runtime.gpu_name || 'available'}${memory}` : `CPU runtime${memory} · No CUDA GPU detected`;
  if (!runtime.cuda_available && device === 'cuda') return {kind:'blocked', blocked:true, title:'Selected GPU is unavailable', summary, message:'Connect a CUDA GPU machine, or choose CPU in model hardware settings for a compatible smaller model.'};
  const cpu = !runtime.cuda_available || model?.device === 'cpu' || (!model && device === 'cpu');
  if (cpu) return {kind:'cpu', blocked:false, title:model?.device === 'cpu' ? 'Model running on CPU' : 'CPU execution selected', summary, message:'Large continuation scans and Jacobian lens calculations can be very slow on CPU. Start with a small model and a limited run; speed has not been measured.'};
  return {kind:'gpu', blocked:false, title:'CUDA GPU detected', summary, message:'GPU presence does not guarantee this model fits. Inspect model requirements before loading; memory use and speed depend on the model and settings.'};
}
