const assert = require('node:assert/strict');
const { test } = require('node:test');
const document = require('../editor/document.js');

test('metadata and body edits retain BOM, unknown frontmatter, spacing and CRLF', () => {
  const source = '\ufeff---\r\nTitle :  Old title  \r\ncustom: keep: this\r\nauthors: Ada, Luca\r\n---\r\n\r\nBody\r\n';
  const projected = document.project(source);
  assert.equal(projected.safe, true);
  assert.equal(projected.title, 'Old title');
  assert.equal(projected.authors, 'Ada, Luca');
  assert.equal(document.update(source, { title: 'Old title', body: '\nBody\n' }), source);
  assert.equal(document.update(source, { title: 'New', body: '\nRevised\n' }),
    '\ufeff---\r\nTitle :  New  \r\ncustom: keep: this\r\nauthors: Ada, Luca\r\n---\r\n\r\nRevised\r\n');
});

test('missing fields are added only when edited and plain Markdown round trips', () => {
  assert.equal(document.update('Body without newline', { body: 'Body without newline', title: '' }), 'Body without newline');
  assert.equal(document.update('Body', { title: 'Title', authors: 'Ada' }), '---\ntitle: Title\nauthors: Ada\n---\nBody');
  assert.equal(document.update('---\nunknown: yes\n---\nBody', { authors: 'Ada' }), '---\nunknown: yes\nauthors: Ada\n---\nBody');
  assert.equal(document.update('---\ntitle: Hi\n---', { body: 'New body' }), '---\ntitle: Hi\n---\nNew body');
  assert.equal(document.update('\ufeffBody', { body: 'Revised', title: 'Title' }), '\ufeff---\ntitle: Title\n---\nRevised');
  assert.equal(document.update('\ufeffBody', { body: 'Revised' }), '\ufeffRevised');
});

test('unsafe projection preserves the entire source and requires raw editing', () => {
  for (const source of ['---\ntitle: A\nTITLE: B\n---\nBody', '---\ntitle: |\n  multiline\n---\nBody',
    '---\ntitle: A\n  continuation\n---\nBody', '---\ntitle: unfinished', '---\r\ntitle: Mixed\n---\r\nBody', '---\n---\nBody']) {
    assert.equal(document.project(source).safe, false, source);
    assert.throws(() => document.update(source, { title: 'Replace' }), /full text.md/);
  }
  assert.throws(() => document.update('Body', { title: 'Two\nlines' }), /single line/);
});
