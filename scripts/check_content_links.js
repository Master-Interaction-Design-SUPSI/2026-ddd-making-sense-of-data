/* Extract links with the site's vendored Markdown parser, without rendering DOM. */
const fs = require('node:fs');
const marked = require('../static/vendor/marked.min.js');

const text = fs.readFileSync(0, 'utf8');
const body = text.replace(/^\uFEFF/, '').replace(/^---[ \t]*\r?\n[\s\S]*?\r?\n---[ \t]*(?:\r?\n|$)/, '');
const references = [];
marked.walkTokens(marked.lexer(body), token => {
  if (token.type === 'link' || token.type === 'image') references.push(token.href);
});
// Annotations are expanded before Markdown by the renderer, including in code.
for (const match of body.matchAll(/\[\[([^\][|]+)\|([^\][|]+)(?:\|([^\][|]+))?\]\]/g)) {
  const target = match[2].trim();
  if (/^([a-z][a-z0-9+.-]*:\/\/|mailto:|www\.|\/|#|\.\/)/i.test(target) ||
      (!/\s/.test(target) && /\.[a-z0-9]{1,5}$/i.test(target))) references.push(target);
}
process.stdout.write(JSON.stringify(references));
