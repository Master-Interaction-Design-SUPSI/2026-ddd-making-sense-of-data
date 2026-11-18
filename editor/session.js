((root) => {
  const model = typeof module !== 'undefined' && module.exports ? require('./model.js') : root.CourseDraft;

  class RecoveryGate {
    constructor(required = false) { this.recoveryRequired = required; this.comparison = false; }
    get readOnly() { return this.recoveryRequired || this.comparison; }
    assertEditable() { if (this.readOnly) throw new Error('Resolve the saved browser draft before editing or saving.'); }
    requireRecovery() { this.recoveryRequired = true; }
    compare() { this.comparison = true; this.recoveryRequired = true; }
    resume() { this.recoveryRequired = this.comparison = false; }
    useBase(confirmed) { if (!confirmed) return false; this.resume(); return true; }
  }

  function snapshot(draft, baseId, missingPaths) {
    return { version: 3, baseId, group: { slug: draft.config.slug, repository: draft.config.repository, branch: draft.config.branch },
      contractVersion: draft.contract.version, baseInventory: [...draft.base].map(([path, { size, sha256 }]) => ({ path, size, sha256 })),
      text: draft.text, table: draft.table, assets: [...draft.updates].map(([path, file]) => ({ path, ...file })),
      missingPaths: [...missingPaths], deletions: [...draft.deletions], removed: [...draft.removed], saved: Date.now() };
  }

  async function restore(saved, active, baseId) {
    if (!saved || ![1, 2, 3].includes(saved.version) || typeof saved.text !== 'string') throw new Error('Unsupported browser draft');
    if (saved.group && ['slug', 'repository', 'branch'].some(key => saved.group[key] !== active.config[key])) throw new Error('This draft belongs to a different group.');
    if (saved.baseId !== baseId) throw new Error('The saved draft has a different base. Download and compare it before replacing the current version.');
    const draft = new model.Draft(active.base, active.config, active.contract);
    draft.text = saved.text;
    draft.table = typeof saved.table === 'string' || saved.table === null ? saved.table : active.table;
    const missingPaths = new Set(saved.version === 1 ? saved.paths || [] : saved.missingPaths || []);
    const assets = saved.version === 1 ? [] : saved.assets;
    if (!Array.isArray(assets) || assets.length > active.contract.limits.files) throw new Error('Invalid saved attachment inventory');
    const paths = new Set();
    for (const asset of assets) {
      if (paths.has(asset.path)) throw new Error('Duplicate saved attachment');
      paths.add(asset.path);
      if (!asset.blob) { missingPaths.add(asset.path); continue; }
      const hash = await model.digest(asset.blob);
      if (hash !== asset.sha256 || asset.size !== asset.blob.size) throw new Error(`Saved attachment failed its integrity check: ${asset.path}`);
      draft.attach(asset.path, asset.blob, hash);
    }
    for (const path of saved.deletions || []) draft.remove(path);
    draft.removed = new Map(saved.removed || []);
    model.select(draft.files(), active.contract, false);
    return { draft, missingPaths };
  }

  const api = { RecoveryGate, snapshot, restore };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CourseDraftSession = api;
})(globalThis);
