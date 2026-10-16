(async () => {
  const $ = id => document.getElementById(id);
  const status = message => { $('status').textContent = message; };
  const fail = error => status(error.message);
  const siteRoot = new URL('../', location.href);
  const contract = await fetch('../static/contract.json').then(response => response.json());
  const groups = await fetch('groups.json').then(response => response.json());
  let draft, config, baseId, loadRevision = 0, renderRevision = 0, ready = false, timer;
  let baseText = '';
  let tableRows = [];
  const tableName = contract.media_table;
  const draftStore = new CourseDraftStorage.DraftStore();
  let channel = null;
  try { if (typeof BroadcastChannel === 'function') channel = new BroadcastChannel('ddd-editor-drafts'); } catch { /* Revision checks still prevent overwrites without tab notifications. */ }
  let persistence = null;
  let saving = Promise.resolve();
  const savedControls = visible => {
    for (const id of ['restore', 'discard', 'saved-download']) $(id).hidden = !visible;
    $('fallback-restore').hidden = !visible || !persistence?.hasDurable || !persistence?.legacy;
  };

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
    $('validation').textContent = 'Draft changed — check again before committing.';
    $('steps').replaceChildren();
  }
  const sent = new Map();
  const rawUrl = (revision, path) => `https://raw.githubusercontent.com/${config.repository}/${encodeURIComponent(revision)}/${path.split('/').map(encodeURIComponent).join('/')}`;
  const github = (action, path = '') => `https://github.com/${config.repository}/${action}/${encodeURIComponent(config.branch)}/${path.split('/').map(encodeURIComponent).join('/')}`;
  const run = fn => Promise.resolve().then(fn).catch(fail);
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

  async function open(template) {
    await saving;
    if (persistence?.unsaved && !window.confirm('Some current edits have not been saved. Download your current text and attachments before switching. Continue and discard these unsaved edits?')) return;
    const revision = ++loadRevision;
    const selected = groups.groups.find(group => group.slug === $('group').value);
    if (!selected) return;
    status('Loading complete inventory…');
    $('text').disabled = true;
    for (const id of ['attach', 'check', 'download', 'stale', 'add-row', 'download-table', 'copy-table']) $(id).disabled = true;
    $('files').replaceChildren(); $('media-rows').replaceChildren(); savedControls(false);
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
    const loadedText = await text.text();
    const context = await openStorage([siteRoot.href, selected.repository, selected.branch, selected.slug].join('|'));
    if (revision !== loadRevision) return;
    config = selected; baseId = identity; invalidate();
    draft = new CourseDraft.Draft(records, config, contract);
    draft.text = baseText = loadedText;
    draft.base.get('text.md').blob = text;
    if (tableBlob) { draft.base.get(tableName).blob = tableBlob; draft.table = await tableBlob.text(); }
    else draft.table = CourseDraft.startingTable(draft.files(), contract);
    persistence = context;
    $('text').value = draft.text; $('text').disabled = false;
    $('base').textContent = template ? `${config.slug}: template — reconcile existing unpublished work before replacing it.` : `${config.slug}: last published version: ${entry.commit}. Newer GitHub edits may exist.`;
    if (!template) {
      const commitLink = document.createElement('a'); commitLink.href = `https://github.com/${config.repository}/commit/${entry.commit}`;
      commitLink.textContent = ' View source commit ↗'; commitLink.target = '_blank'; commitLink.rel = 'noopener'; $('base').append(commitLink);
    }
    $('steps').replaceChildren(); $('freshness').textContent = ''; $('current-text').textContent = '';
    $('saved-files').replaceChildren();
    sent.clear(); missingAttachments.clear();
    for (const id of ['attach', 'check', 'download', 'stale', 'add-row', 'download-table', 'copy-table']) $(id).disabled = false;
    loadRows(!tableBlob);
    savedControls(persistence.pending);
    $('save').textContent = persistence.backend === 'indexeddb'
      ? persistence.hasDurable && persistence.legacy
        ? 'Two saved drafts exist. Download both and any older attachments before choosing text-and-media restoration or the text-only backup.'
        : 'Text and attached media can be saved in this browser only.'
      : 'Media storage is unavailable. Text-only fallback requires media reattachment after reload. Keep your original files.';
    status('Ready to edit. Changes remain in this browser.');
    listFiles(); await preview();
  }

  function save() {
    const context = persistence;
    context.unsaved = true;
    if (context.conflict || context.pending) {
      $('save').textContent = 'A browser draft is waiting. Download, restore or discard it explicitly before saving here. Your current edits are not autosaved yet.';
      return saving;
    }
    const generation = ++context.generation;
    const payload = { version: 2, baseId, text: draft.text, table: draft.table,
      assets: [...draft.updates].map(([path, file]) => ({ path, ...file })),
      missingPaths: [...missingAttachments], deletions: [...draft.deletions], saved: Date.now() };
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
      if (context.conflict) savedControls(true);
      $('save').textContent = context.conflict ? error.message
        : 'Could not save text and media in this browser. The previous saved draft is preserved. Download your text and attachments and keep originals before leaving.';
    });
    return saving;
  }

  async function preview() {
    if (!draft || !ready) return;
    const active = draft, generation = loadRevision, revision = ++renderRevision;
    const files = active.files();
    const wanted = new Set(['visual.svg']);
    let table = null;
    try { table = active.table === null ? null : CourseMediaTable.read(active.table); } catch { /* The table status reports it. */ }
    for (const target of CourseReferences.collect(active.text, table)) {
      try { const path = CourseReferences.localPath(target, contract.reference_schemes); if (path) wanted.add(path); } catch { /* Validation reports malformed references. */ }
    }
    const assets = [];
    for (const path of wanted) {
      const file = files.get(path); if (!file) continue;
      if (!file.blob) file.blob = await boundedBlob(file.url, file);
      if (generation !== loadRevision || revision !== renderRevision) return;
      if (sent.get(path) !== file.sha256) assets.push({ path, bytes: await file.blob.arrayBuffer(), hash: file.sha256 });
    }
    if (generation !== loadRevision || revision !== renderRevision) return;
    const paths = [...files.keys()].filter(path => wanted.has(path));
    const message = { version: 1, type: 'render', slug: config.slug, revision, text: active.text, table: active.table, paths, assets: assets.map(({ path, bytes }) => ({ path, bytes })) };
    CoursePreviewProtocol.validateRender(message, contract);
    $('preview').contentWindow.postMessage(message, location.origin);
    for (const path of sent.keys()) if (!paths.includes(path)) sent.delete(path);
    for (const asset of assets) sent.set(asset.path, asset.hash);
  }

  function listFiles() {
    $('files').replaceChildren();
    for (const [path, file] of draft.files()) {
      const row = document.createElement('li'); row.textContent = `${path} · ${file.size.toLocaleString()} bytes `;
      if (path === 'text.md' || path === tableName) { $('files').append(row); continue; }
      if (file.blob) {
        const downloadFile = document.createElement('button'); downloadFile.textContent = 'Download attachment';
        downloadFile.addEventListener('click', () => download(file.blob, path.split('/').pop())); row.append(downloadFile);
      }
      const remove = document.createElement('button'); remove.textContent = 'Remove from draft';
      remove.addEventListener('click', () => run(async () => {
        draft.remove(path); missingAttachments.delete(path);
        tableRows = tableRows.filter(entry => entry.file !== path); storeRows(); renderRows();
        invalidate(); save(); listFiles(); await preview();
      }));
      row.append(remove);
      $('files').append(row);
    }
    for (const path of missingAttachments) {
      if (draft.files().has(path)) continue;
      const row = document.createElement('li'); row.textContent = `${path} · reattachment required `;
      const discard = document.createElement('button'); discard.textContent = 'Discard missing attachment';
      discard.addEventListener('click', () => { missingAttachments.delete(path); invalidate(); save(); listFiles(); });
      row.append(discard); $('files').append(row);
    }
    for (const path of draft.deletions) {
      const row = document.createElement('li'); row.textContent = `${path} · explicit deletion planned `;
      const undo = document.createElement('button'); undo.textContent = 'Restore file to draft';
      undo.addEventListener('click', () => run(async () => {
        draft.deletions.delete(path);
        if (!tableRows.some(entry => entry.file === path)) { tableRows.push(...rowsFor([path])); storeRows(); renderRows(); }
        invalidate(); save(); listFiles(); await preview();
      }));
      row.append(undo); $('files').append(row);
    }
  }

  async function attach() {
    if (!draft) return;
    const file = $('attachment').files[0]; if (!file) throw new Error('Choose a file first');
    const path = $('path').value.trim();
    const name = $('media-name').value.trim(), source = $('media-source').value.trim();
    const clash = name && tableRows.find(entry => entry.name.toLowerCase() === name.toLowerCase() && entry.file !== path);
    if (clash) throw new Error(`The name "${name}" is already used for ${clash.file}`);
    const active = draft;
    if (file.size > contract.limits.media_bytes) throw new Error('Attachment exceeds the media file limit');
    const hash = await CourseDraft.digest(file);
    if (active !== draft) return;
    draft.attach(path, file, hash); missingAttachments.delete(path);
    const existing = tableRows.find(entry => entry.file === path);
    if (existing) Object.assign(existing, name ? { name } : {}, source ? { source } : {});
    else tableRows.push(name ? { name, source, file: path } : { ...rowsFor([path])[0], source });
    $('media-name').value = $('media-source').value = '';
    storeRows(); renderRows();
    invalidate(); save(); listFiles(); await preview();
  }

  /* ---------- media.csv rows ---------- */

  // Rows named after their files, for media that has none yet.
  function rowsFor(paths) {
    const taken = new Set(tableRows.map(entry => entry.name.toLowerCase()));
    return paths.map(file => {
      const base = file.split('/').pop();
      let name = base.replace(/\.[^.]+$/, '').replace(/[-_]+/g, ' ').trim() || base;
      if (taken.has(name.toLowerCase())) name = base;
      taken.add(name.toLowerCase());
      return { name, source: '', file };
    });
  }

  function tableStatus() {
    try {
      const table = CourseMediaTable.read(draft.table);
      const notes = CourseMediaTable.warnings(table, draft.files().keys());
      $('table-status').textContent = notes.length ? notes.join('. ') + '.' : `${table.rows.length} media listed.`;
    } catch (error) { $('table-status').textContent = error.message; }
  }

  function loadRows(started) {
    try { tableRows = CourseMediaTable.read(draft.table).rows.map(({ name, source, file }) => ({ name, source, file })); }
    catch (error) {
      tableRows = [];
      $('table-status').textContent = error.message;
    }
    renderRows();
    if (started) $('table-status').textContent = 'No media.csv yet: one row per media file was added. Add the source links, then commit media.csv.';
  }

  function storeRows() { draft.table = CourseMediaTable.serialize(tableRows); }

  function renderRows() {
    $('media-rows').replaceChildren(...tableRows.map((entry, index) => {
      const item = document.createElement('li'); item.dataset.index = index;
      for (const [field, label, type] of [['name', 'Name used in the text', 'text'], ['source', 'Link to the source', 'url'], ['file', 'File', 'text']]) {
        const wrapper = document.createElement('label'); wrapper.textContent = label;
        const input = document.createElement('input'); input.type = type; input.dataset.field = field; input.value = entry[field];
        if (field === 'source') input.placeholder = 'https://…';
        wrapper.append(input); item.append(wrapper);
      }
      const actions = document.createElement('div'); actions.className = 'row';
      for (const [action, label] of [['insert', 'Insert in text'], ['remove', 'Remove row']]) {
        const button = document.createElement('button'); button.dataset.action = action; button.textContent = label; actions.append(button);
      }
      item.append(actions);
      return item;
    }));
    tableStatus();
  }

  function insertText(text) {
    $('text').setRangeText(text, $('text').selectionStart, $('text').selectionEnd, 'end'); $('text').dispatchEvent(new Event('input')); $('text').focus();
  }

  function step(text, url, label) {
    const item = document.createElement('li');
    const check = document.createElement('input'); check.type = 'checkbox';
    const wrapper = document.createElement('label'); wrapper.append(check, ` ${text} `);
    if (url) { const link = document.createElement('a'); link.href = url; link.target = '_blank'; link.rel = 'noopener'; link.textContent = label || 'Open GitHub ↗'; wrapper.append(link); }
    item.append(wrapper); $('steps').append(item);
  }

  async function check() {
    const active = draft, revision = mutationRevision;
    if (missingAttachments.size) throw new Error('Reattach or explicitly remove these restored media files first: ' + [...missingAttachments].join(', '));
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
    CourseDraft.validateUploads(active.base, changes, result.files, contract);
    $('validation').textContent = `Known local checks passed: ${result.files.size} files, ${result.total.toLocaleString()} bytes. Importer still validates repository and SVG security. ${result.orphans.length ? 'Unreferenced media still counts: ' + result.orphans.join(', ') + '. ' : ''}${result.notes.join('. ')}`;
    $('steps').replaceChildren();
    step('Review current repository text and changed-media paths before overwriting.', github('blob', 'text.md'));
    for (const change of changes.filter(change => change.path !== 'text.md' && change.path !== tableName && change.operation !== 'delete')) {
      const parent = change.path.includes('/') ? change.path.slice(0, change.path.lastIndexOf('/')) : '';
      step(`${change.operation === 'add' ? 'Upload' : 'Replace'} ${change.path} and commit. Upload into ${parent || 'repository root'}; preserve this exact path.`, github('upload', parent));
    }
    const tableChange = changes.find(change => change.path === tableName);
    if (tableChange?.operation === 'add') {
      const create = new URL(`https://github.com/${config.repository}/new/${encodeURIComponent(config.branch)}`);
      create.searchParams.set('filename', tableName);
      if (draft.table.length < 4000) create.searchParams.set('value', draft.table);
      step('Create media.csv in the repository root. If the page is empty, paste the copied table, then commit.', create.href, 'Create media.csv ↗');
    } else if (tableChange) step('Replace the whole content of media.csv with the copied table and commit.', github('edit', tableName));
    if (changes.some(change => change.path === 'text.md')) step('Paste the reviewed text.md and commit it on the configured branch. For a new group, create the template files first.', github('edit', 'text.md'));
    for (const change of changes.filter(change => change.operation === 'delete')) step(`After committing text that no longer uses it, delete ${change.path} through the file menu.`, github('blob', change.path));
    if (groups.publication_requests?.enabled) {
      step('After completing the applicable GitHub commits, submit this request. Read its result on GitHub; submission does not prove publication.',
        CourseDraft.publicationRequest(groups.publication_requests.repository, config.slug), 'Request publication ↗');
    }
    step('Check the course site after publication. A checked box records your action; it does not verify a commit.', new URL(`#${config.slug}`, siteRoot).href, 'Open published entry ↗');
  }

  $('open').addEventListener('click', () => run(() => open(false)));
  $('template').addEventListener('click', () => run(() => open(true)));
  $('text').addEventListener('input', () => {
    if (!draft) return;
    draft.text = $('text').value; invalidate(); save();
    clearTimeout(timer); timer = setTimeout(() => run(preview), 150);
  });
  $('tools').addEventListener('click', event => {
    const text = event.target.dataset.insert; if (!text || !draft) return;
    insertText(text);
  });
  $('attachment').addEventListener('change', () => {
    const file = $('attachment').files[0]; if (!file) return;
    // Repository filenames are lowercase letters, digits, dots, hyphens and underscores.
    if (!$('path').value) $('path').value = 'media/' + file.name.toLowerCase().replace(/[^a-z0-9._-]+/g, '-').replace(/^[^a-z0-9]+/, '');
    if (!$('media-name').value) $('media-name').value = file.name.replace(/\.[^.]+$/, '').replace(/[-_]+/g, ' ').trim();
  });
  $('media-rows').addEventListener('input', event => {
    const field = event.target.dataset.field; if (!field || !draft) return;
    tableRows[event.target.closest('li').dataset.index][field] = event.target.value.trim();
    storeRows(); tableStatus(); invalidate(); save();
    clearTimeout(timer); timer = setTimeout(() => run(preview), 150);
  });
  $('media-rows').addEventListener('click', event => {
    const action = event.target.dataset.action; if (!action || !draft) return;
    const index = Number(event.target.closest('li').dataset.index);
    if (action === 'insert') { insertText(`[[${tableRows[index].name}]]`); return; }
    tableRows.splice(index, 1); storeRows(); renderRows(); invalidate(); save(); run(preview);
  });
  $('add-row').addEventListener('click', () => {
    tableRows.push({ name: '', source: '', file: 'media/' }); storeRows(); renderRows();
    $('media-rows').lastElementChild.querySelector('input').focus();
  });
  $('download-table').addEventListener('click', () => download(new Blob([draft.table], { type: 'text/csv' }), tableName));
  $('copy-table').addEventListener('click', () => run(async () => { await navigator.clipboard.writeText(draft.table); $('table-status').textContent = 'media.csv copied. Paste it into media.csv on GitHub.'; }));
  $('attach').addEventListener('click', () => run(attach));
  $('check').addEventListener('click', () => run(check));
  function download(blob, name) {
    const url = URL.createObjectURL(blob); const link = document.createElement('a');
    link.href = url; link.download = name; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  $('download').addEventListener('click', () => download(new Blob([draft.text], { type: 'text/plain' }), 'text.md'));
  $('stale').addEventListener('click', () => run(async () => {
    const active = draft;
    $('freshness').textContent = 'Comparing the raw GitHub response…';
    const blob = await fetchBlob(rawUrl(config.branch, 'text.md'), contract.limits.text_bytes);
    const current = await blob.text(); if (active !== draft) return;
    $('current-text').textContent = current;
    $('freshness').textContent = current === baseText ? 'Fetched text matches the base. CDN caching means this does not prove freshness or protect against concurrent edits.' : 'GitHub text differs from the base. Reconcile both versions before committing.';
  }));
  async function restoreSaved(useFallback = false) {
    const context = persistence, active = draft;
    await saving;
    const metadata = context.backend === 'indexeddb' ? await draftStore.read(context.key, false) : null;
    const raw = useFallback ? localStorage.getItem(context.key) : null;
    const saved = useFallback && raw ? { ...JSON.parse(raw), legacyRaw: raw } : await savedRecord(context);
    if (!saved || ![1, 2].includes(saved.version) || typeof saved.text !== 'string') throw new Error('Unsupported browser draft');
    if (saved.baseId !== baseId) throw new Error('The saved draft has a different base. Download or reconcile it before replacing the newer version.');
    const restored = new CourseDraft.Draft(active.base, config, contract);
    restored.text = saved.text;
    restored.table = typeof saved.table === 'string' ? saved.table : active.table;
    const missing = new Set(saved.version === 1 ? saved.paths || [] : saved.missingPaths || []);
    if (saved.version === 2) {
      if (!Array.isArray(saved.assets) || saved.assets.length > contract.limits.files) throw new Error('Invalid saved attachment inventory');
      for (const asset of saved.assets) {
        if (!asset.blob) { missing.add(asset.path); continue; }
        const hash = await CourseDraft.digest(asset.blob);
        if (hash !== asset.sha256 || asset.size !== asset.blob.size) throw new Error(`Saved attachment failed its integrity check: ${asset.path}`);
        restored.attach(asset.path, asset.blob, hash);
      }
    }
    for (const path of saved.deletions || []) if (restored.base.has(path)) restored.remove(path);
    if (context !== persistence || active !== draft) return;
    context.revision = saved.legacyRaw ? metadata?.revision || 0 : saved.revision || 0;
    context.retireLegacy = !!saved.legacyRaw;
    if (saved.legacyRaw) context.legacy = saved.legacyRaw;
    context.conflict = context.pending = false;
    draft = restored; missingAttachments.clear();
    for (const path of missing) missingAttachments.add(path);
    savedControls(false);
    $('text').value = draft.text; loadRows(false); invalidate(); listFiles();
    await save();
    if (context !== persistence) return;
    if (missingAttachments.size) $('save').textContent = 'Draft restored. Reattach or discard missing media: ' + [...missingAttachments].join(', ');
    await preview();
  }
  $('restore').addEventListener('click', () => run(() => restoreSaved()));
  $('fallback-restore').addEventListener('click', () => run(() => restoreSaved(true)));
  $('saved-download').addEventListener('click', () => run(async () => {
    const saved = await savedRecord(persistence);
    if (typeof saved?.text !== 'string') throw new Error('Saved text is unavailable');
    download(new Blob([saved.text], { type: 'text/plain' }), 'text.md');
    $('saved-files').replaceChildren();
    if (persistence.legacy && persistence.hasDurable) {
      const backup = JSON.parse(persistence.legacy);
      const row = document.createElement('li'); row.textContent = 'Text-only backup; reattach paths: ' + (backup.paths || []).join(', ') + ' ';
      const button = document.createElement('button'); button.textContent = 'Download text-only backup';
      button.addEventListener('click', () => download(new Blob([backup.text], { type: 'text/plain' }), 'text.md'));
      row.append(button); $('saved-files').append(row);
    }
    for (const asset of saved.assets || []) {
      if (!asset.blob) continue;
      const row = document.createElement('li'); row.textContent = `${asset.path} `;
      const button = document.createElement('button'); button.textContent = 'Download saved attachment';
      button.addEventListener('click', () => download(asset.blob, asset.path.split('/').pop()));
      row.append(button); $('saved-files').append(row);
    }
  }));
  $('discard').addEventListener('click', () => run(async () => {
    const context = persistence;
    await saving;
    if (context.backend === 'indexeddb') {
      const latest = await draftStore.read(context.key);
      context.revision = await draftStore.discard(context.key, latest?.revision || 0);
    }
    try { localStorage.removeItem(context.key); } catch (error) { if (context.backend === 'local') throw error; }
    context.legacy = null;
    context.conflict = context.pending = false;
    channel?.postMessage({ key: context.key, revision: context.revision });
    if (context !== persistence) return;
    savedControls(false); $('saved-files').replaceChildren(); await save();
  }));
  $('width').addEventListener('change', () => { $('preview').style.width = $('width').value + 'px'; });
  function externalSave(key) {
    if (!persistence || key !== persistence.key) return;
    persistence.conflict = true; savedControls(true);
    $('save').textContent = 'Another tab changed this draft. Restore or discard it explicitly before saving here. Download your current work first.';
  }
  window.addEventListener('storage', event => externalSave(event.key));
  if (channel) channel.onmessage = event => externalSave(event.data?.key);
  window.addEventListener('beforeunload', event => {
    if (!persistence?.unsaved) return;
    event.preventDefault(); event.returnValue = '';
  });
  window.addEventListener('message', event => {
    if (event.source !== $('preview').contentWindow || event.origin !== location.origin || !CoursePreviewProtocol.envelope(event.data)) return;
    if (event.data.type === 'ready') { ready = true; sent.clear(); run(preview); }
    else if (event.data.type === 'error' && event.data.revision === renderRevision) { sent.clear(); status('Preview: ' + event.data.message); }
  });
  $('preview').addEventListener('load', () => $('preview').contentWindow.postMessage({ version: 1, type: 'hello' }, location.origin));
  $('preview').contentWindow.postMessage({ version: 1, type: 'hello' }, location.origin);
  status('Choose your group and open a published version or template.');
})().catch(error => { document.getElementById('status').textContent = error.message; });
