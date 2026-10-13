const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');
const annotations = require('../static/annotations.js');
const marked = require('../static/vendor/marked.min.js');

const context = vm.createContext({
  CourseAnnotations: annotations, marked, URL,
  CSS: { supports: (_, value) => /^(crimson|green|red|#116964|rgb\(10, 20, 30\))$/.test(value) },
  document: { baseURI: 'https://site.test/course/', getElementById: () => null },
  fetch: () => new Promise(() => {}),
});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/main.js'), 'utf8'), context);

test('semantic type, style and color are independent and order does not matter', () => {
  for (const modifiers of ['type=quote;style=italic;color=crimson', 'color=crimson;type=quote;style=italic']) {
    const [annotation] = annotations.parseAll(`[[the quote|Quoted words|${modifiers}]]`);
    assert.equal(annotation.valid, true);
    assert.equal(annotation.type, 'quote');
    assert.equal(annotation.style, 'italic');
    assert.equal(annotation.color, 'crimson');
    const html = context.renderMarkdown(annotation.source);
    assert.match(html, /annotation-quote annotation-style-italic/);
    assert.match(html, /color:crimson;--annotation-color:crimson/);
    assert.match(html, /<q>Quoted words<\/q>/);
  }
});

test('defaults distinguish notes, bibliography and unchanged media annotations', () => {
  const [note, bibliography, media] = annotations.parseAll('[[note|A detail]] [[source|https://example.org|type=bibliography]] [[sketch|media/sketch.svg]]');
  assert.equal(note.type, 'note');
  assert.equal(note.style, 'italic');
  assert.equal(note.color, null);
  assert.equal(bibliography.type, 'bibliography');
  assert.equal(bibliography.resource, true);
  assert.equal(media.type, 'resource');
  assert.equal(media.style, 'inherit');
  assert.match(context.renderMarkdown(note.source), /(?:\{|&#123;)∗(?:\}|&#125;)/);
  assert.match(context.renderMarkdown(bibliography.source), /\(■\)/);
  assert.match(context.renderMarkdown(media.source), /src="media\/sketch.svg"/);
});

test('explicit appearance overrides defaults and preserves legacy markers and color wrappers', () => {
  const [note] = annotations.parseAll('[[note|A detail|style=normal;color=green;type=note]]');
  assert.equal(note.style, 'normal');
  assert.equal(note.color, 'green');
  assert.match(context.renderMarkdown(note.source), /annotation-note annotation-style-normal/);
  assert.match(context.renderMarkdown(note.source), /(?:\{|&#123;)∗(?:\}|&#125;)/);
  assert.match(context.renderMarkdown('[[note|A detail|💡]]'), /(?:\{|&#123;)💡(?:\}|&#125;)/);
  const nested = context.renderMarkdown('{crimson|[[note|A detail|color=green]]}');
  assert.match(nested, /class="tinted" style="color:crimson/);
  assert.match(nested, /annotation-note[^>]+color:green;--annotation-color:green/);
});

test('Markdown italics work in prose and annotation labels', () => {
  const html = context.renderMarkdown('*Prose* and [[*label*|A detail|type=note]]');
  assert.match(html, /<em>Prose<\/em>/);
  assert.match(html, /<em>label<\/em>/);
});

test('unknown, duplicate and malformed modifiers stay readable', () => {
  for (const field of ['type=unknown', 'style=bold', 'color=not-a-color', 'color=red;unknown=x',
    'type=note;type=quote', 'type=note;', 'style=', 'color=red" onclick="bad()', 'color=rgb(0;0;0)']) {
    const source = `[[label|A detail|${field}]]`;
    const html = context.renderMarkdown(source);
    assert.ok(html.includes('label|A detail|'), html);
    assert.doesNotMatch(html, /class="annotation /);
    assert.doesNotMatch(html, /<[^>]+\bonclick=/);
  }
});

test('contributor HTML stays escaped and unsafe links/media stay rejected', () => {
  const html = context.renderMarkdown('[[<img onerror=bad()>|<script>bad()</script>|type=quote;color=crimson]]');
  assert.doesNotMatch(html, /<script>|<img onerror/);
  assert.match(html, /&lt;script&gt;/);
  for (const target of ['javascript:alert(1)', 'data:text/html,bad', 'file:///tmp/secret', 'https:\\bad.test']) {
    assert.equal(context.safeUrl(target, 'group', 'link'), null);
    assert.equal(context.safeUrl(target, 'group', 'media'), null);
  }
  assert.equal(context.safeUrl('media/sketch.svg', 'group', 'media'), 'group/media/sketch.svg');
});
