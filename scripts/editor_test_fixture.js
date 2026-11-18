const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const model = require('../editor/model.js');
const contract = require('../static/contract.json');

class Element {
  children = []; listeners = {}; dataset = {}; value = ''; disabled = false; hidden = false; files = [];
  contentWindow = { postMessage() {} }; style = {};
  attributes = {};
  selectionStart = 0; selectionEnd = 0;
  get options() { return this.children; }
  get lastElementChild() { return this.children.at(-1); }
  append(...children) { this.children.push(...children); if (!this.value) this.value = children[0]?.value || ''; }
  replaceChildren(...children) { this.children = children; this.value = children[0]?.value || ''; }
  addEventListener(type, handler) { this.listeners[type] = handler; }
  removeEventListener(type) { delete this.listeners[type]; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  removeAttribute(name) { delete this.attributes[name]; }
  setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; }
  setRangeText(text, start, end) { this.value = this.value.slice(0, start) + text + this.value.slice(end); this.selectionStart = this.selectionEnd = start + text.length; }
  dispatchEvent(event) { this.listeners[event.type]?.(event); }
  querySelector() { return new Element(); }
  querySelectorAll() { return this.children.filter(child => child instanceof Element); }
  focus() {}
  click() {}
}

async function editorFixture({ record = null, table = null, confirm = true, source = 'Draft text' } = {}) {
  const elements = new Map();
  const element = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const state = { record, writes: 0 };
  const text = new Blob([source]);
  const files = { 'text.md': { size: text.size, sha256: await model.digest(text) },
    'visual.svg': { size: 1, sha256: 'a'.repeat(64) } };
  if (table !== null) files['media.csv'] = { size: new Blob([table]).size, sha256: await model.digest(new Blob([table])) };
  const context = vm.createContext({
    URL, Blob, TextEncoder, TextDecoder, AbortController, AbortSignal, Event, setTimeout, clearTimeout,
    location: new URL('https://site.test/course/editor/'),
    window: { addEventListener() {}, removeEventListener() {}, confirm: () => confirm,
      setTimeout: (fn, ms) => { const timer = setTimeout(fn, ms); timer.unref(); return timer; }, clearTimeout }, localStorage: { getItem: () => null, removeItem() {} },
    document: { getElementById: element, createElement: () => new Element() },
    CourseDraft: model, CourseMediaTable: require('../static/media-table.js'), CourseReferences: require('../static/references.js'),
    CourseDraftSession: require('../editor/session.js'), CourseDraftRecovery: require('../editor/recovery.js'),
    CoursePreviewProtocol: require('../editor/protocol.js'),
    CourseDocument: require('../editor/document.js'), CoursePreviewClient: require('../editor/preview-client.js'),
    CourseMediaPreview: require('../editor/media-preview.js'),
    CourseHandoff: require('../editor/handoff.js'), CourseHandoffView: require('../editor/handoff-view.js'),
    CourseInventory: require('../editor/inventory.js'), CourseMediaView: require('../editor/media-view.js'),
    CourseDraftStorage: { DraftStore: class {
      async read() { return state.record; }
      async save(key, payload, revision) { if (state.saveError) throw state.saveError; state.record = { ...payload, revision: revision + 1 }; state.writes++; return revision + 1; }
      async discard(key, revision) { state.record = { discarded: true, revision: revision + 1 }; return revision + 1; }
    } },
    fetch: async url => {
      if (String(url).endsWith('contract.json')) return Response.json(contract);
      if (String(url).endsWith('groups.json')) return Response.json({ groups: [
        { slug: 'group-01', repository: 'course/group-01', branch: 'main', editable: true, exclude: [] }] });
      if (String(url).endsWith('template.json')) return Response.json({ files, digest: 'template' });
      if (String(url).endsWith('media.csv')) return new Response(table);
      return new Response(text);
    },
  });
  const until = async predicate => {
    for (let attempt = 0; attempt < 200; attempt++) {
      if (predicate()) return;
      await new Promise(resolve => setImmediate(resolve));
    }
    assert.fail('Editor state: ' + element('status').textContent);
  };
  vm.runInContext(fs.readFileSync(require.resolve('../editor/review.js'), 'utf8'), context);
  vm.runInContext(fs.readFileSync(require.resolve('../editor/download.js'), 'utf8'), context);
  vm.runInContext(fs.readFileSync(require.resolve('../editor/persistence.js'), 'utf8'), context);
  vm.runInContext(fs.readFileSync(require.resolve('../editor/editor.js'), 'utf8'), context);
  await until(() => element('template').listeners.click);
  element('template').listeners.click();
  await until(() => element('status').textContent?.startsWith('Ready to edit') || element('status').textContent?.startsWith('Resolve'));
  return { element, state, until, Element };
}
module.exports = { editorFixture };
