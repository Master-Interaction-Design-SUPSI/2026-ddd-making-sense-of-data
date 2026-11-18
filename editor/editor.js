(async () => {
  const $ = id => document.getElementById(id);
  const status = message => { $('status').textContent = message; };
  const feedback = target => typeof target === 'string' ? $(target) : target;
  const fail = (error, target = 'status') => { feedback(target).textContent = error.message; };
  const showView = (view, focus = true) => {
    $('workspace').dataset.view = view;
    $('show-write').setAttribute('aria-pressed', view === 'write');
    $('show-preview').setAttribute('aria-pressed', view === 'preview');
    syncPreviewVisibility();
    if (focus) $(view === 'write' ? 'writing-heading' : 'preview-heading').focus();
  };
  const siteRoot = new URL('../', location.href);
  const contract = await fetch('../static/contract.json').then(response => response.json());
  const groups = await fetch('groups.json').then(response => response.json());
  let draft, config, baseId, loadRevision = 0;
  let baseText = '', baseTable = null;
  let tableRows = [], tableError = false, replacementPath = null, loading = false;
  let importedRecovery = null, insertionTarget = 'body';
  const previewClient = new CoursePreviewClient.PreviewClient({ frame: $('preview'), host: window, origin: location.origin, contract,
    onState: state => { $('preview-status').textContent = state.message; $('preview-status').dataset.state = state.status; } });
  const mediaPreviews = CourseMediaPreview.create(document, URL);
  const narrowScreen = window.matchMedia?.('(width < 800px)');
  function syncPreviewVisibility() { previewClient.setVisible(!narrowScreen?.matches || $('workspace').dataset.view === 'preview'); }
  narrowScreen?.addEventListener('change', syncPreviewVisibility);
  syncPreviewVisibility();
  $('show-write').addEventListener('click', () => showView('write'));
  $('show-preview').addEventListener('click', () => showView('preview'));
  $('retry-preview').addEventListener('click', () => previewClient.retry());
  window.addEventListener('pagehide', event => { if (!event.persisted) previewClient.dispose(); });
  window.addEventListener('pageshow', event => { if (event.persisted) previewClient.retry(); });
  const tableName = contract.media_table;
  const draftStore = new CourseDraftStorage.DraftStore();
  let channel = null;
  try { if (typeof BroadcastChannel === 'function') channel = new BroadcastChannel('ddd-editor-drafts'); } catch { /* Revision checks still prevent overwrites without tab notifications. */ }
  let persistence = null;
  let saving = Promise.resolve();
  const savedControls = visible => {
    $('recovery-notice').hidden = !visible;
    $('fallback-restore').hidden = !visible || !persistence?.hasDurable || !persistence?.legacy;
  };

  function editable() {
    return !!draft && !loading && !persistence?.gate.readOnly && !persistence?.conflict && !persistence?.pending;
  }
  function allowEdit() {
    if (editable()) return true;
    status('Resolve the saved browser draft before editing.');
    return false;
  }
  function updateControls() {
    const allowed = editable();
    for (const id of ['text', 'visual-file', 'replace-visual', 'attachment', 'path', 'media-name', 'media-source', 'repair-source', 'apply-repair', 'cancel-replace']) $(id).disabled = !allowed;
    for (const id of ['attach', 'add-row']) $(id).disabled = !allowed || tableError;
    $('path').disabled = !allowed || !!replacementPath;
    $('download-visual').disabled = !draft?.files().get('visual.svg')?.blob;
    for (const id of ['check', 'download', 'download-table', 'export-draft']) $(id).disabled = !draft || loading || id === 'check' && !allowed;
    const projection = draft && CourseDocument.project(draft.text);
    for (const id of ['title', 'authors', 'body']) $(id).disabled = !allowed || !projection?.safe;
    for (const selector of ['media-rows', 'visual-metadata', 'files', 'tools', 'media-picker']) {
      for (const control of $(selector).querySelectorAll('button, input, select')) control.disabled = !allowed;
    }
  }

  async function openStorage(key) {
    await saving;
    let legacy = null;
    try { legacy = localStorage.getItem(key); } catch { /* IndexedDB may still work when localStorage is denied. */ }
    try {
      const record = await draftStore.read(key);
      return { key, backend: 'indexeddb', revision: record?.revision || 0,
               pending: !!(record && !record.discarded || legacy), hasDurable: !!(record && !record.discarded), conflict: false, legacy, retireLegacy: false,
               generation: 0, unsaved: false };
    } catch {
      return { key, backend: 'local', pending: !!legacy, hasDurable: false, conflict: false, legacy, retireLegacy: false,
               generation: 0, unsaved: false };
    }
  }

  async function savedRecord(context) {
    if (importedRecovery && context === persistence) return importedRecovery;
    if (context.backend === 'indexeddb') {
      const record = await draftStore.read(context.key);
      if (record && !record.discarded) return record;
    }
    const raw = localStorage.getItem(context.key);
    return raw ? { ...JSON.parse(raw), legacyRaw: raw } : null;
  }
  const missingAttachments = new Set();
  let mutationRevision = 0;
  function invalidate() {
    mutationRevision++;
    $('validation').textContent = $('steps').children.length ? 'Draft changed — check again before committing.' : '';
    $('steps').replaceChildren();
    $('error-summary').hidden = true;
  }
  function recordChange() {
    invalidate();
    save();
    preview(150);
  }
  const rawUrl = (revision, path) => `https://raw.githubusercontent.com/${config.repository}/${encodeURIComponent(revision)}/${path.split('/').map(encodeURIComponent).join('/')}`;
  const github = (action, path = '') => `https://github.com/${config.repository}/${action}/${encodeURIComponent(config.branch)}/${path.split('/').map(encodeURIComponent).join('/')}`;
  const run = (fn, target = 'status') => { feedback(target).textContent = ''; return Promise.resolve().then(fn).catch(error => fail(error, target)); };
  for (const group of groups.groups.filter(group => group.editable)) {
    const option = document.createElement('option'); option.value = group.slug; option.textContent = group.slug; $('group').append(option);
  }
  $('group').disabled = false;
  $('open').disabled = $('template').disabled = !$('group').options.length;

  async function fetchBlob(url, limit) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch(url, { cache: 'no-store', redirect: 'error', signal: controller.signal });
      if (!response.ok) throw new Error(`Could not load ${url.pathname || url}`);
      if (!response.body) throw new Error('This browser cannot stream file downloads');
      const reader = response.body.getReader();
      const chunks = []; let size = 0;
      try {
        while (true) {
          const { done, value } = await reader.read(); if (done) break;
          size += value.byteLength;
          if (size > limit) throw new Error('Downloaded file exceeds its byte limit');
          chunks.push(value);
        }
      } catch (error) { await reader.cancel(); throw error; }
      return new Blob(chunks);
    } finally { clearTimeout(timeout); }
  }

  async function boundedBlob(url, record) {
    const blob = await fetchBlob(url, record.size);
    if (blob.size !== record.size || await CourseDraft.digest(blob) !== record.sha256) throw new Error('Publication changed or failed integrity checks. Reopen the group.');
    return blob;
  }

  const sourceText = async blob => new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(await blob.arrayBuffer());

  async function open(template) {
    await saving;
    if (persistence?.unsaved && !window.confirm('Some current edits have not been saved. Download your current text and attachments before switching. Continue and discard these unsaved edits?')) return;
    const revision = ++loadRevision;
    loading = true; updateControls();
    try {
    const selected = groups.groups.find(group => group.slug === $('group').value);
    if (!selected) return;
    status('Loading complete inventory…');

    let entry, identity, prefix;
    if (template) {
      entry = await fetch('template.json').then(response => response.json());
      identity = `template:${entry.digest}`; prefix = new URL('template/', location.href);
    } else {
      const response = await fetch('../publication.json', { cache: 'no-store' });
      if (!response.ok) throw new Error('No published snapshot. Start from the template and reconcile any existing GitHub work.');
      const manifest = await response.json();
      if (manifest.version !== 1) throw new Error('Unsupported publication inventory version');
      entry = manifest.groups[selected.slug];
      if (!entry || entry.repository !== selected.repository || entry.branch !== selected.branch) throw new Error('No matching published snapshot. Reconcile existing GitHub work before using the template.');
      identity = entry.commit; prefix = new URL(`${selected.slug}/`, siteRoot);
    }
    const records = new Map(Object.entries(entry.files));
    const checked = CourseDraft.select(records, contract);
    if (checked.files.size !== records.size) throw new Error('Inventory includes unsupported files');
    identity += ':' + await CourseDraft.digest(new Blob([JSON.stringify({ files: entry.files, exclude: selected.exclude, contract: contract.version })]));
    for (const [path, file] of records) {
      if (!/^[0-9a-f]{64}$/.test(file.sha256)) throw new Error('Invalid inventory digest');
      file.url = new URL(path, prefix);
    }
    const text = await boundedBlob(records.get('text.md').url, records.get('text.md'));
    const tableBlob = records.has(tableName) ? await boundedBlob(records.get(tableName).url, records.get(tableName)) : null;
    if (revision !== loadRevision) return;
    const loadedText = await sourceText(text);
    const context = await openStorage([siteRoot.href, selected.repository, selected.branch, selected.slug].join('|'));
    if (revision !== loadRevision) return;
    const loadedTable = tableBlob ? await sourceText(tableBlob) : null;
    if (revision !== loadRevision) return;
    config = selected; baseId = identity; invalidate(); $('validation').textContent = '';
    $('editing').hidden = false;
    draft = new CourseDraft.Draft(records, config, contract);
    draft.text = baseText = loadedText;
    draft.base.get('text.md').blob = text;
    if (tableBlob) { draft.base.get(tableName).blob = tableBlob; draft.table = loadedTable; }
    else draft.table = CourseDraft.startingTable(draft.files(), contract);
    baseTable = draft.table;
    persistence = context;
    persistence.gate = new CourseDraftSession.RecoveryGate(persistence.pending);
    importedRecovery = null; resetUpload();
    $('recovery-comparison').hidden = true;
    syncDocument();
    $('base').textContent = template ? `${config.slug}: started from the template. Check GitHub first so you don't replace a teammate's work.` : `${config.slug}: last published version (${entry.commit.slice(0, 7)}). Teammates may have newer changes on GitHub.`;
    if (!template) {
      const commitLink = document.createElement('a'); commitLink.href = `https://github.com/${config.repository}/commit/${entry.commit}`;
      commitLink.textContent = ' View on GitHub ↗'; commitLink.target = '_blank'; commitLink.rel = 'noopener'; $('base').append(commitLink);
    }
    $('steps').replaceChildren();
    $('saved-files').replaceChildren();
    missingAttachments.clear();
    for (const id of ['attach', 'check', 'download', 'add-row', 'download-table']) $(id).disabled = false;
    loadRows(!tableBlob);
    savedControls(persistence.pending);
    $('save').textContent = persistence.backend === 'indexeddb'
      ? persistence.hasDurable && persistence.legacy
        ? 'Two saved drafts exist. Download both and any older attachments before choosing text-and-media restoration or the text-only backup.'
        : 'Text and attached media can be saved in this browser only.'
      : 'Media storage is unavailable. Text-only fallback requires media reattachment after reload. Keep your original files.';
    status(persistence.pending ? 'Resolve the saved browser draft before editing. The loaded version is read-only.' : 'Ready to edit. Changes remain in this browser.');
    listFiles(); await preview();
    } finally {
      if (revision === loadRevision) { loading = false; updateControls(); }
    }
  }

  function save() {
    const context = persistence;
    if (!editable()) return saving;
    context.unsaved = true;
    if (context.conflict || context.pending) {
      $('save').textContent = 'A browser draft is waiting. Download, restore or discard it explicitly before saving here. Your current edits are not autosaved yet.';
      return saving;
    }
    const generation = ++context.generation;
    const payload = CourseDraftSession.snapshot(draft, baseId, missingAttachments);
    context.unsaved = true;
    $('save').textContent = 'Saving in this browser…';
    saving = saving.then(async () => {
      if (context.conflict || context.pending || generation !== context.generation) return;
      if (context.backend === 'indexeddb') {
        context.revision = await draftStore.save(context.key, payload, context.revision);
        channel?.postMessage({ key: context.key, revision: context.revision });
        try {
          if (context.retireLegacy && context.legacy && localStorage.getItem(context.key) === context.legacy) {
            localStorage.removeItem(context.key); context.legacy = null;
          }
        } catch { /* A committed IndexedDB draft survives failure to retire the legacy text copy. */ }
      } else {
        if (localStorage.getItem(context.key) !== context.legacy) throw new CourseDraftStorage.DraftConflict();
        const fallback = { version: 1, baseId: payload.baseId, text: payload.text, table: payload.table,
          paths: [...new Set([...payload.assets.map(asset => asset.path), ...payload.missingPaths])],
          deletions: payload.deletions, saved: payload.saved };
        const encoded = JSON.stringify(fallback);
        localStorage.setItem(context.key, encoded);
        context.legacy = encoded;
      }
      if (generation === context.generation) context.unsaved = false;
      if (context === persistence && generation === context.generation) {
        $('save').textContent = context.backend === 'indexeddb'
          ? 'Text and attached media saved in this browser only.'
          : 'Text saved in this browser only. Reattach media after reload.';
      }
    }).catch(error => {
      if (error.name === 'DraftConflict') context.conflict = true;
      if (context !== persistence) return;
      if (context.conflict) { context.gate.requireRecovery(); savedControls(true); updateControls(); }
      $('save').textContent = context.conflict ? error.message
        : 'Could not save text and media in this browser. The previous saved draft is preserved. Download your text and attachments and keep originals before leaving.';
    });
    return saving;
  }

  function setDocumentField(id, value) {
    const field = $(id);
    const normalize = text => text.replace(/\r\n?/g, '\n');
    const before = normalize(field.value), after = normalize(value);
    if (before === after) return;
    const shift = before.endsWith(after) ? before.length - after.length : 0;
    const start = Math.max(0, Math.min(after.length, field.selectionStart - shift));
    const end = Math.max(start, Math.min(after.length, field.selectionEnd - shift));
    field.value = value;
    if (Number.isInteger(start) && Number.isInteger(end)) field.setSelectionRange(start, end);
  }

  function syncDocument(typingField = null) {
    const projection = CourseDocument.project(draft.text);
    setDocumentField('text', draft.text);
    for (const key of ['title', 'authors', 'body']) {
      if (key !== typingField || key === 'body') setDocumentField(key, projection[key]);
    }
    $('document-status').textContent = projection.safe ? '' : projection.reason;
    if (!projection.safe) { $('raw-document').open = true; insertionTarget = 'text'; }
    updateControls();
  }

  function preview(delay = 0) {
    if (!draft) return;
    const active = draft, text = active.text, tableSource = active.table, slug = config.slug;
    const files = active.files();
    previewClient.request(async () => {
      const wanted = new Set(['visual.svg']);
      let table = null;
      try { table = tableSource === null ? null : CourseMediaTable.read(tableSource); } catch { /* Table repair stays available. */ }
      for (const target of CourseReferences.collect(text, table)) {
        try { const path = CourseReferences.localPath(target, contract.reference_schemes); if (path) wanted.add(path); } catch { /* Review reports unsafe references. */ }
      }
      const assets = [];
      for (const path of wanted) {
        const file = files.get(path); if (!file) continue;
        if (!file.blob) file.blob = await boundedBlob(file.url, file);
        assets.push({ path, bytes: await file.blob.arrayBuffer() });
      }
      return { slug, text, table: tableSource, paths: [...files.keys()].filter(path => wanted.has(path)), assets };
    }, delay);
  }

  function beginReplacement(path) {
    if (!allowEdit()) return;
    replacementPath = path; $('path').value = path;
    const metadata = tableRows.find(entry => entry.file === path);
    $('media-name').value = metadata?.name || ''; $('media-source').value = metadata?.source || '';
    $('attachment').value = ''; $('attach').textContent = `Replace ${path}`; $('cancel-replace').hidden = false;
    $('upload').open = true; $('upload-metadata').hidden = true; updateControls(); $('attachment').focus();
  }

  function listFiles() {
    $('files').replaceChildren();
    const files = draft.files();
    $('inventory').textContent = [...files].map(([path, file]) => `${path} · ${file.size.toLocaleString()} bytes`).join('\n');
    $('visual-status').textContent = files.has('visual.svg') ? `visual.svg · ${files.get('visual.svg').size.toLocaleString()} bytes` : 'Main visual is missing. Choose a 1920 × 1080 SVG.';
    renderRows();
    for (const path of missingAttachments) {
      if (draft.files().has(path)) continue;
      const row = document.createElement('li'); row.textContent = `${path} · reattachment required `;
      const discard = document.createElement('button'); discard.textContent = 'Discard missing attachment';
      discard.addEventListener('click', () => { if (!allowEdit()) return; missingAttachments.delete(path); recordChange(); listFiles(); });
      row.append(discard); $('files').append(row);
    }
    for (const path of draft.deletions) {
      const row = document.createElement('li'); row.textContent = `${path} · explicit deletion planned `;
      const undo = document.createElement('button'); undo.textContent = draft.removed.has(path) ? 'Undo removal' : 'Restore original repository file';
      undo.addEventListener('click', () => run(async () => {
        if (!allowEdit()) return;
        if (draft.removed.has(path)) draft.undoRemove(path);
        else {
          if (!window.confirm('This older draft has no exact undo record. Restore the original repository file? Removed metadata cannot be recovered by this action.')) return;
          draft.deletions.delete(path);
        }
        loadRows(false);
        recordChange(); listFiles();
      }));
      row.append(undo); $('files').append(row);
    }
    updateControls();
  }

  function resetUpload() {
    replacementPath = null; $('upload-metadata').hidden = false;
    for (const id of ['attachment', 'path', 'media-name', 'media-source']) $(id).value = '';
    $('attach').textContent = 'Add media'; $('cancel-replace').hidden = true;
    updateControls();
  }
  $('cancel-replace').addEventListener('click', () => { if (allowEdit()) resetUpload(); });

  async function attach() {
    if (!allowEdit()) return;
    if (tableError) throw new Error('Repair media.csv before adding media.');
    const file = $('attachment').files[0]; if (!file) throw new Error('Choose a file first');
    const path = $('path').value.trim();
    const name = $('media-name').value.trim(), source = $('media-source').value.trim();
    const replacing = replacementPath;
    const active = draft;
    if (file.size > contract.limits.media_bytes) throw new Error('Attachment exceeds the media file limit');
    const hash = await CourseDraft.digest(file);
    if (active !== draft) return;
    if (!allowEdit()) return;
    if (replacing) draft.replaceAsset(replacing, file, hash);
    else draft.addAsset(path, file, hash, { ...(name ? { name } : {}), source });
    missingAttachments.delete(path);
    resetUpload();
    loadRows(false);
    recordChange(); listFiles();
  }

  /* ---------- media.csv rows ---------- */

  const noTable = 'No media yet. media.csv is created with the first attached file.';
  const tableText = () => draft.table ?? CourseMediaTable.serialize([]);

  function tableStatus() {
    if (draft.table === null) { $('table-status').textContent = noTable; return; }
    try {
      const table = CourseMediaTable.read(draft.table);
      const notes = CourseMediaTable.warnings(table, draft.files().keys());
      $('table-status').textContent = notes.length ? notes.join('. ') + '.' : `${table.rows.length} media listed.`;
    } catch (error) { $('table-status').textContent = error.message; }
  }

  function loadRows(started) {
    tableError = false; $('table-repair').hidden = true;
    if (draft.table === null) { tableRows = []; renderRows(); $('table-status').textContent = noTable; return; }
    try { tableRows = CourseMediaTable.read(draft.table).rows.map(({ name, source, file }) => ({ name, source, file })); }
    catch (error) {
      tableRows = []; tableError = true;
      $('table-repair').hidden = false; $('repair-source').value = draft.table;
      $('table-status').textContent = error.message;
    }
    renderRows();
    if (started) $('table-status').textContent = 'No media.csv yet: one row per media file was added. Add the source links, then commit media.csv.';
  }

  // A group needs media.csv only once it has a row, or when its repository already has one.
  function storeRows() { if (tableError) throw new Error('Repair media.csv before editing rows.'); draft.table = tableRows.length || draft.base.has(tableName) ? CourseMediaTable.serialize(tableRows) : null; }

  // Name, source and insertion: what a writer needs for each media name.
  function mediaMetadata(entry, index, path) {
    const metadata = document.createElement('div'); metadata.dataset.index = index;
    for (const [field, label, type] of [['name', 'Name', 'text'], ['source', 'Source link', 'url']]) {
      const wrapper = document.createElement('label'); wrapper.textContent = label;
      const input = document.createElement('input'); input.type = type; input.dataset.field = field; input.value = entry[field];
      input.setAttribute('aria-label', `${label}: ${path}`); wrapper.append(input); metadata.append(wrapper);
    }
    const actions = document.createElement('div'); actions.className = 'row';
    const insert = document.createElement('button'); insert.textContent = 'Insert in text'; insert.dataset.action = 'insert'; actions.append(insert);
    metadata.append(actions);
    return metadata;
  }

  // The table row's exact file path and its removal: needed only to repair a row.
  function rowDetails(entry, index) {
    const row = document.createElement('div'); row.dataset.index = index;
    const label = document.createElement('label'); label.textContent = `File path for ${entry.name ? `[[${entry.name}]]` : 'this name'}`;
    const input = document.createElement('input'); input.value = entry.file; input.dataset.field = 'file'; label.append(input);
    const remove = document.createElement('button'); remove.textContent = 'Remove name'; remove.dataset.action = 'remove';
    row.append(label, remove);
    return row;
  }

  function more(...children) {
    const details = document.createElement('details'), summary = document.createElement('summary');
    summary.textContent = 'More'; details.append(summary, ...children);
    return details;
  }

  const useText = count => count === 0 ? 'not in the text yet' : count === 1 ? 'used once in the text' : `used ${count} times in the text`;

  function renderRows() {
    $('media-rows').replaceChildren();
    const inventory = new Map([...draft.files()].filter(([path]) => !['text.md', 'visual.svg', tableName].includes(path)));
    for (const entry of tableRows) if (entry.file !== 'visual.svg' && !inventory.has(entry.file)) inventory.set(entry.file, null);
    const visual = draft.files().get('visual.svg');
    $('visual-preview').replaceChildren(...(visual ? [mediaPreviews.element('visual.svg', visual)] : []));
    $('visual-metadata').replaceChildren(...tableRows.flatMap((entry, index) => {
      if (entry.file !== 'visual.svg') return [];
      const block = document.createElement('div'); block.append(mediaMetadata(entry, index, 'visual.svg'), more(rowDetails(entry, index)));
      return [block];
    }));
    const namedUses = CourseMediaTable.namedReferences(draft.text);
    const directUses = CourseReferences.collect(draft.text);
    for (const [path, file] of inventory) {
      const item = document.createElement('li'); item.dataset.path = path;
      const error = document.createElement('p'); error.className = 'error'; error.dataset.mediaError = ''; error.setAttribute('role', 'status');
      const entries = tableRows.map((entry, index) => ({ entry, index })).filter(({ entry }) => entry.file === path);
      const named = entries.reduce((sum, { entry }) => sum + namedUses.filter(ref => ref.name.toLowerCase() === entry.name.toLowerCase()).length, 0);
      const direct = directUses.filter(target => { try { return CourseReferences.localPath(target, contract.reference_schemes) === path; } catch { return false; } }).length;
      if (file) item.append(mediaPreviews.element(path, file));
      const heading = document.createElement('p'); heading.className = 'media-file';
      heading.textContent = `${path} · ${file ? useText(named + direct) : 'file missing'}`; item.append(heading);
      for (const { entry, index } of entries) item.append(mediaMetadata(entry, index, path));
      if (!entries.length && !tableError) {
        const create = document.createElement('button'); create.textContent = 'Name this media';
        create.addEventListener('click', () => run(() => {
          if (!allowEdit()) return;
          tableRows.push(CourseDraft.rowsFor([path], tableRows)[0]); storeRows(); recordChange(); renderRows(); updateControls();
        }, error)); item.append(create);
      }
      const actions = document.createElement('div'); actions.className = 'row';
      if (file) {
        const replace = document.createElement('button'); replace.textContent = 'Replace file'; replace.addEventListener('click', () => beginReplacement(path)); actions.append(replace);
        if (file.blob) { const button = document.createElement('button'); button.textContent = 'Download file'; button.addEventListener('click', () => download(file.blob, path.split('/').pop())); actions.append(button); }
        const remove = document.createElement('button'); remove.textContent = 'Remove file';
        remove.addEventListener('click', () => run(() => {
          if (!allowEdit()) return;
          draft.removeAsset(path); missingAttachments.delete(path); loadRows(false); recordChange(); listFiles();
        }, error)); actions.append(remove);
      }
      item.append(more(actions, ...entries.map(({ entry, index }) => rowDetails(entry, index))), error); $('media-rows').append(item);
    }
    mediaPreviews.retain(new Set(['visual.svg', ...inventory.keys()]));
    tableStatus();
  }

  function insertText(text) {
    const target = $(insertionTarget);
    target.setRangeText(text, target.selectionStart, target.selectionEnd, 'end');
    target.dispatchEvent(new Event('input')); showView('write', false); target.focus();
  }

  /* ---------- GitHub steps ---------- */

  // One step: a reminder tick, an instruction, the actions it needs and a line for their results.
  function step(text) {
    const item = document.createElement('li');
    const check = document.createElement('input'); check.type = 'checkbox';
    const wrapper = document.createElement('label'); wrapper.append(check, ` ${text}`);
    const actions = document.createElement('div'); actions.className = 'row';
    const result = document.createElement('p'); result.setAttribute('role', 'status');
    item.append(wrapper, actions, result); $('steps').append(item);
    return { item, actions, result };
  }
  function externalLink(url, label) {
    const link = document.createElement('a'); link.href = url; link.target = '_blank'; link.rel = 'noopener'; link.textContent = label;
    return link;
  }
  function stepButton(label, result, action) {
    const button = document.createElement('button'); button.textContent = label;
    button.addEventListener('click', () => run(action, result));
    return button;
  }
  async function copy(text, result) {
    await navigator.clipboard.writeText(text);
    result.textContent = 'Copied. Paste it on GitHub.';
  }
  async function compareGitHub({ item, result }) {
    const active = draft;
    item.querySelector('details')?.remove();
    result.textContent = 'Comparing…';
    const current = await sourceText(await fetchBlob(rawUrl(config.branch, 'text.md'), contract.limits.text_bytes));
    if (active !== draft) return;
    if (current === baseText) { result.textContent = 'Same text as the version you opened. Very recent changes may not show yet.'; return; }
    result.textContent = 'The text on GitHub is different. Bring your teammates’ changes into your draft before you save.';
    const details = document.createElement('details'), summary = document.createElement('summary'), source = document.createElement('pre');
    summary.textContent = 'Text on GitHub'; source.textContent = current;
    details.append(summary, source); item.append(details);
  }

  async function check() {
    if (!allowEdit()) return;
    const active = draft, revision = mutationRevision;
    if (missingAttachments.size) throw Object.assign(new Error('Reattach or explicitly remove these restored media files first: ' + [...missingAttachments].join(', ')), { field: 'media-heading' });
    const result = active.validate();
    const changes = await active.changes();
    if (active !== draft || revision !== mutationRevision) return;
    for (const [path, file] of result.files) {
      if (!path.endsWith('.svg')) continue;
      const blob = file.blob || await boundedBlob(file.url, file);
      const source = new TextDecoder('utf-8', { fatal: true }).decode(await blob.arrayBuffer());
      const svg = new DOMParser().parseFromString(source, 'image/svg+xml');
      const root = svg.documentElement;
      if (svg.querySelector('parsererror') || root.localName !== 'svg' || root.namespaceURI !== 'http://www.w3.org/2000/svg') throw new Error(`${path}: export a valid SVG image`);
      if (path !== 'visual.svg') continue;
      if (root.getAttribute('viewBox')?.trim().split(/[\s,]+/).map(Number).join(',') !== contract.visual_viewbox.join(',')) throw new Error('visual.svg: export a valid 1920 × 1080 SVG');
      for (const [name, expected] of [['width', 1920], ['height', 1080]]) {
        if (root.hasAttribute(name) && Number(root.getAttribute(name).trim().replace(/px$/, '')) !== expected) throw new Error(`visual.svg: ${name} must be ${expected} or omitted`);
      }
    }
    if (active !== draft || revision !== mutationRevision) return;
    $('validation').textContent = ['Checks passed. Follow these steps on GitHub, top to bottom. Ticks are only reminders for you; the site runs its own checks when it publishes.',
      result.orphans.length ? `Media not used in the text, still saved: ${result.orphans.join(', ')}.` : '', result.notes.join('. ')].filter(Boolean).join(' ');
    $('steps').replaceChildren();
    // Any order works: until every commit is in, the page shows what is not there yet as missing.
    const review = step('Check that nobody changed the text on GitHub after the version you opened.');
    review.actions.append(stepButton('Compare with GitHub', review.result, () => compareGitHub(review)), externalLink(github('blob', 'text.md'), 'Open text.md on GitHub ↗'));
    for (const change of changes.filter(change => change.path !== 'text.md' && change.path !== tableName && change.operation !== 'delete')) {
      const parent = change.path.includes('/') ? change.path.slice(0, change.path.lastIndexOf('/')) : '';
      const name = change.path.split('/').pop(), blob = active.files().get(change.path)?.blob;
      const where = parent ? `the ${parent} folder` : 'the main file list (where text.md is)';
      const upload = step(`Download ${name}, then upload it into ${where} on GitHub${change.operation === 'add' ? '' : ' (it replaces the old file)'} and commit.`);
      if (blob) upload.actions.append(stepButton(`Download ${name}`, upload.result, () => download(blob, name)));
      upload.actions.append(externalLink(github('upload', parent), 'Open upload page ↗'));
    }
    const tableChange = changes.find(change => change.path === tableName);
    if (tableChange) {
      const table = step(tableChange.operation === 'add' ? 'Create media.csv on GitHub. If it opens empty, paste the copied table. Commit.' : 'Replace everything in media.csv with the copied table, and commit.');
      const create = new URL(`https://github.com/${config.repository}/new/${encodeURIComponent(config.branch)}`);
      create.searchParams.set('filename', tableName);
      if (draft.table.length < 4000) create.searchParams.set('value', draft.table);
      table.actions.append(stepButton('Copy media.csv', table.result, () => copy(tableText(), table.result)),
        tableChange.operation === 'add' ? externalLink(create.href, 'Create media.csv on GitHub ↗') : externalLink(github('edit', tableName), 'Edit media.csv on GitHub ↗'));
    }
    if (changes.some(change => change.path === 'text.md')) {
      const text = step('Replace everything in text.md with the copied text, and commit.');
      text.actions.append(stepButton('Copy text.md', text.result, () => copy(draft.text, text.result)), externalLink(github('edit', 'text.md'), 'Edit text.md on GitHub ↗'));
    }
    for (const change of changes.filter(change => change.operation === 'delete')) {
      const name = change.path.split('/').pop();
      step(`Delete ${change.path}: open it, choose … → Delete file, and commit.`).actions.append(externalLink(github('blob', change.path), `Open ${name} on GitHub ↗`));
    }
    step('Open your entry on the course site. Updates take a while to appear.').actions.append(externalLink(new URL(`#${config.slug}`, siteRoot).href, 'Open published entry ↗'));
  }

  $('open').addEventListener('click', () => run(() => open(false)));
  $('template').addEventListener('click', () => run(() => open(true)));
  $('text').addEventListener('input', () => {
    if (!allowEdit()) return;
    draft.text = $('text').value; syncDocument(); recordChange(); listFiles();
  });
  for (const key of ['title', 'authors', 'body']) $(key).addEventListener('input', () => run(() => {
    if (!allowEdit()) return;
    draft.text = CourseDocument.update(draft.text, { [key]: $(key).value });
    syncDocument(key); recordChange(); listFiles();
  }, 'text-error'));
  for (const id of ['body', 'text']) $(id).addEventListener('focus', () => { insertionTarget = id; });
  $('insert-media').addEventListener('click', () => {
    if (!allowEdit()) return;
    const available = tableRows.filter(entry => entry.name && entry.file !== 'visual.svg' && draft.files().has(entry.file));
    $('media-choice').replaceChildren(...available.map(entry => { const option = document.createElement('option'); option.value = entry.name; option.textContent = entry.name; return option; }));
    $('insert-error').textContent = available.length ? '' : 'Add media and give it a name before inserting it.';
    $('media-picker').hidden = false;
    if (available.length) $('media-choice').focus();
  });
  $('insert-choice').addEventListener('click', () => {
    if (!allowEdit()) return;
    const name = $('media-choice').value;
    if (!tableRows.some(entry => entry.name === name && entry.file !== 'visual.svg' && draft.files().has(entry.file))) return;
    insertText(`[[${name}]]`); $('media-picker').hidden = true;
  });
  $('cancel-insert').addEventListener('click', () => { $('media-picker').hidden = true; $(insertionTarget).focus(); });
  $('download-visual').addEventListener('click', () => { const file = draft?.files().get('visual.svg'); if (file?.blob) download(file.blob, 'visual.svg'); });
  $('replace-visual').addEventListener('click', () => run(async () => {
    if (!allowEdit()) return;
    const file = $('visual-file').files[0];
    if (!file) throw new Error('Choose a 1920 × 1080 SVG first.');
    if (!file.name.toLowerCase().endsWith('.svg')) throw new Error('Choose an SVG for the main visual.');
    const active = draft, hash = await CourseDraft.digest(file);
    if (active !== draft || !allowEdit()) return;
    draft.attach('visual.svg', file, hash); missingAttachments.delete('visual.svg');
    $('visual-file').value = ''; recordChange(); listFiles();
  }, 'visual-error'));
  $('tools').addEventListener('click', event => {
    const text = event.target.dataset.insert; if (!text || !allowEdit()) return;
    insertText(text);
  });
  $('attachment').addEventListener('change', () => run(() => {
    if (!allowEdit()) return;
    const file = $('attachment').files[0]; if (!file) return;
    if (replacementPath) { $('path').value = replacementPath; return; }
    $('path').value = draft.suggestPath(file.name);
    $('media-name').value = CourseDraft.rowsFor([$('path').value], tableRows)[0].name;
  }, 'attachment-error'));
  function editRow(event) {
    const field = event.target.dataset.field; if (!field || !allowEdit() || tableError) return;
    const entry = tableRows[event.target.closest('[data-index]').dataset.index];
    if (field === 'name') {
      if (!entry.name) {
        const name = event.target.value.trim();
        const trial = tableRows.filter(row => row === entry || row.name.trim()).map(row => ({
          name: row === entry ? name : row.name, source: '', file: 'media/placeholder.png' }));
        CourseMediaTable.read(CourseMediaTable.serialize(trial));
        entry.name = name; storeRows(); tableStatus();
      } else {
        draft.renameAsset(entry.name, event.target.value);
        syncDocument(); loadRows(false);
      }
    } else {
      entry[field] = event.target.value.trim(); storeRows(); tableStatus();
    }
    recordChange();
  }
  const rowFeedback = (event, container) => container === 'visual-metadata' ? 'visual-error' : event.target.closest('li')?.querySelector?.('[data-media-error]') || 'table-error';
  for (const container of ['media-rows', 'visual-metadata']) {
    $(container).addEventListener('input', event => { if (event.target.dataset.field === 'source') run(() => editRow(event), rowFeedback(event, container)); });
    $(container).addEventListener('change', event => {
      if (['name', 'file'].includes(event.target.dataset.field)) run(() => { editRow(event); if (event.target.dataset.field === 'file') listFiles(); }, rowFeedback(event, container));
    });
    $(container).addEventListener('click', event => run(() => {
      const action = event.target.dataset.action; if (!action || !allowEdit() || tableError) return;
      const index = Number(event.target.closest('[data-index]').dataset.index);
      if (action === 'insert') { insertText(`[[${tableRows[index].name}]]`); return; }
      const name = tableRows[index].name;
      if (CourseMediaTable.namedReferences(draft.text).some(ref => ref.name.toLowerCase() === name.toLowerCase())) throw new Error(`Remove [[${name}]] references from the text before removing this name.`);
      tableRows.splice(index, 1); storeRows(); renderRows(); recordChange(); updateControls();
    }, rowFeedback(event, container)));
  }
  $('add-row').addEventListener('click', () => {
    if (!allowEdit() || tableError) return;
    tableRows.push({ name: '', source: '', file: 'media/' }); storeRows(); renderRows(); recordChange();
    updateControls(); $('media-rows').lastElementChild.querySelector('input').focus();
  });
  $('apply-repair').addEventListener('click', () => run(() => {
    if (!allowEdit()) return;
    const source = $('repair-source').value;
    if (CourseDraft.bytes(source) > contract.limits.table_bytes) throw new Error('media.csv exceeds its byte limit');
    CourseMediaTable.read(source);
    draft.table = source; loadRows(false); recordChange(); listFiles();
  }, 'table-error'));
  $('download-table').addEventListener('click', () => download(new Blob([tableText()], { type: 'text/csv' }), tableName));
  $('attach').addEventListener('click', () => run(attach, 'attachment-error'));
  $('check').addEventListener('click', () => {
    $('error-summary').hidden = true;
    for (const id of ['text-error', 'visual-error', 'table-error']) $(id).textContent = '';
    Promise.resolve().then(check).catch(error => {
      const field = error.field || (/media.csv|media table|CSV/i.test(error.message) ? 'repair-source' : /visual.svg/.test(error.message) ? 'visual-file' : !CourseDocument.project(draft.text).safe ? 'text' : 'body');
      const target = field === 'visual-file' ? 'visual-error' : field === 'repair-source' || field === 'media-heading' ? 'table-error' : 'text-error';
      fail(error, target);
      const item = document.createElement('li'), link = document.createElement('a');
      link.href = `#${field}`; link.textContent = error.message;
      link.addEventListener('click', event => { event.preventDefault(); showView('write', false); if (field === 'repair-source') $('table-repair').hidden = false; if (field === 'text') $('raw-document').open = true; $(field).focus(); });
      item.append(link); $('review-errors').replaceChildren(item); $('error-summary').hidden = false; $('error-summary').focus();
    });
  });
  function download(blob, name) {
    const url = URL.createObjectURL(blob); const link = document.createElement('a');
    link.href = url; link.download = name; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  $('download').addEventListener('click', () => download(new Blob([draft.text], { type: 'text/plain' }), 'text.md'));
  async function restoreSaved(useFallback = false) {
    const context = persistence, active = draft;
    await saving;
    const metadata = context.backend === 'indexeddb' ? await draftStore.read(context.key, false) : null;
    const raw = useFallback ? localStorage.getItem(context.key) : null;
    const saved = useFallback && raw ? { ...JSON.parse(raw), legacyRaw: raw } : await savedRecord(context);
    const { draft: restored, missingPaths: missing } = await CourseDraftSession.restore(saved, active, baseId);
    if (context !== persistence || active !== draft) return;
    context.revision = importedRecovery || saved.legacyRaw ? metadata?.revision || 0 : saved.revision || 0;
    context.retireLegacy = !!saved.legacyRaw;
    if (saved.legacyRaw) context.legacy = saved.legacyRaw;
    context.conflict = context.pending = false; context.gate.resume();
    importedRecovery = null; $('recovery-comparison').hidden = true;
    draft = restored; missingAttachments.clear();
    for (const path of missing) missingAttachments.add(path);
    savedControls(false); status('Ready to edit. Changes remain in this browser.');
    syncDocument(); loadRows(false); invalidate(); listFiles(); updateControls();
    await save();
    if (context !== persistence) return;
    if (missingAttachments.size) $('save').textContent = 'Draft restored. Reattach or discard missing media: ' + [...missingAttachments].join(', ');
    await preview();
  }
  $('restore').addEventListener('click', () => run(() => restoreSaved(), 'recovery-error'));
  $('fallback-restore').addEventListener('click', () => run(() => restoreSaved(true), 'recovery-error'));
  async function downloadRecovery(record, label) {
    const encoded = await CourseDraftRecovery.encode(record, config, contract);
    const day = new Date().toISOString().slice(0, 10);
    download(new Blob([encoded], { type: 'application/json' }), `${day}_ddd-${config.slug}_${label}_recovery.json`);
  }
  $('saved-download').addEventListener('click', () => run(async () => {
    const saved = await savedRecord(persistence);
    if (!saved) throw new Error('Saved browser draft is unavailable');
    await downloadRecovery(saved, 'browser-draft');
    $('saved-files').replaceChildren();
    function individual(label, blob, name, path = name) {
      const row = document.createElement('li'); row.textContent = `${path} `;
      const button = document.createElement('button'); button.textContent = label;
      button.addEventListener('click', () => download(blob, name));
      row.append(button); $('saved-files').append(row);
    }
    individual('Download saved text', new Blob([saved.text], { type: 'text/plain' }), 'text.md');
    if (typeof saved.table === 'string') individual('Download saved media.csv', new Blob([saved.table], { type: 'text/csv' }), tableName);
    for (const asset of saved.assets || []) {
      if (asset.blob) individual('Download saved attachment', asset.blob, asset.path.split('/').pop(), asset.path);
    }
    if (persistence.legacy && persistence.hasDurable) {
      const row = document.createElement('li'); row.textContent = 'An older text-only backup is also available. ';
      const button = document.createElement('button'); button.textContent = 'Download older recovery bundle';
      button.addEventListener('click', () => run(() => downloadRecovery(JSON.parse(persistence.legacy), 'older-draft')));
      row.append(button); $('saved-files').append(row);
    }
  }, 'recovery-error'));
  $('export-draft').addEventListener('click', () => run(() => downloadRecovery(CourseDraftSession.snapshot(draft, baseId, missingAttachments), 'current-draft'), 'backup-error'));
  $('compare-saved').addEventListener('click', () => run(async () => {
    const context = persistence;
    const saved = await savedRecord(context);
    if (!saved || context !== persistence) return;
    context.gate.compare(); updateControls();
    $('recovery-comparison').hidden = false; $('recovery-comparison').open = true;
    $('saved-text').textContent = saved.text;
    $('loaded-text').textContent = baseText;
    $('comparison-base').textContent = `Saved base: ${saved.baseId}. Loaded base: ${baseId}. Comparison is read-only.`;
    showView('write', false); $('recovery-comparison').focus();
  }, 'recovery-error'));
  $('recovery-import').addEventListener('change', () => run(async () => {
    const file = $('recovery-import').files[0]; if (!file) return;
    if (!draft) throw new Error('Open the matching group before importing a recovery bundle.');
    if (persistence.pending || persistence.conflict) throw new Error('Download and resolve the existing browser draft before importing another.');
    if (file.size > CourseDraftRecovery.maximumBytes(contract)) throw new Error('Recovery bundle exceeds its byte limit');
    const context = persistence;
    const record = await CourseDraftRecovery.decode(await file.text(), contract);
    if (context !== persistence) return;
    if (['slug', 'repository', 'branch'].some(key => record.group[key] !== config[key])) throw new Error('Open the matching group before importing this bundle.');
    if (!window.confirm('Import this recovery bundle? Download your current draft first if you need to keep both. Imported work stays read-only until you resume or compare it.')) return;
    context.generation++;
    importedRecovery = record; context.pending = true; context.unsaved = true; context.gate.requireRecovery();
    savedControls(true); updateControls();
    $('save').textContent = 'Recovery bundle loaded in memory. Resume it to save in this browser, or download it before leaving.';
  }, 'backup-error'));
  $('discard').addEventListener('click', () => run(async () => {
    const context = persistence;
    if (!window.confirm('Use the loaded version and discard the saved browser draft? Download its recovery bundle first to keep its text, table and attachments.')) return;
    await saving;
    if (context !== persistence) return;
    if (context.backend === 'indexeddb') {
      const latest = await draftStore.read(context.key);
      context.revision = await draftStore.discard(context.key, latest?.revision || 0);
    }
    try { localStorage.removeItem(context.key); } catch (error) { if (context.backend === 'local') throw error; }
    context.legacy = null;
    context.conflict = context.pending = false; context.gate.useBase(true);
    importedRecovery = null; $('recovery-comparison').hidden = true;
    channel?.postMessage({ key: context.key, revision: context.revision });
    if (context !== persistence) return;
    draft = new CourseDraft.Draft(draft.base, config, contract);
    draft.text = baseText; draft.table = baseTable;
    missingAttachments.clear(); replacementPath = null;
    syncDocument();
    resetUpload();
    loadRows(false); invalidate(); listFiles();
    savedControls(false); status('Ready to edit. Changes remain in this browser.'); $('saved-files').replaceChildren(); updateControls(); await save(); await preview();
  }, 'recovery-error'));
  $('width').addEventListener('change', () => { $('preview').style.width = $('width').value === 'fit' ? '100%' : $('width').value + 'px'; });
  function externalSave(key) {
    if (!persistence || key !== persistence.key) return;
    persistence.conflict = true; persistence.gate.requireRecovery(); savedControls(true); updateControls();
    $('save').textContent = 'Another tab changed this draft. Restore or discard it explicitly before saving here. Download your current work first.';
  }
  window.addEventListener('storage', event => externalSave(event.key));
  if (channel) channel.onmessage = event => externalSave(event.data?.key);
  window.addEventListener('beforeunload', event => {
    if (!persistence?.unsaved) return;
    event.preventDefault(); event.returnValue = '';
  });
  status('Choose your group, then open its last published version. Use the template only if your group has nothing published yet.');
})().catch(error => { document.getElementById('status').textContent = error.message; });
