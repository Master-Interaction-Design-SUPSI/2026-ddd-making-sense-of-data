/*
 * Data Driven Design — client-side renderer.
 *
 * The publishing workflow discovers Entry folders and writes entries.json.
 * This script fetches that list, then each folder's text.md and visual.svg,
 * and renders everything in the browser.
 *
 * Folder names become each section's URL fragment.
 * Captions and navigation use the title in text.md when one is provided.
 */

const IMAGE_EXTS = ["png", "jpg", "jpeg", "webp", "gif", "avif", "svg"];
const AUDIO_EXTS = ["mp3", "m4a", "wav", "ogg", "aac", "flac"];
const VIDEO_EXTS = ["mp4", "webm", "mov"];
const DEFAULT_MARKER = "✶";

const pad = (n) => String(n).padStart(2, "0");

const esc = (s) =>
  String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");

/* A URL with no scheme, no leading "/" and no leading "#" is taken to be
   relative to the Entry's folder, so students can keep extra assets there. */
function resolveUrl(url, folder) {
  if (/^([a-z][a-z0-9+.-]*:|\/|#)/i.test(url)) return url;
  return `${folder}/${url}`;
}

let resourceResolver = null;

function safeUrl(url, folder, kind) {
  if (/[\u0000-\u001f\u007f\\]/.test(url)) return null;
  if (!/^([a-z][a-z0-9+.-]*:|\/|#)/i.test(url) && resourceResolver) {
    let path;
    try { path = decodeURIComponent(url.split(/[?#]/)[0]).replace(/^\.\//, ""); }
    catch { return null; }
    if (path.split("/").some(part => ["", ".", ".."].includes(part)) || /[\u0000-\u001f\u007f\\]/.test(path)) return null;
    const owned = resourceResolver(path, kind, folder);
    if (owned === false) return null;
    if (owned) return owned + (url.includes("#") ? url.slice(url.indexOf("#")) : "");
  }
  const resolved = resolveUrl(url, folder);
  try {
    const parsed = new URL(resolved, document.baseURI);
    const allowed = kind === "link"
      ? ["http:", "https:", "mailto:"]
      : ["http:", "https:"];
    return allowed.includes(parsed.protocol) ? resolved : null;
  } catch {
    return null;
  }
}

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

function expandAnnotations(src) {
  return CourseAnnotations.replace(src, (annotation) => {
    const { label, target, resource, type, style, color, marker } = annotation;
    if (!annotation.valid || (color && !CSS.supports("color", color))) return annotation.source;
    const bibliography = type === "bibliography";
    const open = bibliography || type === "note";
    let content = target;
    if (resource) content = bibliography ? linkHtml(target) : resourceHtml(target);
    else if (type === "quote") content = `<q>${target}</q>`;
    const mark = marker || ({ note: DEFAULT_MARKER, bibliography: "■" }[type] || DEFAULT_MARKER);
    const glyph = `<span class="annotation-marker" aria-hidden="true">${esc(mark)}</span>`;
    const appearance = color && !bibliography ? ` style="color:${esc(color)};--annotation-color:${esc(color)}"` : "";
    const heading = bibliography ? `(${glyph} ${label}` : type === "note" ? label : `${label}${glyph}`;
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
    return `<video controls preload="metadata" src="${resolved}"></video>`;
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

function expandColours(src) {
  let out = src;
  for (let guard = 0; guard < 500; guard++) {
    const next = out.replace(COLOUR_RE, (_, colour, text) => {
      const value = colour.trim();
      if (!CSS.supports("color", value)) return text;
      const content = /^\*?✶\s/.test(text.trim()) ? `&#123;${text}&#125;` : text;
      return `<span class="tinted" style="color:${value};--annotation-color:${value}">${content}</span>`;
    });
    if (next === out) break;
    out = next;
  }
  return out;
}

function renderMarkdown(body) {
  // Keep Markdown blockquotes (>) while preventing contributor HTML (<...>).
  const plain = body.replace(/&/g, "&amp;").replace(/</g, "&lt;");
  const expanded = expandColours(expandAnnotations(plain));
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

/* ---------- loading ---------- */

async function findVisual(folder) {
  const url = `${folder}/visual.svg`;
  try {
    const res = await fetch(url, { method: "HEAD" });
    return res.ok ? url : null;
  } catch {
    return null;
  }
}

async function loadEntry(folder) {
  const [text, visual] = await Promise.all([
    fetch(`${folder}/text.md`).then((r) => (r.ok ? r.text() : null)).catch(() => null),
    findVisual(folder),
  ]);
  return { folder, text, visual };
}

/* ---------- rendering ---------- */

function renderEntry(entry) {
  const { meta, body } = parseFrontmatter(entry.text || "");
  entry.title = meta.title || entry.folder;
  entry.authors = meta.authors || "";

  const section = document.createElement("section");
  section.className = "entry";
  section.id = entry.folder;

  let html = `<header class="entry-caption">`;
  html += `<div><h2>${esc(entry.title)}</h2>`;
  if (entry.authors) html += `<p class="entry-authors">${esc(entry.authors)}</p>`;
  html += `</div>`;
  html += `</header>`;
  html += `<div class="entry-text">`;
  if (entry.text !== null) html += renderMarkdown(body);
  html += `</div>`;
  const visual = entry.visual && safeUrl(entry.visual === `${entry.folder}/visual.svg` ? "visual.svg" : entry.visual, entry.folder, "media");
  if (visual) {
    html +=
      `<figure class="entry-visual">` +
      `<img src="${esc(visual)}" width="1920" height="1080" alt="${esc(entry.title)} — visualisation">` +
      `</figure>`;
  }

  const template = document.createElement("template");
  template.innerHTML = html;

  /* Single resolution pass: every relative path in the rendered text —
     annotation resources and markdown-native ones (e.g. ![…](photo.jpg)) —
     resolves against the Entry's folder. The hero visual above is already
     a full path and sits outside this scope. */
  const textEl = template.content.querySelector(".entry-text");
  for (const el of textEl.querySelectorAll("[src]")) {
    const src = el.getAttribute("src");
    const safe = src && safeUrl(src, entry.folder, "media");
    if (safe) el.setAttribute("src", safe);
    else el.removeAttribute("src");
  }
  for (const a of textEl.querySelectorAll("a[href]")) {
    const href = a.getAttribute("href");
    const safe = href && safeUrl(href, entry.folder, "link");
    if (safe) a.setAttribute("href", safe);
    else a.removeAttribute("href");
  }
  prepareInlineImages(textEl);
  CoursePlayback.prepare(textEl);
  section.append(template.content);

  return section;
}

function prepareInlineImages(textEl) {
  for (const image of textEl.querySelectorAll("img[src]")) {
    const content = image.parentElement.matches(".annotation-content") &&
      image.parentElement.childNodes.length === 1 ? image.parentElement : null;
    let target;
    if (content) {
      target = content.parentElement.querySelector(".annotation-toggle");
      target.querySelector(".annotation-marker")?.remove();
      image.alt = target.textContent.trim();
      target.classList.remove("annotation-toggle");
      target.replaceChildren(image);
      content.remove();
    } else {
      // Linked Markdown images keep their link and native activation.
      target = image.closest("a[href]");
      if (!target) {
        target = document.createElement("button");
        target.type = "button";
        image.replaceWith(target);
        target.append(image);
      }
    }
    target.classList.add("inline-image");
    if (target.tagName === "BUTTON") {
      target.setAttribute("aria-label", `Preview image: ${image.alt || "supporting image"}`);
      target.setAttribute("aria-controls", "image-preview");
      target.setAttribute("aria-expanded", "false");
    }
  }
}

function buildNav(entries) {
  const nav = document.getElementById("nav");
  nav.replaceChildren();
  entries.forEach((entry, i) => {
    const a = document.createElement("a");
    a.href = `#${entry.folder}`;
    a.dataset.folder = entry.folder;
    const idx = document.createElement("span");
    idx.className = "nav-index";
    idx.textContent = pad(i + 1);
    a.append(idx, ` ${entry.title}`);
    nav.appendChild(a);
  });
}

/* The bar readout: the Entry currently under the reading band. The
   markers at the right edge follow it — one ✶ per Entry, the current
   one lit in the signal colour. */
function setCurrent(entry, index) {
  const title = document.getElementById("current");
  if (title) title.textContent = entry.title;
  const authors = document.getElementById("current-authors");
  if (authors) authors.textContent = entry.authors || "";
  document
    .querySelectorAll("#markers span")
    .forEach((m, i) => m.classList.toggle("current", i === index));
}

function buildMarkers(entries) {
  const el = document.getElementById("markers");
  if (!el) return;
  el.innerHTML = entries.map(() => `<span>${DEFAULT_MARKER}</span>`).join("");
}

function watchSections(entries) {
  const links = new Map(
    [...document.querySelectorAll("#nav a")].map((a) => [a.dataset.folder, a])
  );
  const indexOf = new Map(entries.map((e, i) => [e.folder, i]));
  const observer = new IntersectionObserver(
    (hits) => {
      for (const hit of hits) {
        links.get(hit.target.id)?.classList.toggle("active", hit.isIntersecting);
        if (hit.isIntersecting && indexOf.has(hit.target.id)) {
          const i = indexOf.get(hit.target.id);
          setCurrent(entries[i], i);
        }
      }
    },
    { rootMargin: "-20% 0px -55% 0px" }
  );
  document.querySelectorAll(".entry").forEach((s) => observer.observe(s));
  return () => observer.disconnect();
}

/* ---------- interactions ---------- */

/* Centre a revealed external player under the activation point. */
function centerAnnotationMedia(el, x) {
  const place = () => {
    const box = el.closest(".annotation-content").getBoundingClientRect();
    const w = el.getBoundingClientRect().width;
    if (!box.width || !w) return;
    const offset = Math.max(0, Math.min(x - box.left - w / 2, box.width - w));
    el.style.marginInlineStart = `${offset}px`;
  };
  place();
}

function toggleCenterX(toggle) {
  const rects = toggle.getClientRects();
  const last = rects[rects.length - 1];
  return last ? last.left + last.width / 2 : 0;
}

function setupInteractions() {
  const clearPreview = setupImagePreviews();
  const lightbox = document.getElementById("lightbox");
  const lightboxImg = lightbox.querySelector("img");

  document.addEventListener("click", (event) => {
    const toggle = event.target.closest(".annotation-toggle");
    if (toggle) {
      const content = toggle.parentElement.querySelector(".annotation-content");
      const open = content.hidden;
      content.hidden = !open;
      toggle.setAttribute("aria-expanded", String(open));
      toggle.parentElement.classList.toggle("open", open);
      if (open) {
        const media = content.querySelector("iframe");
        /* keyboard activation reports clientX 0: centre on the toggle */
        if (media) centerAnnotationMedia(media, event.clientX || toggleCenterX(toggle));
      }
      return;
    }

    const zoomable = event.target.closest(".entry-visual img");
    if (zoomable) {
      lightboxImg.src = zoomable.src;
      lightbox.hidden = false;
      document.body.classList.add("lightbox-open");
      lightbox.querySelector(".lightbox-close")?.focus();
      return;
    }

    if (!lightbox.hidden && event.target.closest("#lightbox")) {
      closeLightbox();
    }
  });

  window.addEventListener("resize", () => {
    for (const media of document.querySelectorAll(
      ".annotation.open .annotation-content iframe"
    )) {
      const toggle = media.closest(".annotation").querySelector(".annotation-toggle");
      centerAnnotationMedia(media, toggleCenterX(toggle));
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !lightbox.hidden) closeLightbox();
    /* g exposes the cell grid the page is set on. */
    if (
      event.key === "g" &&
      !event.metaKey &&
      !event.ctrlKey &&
      !event.altKey &&
      !event.target.closest("input, textarea, [contenteditable]")
    ) {
      document.body.classList.toggle("show-grid");
    }
  });

  function closeLightbox() {
    lightbox.hidden = true;
    lightboxImg.src = "";
    document.body.classList.remove("lightbox-open");
  }
  return () => { clearPreview(); closeLightbox(); };
}

function setupImagePreviews() {
  const preview = document.getElementById("image-preview");
  const image = preview.querySelector("img");
  const video = preview.querySelector("video");
  let animation;
  const dismiss = document.getElementById("image-preview-close");
  let active = null;
  let mode = null;
  let previewScrollX = window.scrollX;
  let previewScrollY = window.scrollY;

  function clear() {
    if (active?.matches("button, .inline-playback")) active.setAttribute("aria-expanded", "false");
    active = null;
    mode = null;
    preview.hidden = true;
    dismiss.hidden = true;
    animation?.cancel();
    image.removeAttribute("src");
    video.pause();
    video.removeAttribute("src");
    video.load();
  }

  function reveal(media) {
    if (!active) return;
    const source = active.querySelector("img, video");
    const width = media.naturalWidth || media.videoWidth;
    const height = media.naturalHeight || media.videoHeight;
    if (!width || !height) return;
    const anchor = source.getBoundingClientRect();
    const scale = Math.min((innerWidth - 32) / width, (innerHeight - 80) / height);
    const w = width * scale;
    const h = height * scale;
    const x = anchor.left + anchor.width / 2;
    const y = anchor.top + anchor.height / 2;
    const left = Math.max(16, Math.min(x - w / 2, innerWidth - w - 16));
    const top = Math.max(48, Math.min(y - h / 2, innerHeight - h - 16));
    Object.assign(preview.style, {
      width: `${w}px`, height: `${h}px`, left: `${left}px`, top: `${top}px`,
      transformOrigin: `${x - left}px ${y - top}px`,
    });
    preview.hidden = false;
    if (!matchMedia("(prefers-reduced-motion: reduce)").matches) {
      animation?.cancel();
      animation = preview.animate([
        { transform: `scale(${anchor.width / w}, ${anchor.height / h})` },
        { transform: "scale(1)" },
      ], { duration: 180, easing: "ease-out" });
    }
  }

  async function playPreview() {
    const target = active;
    try {
      await video.play();
      if (!active || video.hidden) video.pause();
    } catch {
      // A hover cannot override a browser's audio activation policy.
      if (active === target) target.querySelector(".playback-status").textContent = "Click the video to enable sound.";
    }
  }

  function show(target, reason) {
    if (target !== active) {
      clear();
      active = target;
      const source = target.querySelector("img, video");
      image.hidden = source.tagName !== "IMG";
      video.hidden = source.tagName !== "VIDEO";
      if (source.tagName === "IMG") {
        image.src = source.src;
        if (image.complete && image.naturalWidth) reveal(image);
      } else {
        video.src = source.src;
        video.currentTime = source.currentTime;
        video.muted = false;
        playPreview();
      }
    }
    if (reason === "pinned" && !video.hidden && video.paused) playPreview();
    mode = reason;
    previewScrollX = window.scrollX;
    previewScrollY = window.scrollY;
    if (target.matches("button, .inline-playback")) target.setAttribute("aria-expanded", "true");
    dismiss.hidden = reason !== "pinned";
  }

  image.addEventListener("load", () => { if (active && !image.hidden) reveal(image); });
  video.addEventListener("loadedmetadata", () => { if (active && !video.hidden) reveal(video); });
  video.addEventListener("error", clear);
  image.addEventListener("error", clear);
  document.addEventListener("pointerover", event => {
    const target = event.target.closest(".inline-image, .inline-playback");
    if (event.pointerType !== "mouse" || !target || target.contains(event.relatedTarget)) return;
    if (target === active && mode === "pinned") return;
    show(target, "pointer");
  });
  document.addEventListener("pointerout", event => {
    if ((mode === "pointer" || !video.hidden) && active?.contains(event.target) && !active.contains(event.relatedTarget)) clear();
  });
  document.addEventListener("focusin", event => {
    const target = event.target.closest(".inline-image, .inline-playback");
    if (target?.matches(":focus-visible")) show(target, "focus");
  });
  document.addEventListener("focusout", event => {
    if (event.target === dismiss && !active?.contains(event.relatedTarget)) {
      clear();
      return;
    }
    if (active?.contains(event.target) && !active.contains(event.relatedTarget) &&
        !(mode === "pinned" && event.relatedTarget === dismiss)) clear();
  });
  document.addEventListener("click", event => {
    const target = event.target.closest("button.inline-image, .inline-playback");
    if (target) {
      if (active === target && mode === "pinned") clear();
      else show(target, "pinned");
    } else if (!event.target.closest(".inline-image, .inline-playback")) clear();
  });
  dismiss.addEventListener("click", () => {
    const previous = active;
    clear();
    previous?.focus({ preventScroll: true });
    clear(); // Focus restoration must not reopen the dismissed preview.
  });
  document.addEventListener("keydown", event => { if (event.key === "Escape") clear(); });
  window.addEventListener("hashchange", clear);
  window.addEventListener("scroll", () => {
    if (window.scrollX !== previewScrollX || window.scrollY !== previewScrollY) clear();
  }, { passive: true });
  window.addEventListener("blur", clear);
  window.addEventListener("resize", clear);
  document.addEventListener("visibilitychange", () => { if (document.hidden) clear(); });
  document.addEventListener("keydown", event => {
    if ((event.key === "Enter" || event.key === " ") && event.target.matches(".inline-playback")) {
      event.preventDefault();
      event.target.click();
    }
  });
  return clear;
}

/* ---------- boot ---------- */

let clearInteractions;
let disposeSections = () => {};
let playback;
let startRevision = 0;

function disposeRender() {
  clearInteractions?.();
  disposeSections();
  playback?.dispose();
}

function render(entries) {
  if (!clearInteractions) clearInteractions = setupInteractions();
  if (!playback) playback = CoursePlayback.setup();
  const position = { x: window.scrollX, y: window.scrollY };
  disposeRender();
  const shown = entries.filter(entry => entry.text !== null || entry.visual !== null);
  document.getElementById("entries").replaceChildren(...shown.map(renderEntry));
  buildNav(shown);
  buildMarkers(shown);
  if (shown.length) setCurrent(shown[0], 0);
  else {
    document.getElementById("current").textContent = "";
    document.getElementById("current-authors").textContent = "";
  }
  disposeSections = watchSections(shown);
  playback.mount();
  window.scrollTo(position.x, position.y);
}

async function loadEntries() {
  const response = await fetch("entries.json");
  if (!response.ok) throw new Error("Could not load entries.json.");
  const folders = await response.json();
  if (!Array.isArray(folders)) throw new Error("Invalid entries.json.");
  return Promise.all(folders.map(loadEntry));
}

async function start(options = {}) {
  const revision = ++startRevision;
  const entries = await (options.loadEntries || loadEntries)();
  if (revision !== startRevision) return;
  resourceResolver = options.resolveResource || null;
  render(entries);
}

globalThis.CourseSite = {
  start, render,
  dispose() { startRevision++; disposeRender(); resourceResolver = null; },
};
