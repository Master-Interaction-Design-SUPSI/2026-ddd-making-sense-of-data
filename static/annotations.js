((root) => {
  const annotationPattern = /\[\[([^\][|]+)\|([^\][|]+)(?:\|([^\][|]+))?\]\]/g;
  const types = new Set(["note", "bibliography", "quote"]);
  const styles = new Set(["italic", "normal"]);
  const colorPattern = /^(#[0-9a-fA-F]{3,8}|[a-zA-Z][\w-]*|(?:rgb|rgba|hsl|hsla|hwb|lab|lch|oklab|oklch|color)\([^;{}<>"'\\]*\))$/;

  function isResource(target) {
    return /^([a-z][a-z0-9+.-]*:\/\/|mailto:|www\.|\/|#|\.\/)/i.test(target) ||
      (!/\s/.test(target) && /\.[a-z0-9]{1,5}$/i.test(target));
  }

  function parseModifiers(field) {
    if (!field) return {};
    if (!field.includes("=")) return { marker: field.trim() };
    const modifiers = {};
    // The renderer escapes prose before parsing; an ampersand entity's
    // semicolon is part of the URL, not a modifier separator.
    for (const part of field.replace(/&amp;/g, "&").split(";")) {
      const pair = part.trim().match(/^(type|style|color|source)\s*=\s*(.+)$/);
      if (!pair || Object.hasOwn(modifiers, pair[1])) return null;
      const [, key, value] = pair;
      const trimmed = value.trim();
      if (key === "type" && !types.has(trimmed)) return null;
      if (key === "style" && !styles.has(trimmed)) return null;
      if (key === "color" && !colorPattern.test(trimmed)) return null;
      if (key === "source" && !/^https?:\/\/[^\s<>"'\\]+$/i.test(trimmed)) return null;
      modifiers[key] = trimmed;
    }
    return modifiers;
  }

  function parseAll(text) {
    return Array.from(text.matchAll(annotationPattern), (match) => {
      const [, label, rawTarget, field] = match;
      const target = rawTarget.trim();
      const resource = isResource(target);
      const modifiers = parseModifiers(field);
      const type = modifiers?.type || (resource ? "resource" : "note");
      return {
        source: match[0], index: match.index, label: label.trim(), target, resource,
        valid: modifiers !== null, type,
        style: modifiers?.style || (type === "note" ? "normal" : "inherit"),
        sourceUrl: modifiers?.source || null,
        color: modifiers?.color || null, marker: modifiers?.marker || null,
      };
    });
  }

  function replace(text, render) {
    let result = "";
    let offset = 0;
    for (const annotation of parseAll(text)) {
      result += text.slice(offset, annotation.index) + render(annotation);
      offset = annotation.index + annotation.source.length;
    }
    return result + text.slice(offset);
  }

  const api = { isResource, parseAll, replace };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.CourseAnnotations = api;
})(globalThis);
