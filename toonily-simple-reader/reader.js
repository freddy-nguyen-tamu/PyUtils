(() => {
  "use strict";

  const DEFAULTS = {
    autoScrollEnabled: false,
    autoScrollSpeed: 120, // px/second
    arrowScrollStep: 260  // px/key press; holding a key repeats naturally
  };

  const state = {
    autoScrollEnabled: DEFAULTS.autoScrollEnabled,
    autoScrollSpeed: DEFAULTS.autoScrollSpeed,
    arrowScrollStep: DEFAULTS.arrowScrollStep,
    prevUrl: null,
    nextUrl: null,
    initialized: false,
    imagesSettled: false,
    navigating: false,
    rafId: 0,
    lastFrame: 0
  };

  const isChapterPage = () =>
    /^\/serie\/.+\/chapter-[^/]+\/?$/i.test(location.pathname);

  if (!isChapterPage()) return;

  // Register first, at document_start, so Toonily's own keyboard/scroll handlers
  // cannot override the reader controls later.
  window.addEventListener("keydown", onKeyDown, true);
  window.addEventListener("keyup", swallowReaderKeys, true);
  window.addEventListener("wheel", isolateWheelFromSiteScripts, {
    capture: true,
    passive: true
  });

  chrome.storage.local.get(DEFAULTS, (saved) => {
    state.autoScrollEnabled = Boolean(saved.autoScrollEnabled);
    state.autoScrollSpeed = clampNumber(saved.autoScrollSpeed, 10, 3000, DEFAULTS.autoScrollSpeed);
    state.arrowScrollStep = clampNumber(saved.arrowScrollStep, 20, 3000, DEFAULTS.arrowScrollStep);
  });

  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName !== "local") return;

    if (changes.autoScrollEnabled) {
      state.autoScrollEnabled = Boolean(changes.autoScrollEnabled.newValue);
      if (state.autoScrollEnabled) startAutoScroll();
      else stopAutoScroll();
    }

    if (changes.autoScrollSpeed) {
      state.autoScrollSpeed = clampNumber(
        changes.autoScrollSpeed.newValue,
        10,
        3000,
        DEFAULTS.autoScrollSpeed
      );
    }

    if (changes.arrowScrollStep) {
      state.arrowScrollStep = clampNumber(
        changes.arrowScrollStep.newValue,
        20,
        3000,
        DEFAULTS.arrowScrollStep
      );
    }
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initializeReader, { once: true });
  } else {
    initializeReader();
  }

  function clampNumber(value, min, max, fallback) {
    const n = Number(value);
    if (!Number.isFinite(n)) return fallback;
    return Math.min(max, Math.max(min, n));
  }

  function normalizeUrl(value) {
    if (!value) return null;
    try {
      return new URL(value, location.href).href;
    } catch {
      return null;
    }
  }

  function pickImageUrl(img) {
    const candidates = [
      img.currentSrc,
      img.getAttribute("src"),
      img.getAttribute("data-src"),
      img.getAttribute("data-lazy-src"),
      img.getAttribute("data-original"),
      img.getAttribute("data-cfsrc")
    ];

    for (const value of candidates) {
      if (!value) continue;
      const trimmed = value.trim();
      if (!trimmed || trimmed.startsWith("data:image/")) continue;
      const url = normalizeUrl(trimmed);
      if (url) return url;
    }

    const srcset = img.getAttribute("srcset") || img.getAttribute("data-srcset");
    if (srcset) {
      const first = srcset
        .split(",")
        .map(part => part.trim().split(/\s+/)[0])
        .find(Boolean);
      return normalizeUrl(first);
    }

    return null;
  }

  function collectReaderData() {
    let sourceImages = Array.from(
      document.querySelectorAll(
        ".reading-content img.wp-manga-chapter-img, " +
        ".reading-content .page-break img, " +
        ".read-container .reading-content img"
      )
    );

    // Deduplicate DOM nodes and then URLs while preserving chapter order.
    sourceImages = [...new Set(sourceImages)];

    const seen = new Set();
    const images = [];

    for (const img of sourceImages) {
      const src = pickImageUrl(img);
      if (!src || seen.has(src)) continue;
      seen.add(src);

      images.push({
        src,
        alt: img.getAttribute("alt") || "",
        width: Number(img.getAttribute("width")) || 0,
        height: Number(img.getAttribute("height")) || 0
      });
    }

    const prev =
      document.querySelector("a.prev_page[href]") ||
      document.querySelector(".nav-previous a[href]");
    const next =
      document.querySelector("a.next_page[href]") ||
      document.querySelector(".nav-next a[href]");

    return {
      images,
      prevUrl: prev ? normalizeUrl(prev.getAttribute("href")) : findAdjacentChapter(-1),
      nextUrl: next ? normalizeUrl(next.getAttribute("href")) : findAdjacentChapter(1)
    };
  }

  function findAdjacentChapter(direction) {
    const options = Array.from(document.querySelectorAll(".chapter-option[data-redirect]"));
    if (!options.length) return null;

    const currentUrl = new URL(location.href);
    const currentPath = currentUrl.pathname.replace(/\/+$/, "");

    const entries = options
      .map(el => normalizeUrl(el.getAttribute("data-redirect")))
      .filter(Boolean)
      .map(url => {
        const parsed = new URL(url);
        const m = parsed.pathname.match(/\/chapter-([0-9]+(?:\.[0-9]+)?)/i);
        return { url, path: parsed.pathname.replace(/\/+$/, ""), num: m ? Number(m[1]) : NaN };
      });

    const currentEntry = entries.find(entry => entry.path === currentPath);
    if (!currentEntry || !Number.isFinite(currentEntry.num)) return null;

    const candidates = entries
      .filter(entry => Number.isFinite(entry.num))
      .filter(entry => direction > 0 ? entry.num > currentEntry.num : entry.num < currentEntry.num)
      .sort((a, b) => direction > 0 ? a.num - b.num : b.num - a.num);

    return candidates[0]?.url || null;
  }

  function initializeReader() {
    if (state.initialized) return;

    const data = collectReaderData();
    if (!data.images.length) return;

    state.initialized = true;
    state.prevUrl = data.prevUrl;
    state.nextUrl = data.nextUrl;

    const style = document.createElement("style");
    style.id = "toonily-simple-reader-style";
    style.textContent = `
      html, body {
        margin: 0 !important;
        padding: 0 !important;
        width: 100% !important;
        min-height: 100% !important;
        background: #000 !important;
        color: transparent !important;
        overflow-x: hidden !important;
        overflow-y: auto !important;
        overscroll-behavior-x: none !important;
        overscroll-behavior-y: none !important;
        scroll-behavior: auto !important;
        scroll-snap-type: none !important;
        overflow-anchor: none !important;
      }

      body {
        position: static !important;
        transform: none !important;
        touch-action: pan-y !important;
      }

      #toonily-simple-reader {
        display: block !important;
        width: 100% !important;
        margin: 0 !important;
        padding: 0 !important;
        border: 0 !important;
        background: #000 !important;
        text-align: center !important;
        overflow: visible !important;
      }

      #toonily-simple-reader img {
        display: block !important;
        width: auto !important;
        height: auto !important;
        max-width: 100vw !important;
        margin: 0 auto !important;
        padding: 0 !important;
        border: 0 !important;
        outline: 0 !important;
        box-shadow: none !important;
        transform: none !important;
        vertical-align: top !important;
        user-select: none !important;
        -webkit-user-drag: none !important;
      }
    `;

    // Remove all visible site UI/scripts' target DOM. Existing page-level event
    // listeners are neutralized by our capture listeners above.
    const reader = document.createElement("main");
    reader.id = "toonily-simple-reader";
    reader.setAttribute("aria-label", "Chapter images");

    const loadPromises = [];

    for (const item of data.images) {
      const img = document.createElement("img");
      img.src = item.src;
      img.alt = item.alt;
      img.loading = "eager";
      img.decoding = "async";
      img.draggable = false;
      img.referrerPolicy = "strict-origin-when-cross-origin";

      if (item.width > 0) img.width = item.width;
      if (item.height > 0) img.height = item.height;

      const settled = new Promise(resolve => {
        if (img.complete) resolve();
        else {
          img.addEventListener("load", resolve, { once: true });
          img.addEventListener("error", resolve, { once: true });
        }
      });
      loadPromises.push(settled);
      reader.appendChild(img);
    }

    document.head.appendChild(style);
    document.body.replaceChildren(reader);

    // Strip common site scroll/navigation state from the root element.
    document.documentElement.className = "";
    document.body.className = "";
    window.scrollTo(0, 0);

    Promise.allSettled(loadPromises).then(() => {
      state.imagesSettled = true;
      if (state.autoScrollEnabled) startAutoScroll();
    });

    // Do not wait for every image before allowing auto-scroll to start, but
    // do not auto-advance chapters until all image loads have settled.
    if (state.autoScrollEnabled) startAutoScroll();
  }

  function onKeyDown(event) {
    if (!isReaderKey(event.key)) return;
    if (event.ctrlKey || event.altKey || event.metaKey) return;

    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();

    if (!state.initialized) return;

    switch (event.key) {
      case "ArrowRight":
        navigate(state.nextUrl);
        break;
      case "ArrowLeft":
        navigate(state.prevUrl);
        break;
      case "ArrowDown":
        window.scrollBy(0, state.arrowScrollStep);
        maybeAdvanceAtBottom(false);
        break;
      case "ArrowUp":
        window.scrollBy(0, -state.arrowScrollStep);
        break;
      case "Enter":
        setAutoScroll(!state.autoScrollEnabled);
        break;
    }
  }

  function swallowReaderKeys(event) {
    if (!isReaderKey(event.key)) return;
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();
  }

  function isReaderKey(key) {
    return key === "ArrowRight" ||
      key === "ArrowLeft" ||
      key === "ArrowDown" ||
      key === "ArrowUp" ||
      key === "Enter";
  }

  function isolateWheelFromSiteScripts(event) {
    if (!state.initialized) return;
    // Intentionally DO NOT call preventDefault(): native browser wheel/touchpad
    // scrolling remains fully functional. We only stop Toonily's handlers.
    event.stopPropagation();
    event.stopImmediatePropagation();
  }

  function setAutoScroll(enabled) {
    state.autoScrollEnabled = Boolean(enabled);
    chrome.storage.local.set({ autoScrollEnabled: state.autoScrollEnabled });

    if (state.autoScrollEnabled) startAutoScroll();
    else stopAutoScroll();
  }

  function startAutoScroll() {
    if (!state.initialized || !state.autoScrollEnabled || state.navigating) return;
    if (state.rafId) return;

    state.lastFrame = performance.now();
    state.rafId = requestAnimationFrame(autoScrollFrame);
  }

  function stopAutoScroll() {
    if (state.rafId) cancelAnimationFrame(state.rafId);
    state.rafId = 0;
    state.lastFrame = 0;
  }

  function autoScrollFrame(now) {
    state.rafId = 0;

    if (!state.initialized || !state.autoScrollEnabled || state.navigating) return;

    const dt = Math.min(100, Math.max(0, now - state.lastFrame));
    state.lastFrame = now;

    const distance = (state.autoScrollSpeed * dt) / 1000;
    if (distance > 0) window.scrollBy(0, distance);

    if (maybeAdvanceAtBottom(true)) return;

    state.rafId = requestAnimationFrame(autoScrollFrame);
  }

  function isAtBottom() {
    const scrollTop = window.scrollY || document.documentElement.scrollTop || 0;
    const viewport = window.innerHeight || document.documentElement.clientHeight || 0;
    const fullHeight = Math.max(
      document.documentElement.scrollHeight,
      document.body.scrollHeight
    );

    return scrollTop + viewport >= fullHeight - 2;
  }

  function maybeAdvanceAtBottom(fromAutoScroll) {
    if (!isAtBottom()) return false;

    // While images are still resolving, page height can temporarily look short.
    // Never jump chapters until every chapter image has either loaded or errored.
    if (!state.imagesSettled) return false;

    if (state.nextUrl) {
      navigate(state.nextUrl);
      return true;
    }

    // End of series: stop unattended auto-scroll instead of looping or hammering.
    if (fromAutoScroll && state.autoScrollEnabled) {
      setAutoScroll(false);
    }
    return false;
  }

  function navigate(url) {
    if (!url || state.navigating) return;
    state.navigating = true;
    stopAutoScroll();
    location.assign(url);
  }
})();
