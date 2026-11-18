((root) => {
  const CourseMarkup = typeof module !== 'undefined' && module.exports ? require('./markup.js') : root.CourseMarkup;
  const CourseMediaTable = typeof module !== 'undefined' && module.exports ? require('./media-table.js') : root.CourseMediaTable;
  const pad = (n) => String(n).padStart(2, "0");
  const { parseFrontmatter, renderMarkdown, escapeHtml: esc } = CourseMarkup;

  /* A URL with no scheme, no leading "/" and no leading "#" is taken to be
     relative to the Entry's folder, so students can keep extra assets there. */
  const isLocal = url => !/^([a-z][a-z0-9+.-]*:|\/|#)/i.test(url);

  function resolveUrl(url, folder) {
    return isLocal(url) ? `${folder}/${url}` : url;
  }

  function safeUrl(url, folder, kind, resourceResolver = null, baseURI = root.document?.baseURI) {
    if (/[\u0000-\u001f\u007f\\]/.test(url)) return null;
    if (isLocal(url) && resourceResolver) {
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
      const parsed = new URL(resolved, baseURI);
      const allowed = kind === "link"
        ? ["http:", "https:", "mailto:"]
        : ["http:", "https:"];
      return allowed.includes(parsed.protocol) ? resolved : null;
    } catch {
      return null;
    }
  }

  /* One visible mark for a name or file that is not in the group (yet). */
  function missingMark(name) {
    const mark = document.createElement("span");
    mark.className = "annotation annotation-missing";
    const text = document.createElement("span");
    text.className = "annotation-content annotation-text";
    text.textContent = `missing: ${name}`;
    mark.append(text);
    return mark;
  }

  /* An unknown [[name]] stays literal text through Markdown; mark it in text
     nodes only, so attributes, code and the names' characters stay intact. */
  function markMissingNames(textEl) {
    const walker = document.createTreeWalker(textEl, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (node.data.includes("[[") && !node.parentElement.closest("code, pre")) nodes.push(node);
    }
    for (const node of nodes) {
      const parts = CourseMediaTable.splitNames(node.data);
      if (parts.every(part => typeof part === "string")) continue;
      node.replaceWith(...parts.map(part => typeof part === "string" ? part : missingMark(part.name)));
    }
  }

  /* The site knows each entry's files from entries.json; a local path outside
     them is missing. The editor preview supplies its own resolver. */
  function siteResolver(entries) {
    const files = new Map(entries.map(entry => [entry.folder, entry.files]));
    return (path, kind, folder) => !files.get(folder) || files.get(folder).has(path) ? undefined : false;
  }

  const COMMIT_TIME = new Intl.DateTimeFormat("en-GB", {
    day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: "Europe/Zurich",
  });

  /* Which commit is online, or which group commit was not published and why.
     Course-team pages identify the pinned course repository checkout. */
  function statusLine({ online, problem }) {
    if (problem) {
      const reason = problem.message.replace(/[.\s]+$/, "");
      return `Your commit ${problem.commit} was not published: ${reason}.` + (online ? ` The page shows ${online.commit}.` : "");
    }
    if (!online) return null;
    const time = Object.fromEntries(COMMIT_TIME.formatToParts(new Date(online.committed_at)).map(part => [part.type, part.value]));
    return `Online: commit ${online.commit}, ${time.day} ${time.month} ${time.hour}:${time.minute}`;
  }

  function entryIdentifier(folder) {
    if (folder === "materialities-of-ai") return "M-AI";
    const group = /^group-(\d+)$/.exec(folder);
    return group ? `G-${pad(Number(group[1]))}` : folder;
  }

  function withoutGroupPrefix(value, folder) {
    const text = value.trim().replace(/\s+/g, " ");
    const group = /^group-(\d+)$/.exec(folder);
    const prefix = /^(?:group\s*|g\s*-\s*)(\d+)(?:\s*[:–—-]\s*|\s+|$)/i.exec(text);
    return group && prefix && Number(group[1]) === Number(prefix[1])
      ? text.slice(prefix[0].length).trim() : text;
  }

  function commitTime(online) {
    if (!online?.committed_at || Number.isNaN(Date.parse(online.committed_at))) return "—";
    return COMMIT_TIME.format(new Date(online.committed_at)).replace(",", "");
  }

  function describe(entry) {
    const { meta, body } = parseFrontmatter(entry.text || "");
    return { ...entry, body,
      title: withoutGroupPrefix(meta.title || entry.folder, entry.folder) || "Untitled",
      authors: withoutGroupPrefix(meta.authors || "", entry.folder),
    };
  }

  function render(entry, resourceResolver) {
    const { body } = entry;

    const section = document.createElement("section");
    section.className = "entry";
    section.id = entry.folder;

    let html = `<header class="entry-caption">`;
    html += `<p class="entry-id">${esc(entryIdentifier(entry.folder))}</p>`;
    html += `<h2>${esc(entry.title)}</h2>`;
    html += `<p class="entry-authors">${esc(entry.authors)}</p>`;
    html += `<p class="entry-commit" title="${esc(statusLine(entry) || 'No published commit metadata')}">${esc(entry.online?.commit || '—')}</p>`;
    html += `<time class="entry-time"${entry.online?.committed_at ? ` datetime="${esc(entry.online.committed_at)}"` : ''} title="Commit time · Europe/Zurich">${esc(commitTime(entry.online))}</time>`;
    html += `</header>`;
    let table = null;
    try { table = entry.table ? CourseMediaTable.read(entry.table) : null; }
    catch { /* The importer rejects invalid tables; its names then show as missing. */ }
    html += `<div class="entry-text">`;
    if (entry.text !== null) html += renderMarkdown(body, table);
    html += `</div>`;
    const visual = entry.visual && safeUrl(entry.visual === `${entry.folder}/visual.svg` ? "visual.svg" : entry.visual, entry.folder, "media", resourceResolver);
    if (visual) {
      html +=
        `<figure class="entry-visual">` +
        `<button type="button" class="visual-open" aria-label="Open visualisation: ${esc(entry.title)}">` +
        `<img src="${esc(visual)}" width="1920" height="1080" alt="${esc(entry.title)} — visualisation"></button>` +
        `</figure>`;
    }

    const template = document.createElement("template");
    template.innerHTML = html;
    const status = statusLine(entry);
    if (entry.problem && status) {
      const line = document.createElement("p");
      line.className = "entry-authors entry-status";
      line.textContent = status;
      template.content.querySelector(".entry-caption").append(line);
    }

    prepareResources(template.content.querySelector(".entry-text"), table, entry.folder, resourceResolver);
    section.append(template.content);

    return section;
  }

  function prepareInlineImages(textEl) {
    for (const image of textEl.querySelectorAll("img[src]")) {
      const content = image.closest(".annotation-content");
      let target;
      if (content) {
        const label = content.parentElement.querySelector(".annotation-toggle");
        label.querySelector(".annotation-marker")?.remove();
        image.alt = label.textContent.trim();
        target = image.closest("a[href]") || document.createElement("a");
        if (!target.hasAttribute("href")) target.href = image.src;
        target.target = "_blank";
        target.rel = "noopener noreferrer";
        target.setAttribute("aria-label", `Open image source: ${image.alt}`);
        target.replaceChildren(image);
        label.replaceWith(target);
        content.remove();
      } else {
        // Linked Markdown images keep their link and native activation.
        target = image.closest("a[href]");
        if (!target) {
          target = document.createElement("a");
          target.href = image.src;
          target.target = "_blank";
          target.rel = "noopener noreferrer";
          target.setAttribute("aria-label", `Open image source: ${image.alt || "supporting image"}`);
          image.replaceWith(target);
          target.append(image);
        }
      }
      target.classList.add("inline-image");

    }
  }

  function prepareResources(textEl, table, folder, resourceResolver) {
    markMissingNames(textEl);
    const sources = CourseMediaTable.sources(table);
    for (const el of textEl.querySelectorAll("[src]")) {
      const src = el.getAttribute("src");
      if (src && resourceResolver && isLocal(src) && !safeUrl(src, folder, "media", resourceResolver)) {
        (el.closest(".annotation") || el).replaceWith(missingMark(src.replace(/^\.\//, "").split(/[?#]/)[0]));
        continue;
      }
      // A Markdown image links to its media.csv source, like [[name]] media.
      const source = src && sources.get(src.replace(/^\.\//, ""));
      if (source && el.tagName === "IMG" && !el.closest("a[href]")) {
        const link = document.createElement("a");
        link.href = source;
        el.replaceWith(link);
        link.append(el);
      }
      const safe = src && safeUrl(src, folder, "media", resourceResolver);
      if (safe) el.setAttribute("src", safe);
      else el.removeAttribute("src");
    }
    for (const a of textEl.querySelectorAll("a[href]")) {
      const href = a.getAttribute("href");
      const safe = href && safeUrl(href, folder, "link", resourceResolver);
      if (safe) a.setAttribute("href", safe);
      else a.removeAttribute("href");
      if (safe) {
        a.target = "_blank";
        a.rel = "noopener noreferrer";
      }
    }
    prepareInlineImages(textEl);
    CoursePlayback.prepare(textEl);
  }

  const api = { describe, render, safeUrl, siteResolver, statusLine, entryIdentifier, withoutGroupPrefix, commitTime };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CourseEntry = api;
})(globalThis);
