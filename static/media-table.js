/* media.csv: one row per media file — the name written in the text, the
   link to the original source, and the uploaded file's path. Shared by the
   renderer, the editor and the importer's Node link check.

   [[name]] in text.md expands to the existing annotation form
   [[name|file|source=url]], so everything downstream sees one syntax. */
((root) => {
  const HEADER = ["name", "source", "file"];
  const SOURCE = /^https?:\/\/[^\s<>"'\\]+$/i;
  const NAMED = /\[\[([^\][|]+)\]\]/g;
  const PAIR = /\[\[([^\][|]+)\|([^\][|]+)\]\]/g;
  const MAX_NAME = 200;

  function records(text) {
    const source = text.replace(/^\uFEFF/, "");
    const firstLine = source.split(/\r\n|\n|\r/, 1)[0];
    // Spreadsheets in Swiss and Italian locales save semicolon-separated CSV.
    const delimiter = firstLine.split(";").length > firstLine.split(",").length ? ";" : ",";
    const rows = [];
    let row = [], field = "", quoted = false, afterQuote = false, line = 1, rowLine = 1;
    const endField = () => { row.push(field); field = ""; afterQuote = false; };
    const endRow = () => { endField(); rows.push({ line: rowLine, fields: row }); row = []; };
    for (let i = 0; i < source.length; i++) {
      const char = source[i];
      if (quoted) {
        if (char === '"' && source[i + 1] === '"') { field += '"'; i++; }
        else if (char === '"') { quoted = false; afterQuote = true; }
        else { if (char === "\n" || (char === "\r" && source[i + 1] !== "\n")) line++; field += char; }
      } else if (char === delimiter) endField();
      else if (char === "\r" || char === "\n") {
        if (char === "\r" && source[i + 1] === "\n") i++;
        endRow(); line++; rowLine = line;
      } else if (afterQuote) {
        if (char !== " " && char !== "\t") throw new Error(`media.csv line ${line}: text after a closing quote; put the whole field in quotes`);
      } else if (char === '"' && field.trim() === "") { field = ""; quoted = true; }
      else field += char;
    }
    if (quoted) throw new Error(`media.csv line ${rowLine}: a quoted field is never closed`);
    if (field !== "" || row.length) endRow();
    return rows.filter(({ fields }) => fields.some(value => value.trim() !== ""));
  }

  function read(text) {
    if (typeof text !== "string") throw new Error("media.csv: expected text");
    const [header, ...body] = records(text);
    const columns = header ? header.fields.map(value => value.trim().toLowerCase()) : [];
    if (columns.slice(0, 3).join(",") !== HEADER.join(",") || columns.slice(3).some(Boolean)) {
      throw new Error("media.csv: the first line must be name,source,file");
    }
    const rows = [];
    const byName = new Map();
    const byFile = new Map();
    for (const { line, fields } of body) {
      if (fields.slice(3).some(value => value.trim() !== "")) throw new Error(`media.csv line ${line}: use exactly three columns: name, source, file`);
      const [name = "", source = "", file = ""] = fields.map(value => value.trim());
      if (!name) throw new Error(`media.csv line ${line}: add the name used in the text`);
      if (name.length > MAX_NAME || /[\][|\u0000-\u001f\u007f]/.test(name)) {
        throw new Error(`media.csv line ${line}: names cannot contain [ ] | or line breaks`);
      }
      if (source && !SOURCE.test(source)) throw new Error(`media.csv line ${line}: the source must be a full http:// or https:// link`);
      if (!file) throw new Error(`media.csv line ${line}: add the uploaded file's path, e.g. media/photo.jpg`);
      if (/^[a-z][a-z0-9+.-]*:/i.test(file) || /[\\\u0000-\u001f\u007f]/.test(file) || file.split("/").some(part => ["", ".", ".."].includes(part))) {
        throw new Error(`media.csv line ${line}: the file must be a path inside this group, e.g. media/photo.jpg`);
      }
      const key = name.toLowerCase();
      if (byName.has(key)) throw new Error(`media.csv line ${line}: the name "${name}" is already used on line ${byName.get(key).line}`);
      const row = { line, name, source, file };
      rows.push(row);
      byName.set(key, row);
      if (!byFile.has(file)) byFile.set(file, row);
    }
    return { rows, byName, byFile };
  }

  // The annotation grammar reserves [ ] | and ; inside a modifier.
  const encodeSource = url => url.replace(/[\][|;]/g, char => "%" + char.charCodeAt(0).toString(16).toUpperCase());
  const withSource = (label, file, source) => `[[${label}|${file}${source ? `|source=${encodeSource(source)}` : ""}]]`;

  /* [[name]] becomes [[name|file|source=…]]; [[label|media/file]] gains its
     row's source. Unknown names stay literal for validation to report. */
  function expand(body, table) {
    if (!table) return body;
    return body
      .replace(PAIR, (match, label, target) => {
        const row = table.byFile.get(target.trim().replace(/^\.\//, ""));
        return row && row.source ? withSource(label, target.trim(), row.source) : match;
      })
      .replace(NAMED, (match, name) => {
        const row = table.byName.get(name.trim().toLowerCase());
        return row ? withSource(name.trim(), row.file, row.source) : match;
      });
  }

  function unresolved(body) {
    return Array.from(body.matchAll(NAMED), match => match[1].trim());
  }

  // Replace each [[name]] that expand() left unresolved.
  function replaceUnresolved(body, replacer) {
    return body.replace(NAMED, (match, name) => replacer(name.trim()));
  }

  function sources(table) {
    return new Map(table ? [...table.byFile].filter(([, row]) => row.source).map(([file, row]) => [file, row.source]) : []);
  }

  function warnings(table, files) {
    const present = new Set(files);
    const notes = [];
    const unsourced = table.rows.filter(row => !row.source).map(row => row.name);
    if (unsourced.length) notes.push("Rows without a source link: " + unsourced.join(", "));
    const missing = table.rows.filter(row => !present.has(row.file)).map(row => row.file);
    if (missing.length) notes.push("Rows whose file is not in this group yet: " + missing.join(", "));
    return notes;
  }

  const quote = value => /[",;\r\n]|^\s|\s$/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;

  function serialize(rows) {
    return [HEADER, ...rows.map(row => [row.name, row.source, row.file])]
      .map(fields => fields.map(quote).join(",")).join("\n") + "\n";
  }

  const api = { read, expand, unresolved, replaceUnresolved, sources, warnings, serialize };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.CourseMediaTable = api;
})(globalThis);
