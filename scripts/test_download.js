const assert = require('node:assert/strict');
const { test } = require('node:test');
const { fetchBlob } = require('../editor/download.js');

test('bounded download preserves bytes and HTTP/stream failures', async () => {
  const file = await fetchBlob('https://example.org/file', 3, async (url, options) => {
    assert.equal(options.cache, 'no-store'); assert.equal(options.redirect, 'error');
    assert.ok(options.signal instanceof AbortSignal);
    return new Response('abc');
  });
  assert.equal(await file.text(), 'abc');
  await assert.rejects(fetchBlob('missing', 3, async () => new Response('', { status: 404 })), /Could not load missing/);
  await assert.rejects(fetchBlob('empty', 3, async () => ({ ok: true })), /cannot stream/);
});

test('oversize and interrupted streams cancel the reader and keep the original error', async () => {
  for (const interrupted of [false, true]) {
    let cancelled = false;
    const failure = new Error('interrupted');
    const response = { ok: true, body: { getReader: () => ({
      read: async () => { if (interrupted) throw failure; return { value: new Uint8Array(4), done: false }; },
      cancel: async () => { cancelled = true; },
    }) } };
    await assert.rejects(fetchBlob('file', 3, async () => response), interrupted ? error => error === failure : /byte limit/);
    assert.equal(cancelled, true);
  }
});

test('the thirty-second signal deadline rejects downloads as TimeoutError', async t => {
  t.mock.method(AbortSignal, 'timeout', milliseconds => {
    assert.equal(milliseconds, 30000);
    return AbortSignal.abort(new DOMException('Expired', 'TimeoutError'));
  });
  await assert.rejects(fetchBlob('slow', 3, async (url, options) => {
    options.signal.throwIfAborted();
    assert.fail('An expired deadline cannot fetch bytes');
  }), { name: 'TimeoutError' });
});
