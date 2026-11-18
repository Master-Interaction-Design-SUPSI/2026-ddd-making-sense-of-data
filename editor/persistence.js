((root) => {
  const storage = typeof module !== 'undefined' && module.exports ? require('./draft-store.js') : root.CourseDraftStorage;

  class Persistence {
    constructor({ store, local = () => root.localStorage, notify = () => {} }) {
      this.store = store;
      this.local = local;
      this.notify = notify;
      this.saving = Promise.resolve();
    }

    idle() { return this.saving; }

    async open(key) {
      await this.idle();
      let legacy = null;
      try { legacy = this.local().getItem(key); } catch { /* IndexedDB may work when localStorage is denied. */ }
      const context = { key, legacy, conflict: false, retireLegacy: false, generation: 0, unsaved: false };
      try {
        const record = await this.store.read(key);
        const hasDurable = !!(record && !record.discarded);
        return { ...context, backend: 'indexeddb', revision: record?.revision || 0, hasDurable, pending: hasDurable || !!legacy };
      } catch {
        return { ...context, backend: 'local', hasDurable: false, pending: !!legacy };
      }
    }

    async read(context, fallback = false) {
      if (!fallback && context.backend === 'indexeddb') {
        const record = await this.store.read(context.key);
        if (record && !record.discarded) return record;
      }
      const raw = this.local().getItem(context.key);
      return raw ? { ...JSON.parse(raw), legacyRaw: raw } : null;
    }

    metadata(context) {
      return context.backend === 'indexeddb' ? this.store.read(context.key, false) : null;
    }

    resume(context, saved, metadata, imported = false) {
      context.revision = imported || saved.legacyRaw ? metadata?.revision || 0 : saved.revision || 0;
      context.retireLegacy = !!saved.legacyRaw;
      if (saved.legacyRaw) context.legacy = saved.legacyRaw;
      context.conflict = context.pending = false;
      context.gate?.resume();
    }

    async write(context, payload) {
      if (context.backend === 'local') {
        const local = this.local();
        if (local.getItem(context.key) !== context.legacy) throw new storage.DraftConflict();
        const fallback = { version: 1, baseId: payload.baseId, text: payload.text, table: payload.table,
          paths: [...new Set([...payload.assets.map(asset => asset.path), ...payload.missingPaths])],
          deletions: payload.deletions, saved: payload.saved };
        const encoded = JSON.stringify(fallback);
        local.setItem(context.key, encoded);
        context.legacy = encoded;
        return;
      }
      context.revision = await this.store.save(context.key, payload, context.revision);
      this.notify({ key: context.key, revision: context.revision });
      try {
        if (context.retireLegacy && context.legacy && this.local().getItem(context.key) === context.legacy) {
          this.local().removeItem(context.key);
          context.legacy = null;
        }
      } catch { /* The durable draft survives failure to retire an older text copy. */ }
    }

    save(context, payload, onState = () => {}) {
      const generation = ++context.generation;
      context.unsaved = true;
      onState({ status: 'saving', context });
      this.saving = this.saving.then(async () => {
        if (context.conflict || context.pending || generation !== context.generation) return;
        await this.write(context, payload);
        if (generation !== context.generation) return;
        context.unsaved = false;
        onState({ status: 'saved', context });
      }).catch(error => {
        if (error.name === 'DraftConflict') { context.conflict = true; context.gate?.requireRecovery(); }
        onState({ status: context.conflict ? 'conflict' : 'error', context, error });
      });
      return this.saving;
    }

    async discard(context) {
      await this.idle();
      if (context.backend === 'indexeddb') {
        const latest = await this.store.read(context.key);
        context.revision = await this.store.discard(context.key, latest?.revision || 0);
      }
      try { this.local().removeItem(context.key); } catch (error) { if (context.backend === 'local') throw error; }
      context.legacy = null;
      context.conflict = context.pending = false;
      context.gate?.useBase(true);
      this.notify({ key: context.key, revision: context.revision });
    }
  }

  const api = { Persistence };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CoursePersistence = api;
})(globalThis);
