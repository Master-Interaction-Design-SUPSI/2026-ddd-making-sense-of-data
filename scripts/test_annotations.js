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

test('bibliography starts open and resource references stay links rather than players', () => {
  for (const target of ['A written reference', 'https://example.org/data', 'media/sketch.svg',
    'media/example-tone.mp3', 'https://youtu.be/abcdefghijk']) {
    const html = context.renderMarkdown(`[[source|${target}|type=bibliography;color=crimson]]`);
    assert.match(html, /annotation-bibliography annotation-style-inherit open/);
    assert.match(html, /aria-expanded="true"/);
    assert.doesNotMatch(html, / hidden|color:crimson|<iframe|<img|<audio|<video/);
    if (target !== 'A written reference') assert.match(html, /<a href=/);
  }
  const note = context.renderMarkdown('[[note|A detail]]');
  assert.match(note, /aria-expanded="false"/);
  assert.match(note, / hidden>/);
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

test('source line breaks and blank lines produce the same continuous prose', () => {
  const expected = context.renderMarkdown('First words continue. Second thought follows.');
  for (const separator of ['\n', '\r\n', '\n\n', '\r\n\r\n', '\n \t\n', '  \n', '\\\n']) {
    assert.equal(context.renderMarkdown(`First words${separator}continue.${separator}Second thought follows.`), expected);
  }
  assert.equal(context.renderMarkdown('First\n   words'), context.renderMarkdown('First words'));
  assert.equal(context.renderMarkdown('*First\nwords*'), context.renderMarkdown('*First words*'));
});

test('continuous prose preserves structural Markdown boundaries and code newlines', () => {
  const html = context.renderMarkdown('First\nparagraph.\n\nSecond paragraph.\n\n### Heading\n\n- First\n  item\n- Second item\n\n> Quoted\n> words\n\n```text\nfirst\nsecond\n```\n\n    indented\n    code\n\nFinal paragraph.\n\nContinues here.');
  assert.match(html, /<p>First paragraph\. Second paragraph\.<\/p>/);
  assert.match(html, /<h3>Heading<\/h3>/);
  assert.match(html, /<ul>\n<li>First item<\/li>\n<li>Second item<\/li>\n<\/ul>/);
  assert.match(html, /<blockquote>\n<p>Quoted words<\/p>\n<\/blockquote>/);
  assert.match(html, /<code class="language-text">first\nsecond\n<\/code>/);
  assert.match(html, /<code>indented\ncode\n<\/code>/);
  assert.match(html, /<p>Final paragraph\. Continues here\.<\/p>/);
  assert.match(context.renderMarkdown('Keep `first  second` intact.'), /<code>first  second<\/code>/);
});

test('wrapped annotations, links, media and escaped HTML survive prose normalization', () => {
  const html = context.renderMarkdown('A [[wrapped\nlabel|A wrapped\nnote|type=note]]\n\nwith [a\nlink](https://example.org/data?q=1&v=2) and\n![detail](media/example-detail.svg)\n\n[[audio|media/example-tone.mp3]]\n\n[[video|media/example-motion.mp4]]. <script>bad()</script>');
  assert.equal((html.match(/<p>/g) || []).length, 1);
  assert.match(html, /wrapped label<span/);
  assert.match(html, /hidden>A wrapped note<\/span>/);
  assert.match(html, /href="https:\/\/example.org\/data\?q=1&amp;v=2">a link<\/a>/);
  assert.match(html, /src="media\/example-detail.svg"/);
  assert.match(html, /src="media\/example-tone.mp3"/);
  assert.match(html, /src="media\/example-motion.mp4"/);
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /&lt;script&gt;/);
});

test('LF and CRLF front matter stay outside the merged prose', () => {
  for (const newline of ['\n', '\r\n']) {
    const raw = '\uFEFF' + ['---', 'title: A title', 'authors: A, B', '---', '', 'First words.', '', 'Second words.'].join(newline);
    const { meta, body } = context.parseFrontmatter(raw);
    assert.equal(meta.title, 'A title');
    assert.equal(meta.authors, 'A, B');
    assert.equal(context.renderMarkdown(body), '<p>First words. Second words.</p>\n');
  }
});
