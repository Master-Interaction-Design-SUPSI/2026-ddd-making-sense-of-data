const assert = require('node:assert/strict');
const { test } = require('node:test');
const { create } = require('../static/lightbox.js');

class Dialog extends EventTarget {
  open = false;
  image = { src: '', alt: '', removeAttribute() { this.src = ''; } };
  querySelector() { return this.image; }
  showModal() { this.open = true; }
  close() { this.open = false; this.dispatchEvent(new Event('close')); }
}

test('lightbox opens the selected image and restores the invoker on native close', () => {
  const dialog = new Dialog();
  let focused = 0;
  const trigger = { isConnected: true, querySelector: () => ({ src: 'visual.svg', alt: 'Study' }), focus: () => focused++ };
  const states = [];
  const box = create(dialog, open => states.push(open));
  box.open(trigger);
  assert.equal(dialog.open, true); assert.equal(dialog.image.src, 'visual.svg'); assert.equal(dialog.image.alt, 'Study');
  dialog.close();
  assert.equal(dialog.image.src, ''); assert.equal(focused, 1); assert.deepEqual(states, [true, false]);
  box.dispose();
  dialog.dispatchEvent(new Event('close'));
  assert.equal(states.length, 2);
});

test('lightbox teardown closes an open modal and releases callbacks', () => {
  const dialog = new Dialog(); let changes = 0;
  const box = create(dialog, () => changes++);
  box.open({ querySelector: () => ({ src: 'visual.svg', alt: '' }) });
  box.dispose(); box.dispose();
  assert.equal(dialog.open, false); assert.equal(dialog.image.src, ''); assert.equal(changes, 2);
});

test('teardown handles the browser queued close event before listener removal', async () => {
  const dialog = new Dialog();
  dialog.close = function () { this.open = false; queueMicrotask(() => this.dispatchEvent(new Event('close'))); };
  let opened = false;
  const box = create(dialog, value => { opened = value; });
  box.open({ querySelector: () => ({ src: 'visual.svg', alt: '' }) });
  box.dispose();
  assert.equal(opened, false);
  await Promise.resolve(); assert.equal(opened, false);
});
