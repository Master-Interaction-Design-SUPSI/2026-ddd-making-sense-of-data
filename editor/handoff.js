((root) => {
  const link = (url, label) => ({ kind: 'link', url: String(url), label });
  const copy = (text, label) => ({ kind: 'copy', text, label });

  function plan({ changes, files, text, table, config, siteRoot, tableName = 'media.csv' }) {
    const github = (action, path = '') => `https://github.com/${config.repository}/${action}/${encodeURIComponent(config.branch)}/${path.split('/').map(encodeURIComponent).join('/')}`;
    const steps = [];
    const step = (text, ...actions) => steps.push({ text, actions });
    step('Check that nobody changed the text on GitHub after the version you opened.',
      { kind: 'compare', label: 'Compare with GitHub' }, link(github('blob', 'text.md'), 'Open text.md on GitHub ↗'));
    for (const change of changes) {
      if (['text.md', tableName].includes(change.path) || change.operation === 'delete') continue;
      const parent = change.path.includes('/') ? change.path.slice(0, change.path.lastIndexOf('/')) : '';
      const name = change.path.split('/').pop();
      const blob = files.get(change.path)?.blob;
      const where = parent ? `the ${parent} folder` : 'the main file list (where text.md is)';
      const actions = blob ? [{ kind: 'download', label: `Download ${name}`, blob, name }] : [];
      step(`Download ${name}, then upload it into ${where} on GitHub${change.operation === 'add' ? '' : ' (it replaces the old file)'} and commit.`,
        ...actions, link(github('upload', parent), 'Open upload page ↗'));
    }
    const tableChange = changes.find(change => change.path === tableName && change.operation !== 'delete');
    if (tableChange) {
      const create = new URL(`https://github.com/${config.repository}/new/${encodeURIComponent(config.branch)}`);
      create.searchParams.set('filename', tableName);
      if (table.length < 4000) create.searchParams.set('value', table);
      const creating = tableChange.operation === 'add';
      step(creating ? 'Create media.csv on GitHub. If it opens empty, paste the copied table. Commit.' : 'Replace everything in media.csv with the copied table, and commit.',
        copy(table, 'Copy media.csv'), creating ? link(create, 'Create media.csv on GitHub ↗') : link(github('edit', tableName), 'Edit media.csv on GitHub ↗'));
    }
    if (changes.some(change => change.path === 'text.md')) {
      step('Replace everything in text.md with the copied text, and commit.',
        copy(text, 'Copy text.md'), link(github('edit', 'text.md'), 'Edit text.md on GitHub ↗'));
    }
    for (const change of changes) {
      if (change.operation !== 'delete') continue;
      step(`Delete ${change.path}: open it, choose … → Delete file, and commit.`,
        link(github('blob', change.path), `Open ${change.path.split('/').pop()} on GitHub ↗`));
    }
    step('Open your entry on the course site. Updates take a while to appear.',
      link(new URL(`#${config.slug}`, siteRoot), 'Open published entry ↗'));
    return steps;
  }

  const api = { plan };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CourseHandoff = api;
})(globalThis);
