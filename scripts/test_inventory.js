const assert = require('node:assert/strict');
const { test } = require('node:test');
const { describe } = require('../editor/inventory.js');

test('inventory combines named/direct uses, aliases and missing files without mutating rows', () => {
  const rows = [{ name: 'Photo', source: 'https://example.org', file: 'media/a.png' },
    { name: 'Alias', source: '', file: 'media/a.png' }, { name: 'Missing', source: '', file: 'media/missing.png' },
    { name: 'Cover', source: '', file: 'visual.svg' }];
  const files = new Map([['text.md', {}], ['visual.svg', {}], ['media.csv', {}], ['media/a.png', { size: 3 }], ['media/orphan.png', { size: 1 }]]);
  const inventory = describe('[[PHOTO]] [[Photo]] [[Alias]] ![a](media/a.png) `[[Photo]]` [[Cover]]', rows, files);
  assert.deepEqual(inventory.items.map(item => [item.path, item.uses]), [['media/a.png', 4], ['media/orphan.png', 0], ['media/missing.png', 0]]);
  assert.equal(inventory.items[0].entries.length, 2);
  assert.equal(inventory.items[2].file, null);
  assert.equal(inventory.visual.entries[0].index, 3);
  assert.equal(files.size, 5); assert.equal(rows.length, 4);
});

test('unsafe references do not interrupt inventory, and visuals create no automatic metadata', () => {
  const inventory = describe('![a](../escape.png) [[Unknown]]', [], new Map([['visual.svg', {}]]));
  assert.deepEqual(inventory.items, []); assert.deepEqual(inventory.visual.entries, []);
});
