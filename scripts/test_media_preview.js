const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');

class Node extends EventTarget {
  hidden = true; style = {}; dataset = {}; children = [];
  paused = true; src = ''; tagName = 'VIDEO'; naturalWidth = 100; naturalHeight = 50;
  append(child) { this.children.push(child); child.parent = this; }
  remove() { this.parent?.children.splice(this.parent.children.indexOf(this), 1); }
  removeAttribute(name) { delete this[name]; }
  pause() { this.paused = true; }
  load() {}
  play() { this.paused = false; return this.playAction?.() || Promise.resolve(); }
  getBoundingClientRect() { return { top: 100, left: 100, width: 100, height: 50 }; }
}
function fixture() {
  const image = new Node(), video = new Node(), preview = new Node(), dismiss = new Node(), body = new Node();
  preview.querySelector = tag => tag === 'img' ? image : video;
  const document = new Node(); document.body = body;
  document.getElementById = id => id === 'image-preview' ? preview : dismiss;
  document.createElement = () => new Node();
  const window = new Node(); window.scrollX = window.scrollY = 0;
  const context = vm.createContext({ document, window, AbortController, innerWidth: 1200, innerHeight: 800, matchMedia: () => ({ matches: false }) });
  vm.runInContext(fs.readFileSync(require.resolve('../static/media-preview.js'), 'utf8'), context);
  const source = new Node(); source.tagName = 'VIDEO'; source.src = 'clip.mp4';
  const target = { isConnected: true, matches: selector => selector.includes('.inline-playback'), querySelector: () => source, contains: node => node === target, setAttribute() {} };
  function hover() {
    const event = new Event('pointerover');
    Object.defineProperties(event, { target: { value: { closest: () => target } }, pointerType: { value: 'mouse' }, relatedTarget: { value: null } });
    document.dispatchEvent(event);
  }
  return { context, document, preview, body, video, hover };
}

test('preview teardown removes owned elements/listeners and can be repeated', () => {
  const f = fixture();
  for (let count = 0; count < 3; count++) {
    const controller = f.context.CourseMediaPreview.create();
    assert.equal(f.body.children.length, 1); assert.equal(f.preview.children.length, 1);
    controller.dispose(); controller.dispose();
    assert.equal(f.body.children.length, 0); assert.equal(f.preview.children.length, 0);
    f.hover(); assert.equal(f.video.src, undefined);
  }
});

test('late playback cannot reopen a cleared preview', async () => {
  const f = fixture(); let release;
  f.video.playAction = () => new Promise(resolve => { release = resolve; });
  const controller = f.context.CourseMediaPreview.create();
  f.hover(); controller.clear(); release();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.preview.hidden, true); assert.equal(f.video.paused, true);
  controller.dispose();
});
