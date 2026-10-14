((root) => {
  class DraftConflict extends Error {
    constructor() { super('Another tab changed this browser draft. Restore or discard it explicitly before saving here.'); this.name = 'DraftConflict'; }
  }

  class DraftStore {
    constructor(database = 'ddd-editor-drafts', factory) {
      if (factory === undefined) {
        try { factory = root.indexedDB; } catch { factory = null; }
      }
      this.database = database;
      this.factory = factory;
      this.connection = null;
    }

    open() {
      if (this.connection) return this.connection;
      this.connection = new Promise((resolve, reject) => {
        if (!this.factory) { reject(new Error('Browser media storage is unavailable')); return; }
        let blocked = false;
        const request = this.factory.open(this.database, 2);
        request.onupgradeneeded = () => {
          for (const name of ['drafts', 'blobs']) {
            if (!request.result.objectStoreNames.contains(name)) request.result.createObjectStore(name);
          }
        };
        request.onerror = () => reject(request.error);
        request.onblocked = () => { blocked = true; reject(new Error('Close other editor tabs to finish upgrading browser storage')); };
        request.onsuccess = () => {
          const database = request.result;
          if (blocked) { database.close(); return; }
          database.onversionchange = () => { database.close(); this.connection = null; };
          resolve(database);
        };
      });
      return this.connection;
    }

    async read(key, loadAssets = true) {
      const database = await this.open();
      return new Promise((resolve, reject) => {
        const transaction = database.transaction(['drafts', 'blobs'], 'readonly');
        const request = transaction.objectStore('drafts').get(key);
        let value;
        request.onsuccess = () => {
          value = request.result || null;
          if (!value || value.discarded || !loadAssets) return;
          value.assets = (value.assets || []).map(asset => ({ ...asset }));
          for (const asset of value.assets) {
            const blob = transaction.objectStore('blobs').get(asset.sha256);
            blob.onsuccess = () => {
              const record = blob.result;
              asset.blob = record?.bytes instanceof ArrayBuffer
                ? new Blob([record.bytes], { type: record.mime || '' }) : record?.blob || null;
            };
          }
        };
        transaction.oncomplete = () => resolve(value);
        transaction.onerror = event => reject(event.target.error || transaction.error || new Error('Could not read the browser draft'));
        transaction.onabort = () => reject(transaction.error || new Error('Could not read the browser draft'));
      });
    }

    async write(key, payload, expectedRevision, discard = false) {
      if (!discard && (!payload || !Array.isArray(payload.assets) || payload.assets.length > 100)) throw new Error('Invalid browser draft assets');
      if (!discard && payload.assets.some(asset => !asset || typeof asset.path !== 'string' || !/^[0-9a-f]{64}$/.test(asset.sha256) || !(asset.blob instanceof Blob) || asset.size !== asset.blob.size)) throw new Error('Invalid saved attachment');
      const database = await this.open();
      const metadata = await this.read(key, false);
      const present = new Set((metadata?.assets || []).map(asset => asset.sha256));
      const serialized = new Map();
      for (const asset of discard ? [] : payload.assets) {
        if (!present.has(asset.sha256) && !serialized.has(asset.sha256)) {
          serialized.set(asset.sha256, { bytes: await asset.blob.arrayBuffer(), mime: asset.blob.type, format: 1 });
        }
      }
      return new Promise((resolve, reject) => {
        const transaction = database.transaction(['drafts', 'blobs'], 'readwrite');
        const store = transaction.objectStore('drafts');
        const blobs = transaction.objectStore('blobs');
        const request = store.get(key);
        let failure, revision;
        const abort = error => { failure = error; transaction.abort(); };
        request.onsuccess = () => {
          try {
            const previous = request.result;
            if ((previous?.revision || 0) !== expectedRevision) { abort(new DraftConflict()); return; }
            revision = expectedRevision + 1;
            if (!Number.isSafeInteger(revision)) { abort(new Error('Browser draft revision exceeded its limit')); return; }
            const incoming = discard ? [] : payload.assets;
            const before = new Set((previous?.assets || []).map(asset => asset.sha256));
            const after = new Map(incoming.map(asset => [asset.sha256, asset]));
            for (const sha of new Set([...before, ...after.keys()])) {
              if (before.has(sha) && after.has(sha)) continue;
              const request = blobs.get(sha);
              request.onsuccess = () => {
                try {
                const existing = request.result;
                const owners = new Set(existing?.owners || []);
                if (after.has(sha)) {
                  owners.add(key);
                  blobs.put({ ...(existing || serialized.get(sha)), owners: [...owners] }, sha);
                } else {
                  owners.delete(key);
                  if (owners.size) blobs.put({ ...existing, owners: [...owners] }, sha);
                  else blobs.delete(sha);
                }
                } catch (error) { abort(error); }
              };
            }
            if (discard) store.put({ schema: 2, revision, discarded: true }, key);
            else store.put({ ...payload, schema: 2, revision,
                            assets: incoming.map(({ path, size, sha256 }) => ({ path, size, sha256 })) }, key);
          } catch (error) { abort(error); }
        };
        transaction.oncomplete = () => resolve(revision);
        transaction.onerror = event => { failure = failure || event.target.error || transaction.error; };
        transaction.onabort = () => reject(failure || transaction.error || new Error('Browser draft save failed'));
      });
    }

    save(key, payload, expectedRevision) { return this.write(key, payload, expectedRevision); }
    discard(key, expectedRevision) { return this.write(key, null, expectedRevision, true); }
  }

  const api = { DraftStore, DraftConflict };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CourseDraftStorage = api;
})(globalThis);
