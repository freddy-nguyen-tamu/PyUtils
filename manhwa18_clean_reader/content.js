(() => {
  "use strict";

  if (window.__MANHWA18_CLEAN_READER__) return;
  window.__MANHWA18_CLEAN_READER__ = true;

  const DEFAULTS = {
    autoScrollEnabled: false,
    autoScrollSpeed: 70,   // pixels per second
    keyScrollStep: 140     // pixels per Up/Down press
  };

  let settings = { ...DEFAULTS };
  let rafId = 0;
  let lastFrame = 0;
  let nextHref = null;
  let prevHref = null;
  let navigating = false;

  const clamp = (n, lo, hi) => Math.max(lo, Math.min(hi, n));

  function absoluteUrl(value) {
    if (!value) return null;
    try { return new URL(value, location.href).href; }
    catch { return null; }
  }

  function findChapterHref(direction) {
    const selectors = direction === "next"
      ? [
          "a.navi-change-chapter-btn-next[href]",
          'a[rel="next"][href]',
          'a[href*="/chapter-"][class*="next"]'
        ]
      : [
          "a.navi-change-chapter-btn-prev[href]",
          'a[rel="prev"][href]',
          'a[href*="/chapter-"][class*="prev"]'
        ];

    for (const selector of selectors) {
      const a = document.querySelector(selector);
      if (a?.getAttribute("href")) return absoluteUrl(a.getAttribute("href"));
    }

    // Fallback: derive the adjacent chapter from the site's chapter selector.
    const select = document.querySelector("select.navi-change-chapter");
    if (select) {
      const options = [...select.options];
      const current = options.findIndex(o => o.selected);
      if (current >= 0) {
        // This site lists chapters descending, so previous chapter is +1,
        // next chapter is -1 relative to the selected option.
        const target = direction === "next" ? options[current - 1] : options[current + 1];
        const slug = target?.dataset?.c;
        if (slug) {
          const base = location.pathname.replace(/\/chapter-[^/]+\/?$/, "");
          return absoluteUrl(`${base}/${slug}`);
        }
      }
    }
    return null;
  }

  function getReaderImages() {
    const preferred = [
      ...document.querySelectorAll(".read-content img")
    ];

    const source = preferred.length
      ? preferred
      : [...document.images].filter(img => {
          const src = img.currentSrc || img.src || img.dataset?.src || "";
          return /\/uploads\/.+\.(?:jpe?g|png|webp)(?:[?#]|$)/i.test(src);
        });

    const seen = new Set();
    return source.filter(img => {
      const src = absoluteUrl(
        img.currentSrc ||
        img.getAttribute("src") ||
        img.dataset?.src ||
        img.dataset?.lazySrc ||
        img.dataset?.original
      );
      if (!src || seen.has(src)) return false;
      seen.add(src);
      return true;
    }).map((img, i) => ({
      src: absoluteUrl(
        img.currentSrc ||
        img.getAttribute("src") ||
        img.dataset?.src ||
        img.dataset?.lazySrc ||
        img.dataset?.original
      ),
      alt: img.alt || `Page ${i + 1}`
    }));
  }

  function installReader(images) {
    const html = document.documentElement;
    const body = document.body;

    html.style.cssText = `
      margin: 0 !important;
      padding: 0 !important;
      background: #111 !important;
      overflow-x: hidden !important;
      overflow-y: auto !important;
      scroll-behavior: auto !important;
      overscroll-behavior: auto !important;
      height: auto !important;
      min-height: 100% !important;
    `;

    body.replaceChildren();
    body.style.cssText = `
      margin: 0 !important;
      padding: 0 !important;
      width: 100% !important;
      min-height: 100vh !important;
      background: #111 !important;
      overflow: visible !important;
      position: static !important;
    `;

    const reader = document.createElement("main");
    reader.id = "__clean_reader__";
    reader.style.cssText = `
      width: 100% !important;
      margin: 0 !important;
      padding: 0 !important;
      display: flex !important;
      flex-direction: column !important;
      align-items: center !important;
      justify-content: flex-start !important;
      gap: 0 !important;
      background: #111 !important;
    `;

    const frag = document.createDocumentFragment();

    images.forEach(({src, alt}, index) => {
      const img = document.createElement("img");
      img.src = src;
      img.alt = alt;
      img.decoding = "async";
      img.draggable = false;

      // Load the first pages eagerly so the reader appears immediately,
      // then let the browser manage the rest efficiently.
      img.loading = index < 4 ? "eager" : "lazy";
      if (index === 0) img.fetchPriority = "high";

      img.style.cssText = `
        display: block !important;
        width: auto !important;
        max-width: min(100vw, 1100px) !important;
        height: auto !important;
        margin: 0 auto !important;
        padding: 0 !important;
        border: 0 !important;
        outline: 0 !important;
        flex: 0 0 auto !important;
      `;
      frag.appendChild(img);
    });

    reader.appendChild(frag);
    body.appendChild(reader);
  }

  function isEditableTarget(target) {
    return target instanceof HTMLElement &&
      (target.isContentEditable ||
       /^(INPUT|TEXTAREA|SELECT|BUTTON)$/.test(target.tagName));
  }

  function atBottom() {
    const el = document.scrollingElement || document.documentElement;
    return el.scrollTop + el.clientHeight >= el.scrollHeight - 3;
  }

  function go(href) {
    if (!href || navigating) return;
    navigating = true;
    location.assign(href);
  }

  function goNext() { go(nextHref); }
  function goPrev() { go(prevHref); }

  function stopAutoLoop() {
    if (rafId) cancelAnimationFrame(rafId);
    rafId = 0;
    lastFrame = 0;
  }

  function autoFrame(now) {
    if (!settings.autoScrollEnabled || navigating) {
      stopAutoLoop();
      return;
    }

    if (!lastFrame) lastFrame = now;
    const dt = Math.min((now - lastFrame) / 1000, 0.1);
    lastFrame = now;

    if (atBottom()) {
      stopAutoLoop();
      if (nextHref) {
        setTimeout(() => goNext(), 180);
      }
      return;
    }

    const el = document.scrollingElement || document.documentElement;
    el.scrollTop += settings.autoScrollSpeed * dt;
    rafId = requestAnimationFrame(autoFrame);
  }

  function startAutoLoop() {
    stopAutoLoop();
    if (settings.autoScrollEnabled) {
      rafId = requestAnimationFrame(autoFrame);
    }
  }

  async function setAutoScroll(enabled) {
    settings.autoScrollEnabled = !!enabled;
    await chrome.storage.local.set({ autoScrollEnabled: settings.autoScrollEnabled });
    startAutoLoop();
  }

  function onKeyDown(event) {
    if (event.defaultPrevented || event.repeat || isEditableTarget(event.target)) return;
    if (event.ctrlKey || event.metaKey || event.altKey) return;

    if (event.key === "ArrowRight") {
      event.preventDefault();
      goNext();
      return;
    }

    if (event.key === "ArrowLeft") {
      event.preventDefault();
      goPrev();
      return;
    }

    if (event.key === "ArrowDown") {
      event.preventDefault();
      window.scrollBy({ top: settings.keyScrollStep, left: 0, behavior: "auto" });
      return;
    }

    if (event.key === "ArrowUp") {
      event.preventDefault();
      window.scrollBy({ top: -settings.keyScrollStep, left: 0, behavior: "auto" });
      return;
    }

    if (event.key === "Enter") {
      event.preventDefault();
      setAutoScroll(!settings.autoScrollEnabled);
    }
  }

  async function init() {
    // Capture navigation before removing the site's DOM.
    nextHref = findChapterHref("next");
    prevHref = findChapterHref("prev");

    const images = getReaderImages();
    if (!images.length) return;

    settings = {
      ...DEFAULTS,
      ...(await chrome.storage.local.get(DEFAULTS))
    };
    settings.autoScrollSpeed = clamp(Number(settings.autoScrollSpeed) || DEFAULTS.autoScrollSpeed, 5, 1000);
    settings.keyScrollStep = clamp(Number(settings.keyScrollStep) || DEFAULTS.keyScrollStep, 20, 1000);

    installReader(images);

    // Keyboard only. There is intentionally NO wheel/mousewheel/DOMMouseScroll
    // listener anywhere in this extension. Native browser wheel scrolling remains native.
    window.addEventListener("keydown", onKeyDown, { capture: true });

    chrome.storage.onChanged.addListener((changes, area) => {
      if (area !== "local") return;

      if (changes.autoScrollSpeed) {
        settings.autoScrollSpeed = clamp(
          Number(changes.autoScrollSpeed.newValue) || DEFAULTS.autoScrollSpeed,
          5, 1000
        );
      }

      if (changes.keyScrollStep) {
        settings.keyScrollStep = clamp(
          Number(changes.keyScrollStep.newValue) || DEFAULTS.keyScrollStep,
          20, 1000
        );
      }

      if (changes.autoScrollEnabled) {
        settings.autoScrollEnabled = !!changes.autoScrollEnabled.newValue;
        startAutoLoop();
      }
    });

    startAutoLoop();
  }

  init().catch(err => console.error("[Clean Reader]", err));
})();