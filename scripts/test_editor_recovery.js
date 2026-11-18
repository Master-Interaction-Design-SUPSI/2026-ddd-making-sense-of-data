const assert = require('node:assert/strict');
const { test } = require('node:test');
const model = require('../editor/model.js');
const media = require('../static/media-table.js');
const contract = require('../static/contract.json');
const config = { slug: 'group-01', repository: 'course/group-01', branch: 'main', exclude: [] };

async function example() {
  const original = new Blob(['original']);
  const draft = new model.Draft(new Map([['text.md', { size: 0, sha256: await model.digest(new Blob([''])) }], ['visual.svg', { size: 1, sha256: 'b'.repeat(64) }],
    ['media/nested/photo.png', { blob: original, size: original.size, sha256: await model.digest(original) }]]), config, contract);
  draft.table = 'name;source;file\r\nPhoto;https://example.org/source;media/nested/photo.png\r\n';
  return draft;
}

test('remove and undo retain exact replacement bytes and original table source', async () => {
  const draft = await example();
  const replacement = new Blob(['replacement']);
  draft.attach('media/nested/photo.png', replacement, await model.digest(replacement));
  const table = draft.table;
  draft.removeAsset('media/nested/photo.png');
  assert.equal(draft.files().has('media/nested/photo.png'), false);
  draft.undoRemove('media/nested/photo.png');
  assert.equal(await draft.files().get('media/nested/photo.png').blob.text(), 'replacement');
  assert.equal(draft.table, table);
});

test('new assets can be removed and undone without planning a repository deletion', async () => {
  const draft = await example();
  const file = new Blob(['new']);
  draft.addAsset('media/new.png', file, await model.digest(file), { name: 'New', source: '' });
  draft.removeAsset('media/new.png');
  assert.equal((await draft.changes()).some(change => change.path === 'media/new.png'), false);
  draft.undoRemove('media/new.png');
  assert.equal(await draft.files().get('media/new.png').blob.text(), 'new');
  assert.equal(media.read(draft.table).byName.get('new').file, 'media/new.png');
});

test('rename updates case-insensitive media names while preserving literal examples and frontmatter', async () => {
  const draft = await example();
  draft.text = '---\r\ntitle: "[[Photo]]"\r\n---\r\n[[PHOTO]] and [[ Photo ]]\r\n`[[Photo]]` \\[[Photo]]\r\n\r\n```md\r\n[[Photo]]\r\n```\r\n\r\n> [[Photo]] and `[[Photo]]`\r\n\r\n- [[Photo]]\r\n\r\n        [[Photo]]\r\n';
  const expected = draft.text.replace('[[PHOTO]] and [[ Photo ]]', '[[Field photo]] and [[Field photo]]')
    .replace('> [[Photo]]', '> [[Field photo]]').replace('- [[Photo]]', '- [[Field photo]]');
  draft.renameAsset('Photo', 'Field photo');
  assert.equal(draft.text, expected);
  assert.equal(media.read(draft.table).byName.get('field photo').source, 'https://example.org/source');
  const text = draft.text, table = draft.table;
  assert.throws(() => draft.renameAsset('Field photo', 'bad|name'));
  assert.equal(draft.text, text); assert.equal(draft.table, table);
});

test('unreadable CSV blocks media mutations and survives an attempted upload', async () => {
  const draft = await example(); draft.table = 'name,source,file\n"unclosed';
  const file = new Blob(['new']);
  assert.throws(() => draft.addAsset('media/new.png', file, 'a'.repeat(64), { name: 'New', source: '' }), /quoted/);
  assert.throws(() => draft.removeAsset('media/nested/photo.png'), /quoted/);
  assert.equal(draft.table, 'name,source,file\n"unclosed');
  assert.equal(draft.updates.size, 0);
});

test('uploads suggest unique paths and require explicit replacement without changing metadata', async () => {
  const draft = await example();
  assert.equal(draft.suggestPath('Photo.PNG'), 'media/photo.png');
  const file = new Blob(['new']);
  draft.addAsset('media/photo.png', file, await model.digest(file), { name: 'Other', source: '' });
  assert.equal(draft.suggestPath('Photo.PNG'), 'media/photo-2.png');
  assert.equal(draft.suggestPath('Other.PNG'), 'media/other.png');
  assert.throws(() => draft.addAsset('media/nested/photo.png', file, 'a'.repeat(64), { name: 'Overwrite' }), /Replace/);
  const table = draft.table;
  draft.replaceAsset('media/nested/photo.png', file, await model.digest(file));
  assert.equal(draft.table, table);
});

test('used assets cannot be removed and undo does not overwrite newer table edits', async () => {
  const draft = await example(); draft.text = '[[PHOTO]]';
  assert.throws(() => draft.removeAsset('media/nested/photo.png'), /references/);
  draft.text = '';
  draft.removeAsset('media/nested/photo.png');
  const other = new Blob(['other']);
  draft.addAsset('media/other.png', other, await model.digest(other), { name: 'Other', source: '' });
  draft.undoRemove('media/nested/photo.png');
  assert.deepEqual(media.read(draft.table).rows.map(row => row.name), ['Photo', 'Other']);
});

const recovery = () => require('../editor/recovery.js');
const sessions = () => require('../editor/session.js');

test('recovery round-trip preserves nested binary bytes, invalid or null table, deletion undo and identity', async () => {
  const draft = await example();
  const file = new Blob([new Uint8Array([0, 255, 17, 128])], { type: 'image/png' });
  draft.replaceAsset('media/nested/photo.png', file, await model.digest(file));
  draft.removeAsset('media/nested/photo.png');
  const record = sessions().snapshot(draft, 'commit:inventory', ['media/missing.png']);
  const encoded = await recovery().encode(record, config, contract);
  const decoded = await recovery().decode(encoded, contract);
  assert.equal(decoded.group.repository, config.repository);
  assert.equal(decoded.baseId, 'commit:inventory');
  assert.deepEqual(decoded.missingPaths, ['media/missing.png']);
  assert.deepEqual(new Uint8Array(await decoded.assets[0].blob.arrayBuffer()), new Uint8Array([0, 255, 17, 128]));
  const restored = await sessions().restore(decoded, draft, 'commit:inventory');
  restored.draft.undoRemove('media/nested/photo.png');
  assert.equal(restored.draft.table, 'name;source;file\r\nPhoto;https://example.org/source;media/nested/photo.png\r\n');
  for (const table of [null, 'not valid CSV\n"']) {
    record.table = table;
    assert.equal((await recovery().decode(await recovery().encode(record, config, contract), contract)).table, table);
  }
});

test('legacy text-only and blob drafts remain exportable without inventing missing bytes or tables', async () => {
  for (const version of [1, 2]) {
    const record = { version, baseId: 'older', text: 'legacy', paths: ['media/deep/photo.png'],
      assets: version === 2 ? [{ path: 'media/deep/photo.png', size: 4, sha256: 'a'.repeat(64), blob: null }] : undefined,
      deletions: ['media/removed.png'] };
    const decoded = await recovery().decode(await recovery().encode(record, config, contract), contract);
    assert.equal(Object.hasOwn(decoded, 'table'), false);
    assert.deepEqual(decoded.missingPaths, ['media/deep/photo.png']);
    assert.deepEqual(decoded.deletions, ['media/removed.png']);
    assert.deepEqual(decoded.assets, []);
  }
});

test('bundle import rejects tampering, unsafe paths, excess bytes and unsupported versions', async () => {
  const draft = await example(), file = new Blob(['new']);
  draft.replaceAsset('media/nested/photo.png', file, await model.digest(file));
  const encoded = await recovery().encode(sessions().snapshot(draft, 'base', []), config, contract);
  for (const mutate of [
    bundle => bundle.version = 99,
    bundle => bundle.draft.assets[0].sha256 = '0'.repeat(64),
    bundle => bundle.draft.assets[0].path = '../escape.png',
    bundle => bundle.draft.assets.push({ ...bundle.draft.assets[0] }),
    bundle => bundle.draft.assets[0].size = contract.limits.media_bytes + 1,
    bundle => bundle.draft.missingPaths = ['../escape.png'],
  ]) {
    const bundle = JSON.parse(encoded); mutate(bundle);
    await assert.rejects(recovery().decode(JSON.stringify(bundle), contract));
  }
});

test('recovery gate blocks editing and persistence during comparison until explicit resolution', () => {
  const { RecoveryGate } = sessions();
  const gate = new RecoveryGate(true);
  assert.throws(() => gate.assertEditable(), /draft/);
  gate.compare(); assert.equal(gate.readOnly, true);
  assert.throws(() => gate.assertEditable(), /draft/);
  gate.resume(); gate.assertEditable();
  gate.requireRecovery();
  assert.equal(gate.useBase(false), false); assert.equal(gate.readOnly, true);
  assert.equal(gate.useBase(true), true); gate.assertEditable();
});

test('restore with another base or group preserves the active draft and saved record', async () => {
  const draft = await example(); draft.text = 'active';
  const record = sessions().snapshot(draft, 'old-base', []); record.text = 'saved';
  await assert.rejects(sessions().restore(record, draft, 'new-base'), /different base/);
  assert.equal(draft.text, 'active'); assert.equal(record.text, 'saved');
  record.baseId = 'new-base'; record.group.repository = 'other/repository';
  await assert.rejects(sessions().restore(record, draft, 'new-base'), /different group/);
  assert.equal(draft.text, 'active');
});

const { editorFixture } = require('./editor_test_fixture.js');

test('controller keeps unresolved drafts read-only during comparison and confirms discarding', async () => {
  const record = { version: 2, baseId: 'older', text: 'preserved draft', table: null, assets: [], revision: 5 };
  const { element, state, until } = await editorFixture({ record, confirm: false });
  assert.equal(element('text').disabled, true);
  assert.equal(element('attach').disabled, true);
  element('text').value = 'must not save'; element('text').listeners.input();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(state.writes, 0);
  element('compare-saved').listeners.click();
  await until(() => element('saved-text').textContent === 'preserved draft');
  assert.equal(element('text').disabled, true);
  element('discard').listeners.click();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(state.record.text, 'preserved draft'); assert.equal(state.writes, 0);
});

test('controller does not replace malformed CSV by adding a row or attachment', async () => {
  const table = 'name,source,file\n"unclosed';
  const { element, state } = await editorFixture({ table });
  assert.equal(element('add-row').disabled, true);
  assert.equal(element('attach').disabled, true);
  assert.equal(element('repair-source').value, table);
  element('add-row').listeners.click();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(state.writes, 0);
});

test('controller clears each successful upload and suggests a fresh unique destination', async () => {
  const { element, state, until } = await editorFixture();
  const first = new Blob(['first']); first.name = 'first.png';
  element('attachment').files = [first]; element('attachment').listeners.change();
  element('attach').listeners.click();
  await until(() => state.writes > 0);
  assert.equal(element('path').value, ''); assert.equal(element('attachment').value, '');
  const second = new Blob(['second']); second.name = 'second.png';
  element('attachment').files = [second]; element('attachment').listeners.change();
  await until(() => element('path').value === 'media/second.png');
});

test('explicitly using the loaded version restores its text before saving', async () => {
  const record = { version: 2, baseId: 'older', text: 'preserved draft', table: null, assets: [], revision: 5 };
  const { element, state, until } = await editorFixture({ record });
  element('text').value = 'read-only field must not become content';
  element('discard').listeners.click();
  await until(() => state.writes > 0);
  assert.equal(element('text').value, 'Draft text');
  assert.equal(state.record.text, 'Draft text');
  assert.equal(element('text').disabled, false);
});

test('resuming an imported bundle uses the current storage revision, not its exported revision', async () => {
  const first = await editorFixture();
  first.element('text').value = 'Saved elsewhere'; first.element('text').listeners.input();
  await first.until(() => first.state.writes > 0);
  const original = first.state.record;
  const encoded = await recovery().encode({ ...original, revision: 800 }, config, contract);
  const second = await editorFixture();
  second.element('recovery-import').files = [new Blob([encoded])];
  second.element('recovery-import').listeners.change();
  await second.until(() => second.element('text').disabled);
  assert.equal(second.state.writes, 0);
  second.element('restore').listeners.click();
  await second.until(() => second.state.writes > 0);
  assert.equal(second.state.record.revision, 1);
  assert.equal(second.element('text').value, 'Saved elsewhere');
});

test('renaming repeated names in Markdown tables updates each source range exactly once', async () => {
  const draft = await example();
  draft.text = '| [[PHOTO]] | [[PHOTO]] |\n| --- | --- |\n| [[PHOTO]] | `[[PHOTO]]` |\n';
  draft.renameAsset('Photo', 'Field');
  assert.equal(draft.text, '| [[Field]] | [[Field]] |\n| --- | --- |\n| [[Field]] | `[[PHOTO]]` |\n');
});

test('renames preserve link definitions and cover continued list paragraphs', async () => {
  const draft = await example();
  draft.text = '- first [[Photo]]\n  continuation [[PHOTO]]\n';
  draft.renameAsset('Photo', 'Longer name');
  assert.equal(draft.text, '- first [[Longer name]]\n  continuation [[Longer name]]\n');
  draft.text = '[link][x]\n\n[x]: https://a/[[Longer name]]\n[[Longer name]]\n';
  draft.renameAsset('Longer name', 'Photo');
  assert.equal(draft.text, '[link][x]\n\n[x]: https://a/[[Longer name]]\n[[Photo]]\n');
});

test('retained removal history stays exportable before accepting another upload', async () => {
  const bounded = { ...contract, limits: { ...contract.limits, group_bytes: 250, media_bytes: 120 } };
  const draft = new model.Draft(new Map([['text.md', { size: 0, sha256: 'a'.repeat(64) }],
    ['visual.svg', { size: 1, sha256: 'b'.repeat(64) }]]), config, bounded);
  const file = new Blob(['x'.repeat(100)]), hash = await model.digest(file);
  for (const name of ['one', 'two']) {
    draft.addAsset(`media/${name}.png`, file, hash, { name, source: '' });
    draft.removeAsset(`media/${name}.png`);
  }
  const before = sessions().snapshot(draft, 'base', []);
  assert.throws(() => draft.addAsset('media/three.png', file, hash, { name: 'three', source: '' }), /recovery limit/);
  assert.equal(draft.updates.size, 2);
  assert.equal(draft.table, before.table);
  await recovery().decode(await recovery().encode(sessions().snapshot(draft, 'base', []), config, bounded), bounded);
});

test('storage revision conflicts immediately lock controls without hiding current text', async () => {
  const { element, state, until } = await editorFixture();
  state.saveError = new (require('../editor/draft-store.js').DraftConflict)();
  element('text').value = 'Current text survives'; element('text').listeners.input();
  await until(() => element('save').textContent.startsWith('Another tab'));
  assert.equal(element('text').disabled, true);
  assert.equal(element('text').value, 'Current text survives');
  assert.equal(element('export-draft').disabled, false);
  assert.equal(state.writes, 0);
});

test('a duplicate provisional media name is rejected and can be corrected', async () => {
  const table = 'name,source,file\nPhoto,,media/photo.png\n';
  const { element, state, until } = await editorFixture({ table });
  element('add-row').listeners.click();
  await until(() => state.writes > 0);
  const event = { target: { dataset: { field: 'name' }, value: 'Photo', closest: () => ({ dataset: { index: '1' } }) } };
  element('media-rows').listeners.change(event);
  await new Promise(resolve => setImmediate(resolve));
  assert.doesNotMatch(state.record.table, /Photo,,media\/\n/);
  event.target.value = 'New'; element('media-rows').listeners.change(event);
  await until(() => state.record.table.includes('New,,media/'));
});

test('cancel replacement returns to addition mode and preserves the original asset', async () => {
  const { element, state, until } = await editorFixture();
  const first = new Blob(['first']); first.name = 'first.png';
  element('attachment').files = [first]; element('attachment').listeners.change(); element('attach').listeners.click();
  await until(() => state.writes > 0);
  const row = element('media-rows').children.find(row => row.dataset.path === 'media/first.png');
  const descendants = node => node.children.flatMap(child => child?.children ? [child, ...descendants(child)] : []);
  descendants(row).find(button => button.textContent === 'Replace file').listeners.click();
  assert.equal(element('path').disabled, true);
  assert.equal(element('cancel-replace').hidden, false);
  element('cancel-replace').listeners.click();
  assert.equal(element('path').disabled, false);
  assert.equal(element('path').value, '');
  const second = new Blob(['second']); second.name = 'second.png';
  element('attachment').files = [second]; element('attachment').listeners.change(); element('attach').listeners.click();
  await until(() => state.record.assets.length === 2);
  assert.equal(await state.record.assets.find(asset => asset.path === 'media/first.png').blob.text(), 'first');
});

test('saved drafts retain individual text, table and attachment downloads beside the bundle', async () => {
  const blob = new Blob(['saved bytes']);
  const record = { version: 2, baseId: 'older', text: 'preserved draft', table: 'invalid CSV',
    assets: [{ path: 'media/deep/photo.png', size: blob.size, sha256: await model.digest(blob), blob }], revision: 5 };
  const { element } = await editorFixture({ record });
  await element('saved-download').listeners.click();
  const labels = element('saved-files').children.flatMap(row => row.children.map(child => child.textContent));
  assert.ok(labels.includes('Download saved text'));
  assert.ok(labels.includes('Download saved media.csv'));
  assert.ok(labels.includes('Download saved attachment'));
});

test('names in nested link definitions remain literal when quoted or listed prose is renamed', () => {
  for (const source of ['> [link][x]\n>\n> [x]: https://a/[[Photo]]\n> [[Photo]]\n',
    '- [link][x]\n\n  [x]: https://a/[[Photo]]\n  [[Photo]]\n']) {
    const at = source.lastIndexOf('[[Photo]]');
    assert.equal(media.renameReferences(source, 'Photo', 'New'), source.slice(0, at) + '[[New]]' + source.slice(at + 9));
  }
});
