const assert = require('node:assert/strict');
const { test } = require('node:test');
const { plan } = require('../editor/handoff.js');
const config = { repository: 'course/group-01', branch: 'main', slug: 'group-01' };
const input = changes => ({ changes, files: new Map([['media/deep/a.png', { blob: new Blob(['a']) }]]), text: 'Draft', table: 'name,source,file\n', config, siteRoot: 'https://site.test/course/' });

test('handoff orders review, uploads, table, text, deletions and final verification', () => {
  const steps = plan(input([{ path: 'text.md', operation: 'replace' }, { path: 'media/deep/a.png', operation: 'add' },
    { path: 'media.csv', operation: 'add' }, { path: 'media/old.png', operation: 'delete' }]));
  assert.deepEqual(steps.map(step => step.actions[0].kind), ['compare', 'download', 'copy', 'copy', 'link', 'link']);
  assert.equal(steps[1].actions[0].name, 'a.png');
  assert.equal(steps[1].actions[1].url, 'https://github.com/course/group-01/upload/main/media/deep');
  assert.equal(new URL(steps[2].actions[1].url).searchParams.get('value'), 'name,source,file\n');
  assert.equal(steps[3].actions[0].text, 'Draft');
  assert.equal(steps.at(-1).actions[0].url, 'https://site.test/course/#group-01');
});

test('handoff preserves replacement instructions, URL encoding', () => {
  const data = input([{ path: 'media/deep/a.png', operation: 'replace' }]);
  data.config = { ...config, branch: 'teaching/draft' };
  const steps = plan(data);
  assert.match(steps[1].text, /replaces the old file/);
  assert.match(steps[1].actions[1].url, /teaching%2Fdraft\/media\/deep$/);
});

test('deleting an optional table produces a delete step without trying to copy absent text', () => {
  const data = input([{ path: 'media.csv', operation: 'delete' }]); data.table = null;
  const steps = plan(data);
  assert.equal(steps.length, 3); assert.match(steps[1].text, /^Delete media.csv/);
});
