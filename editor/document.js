((root) => {
  const normalize = value => value.replace(/\r\n?/g, '\n');

  function project(source) {
    const endings = source.match(/\r\n|\r|\n/g) || [];
    const newline = endings[0] || '\n';
    const result = { safe: true, title: '', authors: '', body: source, fields: {}, newline, source, header: null };
    const unsafe = reason => ({ ...result, safe: false, reason: `${reason} Edit full text.md; the source is preserved.` });
    if (endings.some(value => value !== newline)) return unsafe('This source mixes line endings.');
    const start = source.startsWith('\ufeff') ? 1 : 0;
    result.body = source.slice(start);
    const lines = [...source.slice(start).matchAll(/([^\r\n]*)(\r\n|\r|\n|$)/g)].filter(match => match[0]);
    if (!/^---[ \t]*$/.test(lines[0]?.[1] || '')) return result;
    const close = lines.findIndex((line, index) => index > 0 && /^---[ \t]*$/.test(line[1]));
    if (close < 0) return unsafe('The frontmatter has no closing delimiter.');
    if (close === 1) return unsafe('This empty header is ambiguous in the course renderer.');
    const closing = lines[close];
    result.header = { close: start + closing.index, end: start + closing.index + closing[0].length, terminated: !!closing[2] };
    result.body = source.slice(result.header.end);
    let current = null;
    for (const line of lines.slice(1, close)) {
      if (/^[ \t]+\S/.test(line[1]) && current) return unsafe(`${current} has a multiline value.`);
      const match = line[1].match(/^([A-Za-z][\w-]*)(\s*:[ \t]*)(.*?)([ \t]*)$/);
      current = null;
      if (!match) continue;
      const key = match[1].toLowerCase();
      if (!['title', 'authors'].includes(key)) continue;
      if (result.fields[key]) return unsafe(`${key} occurs more than once.`);
      if (/^[|>]/.test(match[3])) return unsafe(`${key} has a multiline value.`);
      current = key;
      const offset = start + line.index + match[1].length + match[2].length;
      result.fields[key] = { start: offset, end: offset + match[3].length };
      result[key] = match[3];
    }
    return result;
  }

  function update(source, patch) {
    const projection = project(source);
    if (!projection.safe) throw new Error(projection.reason);
    const edits = [], additions = [];
    for (const key of ['title', 'authors']) {
      if (!(key in patch) || patch[key] === projection[key]) continue;
      if (/[\r\n]/.test(patch[key])) throw new Error(`${key} must be a single line`);
      const value = patch[key].trim();
      const range = projection.fields[key];
      if (range) edits.push({ ...range, value });
      else if (value) additions.push(`${key}: ${value}${projection.newline}`);
    }
    if ('body' in patch && normalize(patch.body) !== normalize(projection.body)) {
      const body = normalize(patch.body).replace(/\n/g, projection.newline);
      edits.push({ start: projection.header?.end ?? (source.startsWith('\ufeff') ? 1 : 0), end: source.length,
        value: projection.header && !projection.header.terminated && body ? projection.newline + body : body });
    }
    if (additions.length) {
      if (projection.header) edits.push({ start: projection.header.close, end: projection.header.close, value: additions.join('') });
      else {
        const bom = source.startsWith('\ufeff') ? 1 : 0;
        edits.push({ start: bom, end: bom, value: `---${projection.newline}${additions.join('')}---${projection.newline}` });
      }
    }
    return edits.sort((a, b) => b.start - a.start || b.end - a.end)
      .reduce((text, edit) => text.slice(0, edit.start) + edit.value + text.slice(edit.end), source);
  }

  const api = { project, update };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CourseDocument = api;
})(globalThis);
