// MCP Apps 2026-01-26. Compute commands use the authenticated host, never browser credentials.
const COMPUTE_TOOLS = new Set([
  'fork_microscope_compute_status',
  'fork_microscope_approve_plan',
  'fork_microscope_cancel_investigation',
  'fork_microscope_finish_compute',
  'fork_microscope_save_hosted_evidence',
  'fork_microscope_render_investigation',
]);

export function computeToolData(result) {
  if (result?.isError || result?.is_error) {
    const message = result.content?.filter(item => item.type === 'text').map(item => item.text).join(' ');
    throw new Error(message || 'The compute request failed.');
  }
  if (result?.structuredContent) return result.structuredContent;
  const text = result?.content?.find(item => item.type === 'text')?.text;
  if (text) return JSON.parse(text);
  throw new Error('The host returned no structured compute status.');
}

export function createComputeBridge({onResult = () => {}, onContext = () => {}, onError = () => {}, env = globalThis, timeoutMs = 30000} = {}) {
  const pending = new Map();
  let sequence = 0, origin = null, ready = false, started = false, disposed = false;
  let observer, lastSize = '', startPromise;
  const post = message => env.parent.postMessage(message, origin && origin !== 'null' ? origin : '*');
  function request(method, params) {
    if (disposed) return Promise.reject(new Error('Compute connection closed.'));
    const id = ++sequence;
    return new Promise((resolve, reject) => {
      const timer = env.setTimeout(() => {
        pending.delete(id);
        reject(new Error('No host response. The action may have completed; refresh status or retry the same action.'));
      }, timeoutMs);
      pending.set(id, {resolve, reject, timer});
      try { post({jsonrpc: '2.0', id, method, params}); }
      catch (error) { env.clearTimeout(timer); pending.delete(id); reject(error); }
    });
  }
  function resize() {
    if (!ready || !env.document?.body) return;
    const height = Math.ceil(env.document.body.scrollHeight);
    const width = Math.ceil(env.document.documentElement.clientWidth);
    const size = `${width}:${height}`;
    if (size !== lastSize) {
      lastSize = size;
      post({jsonrpc: '2.0', method: 'ui/notifications/size-changed', params: {width, height}});
    }
  }
  function receive(event) {
    if (disposed || event.source !== env.parent || env.parent === env || (origin !== null && event.origin !== origin)) return;
    const message = event.data;
    if (!message || message.jsonrpc !== '2.0') return;
    if (message.id !== undefined && pending.has(message.id) && !message.method) {
      if (origin === null) origin = event.origin;
      const entry = pending.get(message.id);
      pending.delete(message.id); env.clearTimeout(entry.timer);
      if (message.error) entry.reject(new Error(message.error.message || 'Host request failed.'));
      else entry.resolve(message.result);
      return;
    }
    if (!ready) return;
    if (message.method === 'ui/notifications/tool-result') {
      try { onResult(computeToolData(message.params)); } catch (error) { onError(error); }
    } else if (message.method === 'ui/notifications/host-context-changed') {
      onContext(message.params || {}); resize();
    } else if (message.method === 'ui/notifications/tool-cancelled') {
      onError(new Error('The host stopped waiting. Refresh status to check whether compute is still running.'));
    } else if (message.method === 'ui/resource-teardown' && message.id !== undefined) {
      post({jsonrpc: '2.0', id: message.id, result: {}}); dispose();
    }
  }
  function dispose() {
    disposed = true; ready = false; observer?.disconnect();
    env.removeEventListener('message', receive);
    for (const entry of pending.values()) {
      env.clearTimeout(entry.timer); entry.reject(new Error('Compute connection closed.'));
    }
    pending.clear();
  }
  function start() {
    if (started) return startPromise;
    started = true;
    startPromise = (async () => {
      if (env.parent === env) throw new Error('Open managed compute through a compatible MCP Apps host.');
      env.addEventListener('message', receive);
      const initial = await request('ui/initialize', {
        protocolVersion: '2026-01-26',
        appInfo: {name: 'Fork Microscope managed compute', version: '1.0.0'},
        appCapabilities: {availableDisplayModes: ['inline']},
      });
      if (initial?.protocolVersion !== '2026-01-26') throw new Error('Unsupported MCP Apps protocol version.');
      ready = true;
      post({jsonrpc: '2.0', method: 'ui/notifications/initialized', params: {}});
      onContext(initial.hostContext || {});
      if (env.ResizeObserver && env.document?.body) {
        observer = new env.ResizeObserver(resize); observer.observe(env.document.body);
      }
      resize();
    })();
    startPromise.catch(onError);
    return startPromise;
  }
  return {
    start, dispose, resize,
    async callTool(name, args = {}) {
      if (!COMPUTE_TOOLS.has(name)) throw new Error('This panel only permits managed compute and saved-evidence actions.');
      await start();
      return computeToolData(await request('tools/call', {name, arguments: args}));
    },
  };
}
