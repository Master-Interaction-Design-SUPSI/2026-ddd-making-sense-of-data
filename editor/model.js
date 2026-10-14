((root) => {
  const references = typeof module !== 'undefined' && module.exports ? require('../static/references.js') : root.CourseReferences;
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

  function select(files, contract, requireVisual = true) {
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
      const media = contract.media_extensions.includes(extension) && (parts.length === 1 || parts[0] === 'media');
      if (!required && !media) continue;
      if (name.length > limits.path_length || parts.length > limits.path_depth || !parts.every(value => part.test(value))) throw new Error(`${name}: invalid content filename`);
      const limit = name === 'text.md' ? limits.text_bytes : extension === '.svg' ? limits.svg_bytes : limits.media_bytes;
      if (!Number.isSafeInteger(record.size) || record.size < 0) throw new Error(`${name}: invalid file size`);
      if (record.size > limit) throw new Error(`${name}: exceeds the ${limit}-byte file limit`);
      total += record.size;
      selected.set(name, record);
    }
    if (selected.size > limits.files || total > limits.group_bytes) throw new Error(`Group content exceeds ${limits.files} files or ${limits.group_bytes / 1048576} MiB`);
    for (const name of requireVisual ? [...contract.required_files, contract.conditional_visual] : contract.required_files) {
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

  function validateUploads(base, changes, final, contract, requireVisual) {
    const intermediate = new Map(base);
    for (const change of changes.filter(change => change.path !== 'text.md' && change.operation !== 'delete')) {
      intermediate.set(change.path, final.get(change.path));
      try { select(intermediate, contract, requireVisual); }
      catch (error) { throw new Error(`Uploading ${change.path} would temporarily break the content limits or paths: ${error.message}. Reconcile the commit sequence before uploading.`); }
    }
    intermediate.set('text.md', final.get('text.md'));
    try { select(intermediate, contract, requireVisual); }
    catch (error) { throw new Error(`Committing text before deleting obsolete files would temporarily break the content limits: ${error.message}. Reconcile the commit sequence before uploading.`); }
  }

  class Draft {
    constructor(base, config, contract) {
      this.base = new Map(base);
      this.config = config;
      this.contract = contract;
      this.updates = new Map();
      this.deletions = new Set();
      this.text = '';
      this.complete = true;
    }
    attach(path, blob, sha256) {
      if (path === 'text.md') throw new Error('Edit text.md in the Markdown field');
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
      return files;
    }
    validate() {
      const result = select(this.files(), this.contract, this.config.require_visual);
      const used = new Set(references.validate(this.text, result.files.keys(), this.contract.reference_schemes));
      return { ...result, orphans: [...result.files.keys()].filter(name => !['text.md', 'visual.svg'].includes(name) && !used.has(name)) };
    }
    async changes() {
      const changes = [];
      for (const [path, file] of this.files()) {
        const base = this.base.get(path);
        if (path === 'text.md') file.sha256 = await digest(file.blob);
        if (!base) changes.push({ operation: 'add', path });
        else if (file.sha256 !== base.sha256) changes.push({ operation: 'replace', path });
      }
      for (const path of this.deletions) changes.push({ operation: 'delete', path });
      return changes.sort((a, b) => a.path.localeCompare(b.path));
    }
  }
  const api = { bytes, digest, select, validateUploads, publicationRequest, Draft };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CourseDraft = api;
})(globalThis);
