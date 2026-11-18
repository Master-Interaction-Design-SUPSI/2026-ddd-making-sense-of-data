((root) => {
  function create() {
    const listeners = new AbortController();
    const listen = (target, type, handler, options = {}) => target.addEventListener(type, handler, { ...options, signal: listeners.signal });
    const preview = document.getElementById("image-preview");
    const image = preview.querySelector("img");
    const video = preview.querySelector("video");
    const dismiss = document.getElementById("image-preview-close");
    const audio = document.createElement("audio");
    audio.preload = "none";
    audio.hidden = true;
    audio.dataset.hoverAudio = "";
    preview.append(audio);
    const enableSound = document.createElement("button");
    enableSound.type = "button";
    enableSound.className = "enable-hover-sound";
    enableSound.textContent = "Enable sound";
    enableSound.hidden = true;
    document.body.append(enableSound);
    let blockedTarget = null;
    let playRevision = 0;
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
      image.removeAttribute("src");
      playRevision++;
      video.pause();
      video.removeAttribute("src");
      video.load();
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
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
      });
      preview.hidden = false;
    }

    async function playPreview() {
      const target = active;
      const media = target?.matches(".inline-audio") ? audio : video;
      const revision = ++playRevision;
      media.muted = false;
      media.defaultMuted = false;
      try {
        await media.play();
        if (revision !== playRevision) return;
        if (active !== target) { media.pause(); return; }
        enableSound.hidden = true;
        blockedTarget = null;
      } catch (error) {
        if (revision !== playRevision || active !== target) return;
        if (error.name === "NotAllowedError") {
          blockedTarget = target;
          enableSound.hidden = false;
        } else {
          const status = target.querySelector(".playback-status");
          if (status) status.textContent = "Could not play this media.";
        }
      }
    }

    listen(enableSound, "click", () => {
      const target = active?.matches(".inline-audio, .inline-playback") ? active : blockedTarget;
      if (!target?.isConnected) { enableSound.hidden = true; return; }
      // Retry the same shared media element inside this user gesture.
      if (active === target) playPreview();
      else show(target, "pinned");
    });

    function prepareImage(source) {
      image.src = source.src;
      if (image.complete && image.naturalWidth) reveal(image);
    }

    function prepareVideo(source, reason) {
      video.src = source.src;
      video.currentTime = source.currentTime;
      video.muted = false;
      video.defaultMuted = false;
      video.loop = true;
      if (!matchMedia("(prefers-reduced-motion: reduce)").matches || reason === "pinned") playPreview();
    }

    function prepareTarget(target, reason) {
      const isAudio = target.matches(".inline-audio");
      const source = isAudio ? null : target.querySelector("img, video");
      image.hidden = source?.tagName !== "IMG";
      video.hidden = source?.tagName !== "VIDEO";
      if (isAudio) {
        audio.src = target.dataset.audioSrc;
        playPreview();
        return;
      }
      if (source.tagName === "IMG") prepareImage(source);
      else prepareVideo(source, reason);
    }

    function show(target, reason) {
      const changed = target !== active;
      if (changed) {
        clear();
        active = target;
        prepareTarget(target, reason);
      }
      if (!changed && reason === "pinned" && !video.hidden && video.paused) playPreview();
      mode = reason;
      previewScrollX = window.scrollX;
      previewScrollY = window.scrollY;
      if (target.matches("button, .inline-playback")) target.setAttribute("aria-expanded", "true");
      dismiss.hidden = reason !== "pinned";
    }

    listen(image, "load", () => { if (active && !image.hidden) reveal(image); });
    listen(video, "loadedmetadata", () => { if (active && !video.hidden) reveal(video); });
    listen(video, "error", clear);
    listen(image, "error", clear);
    listen(document, "pointerover", event => {
      const target = event.target.closest(".inline-image, .inline-playback, .inline-audio");
      if (event.pointerType !== "mouse" || !target || target.contains(event.relatedTarget)) return;
      if (target === active && mode === "pinned") return;
      show(target, "pointer");
    });
    listen(document, "pointerout", event => {
      if ((mode === "pointer" || !video.hidden || active?.matches(".inline-audio")) && active?.contains(event.target) && !active.contains(event.relatedTarget)) clear();
    });
    listen(document, "focusin", event => {
      const target = event.target.closest(".inline-image, .inline-playback, .inline-audio");
      if (target?.matches(":focus-visible")) show(target, "focus");
    });
    listen(document, "focusout", event => {
      if (event.target === dismiss && !active?.contains(event.relatedTarget)) {
        clear();
        return;
      }
      if (active?.contains(event.target) && !active.contains(event.relatedTarget) &&
          !(mode === "pinned" && event.relatedTarget === dismiss)) clear();
    });
    listen(document, "click", event => {
      if (event.target.closest(".enable-hover-sound")) return;
      const sourceLink = event.target.closest("a.inline-image, a.inline-playback, a.inline-audio");
      if (sourceLink) { clear(); return; }
      if (!event.target.closest(".inline-image, .inline-playback, .inline-audio")) clear();
    });
    listen(dismiss, "click", () => {
      const previous = active;
      clear();
      previous?.focus({ preventScroll: true });
      clear(); // Focus restoration must not reopen the dismissed preview.
    });
    listen(document, "keydown", event => { if (event.key === "Escape") clear(); });
    listen(window, "hashchange", clear);
    listen(window, "scroll", () => {
      if (window.scrollX !== previewScrollX || window.scrollY !== previewScrollY) clear();
    }, { passive: true });
    listen(window, "blur", clear);
    listen(window, "resize", clear);
    listen(document, "visibilitychange", () => { if (document.hidden) clear(); });
    function reset() {
      clear();
      blockedTarget = null;
      enableSound.hidden = true;
    }

    return { clear: reset, dispose() { reset(); listeners.abort(); audio.remove(); enableSound.remove(); } };
  }

  root.CourseMediaPreview = { create };
})(globalThis);
