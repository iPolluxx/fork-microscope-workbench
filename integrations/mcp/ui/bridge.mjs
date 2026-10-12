// MCP Apps 2026-01-26. Saved-evidence reads and optional host-mediated file downloads. No direct network, credentials or model execution.
const READ_TOOLS = new Set([
  'fork_microscope_get_investigation_view',
  'fork_microscope_inspect_investigation',
  'fork_microscope_read_investigation_bytes',
]);

export function toolData(result) {
  if (result?.isError || result?.is_error) {
    const message = result.content?.filter(x => x.type === 'text').map(x => x.text).join(' ');
    throw new Error(message || 'The saved-evidence request failed.');
  }
  if (result?.structuredContent) return result.structuredContent;
  const text = result?.content?.find(x => x.type === 'text')?.text;
  if (text) return JSON.parse(text);
  throw new Error('The host returned no structured saved evidence.');
}

export function validDownloadUrl(value) {
  if (typeof value !== 'string' || !/^https:\/\//i.test(value) || value.length > 16384 || /[\u0000-\u0020\u007f]/.test(value)) throw new Error('The host returned an invalid download link.');
  let parsed;
  try { parsed = new URL(value); } catch { throw new Error('The host returned an invalid download link.'); }
  if (parsed.protocol !== 'https:' || !parsed.hostname || parsed.username || parsed.password || parsed.hash) throw new Error('The host returned an invalid download link.');
  return value;
}

export function createBridge({onResult = () => {}, onContext = () => {}, onError = () => {}, env = globalThis, timeoutMs = 20000} = {}) {
  const pending = new Map();
  let nextId = 0, origin = null, ready = false, started = false, disposed = false;
  let context = {}, observer, lastSize = '', startPromise;
  const post = message => env.parent.postMessage(message, origin && origin !== 'null' ? origin : '*');
  function request(method, params) {
    if (disposed) return Promise.reject(new Error('Viewer connection closed.'));
    const id = ++nextId;
    return new Promise((resolve, reject) => {
      const timer = env.setTimeout(() => {
        pending.delete(id);
        reject(new Error('The host did not respond. Saved evidence was not refreshed.'));
      }, timeoutMs);
      pending.set(id, {resolve, reject, timer});
      try { post({jsonrpc:'2.0', id, method, params}); }
      catch (error) { env.clearTimeout(timer); pending.delete(id); reject(error); }
    });
  }
  function notify(method, params = {}) { if (ready) post({jsonrpc:'2.0', method, params}); }
  function resize() {
    if (!ready || !env.document?.body) return;
    const height = Math.ceil(env.document.body.scrollHeight);
    const width = Math.ceil(env.document.documentElement.clientWidth);
    const size = `${width}:${height}`;
    if (size !== lastSize) { lastSize = size; notify('ui/notifications/size-changed', {width, height}); }
  }
  function receive(event) {
    if (disposed || event.source !== env.parent || env.parent === env) return;
    if (origin !== null && event.origin !== origin) return;
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
    // Hosts deliver authoritative notifications only after the initialization response.
    if (!ready) return;
    if (message.method === 'ui/notifications/tool-result') {
      try { onResult(toolData(message.params)); } catch (error) { onError(error); }
    } else if (message.method === 'ui/notifications/host-context-changed') {
      context = {...context, ...message.params}; onContext(context); resize();
    } else if (message.method === 'ui/notifications/tool-cancelled') {
      onError(new Error('The host cancelled the saved-evidence request.'));
    } else if (message.method === 'ui/resource-teardown' && message.id !== undefined) {
      post({jsonrpc:'2.0', id:message.id, result:{}}); dispose();
    }
  }
  function dispose() {
    disposed = true; ready = false; observer?.disconnect();
    env.removeEventListener('message', receive);
    for (const entry of pending.values()) {
      env.clearTimeout(entry.timer); entry.reject(new Error('Viewer connection closed.'));
    }
    pending.clear();
  }
  function start() {
    if (started) return startPromise;
    started = true;
    startPromise = (async () => {
      if (env.parent === env) throw new Error('Open this viewer through a compatible MCP Apps host.');
      env.addEventListener('message', receive);
      const initial = await request('ui/initialize', {
        protocolVersion:'2026-01-26',
        appInfo:{name:'Fork Microscope saved evidence', version:'1.0.0'},
        appCapabilities:{availableDisplayModes:['inline','fullscreen']},
      });
      if (initial?.protocolVersion !== '2026-01-26') throw new Error('Unsupported MCP Apps protocol version.');
      context = initial.hostContext || {}; ready = true;
      notify('ui/notifications/initialized'); onContext(context);
      if (env.ResizeObserver && env.document?.body) {
        observer = new env.ResizeObserver(resize); observer.observe(env.document.body);
      }
      resize();
    })();
    // start is safe for UI callers which do not await initialization.
    startPromise.catch(error => { onError(error); });
    return startPromise;
  }
  function fileExportAvailable() {
    return !disposed && typeof env.File === 'function' && typeof env.openai?.uploadFile === 'function' && typeof env.openai?.getFileDownloadUrl === 'function';
  }
  function currentExport(isCurrent) {
    if (disposed || !isCurrent()) throw new Error('Export cancelled because the investigation changed.');
  }
  async function hostFileCall(operation) {
    let timer;
    try {
      return await Promise.race([operation, new Promise((_resolve, reject) => {
        timer = env.setTimeout(() => reject(new Error('The host file request timed out.')), timeoutMs);
      })]);
    } finally { env.clearTimeout(timer); }
  }
  return {
    start, dispose, resize, fileExportAvailable,
    async prepareFileDownload(bytes, name, isCurrent = () => true) {
      if (!fileExportAvailable()) throw new Error('This host does not provide temporary file downloads.');
      if (!(bytes instanceof Uint8Array) || !/^fork-investigation-[a-f0-9]{12}\.json$/.test(name)) throw new Error('Verified JSON export is required.');
      await start(); currentExport(isCurrent);
      const host = env.openai;
      try {
        const file = new env.File([bytes], name, {type:'application/json'});
        // The default upload is temporary. Never request Library persistence.
        const uploaded = await hostFileCall(host.uploadFile(file));
        currentExport(isCurrent);
        if (typeof uploaded?.fileId !== 'string' || !uploaded.fileId || uploaded.fileId.length > 512) throw new Error('Invalid host file identity.');
        const link = await hostFileCall(host.getFileDownloadUrl({fileId:uploaded.fileId}));
        currentExport(isCurrent);
        return validDownloadUrl(link?.downloadUrl);
      } catch {
        currentExport(isCurrent);
        // Host errors may contain signed URLs. Do not render or persist them.
        throw new Error('The host could not prepare a temporary file download. No download was requested.');
      }
    },
    async openFileDownload(downloadUrl, isCurrent = () => true) {
      const href = validDownloadUrl(downloadUrl); currentExport(isCurrent);
      try {
        if (typeof env.openai?.openExternal === 'function') {
          await hostFileCall(env.openai.openExternal({href,redirectUrl:false}));
        } else {
          await start(); currentExport(isCurrent);
          const result = await request('ui/open-link', {url:href});
          if (result?.isError) throw new Error('Host rejected download navigation.');
        }
        currentExport(isCurrent);
      } catch {
        currentExport(isCurrent);
        throw new Error('The host could not open the verified download. Try preparing it again.');
      }
    },
    async callTool(name, args) {
      if (!READ_TOOLS.has(name)) throw new Error('This viewer can only read saved evidence.');
      await start();
      return toolData(await request('tools/call', {name, arguments:args}));
    },
    async requestDisplayMode(mode) {
      await start();
      if (!context.availableDisplayModes?.includes(mode)) return {mode:context.displayMode || 'inline'};
      const result = await request('ui/request-display-mode', {mode});
      context = {...context, displayMode:result.mode}; onContext(context); return result;
    },
    saveState(selection) {
      // Optional widget-local state only. Never persist evidence or use it as authority.
      if (env.openai?.setWidgetState) env.openai.setWidgetState({selection});
    },
  };
}
