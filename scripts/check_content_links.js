/* Extract links with the site's vendored Markdown parser, without rendering DOM. */
const fs = require('node:fs');
const marked = require('../static/vendor/marked.min.js');
const annotations = require('../static/annotations.js');

const text = fs.readFileSync(0, 'utf8');
const body = text.replace(/^\uFEFF/, '').replace(/^---[ \t]*\r?\n[\s\S]*?\r?\n---[ \t]*(?:\r?\n|$)/, '');
const references = [];
const prose = annotations.replace(body, annotation =>
  annotation.label + ' ' + (annotation.resource ? '' : annotation.target));
marked.walkTokens(marked.lexer(prose), token => {
  if (token.type === 'link' || token.type === 'image') references.push(token.href);
});
// Annotations are expanded before Markdown by the renderer, including in code.
for (const annotation of annotations.parseAll(body)) {
  if (annotation.resource) references.push(annotation.target);
}
process.stdout.write(JSON.stringify(references));
