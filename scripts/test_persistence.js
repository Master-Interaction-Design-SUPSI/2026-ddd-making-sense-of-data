const assert = require('node:assert/strict');
const { test } = require('node:test');
const { Persistence } = require('../editor/persistence.js');

function fixture({ record = null, legacy = null, denied = false } = {}) {
  const writes = [], notices = [];
  let current = record;
  const local = { getItem: () => legacy, setItem: (key, value) => { legacy = value; }, removeItem: () => { legacy = null; } };
  const store = {
    read: async () => { if (denied) throw new Error('denied'); return current; },
    save: async (key, payload, revision) => { writes.push({ payload, revision }); current = { ...payload, revision: revision + 1 }; return revision + 1; },
    discard: async (key, revision) => { current = { discarded: true, revision: revision + 1 }; return revision + 1; },
  };
  const persistence = new Persistence({ store, local: () => local, notify: message => notices.push(message) });
  return { persistence, store, writes, notices, legacy: () => legacy };
}
const payload = text => ({ text, baseId: 'base', table: null, assets: [], missingPaths: [], deletions: [] });

test('queued saves serialize revisions and suppress superseded or newly locked work', async () => {
  const f = fixture(); const context = await f.persistence.open('key');
  f.persistence.save(context, payload('old'));
  await f.persistence.save(context, payload('new'));
  assert.deepEqual(f.writes.map(write => write.payload.text), ['new']);
  assert.equal(context.revision, 1); assert.equal(context.unsaved, false);
  f.persistence.save(context, payload('blocked')); context.pending = true;
  await f.persistence.idle();
  assert.equal(f.writes.length, 1); assert.equal(context.unsaved, true);
});

test('failed save preserves the last complete record and reports conflict', async () => {
  const f = fixture({ record: { ...payload('previous'), revision: 4 } });
  const context = await f.persistence.open('key'); context.pending = false;
  const events = [];
  f.store.save = async () => { throw Object.assign(new Error('another tab'), { name: 'DraftConflict' }); };
  await f.persistence.save(context, payload('new'), state => events.push(state));
  assert.equal((await f.persistence.read(context)).text, 'previous');
  assert.equal(context.conflict, true); assert.equal(context.unsaved, true);
  assert.equal(events.at(-1).status, 'conflict');
});

test('legacy text is retired only after a successful durable save', async () => {
  const legacy = JSON.stringify({ version: 1, text: 'old' });
  const f = fixture({ legacy }); const context = await f.persistence.open('key');
  const saved = await f.persistence.read(context);
  f.persistence.resume(context, saved, { revision: 0 });
  f.store.save = async () => { throw new Error('quota'); };
  await f.persistence.save(context, payload('new'));
  assert.equal(f.legacy(), legacy);
  f.store.save = async () => 1;
  await f.persistence.save(context, payload('new'));
  assert.equal(f.legacy(), null);
});

test('local fallback refuses to overwrite a concurrently changed text backup', async () => {
  const f = fixture({ denied: true }); const context = await f.persistence.open('key');
  assert.equal(context.backend, 'local');
  await f.persistence.save(context, payload('saved'));
  assert.equal(JSON.parse(f.legacy()).text, 'saved');
  context.legacy = 'stale';
  await f.persistence.save(context, payload('overwrite'));
  assert.equal(context.conflict, true); assert.equal(JSON.parse(f.legacy()).text, 'saved');
});

test('discard writes a durable tombstone and clears the legacy copy', async () => {
  const f = fixture({ record: { ...payload('previous'), revision: 4 }, legacy: 'old' });
  const context = await f.persistence.open('key');
  await f.persistence.discard(context);
  assert.equal(context.revision, 5); assert.equal(context.pending, false); assert.equal(f.legacy(), null);
  assert.equal(await f.persistence.read(context), null); assert.equal(f.notices.length, 1);
});
