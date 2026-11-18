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

const { statusLine, entryIdentifier, commitTime } = CourseEntry;

/* ---------- loading ---------- */

async function loadEntry({ slug, files, online, problem }) {
  const present = new Set(files);
  const read = name => present.has(name) ? fetch(`${slug}/${name}`).then((r) => (r.ok ? r.text() : null)).catch(() => null) : null;
  const [text, table] = await Promise.all([read("text.md"), read("media.csv")]);
  return { folder: slug, files: present, text, table, visual: present.has("visual.svg") ? `${slug}/visual.svg` : null,
           online, problem };
}

function buildNav(entries) {
  const nav = document.getElementById("nav");
  nav.replaceChildren();
  entries.forEach((entry) => {
    const a = document.createElement("a");
    a.href = `#${entry.folder}`;
    a.dataset.folder = entry.folder;
    const idx = document.createElement("span");
    idx.className = "nav-index";
    idx.textContent = entryIdentifier(entry.folder);
    const title = entry.title;
    const fullLabel = `${idx.textContent} ${title}`;
    a.title = fullLabel;
    a.setAttribute("aria-label", fullLabel);
    a.append(idx);
    nav.appendChild(a);
  });
}

/* The bar readout: the Entry at the top of the reading area. */
function setCurrent(entry) {
  const title = document.getElementById("current");
  if (title) title.textContent = entry.title;
  const authors = document.getElementById("current-authors");
  if (authors) authors.textContent = entry.authors || "";
  const identifier = document.getElementById("current-id");
  if (identifier) identifier.textContent = entry.folder ? entryIdentifier(entry.folder) : "";
  const commit = document.getElementById("current-commit");
  if (commit) {
    commit.textContent = entry.folder ? entry.online?.commit || "—" : "";
    commit.title = entry.folder ? statusLine(entry) || "No published commit metadata" : "";
  }
  const time = document.getElementById("current-time");
  if (time) {
    time.textContent = entry.folder ? commitTime(entry.online) : "";
    if (entry.online?.committed_at) time.dateTime = entry.online.committed_at;
    else time.removeAttribute("datetime");
    time.title = "Commit time · Europe/Zurich";
  }
  if (title) title.title = entry.title;
  if (authors) authors.title = entry.authors || "";
}

function watchSections(entries) {
  const links = new Map(
    [...document.querySelectorAll("#nav a")].map((a) => [a.dataset.folder, a])
  );
  const observer = new IntersectionObserver(
    (hits) => {
      for (const hit of hits) {
        links.get(hit.target.id)?.classList.toggle("active", hit.isIntersecting);
      }
    },
    { rootMargin: "-20% 0px -55% 0px" }
  );
  const sections = [...document.querySelectorAll(".entry")];
  sections.forEach((s) => observer.observe(s));

  /* One copy of the metadata at a time: the readout stays blank while the
     Entry's own caption is in view and takes over once it slides under the
     bar. Shared columns make the hand-off read as the caption docking. */
  const bar = document.querySelector(".site-bar");
  const readout = bar.querySelector(".bar-metadata");
  let frame = 0;
  let current = -1;
  const update = () => {
    frame = 0;
    const edge = bar.getBoundingClientRect().bottom;
    const i = sections.findIndex((s) => s.getBoundingClientRect().bottom > edge);
    const caption = sections[i]?.querySelector(".entry-caption");
    const captionInView = caption?.getClientRects().length > 0 && caption.getBoundingClientRect().bottom > edge;
    const docked = i >= 0 && !captionInView;
    if (docked && i !== current) {
      setCurrent(entries[i]);
      current = i;
    }
    readout.classList.toggle("docked", docked);
  };
  const schedule = () => {
    if (!frame) frame = requestAnimationFrame(update);
  };
  update();
  window.addEventListener("scroll", schedule, { passive: true });
  window.addEventListener("resize", schedule);
  return () => {
    observer.disconnect();
    window.removeEventListener("scroll", schedule);
    window.removeEventListener("resize", schedule);
    cancelAnimationFrame(frame);
  };
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
  const listeners = new AbortController();
  const listen = (target, type, handler, options = {}) => target.addEventListener(type, handler, { ...options, signal: listeners.signal });
  const preview = CourseMediaPreview.create();
  const lightbox = CourseLightbox.create(document.getElementById("lightbox"), open => {
    document.body.classList.toggle("lightbox-open", open);
    if (open) preview.clear();
    playback?.refresh();
  });

  function toggleAnnotation(toggle, event) {
    const content = toggle.parentElement.querySelector(".annotation-content");
    const open = content.hidden;
    content.hidden = !open;
    toggle.setAttribute("aria-expanded", String(open));
    toggle.parentElement.classList.toggle("open", open);
    if (!open) return;
    const media = content.querySelector("iframe");
    if (media) centerAnnotationMedia(media, event.clientX || toggleCenterX(toggle));
  }

  listen(document, "click", event => {
    const toggle = event.target.closest(".annotation-toggle");
    if (toggle) { toggleAnnotation(toggle, event); return; }
    const visual = event.target.closest(".visual-open");
    if (visual) lightbox.open(visual);
  });

  listen(window, "resize", () => {
    for (const media of document.querySelectorAll(
      ".annotation.open .annotation-content iframe"
    )) {
      const toggle = media.closest(".annotation").querySelector(".annotation-toggle");
      centerAnnotationMedia(media, toggleCenterX(toggle));
    }
  });

  listen(document, "keydown", (event) => {
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

  return () => { listeners.abort(); preview.dispose(); lightbox.dispose(); };
}

/* ---------- boot ---------- */

let clearInteractions;
let disposeSections = () => {};
let playback;
let startRevision = 0;

function disposeRender() {
  clearInteractions?.();
  clearInteractions = null;
  disposeSections();
  playback?.dispose();
}

function render(entries, resolveResource = CourseEntry.siteResolver(entries)) {
  const position = { x: window.scrollX, y: window.scrollY };
  disposeRender();
  clearInteractions = setupInteractions();
  if (!playback) playback = CoursePlayback.setup();
  const shown = entries.filter(entry => entry.text !== null || entry.visual !== null || entry.problem).map(CourseEntry.describe);
  document.getElementById("entries").replaceChildren(...shown.map(entry => CourseEntry.render(entry, resolveResource)));
  buildNav(shown);
  disposeSections = watchSections(shown);
  playback.mount();
  window.scrollTo(position.x, position.y);
}

async function loadEntries() {
  const response = await fetch("entries.json");
  if (!response.ok) throw new Error("Could not load entries.json.");
  const items = await response.json();
  if (!Array.isArray(items) || !items.every(item => typeof item?.slug === "string" && Array.isArray(item.files))) {
    throw new Error("Invalid entries.json.");
  }
  return Promise.all(items.map(loadEntry));
}

async function start(options = {}) {
  const revision = ++startRevision;
  const entries = await (options.loadEntries || loadEntries)();
  if (revision !== startRevision) return;
  render(entries, options.resolveResource || CourseEntry.siteResolver(entries));
}

globalThis.CourseSite = {
  start, render,
  dispose() { startRevision++; disposeRender(); },
};
