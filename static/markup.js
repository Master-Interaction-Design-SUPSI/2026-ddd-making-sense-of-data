((root) => {
  const marked = typeof module !== 'undefined' && module.exports ? require('./vendor/marked.min.js') : root.marked;
  const annotations = typeof module !== 'undefined' && module.exports ? require('./annotations.js') : root.CourseAnnotations;
  const media = typeof module !== 'undefined' && module.exports ? require('./media-table.js') : root.CourseMediaTable;

  const IMAGE_EXTS = ["png", "jpg", "jpeg", "webp", "gif", "avif", "svg"];
  const AUDIO_EXTS = ["mp3", "m4a", "wav", "ogg", "aac", "flac"];
  const VIDEO_EXTS = ["mp4", "webm", "mov"];
  const DEFAULT_MARKER = "✶";

  const esc = (s) =>
    String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");

  function extensionOf(url) {
    const path = url.split(/[?#]/)[0];
    const dot = path.lastIndexOf(".");
    return dot === -1 ? "" : path.slice(dot + 1).toLowerCase();
  }

  /* ---------- frontmatter ---------- */

  function parseFrontmatter(raw) {
    const meta = {};
    let body = raw.replace(/^﻿/, "").replace(/\r\n?/g, "\n");
    const match = body.match(/^---[ \t]*\n([\s\S]*?)\n---[ \t]*\n?/);
    if (match) {
      body = body.slice(match[0].length);
      for (const line of match[1].split("\n")) {
        const kv = line.match(/^([A-Za-z][\w-]*)\s*:\s*(.+)$/);
        if (kv) meta[kv[1].toLowerCase()] = kv[2].trim();
      }
    }
    return { meta, body };
  }

  /* ---------- extended markup ---------- */

  function annotationHeading(type, label, glyph) {
    if (type === "bibliography") return `(${glyph} <span class="bibliography-label">${label}</span>`;
    if (type === "note") return label;
    return `${label}${glyph}`;
  }

  function expandAnnotations(src, supportsColor) {
    return annotations.replace(src, (annotation) => {
      const { label, target, resource, type, style, color, marker, sourceUrl } = annotation;
      if (!annotation.valid || (color && !supportsColor(color))) return annotation.source;
      const bibliography = type === "bibliography";
      if (bibliography && resource) {
        return `<span class="annotation annotation-bibliography">(■ <a href="${esc(target)}" target="_blank" rel="noopener noreferrer">${label}</a>)</span>`;
      }
      const open = bibliography || type === "note";
      let content = target;
      if (resource) content = resourceHtml(target);
      else if (type === "quote") content = `<q>${target}</q>`;
      if (sourceUrl && resource && [...IMAGE_EXTS, ...AUDIO_EXTS, ...VIDEO_EXTS].includes(extensionOf(target))) {
        content = `<a href="${esc(sourceUrl.replace(/&amp;/g, "&"))}" target="_blank" rel="noopener noreferrer">${content}</a>`;
      }
      const mark = marker || ({ note: DEFAULT_MARKER, bibliography: "■" }[type] || DEFAULT_MARKER);
      const glyph = `<span class="annotation-marker" aria-hidden="true">${esc(mark)}</span>`;
      const appearance = color && !bibliography ? ` style="color:${esc(color)};--annotation-color:${esc(color)}"` : "";
      const heading = annotationHeading(type, label, glyph);
      if (bibliography) content = `. ${content}`;
      if (type === "note") content = ` &#123;${glyph} ${content}&#125;`;
      return (
        `<span class="annotation annotation-${type} annotation-style-${style}${open ? " open" : ""}"${appearance}>` +
        `<button type="button" class="annotation-toggle" aria-expanded="${open}">${heading}</button>` +
        `<span class="annotation-content${resource ? "" : " annotation-text"}"${open ? "" : " hidden"}>${content}</span>` +
        `${bibliography ? ")" : ""}</span>`
      );
    });
  }

  /* Embedded players: a recognised share link becomes an iframe on its own
     line. Adding a service is one line: [pattern with the id captured,
     (id, url) => player src]. */
  const EMBEDS = [
    [
      /(?:youtube\.com\/(?:watch\?[^#\s]*v=|shorts\/|live\/|embed\/)|youtu\.be\/)([\w-]{6,})/,
      (id, url) => {
        const t = url.match(/[?&](?:t|start)=(\d+)/);
        return `https://www.youtube-nocookie.com/embed/${id}${t ? `?start=${t[1]}` : ""}`;
      },
    ],
    [/vimeo\.com\/(\d+)/, (id) => `https://player.vimeo.com/video/${id}`],
    [
      /giphy\.com\/(?:gifs|clips|stickers|embed)\/(?:[\w-]*-)?(\w+)/,
      (id) => `https://giphy.com/embed/${id}`,
    ],
    [
      /tenor\.com\/(?:[a-z-]+\/)?view\/(?:[\w-]*-)?(\d+)/,
      (id) => `https://tenor.com/embed/${id}`,
    ],
  ];

  function embedHtml(url) {
    for (const [pattern, src] of EMBEDS) {
      const m = url.match(pattern);
      if (m) {
        return (
          `<iframe src="${esc(src(m[1], url))}" loading="lazy" ` +
          `allow="fullscreen; picture-in-picture" title="Embedded media"></iframe>` +
          `<a href="${esc(url)}" target="_blank" rel="noopener">Open media on provider</a>`
        );
      }
    }
    return null;
  }

  /* URLs are kept as written here; relative ones are resolved against the
     Entry folder in a single DOM pass over the rendered text (renderEntry). */
  function resourceHtml(url) {
    const embed = embedHtml(url);
    if (embed) return embed;
    const resolved = esc(url);
    const ext = extensionOf(url);
    if (IMAGE_EXTS.includes(ext))
      return `<img src="${resolved}" alt="" loading="lazy">`;
    if (AUDIO_EXTS.includes(ext))
      return `<audio controls preload="none" src="${resolved}"></audio>`;
    if (VIDEO_EXTS.includes(ext))
      return `<video controls muted loop playsinline preload="metadata" src="${resolved}"></video>`;
    return linkHtml(url);
  }

  function linkHtml(url) {
    let label = url.replace(/^https?:\/\//, "").replace(/\/$/, "");
    if (label.length > 60) label = label.slice(0, 57) + "…";
    return `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(label)}</a>`;
  }

  /* Colour span: {colour|text} — any CSS colour (named, #hex, rgb(), oklch()…).
     Innermost spans are expanded first, so nesting around annotations works:
     {crimson|[[text|url]]} tints the annotation too. Invalid colours are
     dropped silently, keeping the text. */
  const COLOUR_RE =
    /\{\s*(#[0-9a-fA-F]{3,8}|[a-zA-Z][\w-]*|(?:rgb|rgba|hsl|hsla|hwb|lab|lch|oklab|oklch|color)\([^)]*\))\s*\|([^{}]*)\}/;

  function expandColours(src, supportsColor) {
    let out = src;
    for (let guard = 0; guard < 500; guard++) {
      const next = out.replace(COLOUR_RE, (_, colour, text) => {
        const value = colour.trim();
        if (!supportsColor(value)) return text;
        const content = /^\*?✶\s/.test(text.trim()) ? `&#123;${text}&#125;` : text;
        return `<span class="tinted" style="color:${value};--annotation-color:${value}">${content}</span>`;
      });
      if (next === out) break;
      out = next;
    }
    return out;
  }

  function renderMarkdown(body, table = null, supportsColor = value => root.CSS.supports("color", value)) {
    // Keep Markdown blockquotes (>) while preventing contributor HTML (<...>).
    const plain = media.expand(body, table).replace(/&/g, "&amp;").replace(/</g, "&lt;");
    const expanded = expandColours(expandAnnotations(plain, supportsColor), supportsColor);
    const tokens = marked.lexer(expanded);
    marked.walkTokens(tokens, (token) => {
      if (token.type === "br") {
        token.type = "text";
        token.text = " ";
      } else if (token.type === "text" && !token.tokens) {
        token.text = token.text.replace(/[ \t]*(?:\r?\n[ \t]*)+/g, " ");
      }
    });
    const flow = [];
    for (const token of tokens) {
      if (token.type === "space") continue;
      const previous = flow[flow.length - 1];
      if (token.type === "paragraph" && previous?.type === "paragraph") {
        previous.tokens.push({ type: "text", raw: " ", text: " " });
        for (const inline of token.tokens) previous.tokens.push(inline);
      } else {
        flow.push(token);
      }
    }
    return marked.parser(flow);
  }

  const api = { parseFrontmatter, renderMarkdown, escapeHtml: esc };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CourseMarkup = api;
})(globalThis);
