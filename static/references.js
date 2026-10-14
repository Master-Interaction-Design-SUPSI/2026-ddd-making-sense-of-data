((root) => {
  const marked = typeof module !== 'undefined' && module.exports ? require('./vendor/marked.min.js') : root.marked;
  const annotations = typeof module !== 'undefined' && module.exports ? require('./annotations.js') : root.CourseAnnotations;

  function collect(text) {
    const body = text.replace(/^\uFEFF/, '').replace(/^---[ \t]*\r?\n[\s\S]*?\r?\n---[ \t]*(?:\r?\n|$)/, '');
    const references = [];
    const prose = annotations.replace(body, annotation =>
      annotation.label + ' ' + (annotation.resource ? '' : annotation.target));
    marked.walkTokens(marked.lexer(prose), token => {
      if (token.type === 'link' || token.type === 'image') references.push(token.href);
    });
    for (const annotation of annotations.parseAll(body)) {
      if (annotation.resource) references.push(annotation.target);
    }
    return references;
  }

  function localPath(target, schemes = ['https', 'http', 'mailto']) {
    if (typeof target !== 'string' || /[\u0000-\u001f\u007f\\]/.test(target)) throw new Error('Invalid link or media reference');
    const scheme = target.match(/^([a-z][a-z0-9+.-]*):/i);
    if (scheme) {
      if (!schemes.includes(scheme[1].toLowerCase())) throw new Error(`Unsupported URL scheme: ${target}`);
      return null;
    }
    if (target.startsWith('#')) return null;
    let path;
    try { path = decodeURIComponent(target.split(/[?#]/)[0]); }
    catch { throw new Error(`Invalid encoded path: ${target}`); }
    if (path.startsWith('./')) path = path.slice(2);
    if (/[\u0000-\u001f\u007f\\]/.test(path) || path.split('/').some(part => ['', '.', '..'].includes(part))) {
      throw new Error(`Reference must stay inside this group: ${target}`);
    }
    return path;
  }

  function validate(text, files, schemes) {
    const paths = new Set(files);
    const used = new Set();
    for (const target of collect(text)) {
      const path = localPath(target, schemes);
      if (path === null) continue;
      if (!paths.has(path)) throw new Error(`text.md: ${target} must reference an imported file inside this group`);
      used.add(path);
    }
    return [...used];
  }

  const api = { collect, localPath, validate };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CourseReferences = api;
})(globalThis);
