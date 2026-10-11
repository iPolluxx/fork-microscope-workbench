/** A file stays in this page's memory and goes directly to the authenticated API. */
export const MAX_IMPORT_BYTES = 16 * 1024 * 1024;
const HASH = /^[a-f0-9]{64}$/;
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);

export function createSavedImportController({getClient, onChange = () => {}, cryptoImpl = globalThis.crypto, uuid = () => cryptoImpl.randomUUID(), maxBytes = MAX_IMPORT_BYTES} = {}) {
  let pending = false, generation = 0, error = '', result = null;
  const intents = new Map();
  const state = () => ({pending, error, result});
  const emit = () => onChange(state());
  return {
    state,
    reset() { generation++; pending = false; error = ''; result = null; intents.clear(); emit(); },
    async submit(file, synthetic = false) {
      if (pending) return false;
      const client = getClient();
      if (!client?.user) { error = 'Sign in to your invited account before importing a saved file.'; emit(); return false; }
      const ticket = generation, owner = client.user, epoch = client.epoch;
      const current = () => ticket === generation && client.user === owner && client.epoch === epoch;
      pending = true; error = ''; result = null; emit();
      try {
        if (!file || typeof file.arrayBuffer !== 'function') throw Error('Choose one exported investigation JSON file.');
        if (!Number.isSafeInteger(file.size) || file.size <= 0 || file.size > maxBytes) throw Error(`Choose a nonempty JSON file no larger than ${maxBytes / 1024 / 1024} MiB.`);
        const bytes = new Uint8Array(await file.arrayBuffer());
        if (!current()) return false;
        if (!bytes.byteLength || bytes.byteLength > maxBytes || bytes.byteLength !== file.size) throw Error('The selected file size changed. Choose the file again.');
        let parsed;
        try { parsed = JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(bytes)); } catch { throw Error('The selected file is not valid UTF-8 JSON. Nothing was imported.'); }
        if (!object(parsed)) throw Error('Choose an exported investigation JSON object. Nothing was imported.');
        const digest = new Uint8Array(await cryptoImpl.subtle.digest('SHA-256', bytes));
        const sha256 = Array.from(digest, value => value.toString(16).padStart(2, '0')).join('');
        if (!current()) return false;
        const intentKey = sha256 + ':' + (synthetic === true);
        if (!intents.has(intentKey)) intents.set(intentKey, 'file-import-' + uuid());
        const response = await client.importEvidence(bytes, sha256, intents.get(intentKey), synthetic === true);
        if (!current()) return false;
        const saved = response?.saved_evidence;
        if (!saved?.server_saved || !HASH.test(saved.file_sha256 || '') || saved.file_sha256 !== sha256 || saved.bytes !== bytes.byteLength || saved.provenance?.source !== 'user_file' || saved.provenance?.synthetic !== (synthetic === true) || saved.provenance?.execution_verified !== false || typeof saved.bundle_id !== 'string' || !saved.bundle_id) {
          throw Error('The service did not confirm the selected file hash and size. Retry the same file to check whether the import completed.');
        }
        result = saved; return true;
      } catch (caught) {
        if (current()) {
          const status = caught?.status;
          error = caught?.message || 'The import failed.';
          if ([401, 403].includes(status)) error += ' Check your signed-in account and invited workspace access.';
          else if (status === 404) error += ' Saved-file import is not enabled on this deployment. Keep the original JSON and contact the pilot operator.';
          else if (status === 413) error += ' Choose a smaller exported investigation.';
          else if (status === 429) error += /quota/i.test(error) ? ' Wait for retention cleanup or contact the pilot operator about the account quota.' : ' Another verification or request limit may be active. Retry the same file shortly.';
          else if (status === 409) error += ' Check the file label and retry settings; do not change evidence bytes to bypass the conflict.';
          else if (status >= 500 || [408, 425].includes(status) || !Number.isInteger(status)) error += ' An upload may have completed. Retry the same file and label; the request identity is retained.';
        }
        return false;
      } finally { if (ticket === generation) { pending = false; emit(); } }
    },
  };
}
