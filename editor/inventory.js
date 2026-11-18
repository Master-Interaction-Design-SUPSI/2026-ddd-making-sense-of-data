((root) => {
  const media = typeof module !== 'undefined' && module.exports ? require('../static/media-table.js') : root.CourseMediaTable;
  const references = typeof module !== 'undefined' && module.exports ? require('../static/references.js') : root.CourseReferences;

  function increment(counts, key) { counts.set(key, (counts.get(key) || 0) + 1); }

  function describe(text, rows, files, schemes, tableName = 'media.csv') {
    const names = new Map(), direct = new Map(), byFile = new Map();
    for (const reference of media.namedReferences(text)) increment(names, reference.name.toLowerCase());
    for (const target of references.collect(text)) {
      try {
        const path = references.localPath(target, schemes);
        if (path) increment(direct, path);
      } catch { /* Review reports unsafe references; the inventory remains usable. */ }
    }
    rows.forEach((entry, index) => {
      if (!byFile.has(entry.file)) byFile.set(entry.file, []);
      byFile.get(entry.file).push({ entry, index });
    });
    const inventory = new Map([...files].filter(([path]) => !['text.md', 'visual.svg', tableName].includes(path)));
    for (const row of rows) {
      if (row.file !== 'visual.svg' && !inventory.has(row.file)) inventory.set(row.file, null);
    }
    const items = [...inventory].map(([path, file]) => {
      const entries = byFile.get(path) || [];
      const named = entries.reduce((count, { entry }) => count + (names.get(entry.name.toLowerCase()) || 0), 0);
      return { path, file, entries, uses: named + (direct.get(path) || 0) };
    });
    return { items, visual: { file: files.get('visual.svg'), entries: byFile.get('visual.svg') || [] } };
  }

  const api = { describe };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CourseInventory = api;
})(globalThis);
