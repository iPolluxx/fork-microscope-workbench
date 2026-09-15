// Importing an archive never attaches a model or starts generation.
const input = document.getElementById('evidence-import');
const button = document.getElementById('import-evidence');
const status = document.getElementById('import-status');
button?.addEventListener('click', () => input.click());
input?.addEventListener('change', async () => {
  const file = input.files?.[0];
  if (!file) return;
  button.disabled = true;
  try {
    if (file.size > 64 * 1024 * 1024) throw Error('Choose an evidence export under 64 MB. Larger archives can still be copied into the worker’s live-runs folder.');
    status.textContent = 'Checking and importing saved runs and readouts…';
    const text = await file.text();
    try { JSON.parse(text); } catch { throw Error('This is not a valid JSON export. Choose the file downloaded with Export evidence.'); }
    const response = await window.workerFetch('/api/live/import', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: text,
    });
    const value = await response.json();
    if (!response.ok) throw Error(value.error || 'The evidence could not be imported.');
    status.textContent = value.run_ids ? `Imported ${value.run_ids.length} linked runs, ${value.lens_ids.length} lens readouts and ${value.patch_ids?.length || 0} patch experiments. Opening…` : value.already_present ? 'Already saved here. Opening the run…' : 'Evidence imported. Opening the run…';
    location.assign('/observatory.html?run=' + encodeURIComponent(value.id));
  } catch (error) {
    status.textContent = error instanceof TypeError ? 'Cannot reach this worker. Reconnect it, then import the file again.' : error.message;
  } finally {
    button.disabled = false;
    input.value = '';
  }
});
