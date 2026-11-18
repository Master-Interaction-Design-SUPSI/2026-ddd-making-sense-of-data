((root) => {
  const references = typeof module !== 'undefined' && module.exports ? require('../static/references.js') : root.CourseReferences;
  const media = typeof module !== 'undefined' && module.exports ? require('../static/media-table.js') : root.CourseMediaTable;
  const bytes = text => new TextEncoder().encode(text).byteLength;
  const digest = async blob => [...new Uint8Array(await crypto.subtle.digest('SHA-256', await blob.arrayBuffer()))]
    .map(value => value.toString(16).padStart(2, '0')).join('');

  // complete: the file set is a whole group, so every required file must be present.
  function select(files, contract, complete = true) {
    const selected = new Map();
    const part = new RegExp(`^${contract.path_component}$`);
    const limits = contract.limits;
    let total = 0;
    for (const [name, record] of files) {
      if (typeof name !== 'string' || !name || /[\u0000-\u001f\u007f\\]/.test(name)) throw new Error('Invalid content path');
      const parts = name.split('/');
      if (parts.some(value => ['', '.', '..'].includes(value))) throw new Error(`${name}: traversal is not allowed`);
      const extension = name.includes('.') ? '.' + name.split('.').pop().toLowerCase() : '';
      const stem = name.includes('.') ? name.slice(0, name.lastIndexOf('.')) : name;
      if (parts.length === 1 && stem.toLowerCase() === 'visual' && name !== 'visual.svg') throw new Error(`${name}: use exactly visual.svg`);
      const required = name === 'text.md' || name === 'visual.svg';
      const attachment = contract.media_extensions.includes(extension) && (parts.length === 1 || parts[0] === 'media');
      const table = name === contract.media_table;
      if (!required && !attachment && !table) continue;
      if (name.length > limits.path_length || parts.length > limits.path_depth || !parts.every(value => part.test(value))) throw new Error(`${name}: invalid content filename`);
      const limit = name === 'text.md' ? limits.text_bytes : table ? limits.table_bytes : extension === '.svg' ? limits.svg_bytes : limits.media_bytes;
      if (!Number.isSafeInteger(record.size) || record.size < 0) throw new Error(`${name}: invalid file size`);
      if (record.size > limit) throw new Error(`${name}: exceeds the ${limit}-byte file limit`);
      total += record.size;
      selected.set(name, record);
    }
    if (selected.size > limits.files || total > limits.group_bytes) throw new Error(`Group content exceeds ${limits.files} files or ${limits.group_bytes / 1048576} MiB`);
    for (const name of complete ? contract.required_files : []) {
      if (!selected.has(name)) throw new Error(`Group content requires ${name}`);
    }
    for (const name of selected.keys()) {
      const parts = name.split('/');
      for (let i = 1; i < parts.length; i++) {
        if (selected.has(parts.slice(0, i).join('/'))) throw new Error(`${name}: a parent path is a file`);
      }
    }
    return { files: selected, total };
  }

  class Draft {
    constructor(base, config, contract) {
      this.base = new Map(base);
      this.config = config;
      this.contract = contract;
      this.updates = new Map();
      this.deletions = new Set();
      this.removed = new Map();
      this.text = '';
      this.table = null;
      this.complete = true;
    }
    attach(path, blob, sha256) {
      if (path === 'text.md') throw new Error('Edit text.md in the Markdown field');
      if (path === this.contract.media_table) throw new Error('Edit media.csv in the media table');
      if (this.config.exclude.includes(path)) throw new Error(`${path}: excluded by the course maintainer`);
      const trial = this.files();
      trial.set(path, { size: blob.size });
      if (!select(trial, this.contract, false).files.has(path)) throw new Error(`${path}: unsupported attachment`);
      const retained = new Map(this.updates);
      retained.set(path, { size: blob.size });
      try { select(retained, this.contract, false); }
      catch { throw new Error('This draft exceeds its recovery limit. Download a recovery bundle before starting a fresh draft.'); }
      this.updates.set(path, { blob, size: blob.size, sha256 });
      this.deletions.delete(path);
    }
    remove(path) {
      if (!this.complete) throw new Error('A partial inventory cannot plan deletions');
      if (this.base.has(path) || this.updates.has(path)) this.deletions.add(path);
    }
    mediaRows() {
      return this.table === null ? [] : media.read(this.table).rows.map(({ name, source, file }) => ({ name, source, file }));
    }
    suggestPath(filename) {
      const safe = filename.toLowerCase().replace(/[^a-z0-9._-]+/g, '-').replace(/^[^a-z0-9]+/, '');
      if (!safe) throw new Error('Choose a file with a valid filename');
      const dot = safe.lastIndexOf('.');
      const stem = dot > 0 ? safe.slice(0, dot) : safe, extension = dot > 0 ? safe.slice(dot) : '';
      const taken = new Set([...this.base.keys(), ...this.updates.keys()]);
      let path = `media/${safe}`;
      for (let i = 2; taken.has(path); i++) path = `media/${stem}-${i}${extension}`;
      return path;
    }
    addAsset(path, blob, hash, metadata) {
      if (this.base.has(path) || this.updates.has(path)) throw new Error(`${path}: use Replace on the existing asset`);
      const rows = this.mediaRows();
      const row = { ...rowsFor([path], rows)[0], ...metadata, file: path };
      const table = path === 'visual.svg' ? this.table : media.serialize([...rows, row]);
      if (table !== null) media.read(table);
      this.attach(path, blob, hash);
      this.table = table;
    }
    replaceAsset(path, blob, hash) {
      if (!this.files().has(path)) throw new Error(`${path}: choose an existing asset to replace`);
      this.attach(path, blob, hash);
    }
    renameAsset(previous, next) {
      const rows = this.mediaRows();
      const row = rows.find(row => row.name.toLowerCase() === previous.toLowerCase());
      if (!row) throw new Error('Media name is no longer present');
      row.name = next.trim();
      const table = media.serialize(rows);
      media.read(table);
      const text = media.renameReferences(this.text, previous, row.name);
      this.table = table; this.text = text;
    }
    removeAsset(path) {
      const rows = this.mediaRows();
      if (!this.files().has(path)) throw new Error(`${path}: file is no longer present`);
      const names = new Set(rows.filter(row => row.file === path).map(row => row.name.toLowerCase()));
      const named = media.namedReferences(this.text).filter(ref => names.has(ref.name.toLowerCase()));
      const direct = references.collect(this.text).some(target => references.localPath(target, this.contract.reference_schemes) === path);
      if (named.length || direct) throw new Error(`${path}: remove its text references first${named.length ? ': ' + named.map(ref => `[[${ref.name}]]`).join(', ') : ''}`);
      const tableBefore = this.table;
      const remaining = rows.filter(row => row.file !== path);
      const tableAfter = remaining.length || this.base.has(this.contract.media_table) ? media.serialize(remaining) : null;
      this.remove(path);
      this.removed.set(path, { tableBefore, tableAfter });
      this.table = tableAfter;
    }
    undoRemove(path) {
      const removed = this.removed.get(path);
      if (!removed) throw new Error(`${path}: exact undo is unavailable for this older deletion`);
      let table = removed.tableBefore;
      if (this.table !== removed.tableAfter) {
        const rows = this.mediaRows();
        const before = removed.tableBefore === null ? [] : media.read(removed.tableBefore).rows;
        for (let i = 0; i < before.length; i++) {
          if (before[i].file === path) rows.splice(Math.min(i, rows.length), 0, before[i]);
        }
        table = rows.length || this.base.has(this.contract.media_table) ? media.serialize(rows) : null;
        if (table !== null) media.read(table);
      }
      this.table = table;
      this.deletions.delete(path); this.removed.delete(path);
    }
    files() {
      const files = new Map(this.base);
      for (const name of this.deletions) files.delete(name);
      for (const [name, file] of this.updates) if (!this.deletions.has(name)) files.set(name, file);
      files.set('text.md', { size: bytes(this.text), blob: new Blob([this.text], { type: 'text/plain' }) });
      if (this.table !== null) files.set(this.contract.media_table, { size: bytes(this.table), blob: new Blob([this.table], { type: 'text/csv' }) });
      return files;
    }
    validate() {
      // media.csv is optional: a draft without media has none.
      const table = this.table === null ? null : media.read(this.table);
      const result = select(this.files(), this.contract);
      // Names and files that do not resolve yet are notes: the page shows them as missing.
      const checked = references.validate(this.text, result.files.keys(), this.contract.reference_schemes, table);
      const used = new Set(checked.used);
      const fixed = [...this.contract.required_files, this.contract.media_table];
      return { ...result, orphans: [...result.files.keys()].filter(name => !fixed.includes(name) && !used.has(name)),
               notes: [...checked.warnings, ...(table ? media.warnings(table, result.files.keys()) : [])] };
    }
    async changes() {
      const changes = [];
      for (const [path, file] of this.files()) {
        const base = this.base.get(path);
        if (path === 'text.md' || path === this.contract.media_table) file.sha256 = await digest(file.blob);
        if (!base) changes.push({ operation: 'add', path });
        else if (file.sha256 !== base.sha256) changes.push({ operation: 'replace', path });
      }
      for (const path of this.deletions) if (this.base.has(path)) changes.push({ operation: 'delete', path });
      return changes.sort((a, b) => a.path.localeCompare(b.path));
    }
  }
  /* A group without media.csv starts from one row per existing media file,
     named after the file, so only the source links remain to be filled in.
     Without media it needs no table: null. */
  function startingTable(files, contract) {
    const mediaFiles = [...files.keys()].filter(file => ![...contract.required_files, contract.media_table].includes(file));
    if (!mediaFiles.length) return null;
    const rows = rowsFor(mediaFiles.sort());
    return media.serialize(rows);
  }

  function rowsFor(paths, existingRows = []) {
    const taken = new Set(existingRows.map(row => row.name.toLowerCase()));
    return paths.map(file => {
      const filename = file.split('/').pop();
      const stem = filename.replace(/\.[^.]+$/, '').replace(/[-_]+/g, ' ').trim() || filename;
      let name = stem;
      if (taken.has(name.toLowerCase())) name = filename;
      for (let suffix = 2; taken.has(name.toLowerCase()); suffix++) name = `${filename} ${suffix}`;
      taken.add(name.toLowerCase());
      return { name, source: '', file };
    });
  }

  const api = { bytes, digest, select, startingTable, rowsFor, Draft };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CourseDraft = api;
})(globalThis);
