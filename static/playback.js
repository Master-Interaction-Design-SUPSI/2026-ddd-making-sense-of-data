((root) => {
  function createVideoController(video, render, allowAutoplay = true) {
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
      if (!active || pending) return;
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
        status = error?.name === "NotAllowedError" ? "Press Play to start." : "Could not play this video.";
      }
      update();
    }

    function setActive(next) {
      if (active === next) return;
      active = next;
      if (!active) pause();
      else if (!userPaused && !autoplayBlocked && video.muted && !video.ended) start(true);
    }

    function toggle() {
      if (!video.paused || pending) {
        userPaused = true;
        pause();
      } else {
        userPaused = false;
        start(false);
      }
    }

    video.addEventListener("play", () => {
      if (!active) video.pause();
      update();
    });
    video.addEventListener("pause", update);
    video.addEventListener("volumechange", update);
    video.addEventListener("ended", () => { userPaused = true; update(); });
    video.addEventListener("error", () => {
      autoplayBlocked = true;
      status = "Could not load this video.";
      pause();
    });
    update();
    return { setActive, toggle, toggleSound: () => { video.muted = !video.muted; update(); } };
  }

  function prepare(textEl) {
    for (const media of textEl.querySelectorAll("audio[src], video[src]")) {
      const content = media.closest(".annotation-content");
      const label = content?.parentElement.querySelector(".annotation-toggle");
      const name = label ? [...label.childNodes].filter(node => !node.classList?.contains("annotation-marker"))
        .map(node => node.textContent).join("").trim() : "Inline media";
      media.setAttribute("aria-label", name);
      if (content) {
        label.replaceWith(media);
        content.remove();
      }
      if (media.tagName === "AUDIO") {
        media.classList.add("inline-audio");
        continue;
      }
      media.controls = false;
      media.muted = true;
      media.defaultMuted = true;
      media.playsInline = true;
      const wrapper = document.createElement("span");
      wrapper.className = "inline-playback";
      media.replaceWith(wrapper);
      wrapper.append(media);
      const controls = document.createElement("span");
      controls.className = "playback-controls";
      for (const [action, text] of [["play", "Play"], ["sound", "Sound"]]) {
        const button = document.createElement("button");
        button.type = "button";
        button.dataset.action = action;
        button.textContent = text;
        button.setAttribute("aria-label", `${text}: ${name}`);
        controls.append(button);
      }
      const status = document.createElement("span");
      status.className = "playback-status";
      status.setAttribute("role", "status");
      wrapper.append(controls, status);
    }
    // External players are only loaded when their annotation is revealed.
    for (const player of textEl.querySelectorAll("iframe[src]")) {
      player.dataset.playerSrc = player.getAttribute("src");
      player.removeAttribute("src");
    }
  }

  function setup() {
    const videos = new Map();
    const allowAutoplay = !matchMedia("(prefers-reduced-motion: reduce)").matches;
    for (const wrapper of document.querySelectorAll(".inline-playback")) {
      const video = wrapper.querySelector("video");
      const play = wrapper.querySelector('[data-action="play"]');
      const sound = wrapper.querySelector('[data-action="sound"]');
      const name = video.getAttribute("aria-label");
      const controller = createVideoController(video, state => {
        const action = state.playing || state.pending ? "Pause" : "Play";
        play.textContent = action;
        play.setAttribute("aria-label", `${action}: ${name}`);
        sound.textContent = state.muted ? "Sound" : "Mute";
        sound.setAttribute("aria-label", `${state.muted ? "Enable sound" : "Mute"}: ${name}`);
        sound.setAttribute("aria-pressed", String(!state.muted));
        wrapper.querySelector(".playback-status").textContent = state.status;
      }, allowAutoplay);
      videos.set(video, controller);
      play.addEventListener("click", () => { controller.setActive(isActive(video)); controller.toggle(); });
      sound.addEventListener("click", controller.toggleSound);
    }
    const media = [...document.querySelectorAll(".entry-text audio, .entry-text video, .entry-text iframe[data-player-src]")];

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

    const observer = new IntersectionObserver(refresh, { threshold: [0, 0.5, 1], rootMargin: "-40px 0px 0px" });
    media.forEach(element => observer.observe(element));
    document.addEventListener("visibilitychange", () => { if (document.hidden) stopAll(); else refresh(); });
    window.addEventListener("hashchange", stopAll);
    window.addEventListener("pagehide", stopAll);
    window.addEventListener("pageshow", refresh);
    document.addEventListener("click", () => queueMicrotask(refresh));
    document.addEventListener("keydown", event => { if (event.key === "Escape") queueMicrotask(refresh); });
    refresh();
  }

  const api = { createVideoController, prepare, setup };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.CoursePlayback = api;
})(globalThis);
