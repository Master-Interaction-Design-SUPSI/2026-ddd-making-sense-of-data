((root) => {
  function createVideoController(video, render, allowAutoplay = true) {
    const listeners = new AbortController();
    const listen = (type, handler) => video.addEventListener(type, handler, { signal: listeners.signal });
    let disposed = false;
    let active = false;
    let userPaused = false;
    let autoplayBlocked = !allowAutoplay;
    let pending = false;
    let generation = 0;
    let status = "";

    const update = () => render({ playing: !video.paused, pending, muted: video.muted, status });

    function pause() {
      generation++;
      pending = false;
      video.pause();
      update();
    }

    async function start(automatic) {
      if (disposed || !active || pending) return;
      const request = ++generation;
      pending = true;
      status = "";
      if (automatic) video.muted = true;
      update();
      try {
        await video.play();
        if (request !== generation) {
          if (!active || userPaused) video.pause();
          return;
        }
        pending = false;
        if (!active) video.pause();
      } catch (error) {
        if (request !== generation) return;
        pending = false;
        if (automatic) autoplayBlocked = true;
        status = error?.name === "NotAllowedError" ? "Automatic playback is unavailable." : "Could not play this video.";
      }
      update();
    }

    function setActive(next) {
      if (disposed || active === next) return;
      active = next;
      if (!active) pause();
      else if (!userPaused && !autoplayBlocked && video.muted && !video.ended) start(true);
    }

    function toggle() {
      if (disposed) return;
      if (!video.paused || pending) {
        userPaused = true;
        pause();
      } else {
        userPaused = false;
        start(false);
      }
    }

    listen("play", () => {
      if (!active) video.pause();
      update();
    });
    listen("pause", update);
    listen("volumechange", update);
    listen("ended", () => { userPaused = true; update(); });
    listen("error", () => {
      autoplayBlocked = true;
      status = "Could not load this video.";
      pause();
    });
    update();
    return {
      setActive, toggle,
      toggleSound: () => { if (!disposed) { video.muted = !video.muted; update(); } },
      dispose: () => { active = false; disposed = true; pause(); listeners.abort(); },
    };
  }

  function prepare(textEl) {
    for (const media of textEl.querySelectorAll("audio[src], video[src]")) {
      const content = media.closest(".annotation-content");
      const label = content?.parentElement.querySelector(".annotation-toggle");
      const name = label ? [...label.childNodes].filter(node => !node.classList?.contains("annotation-marker"))
        .map(node => node.textContent).join("").trim() : "Inline media";
      media.setAttribute("aria-label", name);
      const sourceLink = media.closest("a[href]");
      const wrapper = sourceLink || document.createElement("a");
      if (!sourceLink) wrapper.href = media.src;
      wrapper.target = "_blank";
      wrapper.rel = "noopener noreferrer";
      if (media.tagName === "AUDIO") {
        wrapper.className = "inline-audio";
        wrapper.dataset.file = media.getAttribute("src");
        wrapper.setAttribute("aria-label", `Open audio source: ${name}`);
        const icon = document.createElement("span");
        icon.textContent = "▶";
        icon.setAttribute("aria-hidden", "true");
        if (!content && !sourceLink) media.replaceWith(wrapper);
        wrapper.replaceChildren(icon, ` ${name}`);
        if (content) { label.replaceWith(wrapper); content.remove(); }
        continue;
      }
      media.controls = false;
      media.muted = true;
      media.defaultMuted = true;
      media.loop = true;
      media.playsInline = true;
      wrapper.className = "inline-playback";
      wrapper.setAttribute("aria-label", `Open video source: ${name}`);
      if (content) { label.replaceWith(wrapper); content.remove(); }
      else if (!sourceLink) media.replaceWith(wrapper);
      wrapper.replaceChildren(media);
      const status = document.createElement("span");
      status.className = "playback-status";
      status.setAttribute("role", "status");
      wrapper.append(status);
    }
    // External players are only loaded when their annotation is revealed.
    for (const player of textEl.querySelectorAll("iframe[src]")) {
      player.dataset.playerSrc = player.getAttribute("src");
      player.removeAttribute("src");
    }
  }

  let mounted;
  function setup() {
    if (mounted) return mounted;
    const videos = new Map();
    let media = [];
    let observer;
    const allowAutoplay = !matchMedia("(prefers-reduced-motion: reduce)").matches;
    function mount() {
      dispose();
      for (const wrapper of document.querySelectorAll(".inline-playback")) {
        const video = wrapper.querySelector("video");
        videos.set(video, createVideoController(video, state => {
          wrapper.querySelector(".playback-status").textContent = state.status;
        }, allowAutoplay));
      }
      media = [...document.querySelectorAll(".entry-text audio, .entry-text video, .entry-text iframe[data-player-src]")];
      observer = new IntersectionObserver(refresh, { threshold: [0, 0.5, 1], rootMargin: "-40px 0px 0px" });
      media.forEach(element => observer.observe(element));
      refresh();
    }

    function dispose() {
      stopAll();
      observer?.disconnect();
      videos.forEach(controller => controller.dispose());
      videos.clear();
      media = [];
    }

    function isActive(element) {
      if (document.hidden || !document.getElementById("lightbox").hidden || element.closest("[hidden]")) return false;
      const rect = element.getBoundingClientRect();
      if (!rect.width || !rect.height) return false;
      const width = Math.max(0, Math.min(rect.right, innerWidth) - Math.max(rect.left, 0));
      const height = Math.max(0, Math.min(rect.bottom, innerHeight) - Math.max(rect.top, 40));
      return width * height / (rect.width * rect.height) >= 0.5;
    }

    function refresh() {
      for (const element of media) {
        const active = isActive(element);
        if (videos.has(element)) videos.get(element).setActive(active);
        else if (element.tagName === "AUDIO") {
          if (!active) element.pause();
        } else if (active && !element.hasAttribute("src")) element.src = element.dataset.playerSrc;
        else if (!active) element.removeAttribute("src");
      }
    }

    function stopAll() {
      for (const element of media) {
        if (videos.has(element)) videos.get(element).setActive(false);
        else if (element.tagName === "AUDIO") element.pause();
        else element.removeAttribute("src");
      }
    }

    document.addEventListener("visibilitychange", () => { if (document.hidden) stopAll(); else refresh(); });
    window.addEventListener("hashchange", stopAll);
    window.addEventListener("pagehide", stopAll);
    window.addEventListener("pageshow", refresh);
    document.addEventListener("click", () => queueMicrotask(refresh));
    document.addEventListener("keydown", event => { if (event.key === "Escape") queueMicrotask(refresh); });
    mounted = { mount, dispose };
    return mounted;
  }

  const api = { createVideoController, prepare, setup };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.CoursePlayback = api;
})(globalThis);
