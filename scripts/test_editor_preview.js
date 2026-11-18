const assert = require('node:assert/strict');
const { test } = require('node:test');
const { PreviewClient } = require('../editor/preview-client.js');
const protocol = require('../editor/protocol.js');
const contract = require('../static/contract.json');

function fixture() {
  const handlers = {}, timers = new Map(), messages = [], states = [];
  let timerId = 0;
  const sender = { postMessage: (message, origin) => messages.push({ message, origin }) };
  const frame = { contentWindow: sender, addEventListener: (name, fn) => { handlers[name] = fn; }, removeEventListener() {} };
  const host = { addEventListener: (name, fn) => { handlers[name] = fn; }, removeEventListener() {},
    setTimeout: fn => { timers.set(++timerId, fn); return timerId; }, clearTimeout: id => timers.delete(id) };
  const client = new PreviewClient({ frame, host, origin: 'https://site.test', contract, onState: state => states.push(state) });
  const reply = (message, patch = {}) => handlers.message({ source: sender, origin: 'https://site.test', data: { version: 1, ...message }, ...patch });
  return { client, reply, messages, states, handlers, timers };
}
const payload = () => ({ slug: 'group', text: 'Draft', table: null, paths: [], assets: [] });
const tick = () => new Promise(resolve => setImmediate(resolve));

test('only current validated acknowledgements mark the requested preview as current', async () => {
  const f = fixture();
  f.client.request(payload);
  f.reply({ type: 'ready' }); await tick();
  assert.equal(f.client.requestedRevision, 1);
  assert.equal(f.client.acknowledgedRevision, 0);
  for (const patch of [{ origin: 'https://evil.test' }, { source: {} }, { data: { type: 'ack', revision: 1, version: 9 } }]) {
    f.reply({ type: 'ack', revision: 1 }, patch);
  }
  f.reply({ type: 'ack', revision: '1' }); f.reply({ type: 'ack', revision: 2 });
  assert.equal(f.client.acknowledgedRevision, 0);
  f.reply({ type: 'ack', revision: 1 });
  assert.equal(f.states.at(-1).status, 'current');
  f.client.request(payload); await tick();
  assert.equal(f.states.at(-1).status, 'stale');
  f.reply({ type: 'ack', revision: 1 });
  assert.equal(f.client.acknowledgedRevision, 1);
  f.reply({ type: 'ack', revision: 2 });
  assert.equal(f.client.acknowledgedRevision, 2);
  assert.ok(f.messages.every(entry => entry.origin === 'https://site.test'));
  f.client.dispose();
});

test('superseded asynchronous payloads never arrive after a newer request', async () => {
  const f = fixture(); f.reply({ type: 'ready' });
  let release;
  f.client.request(() => new Promise(resolve => { release = resolve; }));
  f.client.request(() => ({ ...payload(), text: 'Newest' })); await tick();
  release(payload()); await tick();
  const renders = f.messages.filter(entry => entry.message.type === 'render');
  assert.equal(renders.length, 1); assert.equal(renders[0].message.text, 'Newest');
  f.client.dispose();
});

test('errors, missing replies, retry and frame reload cannot reuse acknowledged assets', async () => {
  const f = fixture(); f.client.request(payload); f.reply({ type: 'ready' }); await tick();
  f.reply({ type: 'error', revision: 1, message: 'Render failed' });
  assert.equal(f.states.at(-1).status, 'unavailable');
  f.client.retry(); f.reply({ type: 'ready' }); await tick();
  assert.equal(f.messages.at(-1).message.revision, 2);
  f.reply({ type: 'ack', revision: 2 });
  f.handlers.load(); f.reply({ type: 'ready' }); await tick();
  assert.equal(f.messages.at(-1).message.revision, 3);
  [...f.timers.values()].at(-1)();
  assert.equal(f.states.at(-1).status, 'unavailable');
  f.client.dispose();
  const count = f.messages.length;
  f.reply({ type: 'ready' }); f.client.request(payload); await tick();
  assert.equal(f.messages.length, count);
});

test('reply schema rejects malformed error and acknowledgement messages', () => {
  for (const value of [null, [], { version: 1, type: 'ack', revision: 0 }, { version: 1, type: 'error', revision: 1, message: {} }]) {
    assert.equal(protocol.validReply(value), false);
  }
  assert.equal(protocol.validReply({ version: 1, type: 'error', revision: 1, message: 'Failed' }), true);
});

test('rapid edits immediately mark preview stale and coalesce expensive payload builds', async () => {
  const f = fixture(); f.reply({ type: 'ready' });
  let builds = 0;
  f.client.request(() => { builds++; return payload(); }, 150);
  f.client.request(() => { builds++; return { ...payload(), text: 'Latest' }; }, 150);
  assert.equal(builds, 0); assert.equal(f.states.at(-1).status, 'stale');
  [...f.timers.values()].at(-1)(); await tick();
  assert.equal(builds, 1); assert.equal(f.messages.at(-1).message.text, 'Latest');
  f.client.dispose();
});

test('a hidden preview suspends playback and becoming visible requests a fresh render', async () => {
  const f = fixture(); f.client.setVisible(false);
  f.client.request(payload); f.reply({ type: 'ready' }); await tick();
  assert.ok(f.messages.some(entry => entry.message.type === 'visibility' && entry.message.visible === false));
  f.client.setVisible(true); await tick();
  assert.equal(f.messages.at(-1).message.revision, 2);
  assert.equal(f.client.requestedRevision, 2);
  f.client.dispose();
});

test('explicit retry restarts a frame whose bootstrap never installed its listener', () => {
  const f = fixture();
  let reloads = 0;
  Object.defineProperty(f.client.frame, 'src', { get: () => 'preview.html', set: () => { reloads++; } });
  f.client.retry(); assert.equal(reloads, 1);
  f.handlers.load(); assert.equal(reloads, 1);
  f.client.dispose();
});

test('iframe endpoint checks sender, disposes replaced URLs and retains same-group scroll', async () => {
  const vm = require('node:vm'), fs = require('node:fs');
  const handlers = {}, replies = [], revoked = [], renders = [];
  let urls = 0, disposals = 0;
  const parent = { postMessage: message => replies.push(message) };
  const window = { scrollX: 0, scrollY: 0, addEventListener: (type, fn) => { handlers[type] = fn; },
    scrollTo(x, y) { this.scrollX = x; this.scrollY = y; } };
  const context = vm.createContext({ window, parent, location: { origin: 'https://site.test' }, Blob,
    fetch: async () => Response.json(contract), CoursePreviewProtocol: protocol,
    URL: { createObjectURL: () => `blob:asset-${++urls}`, revokeObjectURL: url => revoked.push(url) },
    document: { getElementById: () => ({ replaceChildren() {} }) },
    CourseSite: { dispose() { disposals++; }, async start(options) { renders.push(await options.loadEntries()); window.scrollY = 0; } },
  });
  await vm.runInContext(fs.readFileSync(require.resolve('../editor/preview.js'), 'utf8'), context);
  const message = { ...payload(), version: 1, type: 'render', revision: 1,
    paths: ['visual.svg'], assets: [{ path: 'visual.svg', bytes: new ArrayBuffer(1) }] };
  const send = (data, patch = {}) => handlers.message({ data, source: parent, origin: 'https://site.test', ...patch });
  await send(message, { origin: 'https://evil.test' }); await send(message, { source: {} });
  assert.equal(renders.length, 0);
  await send(message); assert.equal(replies.at(-1).type, 'ack');
  window.scrollY = 123;
  await send({ ...message, revision: 2 });
  assert.equal(window.scrollY, 123); assert.deepEqual(revoked, ['blob:asset-1']);
  const before = disposals;
  await send({ version: 1, type: 'visibility', visible: 'false' }); assert.equal(disposals, before);
  await send({ version: 1, type: 'visibility', visible: false }); assert.equal(disposals, before + 1);
  await send({ ...message, revision: 3, slug: 'another-group' }); assert.equal(window.scrollY, 0);
  handlers.pagehide(); assert.deepEqual(revoked, ['blob:asset-1', 'blob:asset-2', 'blob:asset-3']);
});
