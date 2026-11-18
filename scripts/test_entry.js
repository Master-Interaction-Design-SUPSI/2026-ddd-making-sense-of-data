const assert = require('node:assert/strict');
const { test } = require('node:test');
const entry = require('../static/entry.js');

test('entry metadata is derived without mutating source or inventory', () => {
  const source = Object.freeze({ folder: 'group-02', text: '---\ntitle: Group 02: Study\nauthors: G-02 - Ada\n---\nBody', files: new Set(['text.md']) });
  const described = entry.describe(source);
  assert.equal(described.title, 'Study');
  assert.equal(described.authors, 'Ada');
  assert.equal(described.body, 'Body');
  assert.equal(source.title, undefined);
  assert.equal(described.files, source.files);
});

test('resource resolution is explicit and isolated between entries/previews', () => {
  const base = 'https://site.test/course/';
  const owned = (path, kind, folder) => path === 'media/a.svg' && folder === 'group-02' ? 'blob:owned' : false;
  assert.equal(entry.safeUrl('media/a.svg#detail', 'group-02', 'media', owned, base), 'blob:owned#detail');
  assert.equal(entry.safeUrl('media/a.svg', 'group-02', 'media', () => false, base), null);
  assert.equal(entry.safeUrl('media/a.svg', 'group-02', 'media', null, base), 'group-02/media/a.svg');
  for (const path of ['../a.svg', '%zz', 'media/%2e%2e/a.svg', 'blob:unowned', 'data:image/svg+xml,bad']) {
    assert.equal(entry.safeUrl(path, 'group-02', 'media', owned, base), null);
  }
  assert.equal(entry.safeUrl('mailto:a@example.org', 'group-02', 'link', owned, base), 'mailto:a@example.org');
  assert.equal(entry.safeUrl('mailto:a@example.org', 'group-02', 'media', owned, base), null);
});
