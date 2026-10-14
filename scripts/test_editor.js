const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const { Draft, bytes, digest, select, publicationRequest } = require('../editor/model.js');
const { validateRender } = require('../editor/protocol.js');
const references = require('../static/references.js');
const contract = require('../static/contract.json');
const config = { exclude: ['media/excluded.mov'], require_visual: false };

test('publication requests prefill only the registered group on the fixed course form', () => {
  const url = new URL(publicationRequest('Course/site', 'city-water'));
  assert.equal(url.origin, 'https://github.com');
  assert.equal(url.pathname, '/Course/site/issues/new');
  assert.equal(url.searchParams.get('template'), 'publish.yml');
  assert.equal(url.searchParams.get('group'), 'city-water');
  assert.equal(url.searchParams.get('labels'), 'publication-request');
  for (const [repository, slug] of [['https://evil.test/course', 'city-water'], ['Course/site', '../city-water'],
    ['Course/site', 'city-water&group=other'], ['Course/site.git', 'city-water']]) {
    assert.throws(() => publicationRequest(repository, slug), /identity/);
  }
});

test('UTF-8 and exact admission boundaries include unreferenced files', () => {
  assert.equal(bytes('è✶'), 5);
  const files = new Map([['text.md', { size: 1 }], ['media/first.mp4', { size: 20971520 }], ['media/second.mp4', { size: 20971520 }], ['media/third.mp4', { size: 10485759 }]]);
  assert.equal(select(files, contract, false).total, contract.limits.group_bytes);
  files.get('media/third.mp4').size++;
  assert.throws(() => select(files, contract, false), /50 MiB/);
});

test('unloaded base files survive text edits; deletion is explicit', async () => {
  const text = new Blob(['Original']);
  const base = new Map([['text.md', { size: text.size, sha256: await digest(text) }], ['media/orphan.png', { size: 30, sha256: 'a'.repeat(64) }]]);
  const draft = new Draft(base, config, contract);
  draft.text = 'Revised';
  assert.equal(draft.validate().total, 37);
  assert.deepEqual(draft.validate().orphans, ['media/orphan.png']);
  assert.deepEqual(await draft.changes(), [{ operation: 'replace', path: 'text.md' }]);
  draft.remove('media/orphan.png');
  assert.deepEqual(await draft.changes(), [{ operation: 'delete', path: 'media/orphan.png' }, { operation: 'replace', path: 'text.md' }]);
});

test('content hashes distinguish unchanged attachment and same-name replacement', async () => {
  const first = new Blob(['first']);
  const draft = new Draft(new Map([['text.md', { size: 0, sha256: await digest(new Blob([''])) }], ['media/photo.png', { size: 5, sha256: await digest(first) }]]), config, contract);
  draft.attach('media/photo.png', first, await digest(first));
  assert.deepEqual(await draft.changes(), []);
  const second = new Blob(['second']);
  draft.attach('media/photo.png', second, await digest(second));
  assert.deepEqual(await draft.changes(), [{ operation: 'replace', path: 'media/photo.png' }]);
  assert.throws(() => draft.attach('media/excluded.mov', second, 'x'), /excluded/);
  assert.throws(() => draft.attach('code.js', second, 'x'), /unsupported/);
});

test('partial inventories cannot infer deletions', () => {
  const draft = new Draft(new Map(), config, contract);
  draft.complete = false;
  assert.throws(() => draft.remove('media/unknown.png'), /partial/);
});

test('shared references cover frontmatter, annotations, Markdown and encoded paths', () => {
  const text = '---\ntitle: [ignored](missing.png)\n---\n[[detail|media/detail.svg]] [photo](media/ph%6fto.png?x=1#part)';
  assert.deepEqual(references.validate(text, ['media/detail.svg', 'media/photo.png']), ['media/photo.png', 'media/detail.svg']);
  for (const target of ['%2e%2e/secret.svg', 'media/%2fsecret.png', 'media/%00secret.png', '//host/path.png', 'blob:arbitrary', 'data:image/png,xx', 'javascript:alert(1)', '%zz']) {
    assert.throws(() => references.localPath(target));
  }
  assert.throws(() => references.validate('![missing](media/missing.png)', []), /imported file/);
});

test('preview schema rejects unsafe paths, payloads, duplicate assets and byte excess', () => {
  const valid = { version: 1, type: 'render', slug: 'group', revision: 1, text: 'Draft', paths: ['media/pic.png'], assets: [{ path: 'media/pic.png', bytes: new ArrayBuffer(1) }] };
  validateRender(valid, contract);
  for (const patch of [{ slug: '../group' }, { revision: NaN }, { version: 99 }, { text: 'x'.repeat(contract.limits.text_bytes + 1) }, { paths: ['../pic.png'] }, { assets: [{ path: 'media/pic.png', bytes: 'not bytes' }] }, { assets: [valid.assets[0], valid.assets[0]] }]) {
    assert.throws(() => validateRender({ ...valid, ...patch }, contract));
  }
});

test('renderer import has no automatic fetching or start', () => {
  const vm = require('node:vm');
  let calls = 0;
  const context = vm.createContext({ fetch() { calls++; }, URL, document: {} });
  vm.runInContext(fs.readFileSync(require.resolve('../static/main.js'), 'utf8'), context);
  assert.equal(calls, 0);
  assert.equal(typeof context.CourseSite.start, 'function');
});

test('upload sequence counts replacements and obsolete files before deletion', () => {
  const { validateUploads } = require('../editor/model.js');
  const base = new Map([['text.md', { size: 1 }], ['media/replacement.mp4', { size: 2 * 1048576 }], ['media/obsolete.mp4', { size: 20 * 1048576 }], ['media/kept.mp4', { size: 20 * 1048576 }]]);
  const final = new Map(base);
  final.set('media/replacement.mp4', { size: 20 * 1048576 });
  final.delete('media/obsolete.mp4');
  assert.doesNotThrow(() => select(final, contract, false));
  assert.throws(() => validateUploads(base, [{ operation: 'replace', path: 'media/replacement.mp4' }, { operation: 'delete', path: 'media/obsolete.mp4' }], final, contract, false), /temporarily/);
  const countBase = new Map([['text.md', { size: 0 }], ...Array.from({ length: 99 }, (_, i) => [`media/${i}.png`, { size: 1 }])]);
  const countFinal = new Map(countBase);
  countFinal.delete('media/0.png'); countFinal.set('media/new.png', { size: 1 });
  assert.throws(() => validateUploads(countBase, [{ operation: 'add', path: 'media/new.png' }], countFinal, contract, false), /100 files/);
});

test('text commit is checked before obsolete-file deletion', () => {
  const { validateUploads } = require('../editor/model.js');
  const base = new Map([['text.md', { size: 1 }], ['media/first.mp4', { size: 20 * 1048576 }], ['media/second.mp4', { size: 20 * 1048576 }], ['media/obsolete.mp4', { size: 10 * 1048576 - 2 }]]);
  const final = new Map(base);
  final.set('text.md', { size: 101 }); final.delete('media/obsolete.mp4');
  assert.doesNotThrow(() => select(final, contract, false));
  assert.throws(() => validateUploads(base, [{ operation: 'replace', path: 'text.md' }, { operation: 'delete', path: 'media/obsolete.mp4' }], final, contract, false), /Committing text/);
});

test('browser selection matches the authoritative importer on the shared parity corpus', () => {
  const { spawnSync } = require('node:child_process');
  const path = require('node:path');
  const cases = [
    { files: { 'text.md': 0 }, visual: false },
    { files: { 'text.md': 0 }, visual: true },
    { files: { 'text.md': 5, 'visual.svg': 1, 'visual.detail.png': 2 } },
    { files: { 'text.md': 5, 'visual.svg': 1, 'media/unreferenced.png': 2 } },
    { files: { 'text.md': 262144, 'visual.svg': 5242880 } },
    { files: { 'text.md': 262145, 'visual.svg': 1 } },
    { files: { 'text.md': 1, 'visual.svg': 5242881 } },
    { files: { 'text.md': 1, 'visual.svg': 1, 'media/clip.mp4': 20971521 } },
    { files: { 'text.md': 1, 'media/a.mp4': 20971520, 'media/b.mp4': 20971520, 'media/c.mp4': 10485759 }, visual: false },
    { files: { 'text.md': 1, 'media/a.mp4': 20971520, 'media/b.mp4': 20971520, 'media/c.mp4': 10485760 }, visual: false },
    { files: { 'text.md': 1, 'visual.svg': 1, 'media/photo.png': true } },
    { files: { 'text.md': 1, 'visual.svg': 1, 'media/Photo.png': 1 } },
    { files: { 'text.md': 1, 'visual.svg': 1, 'media/../photo.png': 1 } },
    { files: { 'text.md': 1, 'visual.svg': 1, 'media/photo.png': 1, 'media/photo.png/child.png': 1 } },
    { files: { 'text.md': 1, 'media/excluded.mov': 20971521 }, exclude: ['media/excluded.mov'], visual: false },
  ];
  const python = spawnSync('python3', ['-c', `
import json, sys
sys.path.insert(0, sys.argv[1])
from import_groups import validate_tree
from github_content import ImportFailure
results = []
for case in json.load(sys.stdin):
    tree = {'truncated': False, 'tree': [{'path': name, 'size': size, 'type': 'blob', 'mode': '100644', 'sha': 'a' * 40} for name, size in case['files'].items()]}
    try:
        results.append(sorted(validate_tree(tree, case.get('exclude', []), require_visual=case.get('visual', True))))
    except (ValueError, ImportFailure):
        results.append(None)
print(json.dumps(results))
`, path.resolve(__dirname)], { input: JSON.stringify(cases), encoding: 'utf8' });
  assert.equal(python.status, 0, python.stderr);
  const expected = JSON.parse(python.stdout);
  for (let i = 0; i < cases.length; i++) {
    const input = cases[i];
    const files = new Map(Object.entries(input.files).filter(([name]) => !(input.exclude || []).includes(name)).map(([name, size]) => [name, { size }]));
    let selected = null;
    try { selected = [...select(files, contract, input.visual !== false).files.keys()].sort(); } catch {}
    assert.deepEqual(selected, expected[i], JSON.stringify(input));
  }
});
