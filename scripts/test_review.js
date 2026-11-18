const assert = require('node:assert/strict');
const { test } = require('node:test');
const { validateSvg, validate } = require('../editor/review.js');
const contract = require('../static/contract.json');
const parser = attributes => class { parseFromString() { return {
  querySelector: () => attributes.invalid || false,
  documentElement: { localName: 'svg', namespaceURI: 'http://www.w3.org/2000/svg', getAttribute: name => attributes[name] ?? null, hasAttribute: name => name in attributes },
}; } };

test('review checks the main SVG dimensions while allowing other static SVG sizes', () => {
  validateSvg('', 'visual.svg', contract, parser({ viewBox: '0 0 1920 1080', width: '1920px' }));
  validateSvg('', 'media/detail.svg', contract, parser({ viewBox: '0 0 10 10' }));
  assert.throws(() => validateSvg('', 'visual.svg', contract, parser({ viewBox: '0 0 10 10' })), /1920/);
  assert.throws(() => validateSvg('', 'visual.svg', contract, parser({ viewBox: '0 0 1920 1080', height: '20' })), /height/);
  assert.throws(() => validateSvg('', 'media/bad.svg', contract, parser({ invalid: true })), /valid SVG/);
});

test('review refuses missing recovery bytes before reading or preparing handoff', async () => {
  let checked = false;
  await assert.rejects(validate({ validate: () => { checked = true; } }, new Set(['media/lost.png']), contract), error => error.field === 'media-heading');
  assert.equal(checked, false);
});
