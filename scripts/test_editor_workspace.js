const assert = require('node:assert/strict');
const { test } = require('node:test');
const { editorFixture } = require('./editor_test_fixture.js');

test('structured writing saves metadata and body without rewriting unknown frontmatter', async () => {
  const { element, state, until } = await editorFixture({ source: '---\r\ntitle: Original\r\ncustom: keep\r\n---\r\nBody\r\n' });
  assert.equal(element('title').value, 'Original');
  element('title').value = 'Revised'; element('title').listeners.input();
  await until(() => state.record?.text.includes('Revised'));
  assert.equal(state.record.text, '---\r\ntitle: Revised\r\ncustom: keep\r\n---\r\nBody\r\n');
  element('body').value = 'New body\n'; element('body').listeners.input();
  await until(() => state.record.text.includes('New body'));
  assert.equal(state.record.text, '---\r\ntitle: Revised\r\ncustom: keep\r\n---\r\nNew body\r\n');
});

test('ambiguous source keeps raw editing available while structured fields are locked', async () => {
  const source = '---\ntitle: |\n  Keep this\n---\nBody';
  const { element } = await editorFixture({ source });
  assert.equal(element('body').disabled, true); assert.equal(element('text').disabled, false);
  assert.equal(element('text').value, source); assert.equal(element('raw-document').open, true);
});

test('dedicated main visual replacement preserves exact CSV and creates no media row', async () => {
  const table = '\ufeffname;source;file\r\nPhoto;https://example.org;media/photo.png\r\n';
  const { element, state, until } = await editorFixture({ table });
  const blob = new Blob(['<svg/>'], { type: 'image/svg+xml' });
  Object.defineProperty(blob, 'name', { value: 'export.svg' });
  element('visual-file').files = [blob]; element('replace-visual').listeners.click();
  await until(() => state.record?.assets.some(asset => asset.path === 'visual.svg'));
  assert.equal(state.record.table, table);
  assert.equal(element('visual-file').value, '');
});

test('media insertion offers only actual inventory names and never inserts a placeholder', async () => {
  const { element, state, until } = await editorFixture();
  element('insert-media').listeners.click();
  assert.match(element('insert-error').textContent, /Add media/);
  assert.equal(state.writes, 0);
  const blob = new Blob(['image']); Object.defineProperty(blob, 'name', { value: 'photo.png' });
  element('attachment').files = [blob]; element('attachment').listeners.change(); element('attach').listeners.click();
  await until(() => state.record?.assets.length === 1);
  element('insert-media').listeners.click();
  assert.equal(element('media-choice').children[0].textContent, 'photo');
  assert.equal(element('media-rows').children.length, 1);
  assert.equal(element('files').children.length, 0);
  element('body').selectionStart = 6; element('body').selectionEnd = 10;
  element('show-preview').listeners.click(); element('show-write').listeners.click();
  element('insert-choice').listeners.click();
  await until(() => state.record.text.includes('[[photo]]'));
  assert.equal(state.record.text, 'Draft [[photo]]');
});

test('visual errors are reported beside the replacement action', async () => {
  const { element, until } = await editorFixture();
  element('visual-file').files = []; element('replace-visual').listeners.click();
  await until(() => element('visual-error').textContent?.includes('Choose'));
  assert.match(element('visual-error').textContent, /Choose/);
  assert.doesNotMatch(element('status').textContent, /Choose/);
});

test('failed review focuses a linked summary without losing invalid CSV', async () => {
  const { element, state, until } = await editorFixture({ table: 'broken' });
  element('check').listeners.click();
  await until(() => element('review-errors').children.length);
  assert.equal(element('error-summary').hidden, false);
  assert.equal(element('review-errors').children[0].children[0].href, '#repair-source');
  assert.equal(state.writes, 0); assert.equal(element('repair-source').value, 'broken');
});

test('pasted frontmatter reprojects fields before the next structured body edit', async () => {
  const { element, state, until } = await editorFixture();
  element('body').value = '---\ntitle: Pasted\n---\nBody'; element('body').listeners.input();
  await until(() => state.record?.text.includes('Pasted'));
  assert.equal(element('title').value, 'Pasted'); assert.equal(element('body').value, 'Body');
  element('body').value += '!'; element('body').listeners.input();
  await until(() => state.record.text.endsWith('!'));
  assert.equal(state.record.text, '---\ntitle: Pasted\n---\nBody!');
});

test('a structured edit that introduces ambiguous metadata falls back to intact raw source', async () => {
  const { element, state, until } = await editorFixture();
  const source = '---\ntitle: A\ntitle: B\n---\nBody';
  element('body').value = source; element('body').listeners.input();
  await until(() => state.record);
  assert.equal(element('body').disabled, true); assert.equal(element('raw-document').open, true);
  assert.equal(element('text').value, source); assert.match(element('document-status').textContent, /full text.md/);
});

test('existing main visual media metadata remains editable without creating an automatic row', async () => {
  const { element, state, until } = await editorFixture({ source: '[[cover]]', table: 'name,source,file\ncover,https://example.org,visual.svg\n' });
  assert.equal(element('media-rows').children.length, 0); assert.equal(element('visual-metadata').children.length, 1);
  element('visual-metadata').listeners.change({ target: { dataset: { field: 'name' }, value: 'Front cover',
    closest: () => ({ dataset: { index: 0 } }) } });
  await until(() => state.record);
  assert.equal(state.record.text, '[[Front cover]]');
  assert.match(state.record.table, /Front cover,https:\/\/example.org,visual.svg/);
});

test('reprojection retains intermediate spaces while typing a multiword title', async () => {
  const { element, state, until } = await editorFixture();
  element('title').value = 'New'; element('title').listeners.input();
  await until(() => state.writes === 1);
  element('title').value += ' '; element('title').listeners.input();
  await until(() => state.writes === 2);
  assert.equal(element('title').value, 'New ');
  element('title').value += 'title'; element('title').listeners.input();
  await until(() => state.writes === 3);
  assert.equal(state.record.text, '---\ntitle: New title\n---\nDraft text');
});

test('media previews keep one element per unchanged file and release replaced object URLs', () => {
  const { create } = require('../editor/media-preview.js');
  const made = [], revoked = [];
  const previews = create({ createElement: tag => ({ tag }) },
    { createObjectURL: blob => { made.push(blob); return `blob:${made.length}`; }, revokeObjectURL: url => revoked.push(url) });
  const photo = new Blob(['a']), published = { url: new URL('https://site.test/g/media/clip.mp4') };
  const image = previews.element('media/photo.png', { blob: photo });
  assert.deepEqual([image.tag, image.src, image.alt], ['img', 'blob:1', '']);
  assert.equal(previews.element('media/photo.png', { blob: photo }), image);
  const clip = previews.element('media/clip.mp4', published);
  assert.deepEqual([clip.tag, clip.src, clip.controls], ['video', 'https://site.test/g/media/clip.mp4', true]);
  assert.equal(previews.element('media/sound.mp3', { blob: new Blob(['b']) }).tag, 'audio');
  assert.notEqual(previews.element('media/photo.png', { blob: new Blob(['c']) }), image);
  assert.deepEqual(revoked, ['blob:1']);
  previews.retain(new Set(['media/clip.mp4']));
  assert.deepEqual(revoked.toSorted(), ['blob:1', 'blob:2', 'blob:3']);
  assert.equal(made.length, 3);
});
