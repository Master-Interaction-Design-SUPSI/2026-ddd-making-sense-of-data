((root) => {
  const model = typeof module !== 'undefined' && module.exports ? require('./model.js') : root.CourseDraft;
  const format = 'ddd-editor-recovery';
  const maximumBytes = contract => contract.limits.group_bytes * 2 + contract.limits.table_bytes * contract.limits.files * 2;

  function checkPath(path, contract, asset = false) {
    const selected = model.select(new Map([[path, { size: 0 }]]), contract, false).files;
    if (!selected.has(path) || asset && ['text.md', contract.media_table].includes(path)) throw new Error('Invalid recovery file path');
  }

  function checkRecord(record, contract) {
    if (!record || ![1, 2, 3].includes(record.version) || typeof record.baseId !== 'string' || record.baseId.length > 1024
        || typeof record.text !== 'string' || model.bytes(record.text) > contract.limits.text_bytes) throw new Error('Invalid recovery draft');
    if (Object.hasOwn(record, 'table') && record.table !== null && (typeof record.table !== 'string' || model.bytes(record.table) > contract.limits.table_bytes)) throw new Error('Invalid recovery media.csv');
    const group = record.group;
    if (!group || !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(group.slug) || group.slug.length > 63
        || typeof group.branch !== 'string' || !group.branch || group.branch.length > 255 || /[\u0000-\u001f\u007f]/.test(group.branch)
        || typeof group.repository !== 'string' || !/^[\w-]+\/[\w.-]+$/.test(group.repository)) throw new Error('Invalid recovery group identity');
    for (const field of ['deletions', 'missingPaths']) {
      if (!Array.isArray(record[field]) || record[field].length > contract.limits.files) throw new Error('Invalid recovery paths');
      for (const path of record[field]) checkPath(path, contract, true);
    }
    if (!Array.isArray(record.assets) || record.assets.length > contract.limits.files) throw new Error('Invalid recovery assets');
    const inventory = new Map();
    for (const asset of record.assets) {
      checkPath(asset.path, contract, true);
      if (inventory.has(asset.path) || !/^[0-9a-f]{64}$/.test(asset.sha256)) throw new Error('Invalid or duplicate recovery asset');
      inventory.set(asset.path, asset);
    }
    model.select(inventory, contract, false);
    if (record.baseInventory !== undefined) {
      if (!Array.isArray(record.baseInventory) || record.baseInventory.length > contract.limits.files) throw new Error('Invalid recovery base inventory');
      const base = new Map();
      for (const file of record.baseInventory) {
        checkPath(file.path, contract);
        if (base.has(file.path) || !/^[0-9a-f]{64}$/.test(file.sha256)) throw new Error('Invalid recovery base digest');
        base.set(file.path, file);
      }
      model.select(base, contract, false);
    }
    if (!Array.isArray(record.removed) || record.removed.length > contract.limits.files) throw new Error('Invalid recovery undo records');
    const removed = new Set();
    for (const entry of record.removed) {
      if (!Array.isArray(entry) || entry.length !== 2) throw new Error('Invalid recovery undo record');
      const [path, tables] = entry;
      checkPath(path, contract, true);
      if (removed.has(path) || !record.deletions.includes(path) || !tables) throw new Error('Invalid recovery deletion undo');
      removed.add(path);
      for (const key of ['tableBefore', 'tableAfter']) {
        if (tables[key] !== null && (typeof tables[key] !== 'string' || model.bytes(tables[key]) > contract.limits.table_bytes)) throw new Error('Invalid recovery undo table');
      }
    }
  }

  async function encode(saved, group, contract) {
    const missing = new Set(saved.version === 1 ? saved.paths || [] : saved.missingPaths || []);
    const assets = [];
    for (const asset of saved.assets || []) {
      if (!asset.blob) { missing.add(asset.path); continue; }
      if (asset.size !== asset.blob.size || await model.digest(asset.blob) !== asset.sha256) throw new Error(`Recovery integrity check failed: ${asset.path}`);
      const bytes = new Uint8Array(await asset.blob.arrayBuffer());
      let binary = '';
      for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
      assets.push({ path: asset.path, size: asset.size, sha256: asset.sha256, mime: asset.blob.type, bytes: btoa(binary) });
    }
    const record = { ...saved, group: saved.group || group, assets, missingPaths: [...missing], deletions: saved.deletions || [], removed: saved.removed || [] };
    delete record.legacyRaw; delete record.paths;
    checkRecord(record, contract);
    const encoded = JSON.stringify({ format, version: 1, draft: record });
    if (model.bytes(encoded) > maximumBytes(contract)) throw new Error('Recovery bundle exceeds its byte limit');
    return encoded;
  }

  async function decode(encoded, contract) {
    if (typeof encoded !== 'string' || model.bytes(encoded) > maximumBytes(contract)) throw new Error('Recovery bundle exceeds its byte limit');
    const bundle = JSON.parse(encoded);
    if (bundle?.format !== format || bundle.version !== 1) throw new Error('Unsupported recovery bundle');
    const record = bundle.draft;
    checkRecord(record, contract);
    const assets = [];
    for (const asset of record.assets) {
      if (typeof asset.bytes !== 'string' || asset.bytes.length !== 4 * Math.ceil(asset.size / 3)
          || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(asset.bytes)) throw new Error('Invalid recovery attachment bytes');
      const binary = atob(asset.bytes);
      const blob = new Blob([Uint8Array.from(binary, char => char.charCodeAt(0))], { type: typeof asset.mime === 'string' ? asset.mime : '' });
      if (blob.size !== asset.size || await model.digest(blob) !== asset.sha256) throw new Error(`Recovery integrity check failed: ${asset.path}`);
      assets.push({ path: asset.path, size: asset.size, sha256: asset.sha256, blob });
    }
    return { ...record, version: 3, assets };
  }

  const api = { encode, decode, maximumBytes };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CourseDraftRecovery = api;
})(globalThis);
