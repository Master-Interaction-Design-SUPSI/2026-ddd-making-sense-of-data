((root) => {
  const references = typeof module !== 'undefined' && module.exports ? require('../static/references.js') : root.CourseReferences;
  const media = typeof module !== 'undefined' && module.exports ? require('../static/media-table.js') : root.CourseMediaTable;
  const bytes = text => new TextEncoder().encode(text).byteLength;
  const digest = async blob => [...new Uint8Array(await crypto.subtle.digest('SHA-256', await blob.arrayBuffer()))]
    .map(value => value.toString(16).padStart(2, '0')).join('');

  function publicationRequest(repository, slug) {
    if (typeof repository !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9-]*\/[A-Za-z0-9_][A-Za-z0-9_.-]*$/.test(repository) || repository.endsWith('.git')
        || typeof slug !== 'string' || slug.length > 63 || !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(slug)) {
      throw new Error('Invalid publication request identity');
    }
    const url = new URL(`https://github.com/${repository}/issues/new`);
    url.searchParams.set('template', 'publish.yml');
    url.searchParams.set('group', slug);
    url.searchParams.set('labels', 'publication-request');
    return url.href;
  }

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
      this.updates.set(path, { blob, size: blob.size, sha256 });
      this.deletions.delete(path);
    }
    remove(path) {
      if (!this.complete) throw new Error('A partial inventory cannot plan deletions');
      this.updates.delete(path);
      if (this.base.has(path)) this.deletions.add(path);
    }
    files() {
      const files = new Map(this.base);
      for (const name of this.deletions) files.delete(name);
      for (const [name, file] of this.updates) files.set(name, file);
      files.set('text.md', { size: bytes(this.text), blob: new Blob([this.text], { type: 'text/plain' }) });
      if (this.table !== null) files.set(this.contract.media_table, { size: bytes(this.table), blob: new Blob([this.table], { type: 'text/csv' }) });
      return files;
    }
    validate() {
      if (this.table === null) throw new Error('media.csv: add the media table');
      const table = media.read(this.table);
      const result = select(this.files(), this.contract);
      // Names and files that do not resolve yet are notes: the page shows them as missing.
      const checked = references.validate(this.text, result.files.keys(), this.contract.reference_schemes, table);
      const used = new Set(checked.used);
      const fixed = ['text.md', 'visual.svg', this.contract.media_table];
      return { ...result, orphans: [...result.files.keys()].filter(name => !fixed.includes(name) && !used.has(name)),
               notes: [...checked.warnings, ...media.warnings(table, result.files.keys())] };
    }
    async changes() {
      const changes = [];
      for (const [path, file] of this.files()) {
        const base = this.base.get(path);
        if (path === 'text.md' || path === this.contract.media_table) file.sha256 = await digest(file.blob);
        if (!base) changes.push({ operation: 'add', path });
        else if (file.sha256 !== base.sha256) changes.push({ operation: 'replace', path });
      }
      for (const path of this.deletions) changes.push({ operation: 'delete', path });
      return changes.sort((a, b) => a.path.localeCompare(b.path));
    }
  }
  /* A group without media.csv starts from one row per existing media file,
     named after the file, so only the source links remain to be filled in. */
  function startingTable(files, contract) {
    const taken = new Set();
    const rows = [...files.keys()].filter(file => !['text.md', 'visual.svg', contract.media_table].includes(file)).sort().map(file => {
      const base = file.split('/').pop();
      let name = base.replace(/\.[^.]+$/, '').replace(/[-_]+/g, ' ').trim() || base;
      if (taken.has(name.toLowerCase())) name = base;
      taken.add(name.toLowerCase());
      return { name, source: '', file };
    });
    return media.serialize(rows);
  }

  const api = { bytes, digest, select, publicationRequest, startingTable, Draft };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CourseDraft = api;
})(globalThis);
