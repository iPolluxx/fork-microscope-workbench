import test from 'node:test';
import assert from 'node:assert/strict';
import { File } from 'node:buffer';
import {createBridge, toolData, validDownloadUrl} from '../ui/bridge.mjs';

function harness() {
  const sent = [], results = [], errors = [], contexts = [];
  let listener;
  const env = {
    parent:{postMessage(message, origin) {sent.push({message, origin});}},
    addEventListener(type, fn) {if(type === 'message') listener = fn;},
    removeEventListener() {listener = null;}, setTimeout, clearTimeout,
    document:{body:{scrollHeight:300}, documentElement:{clientWidth:390}},
  };
  const emit = (data, source=env.parent, origin='https://host.example') => listener?.({data, source, origin});
  const bridge = createBridge({env, timeoutMs:100, onResult:r=>results.push(r), onError:e=>errors.push(e), onContext:c=>contexts.push(c)});
  async function init(context={availableDisplayModes:['inline']}) {
    const started = bridge.start();
    const request = sent[0].message;
    assert.equal(request.method,'ui/initialize');
    emit({jsonrpc:'2.0', id:request.id, result:{protocolVersion:'2026-01-26',hostContext:context}});
    await started;
  }
  return {env,sent,results,errors,contexts,emit,bridge,init};
}

test('initializes, announces readiness, sizes and receives only parent evidence', async () => {
  const h=harness(); await h.init();
  assert(h.sent.some(x=>x.message.method==='ui/notifications/initialized'));
  assert(h.sent.some(x=>x.message.method==='ui/notifications/size-changed' && x.message.params.width===390));
  const update={jsonrpc:'2.0',method:'ui/notifications/tool-result',params:{structuredContent:{schema:'saved'}}};
  h.emit(update, {}); h.emit(update,h.env.parent,'https://other.example');
  assert.equal(h.results.length,0);
  h.emit(update); assert.deepEqual(h.results,[{schema:'saved'}]); h.bridge.dispose();
});

test('UI can request saved evidence only and responses do not bypass UI race guards', async () => {
  const h=harness(); await h.init();
  await assert.rejects(h.bridge.callTool('fork_microscope_start_workflow',{}),/only read/);
  const promise=h.bridge.callTool('fork_microscope_get_investigation_view',{bundle_id:'saved.json',expected_file_sha256:'a'.repeat(64)});
  await Promise.resolve();
  const request=h.sent.at(-1).message;
  assert.equal(request.method,'tools/call');
  assert.equal(h.sent.at(-1).origin,'https://host.example');
  h.emit({jsonrpc:'2.0',id:request.id,result:{structuredContent:{selection:{token_position:16}}}});
  assert.equal((await promise).selection.token_position,16);
  assert.equal(h.results.length,0);h.bridge.dispose();
});

test('host context merges and unsupported fullscreen does not send a request', async () => {
  const h=harness();await h.init({theme:'dark',availableDisplayModes:['inline']});
  const count=h.sent.length;
  assert.equal((await h.bridge.requestDisplayMode('fullscreen')).mode,'inline');
  assert.equal(h.sent.length,count);
  h.emit({jsonrpc:'2.0',method:'ui/notifications/host-context-changed',params:{displayMode:'inline'}});
  assert.equal(h.contexts.at(-1).theme,'dark');h.bridge.dispose();
});

test('host errors and teardown reject outstanding reads without silent success', async () => {
  const h=harness();await h.init();
  const promise=h.bridge.callTool('fork_microscope_read_investigation_bytes',{});
  await Promise.resolve();
  const request=h.sent.at(-1).message;
  h.emit({jsonrpc:'2.0',id:request.id,result:{isError:true,content:[{type:'text',text:'Evidence changed'}]}});
  await assert.rejects(promise,/Evidence changed/);
  h.emit({jsonrpc:'2.0',id:500,method:'ui/resource-teardown',params:{}});
  assert(h.sent.some(x=>x.message.id===500 && x.message.result));
  await assert.rejects(h.bridge.callTool('fork_microscope_read_investigation_bytes',{}),/closed/);
});

test('missing host response times out and reports the connection limit', async () => {
  const h=harness();await assert.rejects(h.bridge.start(),/did not respond/);
  assert.equal(h.errors.length,1);h.bridge.dispose();
});

test('toolData supports text clients but rejects errors and empty results', () => {
  assert.deepEqual(toolData({content:[{type:'text',text:'{"ok":true}'}]}),{ok:true});
  assert.throws(()=>toolData({isError:true}),/failed/);
  assert.throws(()=>toolData({}),/no structured/);
});


test('optional host file helpers upload exact JSON bytes without Library persistence and validate the URL', async () => {
  const h=harness(); await h.init();
  assert.equal(h.bridge.fileExportAvailable(),false);
  await assert.rejects(h.bridge.prepareFileDownload(new Uint8Array([1]),'fork-investigation-aaaaaaaaaaaa.json'),/does not provide/);
  const uploads=[], downloadArgs=[], opened=[]; h.env.File=File;
  h.env.openai={async uploadFile(...args) { uploads.push(args); return {fileId:'file-fixture'}; }, async getFileDownloadUrl(args) { downloadArgs.push(args); return {downloadUrl:'https://files.example/fixture.json?signature=fixture'}; },async openExternal(args) {opened.push(args);} };
  assert.equal(h.bridge.fileExportAvailable(),true);
  const bytes=new Uint8Array([0,10,13,127,128,255]);
  const url=await h.bridge.prepareFileDownload(bytes,'fork-investigation-aaaaaaaaaaaa.json');
  assert.equal(uploads.length,1); assert.equal(uploads[0].length,1);
  assert.equal(uploads[0][0].name,'fork-investigation-aaaaaaaaaaaa.json');
  assert.equal(uploads[0][0].type,'application/json');
  assert.deepEqual(new Uint8Array(await uploads[0][0].arrayBuffer()),bytes);
  assert.deepEqual(downloadArgs,[{fileId:'file-fixture'}]);
  assert.equal(opened.length,0);
  await h.bridge.openFileDownload(url);
  assert.deepEqual(opened,[{href:url,redirectUrl:false}]);
  h.bridge.dispose();
});

test('download URLs reject insecure schemes, credentials, fragments and malformed values', () => {
  for (const value of ['http://files.example/file','javascript:alert(1)','data:application/json,{}','blob:https://files.example/a','https://name:secret@files.example/file','https://files.example/file#fragment','https://files.example/file\n',null]) assert.throws(()=>validDownloadUrl(value),/invalid download link/);
  assert.equal(validDownloadUrl('https://files.example/file?signature=fixture'),'https://files.example/file?signature=fixture');
});

test('file preparation cancels before upload or after its result and sanitizes host failures', async () => {
  const h=harness(); await h.init(); h.env.File=File; let active=false, uploaded=0, urls=0;
  h.env.openai={async uploadFile() {uploaded++; active=false; return {fileId:'file-fixture'};},async getFileDownloadUrl() {urls++;return {downloadUrl:'https://files.example/file'};}};
  await assert.rejects(h.bridge.prepareFileDownload(new Uint8Array([1]),'fork-investigation-aaaaaaaaaaaa.json',()=>active),/cancelled/);
  assert.equal(uploaded,0); active=true;
  await assert.rejects(h.bridge.prepareFileDownload(new Uint8Array([1]),'fork-investigation-aaaaaaaaaaaa.json',()=>active),/cancelled/);
  assert.equal(uploaded,1); assert.equal(urls,0);
  h.env.openai.uploadFile=async()=>{throw new Error('private signed location must stay hidden');};
  await assert.rejects(h.bridge.prepareFileDownload(new Uint8Array([1]),'fork-investigation-aaaaaaaaaaaa.json'),error=>/could not prepare/.test(error.message)&&!error.message.includes('private signed'));
  h.env.openai.uploadFile=async()=>({fileId:'file-fixture'}); h.env.openai.getFileDownloadUrl=async()=>({downloadUrl:'http://files.example/file'});
  await assert.rejects(h.bridge.prepareFileDownload(new Uint8Array([1]),'fork-investigation-aaaaaaaaaaaa.json'),/could not prepare/);
  h.bridge.dispose();
});

test('opening uses standard MCP ui/open-link when the optional openExternal helper is absent', async () => {
  const h=harness(); await h.init();
  const count=h.sent.length;
  await assert.rejects(h.bridge.openFileDownload('https://files.example/file',()=>false),/cancelled/); assert.equal(h.sent.length,count);
  const opened=h.bridge.openFileDownload('https://files.example/file'); await Promise.resolve();
  const request=h.sent.at(-1).message; assert.equal(request.method,'ui/open-link'); assert.deepEqual(request.params,{url:'https://files.example/file'});
  h.emit({jsonrpc:'2.0',id:request.id,result:{}}); await opened;
  h.bridge.dispose();
});


test('incomplete file APIs remain unavailable and late download URLs cannot escape cancellation', async () => {
  const h=harness(); await h.init();h.env.File=File;h.env.openai={uploadFile:async()=>({fileId:'file-fixture'})};
  assert.equal(h.bridge.fileExportAvailable(),false);
  let active=true,opened=0;
  h.env.openai.getFileDownloadUrl=async()=>{active=false;return {downloadUrl:'https://files.example/file'};};
  h.env.openai.openExternal=async()=>{opened++;};
  await assert.rejects(h.bridge.prepareFileDownload(new Uint8Array([1]),'fork-investigation-aaaaaaaaaaaa.json',()=>active),/cancelled/);
  assert.equal(opened,0);
  h.env.openai.uploadFile=()=>new Promise(()=>{});
  await assert.rejects(h.bridge.prepareFileDownload(new Uint8Array([1]),'fork-investigation-aaaaaaaaaaaa.json'),/could not prepare/);
  h.bridge.dispose();
});
