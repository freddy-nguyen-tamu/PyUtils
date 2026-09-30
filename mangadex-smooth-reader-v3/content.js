(() => {
  "use strict";

  const DEFAULTS = {
    autoScrollEnabled: false,
    autoScrollSpeed: 120,
    keyScrollPixels: 420,
    maxReaderWidth: 1100,
    v3Initialized: true
  };

  let settings = { ...DEFAULTS };

  let readerRoot = null;
  let imageColumn = null;
  let timer = null;
  let navigating = false;
  let lastSourceKey = "";
  let rebuildTimer = null;

  // Safety state for this chapter only.
  let chapterOpenedAt = performance.now();
  let autoStartScrollTop = 0;
  let autoAccumulatedMovement = 0;
  let lastAutoScrollTop = 0;
  let bottomStableSince = 0;

  function isChapterPage() {
    return /^\/chapter\/[0-9a-f-]+/i.test(location.pathname);
  }

  function storeGet(keys) {
    return new Promise(resolve => chrome.storage.local.get(keys, resolve));
  }

  function storeSet(values) {
    return new Promise(resolve => chrome.storage.local.set(values, resolve));
  }

  function clamp(n, min, max) {
    return Math.max(min, Math.min(max, n));
  }

  function scroller() {
    return document.scrollingElement || document.documentElement || document.body;
  }

  function scrollTop() {
    return scroller()?.scrollTop || 0;
  }

  function setScrollTop(value) {
    const el = scroller();
    if (el) el.scrollTop = value;
  }

  function maxScrollTop() {
    const el = scroller();
    if (!el) return 0;
    return Math.max(0, el.scrollHeight - el.clientHeight);
  }

  function remainingScroll() {
    return Math.max(0, maxScrollTop() - scrollTop());
  }

  function resetChapterSafety() {
    chapterOpenedAt = performance.now();
    autoStartScrollTop = scrollTop();
    autoAccumulatedMovement = 0;
    lastAutoScrollTop = scrollTop();
    bottomStableSince = 0;
    navigating = false;
  }

  function ensureRoot() {
    if (readerRoot?.isConnected) return;

    document.documentElement.classList.add("mdsr-active");
    document.body.classList.add("mdsr-active");

    readerRoot = document.createElement("main");
    readerRoot.id = "mdsr-root";

    imageColumn = document.createElement("div");
    imageColumn.id = "mdsr-images";

    readerRoot.appendChild(imageColumn);
    document.body.appendChild(readerRoot);

    applyWidth();
  }

  function applyWidth() {
    if (!readerRoot) return;
    readerRoot.style.setProperty(
      "--mdsr-max-width",
      `${clamp(Number(settings.maxReaderWidth) || DEFAULTS.maxReaderWidth, 420, 2400)}px`
    );
  }

  function getSourceImages() {
    const selectors = [
      ".md--reader-pages .md--page img",
      ".md--reader-pages img",
      ".pages-wrap .md--page img"
    ];

    for (const selector of selectors) {
      const imgs = [...document.querySelectorAll(selector)].filter(img => {
        if (!(img instanceof HTMLImageElement)) return false;
        if (img.closest("#mdsr-root")) return false;

        const src = img.currentSrc || img.src || "";
        return Boolean(src && !src.includes("/img/"));
      });

      if (imgs.length) return imgs;
    }

    return [];
  }

  function sourceKey(images) {
    return images.map(img => `${img.currentSrc || img.src}|${img.alt || ""}`).join("\n");
  }

  function cloneImage(source) {
    const img = document.createElement("img");
    img.className = "mdsr-page";
    img.alt = source.alt || "";
    img.decoding = "async";
    img.loading = "eager";
    img.draggable = false;

    const sync = () => {
      const src = source.currentSrc || source.src || "";
      if (src && img.src !== src) img.src = src;
    };

    sync();

    const observer = new MutationObserver(sync);
    observer.observe(source, {
      attributes: true,
      attributeFilter: ["src", "srcset"]
    });

    const markReady = () => {
      if (img.naturalWidth > 0 && img.naturalHeight > 0) {
        img.dataset.ready = "1";
      }
    };

    img.addEventListener("load", markReady);
    if (img.complete) markReady();

    return img;
  }

  function rebuildReader(force = false) {
    if (!isChapterPage()) return;

    const images = getSourceImages();
    if (!images.length) return;

    ensureRoot();

    const key = sourceKey(images);
    if (!force && key === lastSourceKey && imageColumn.children.length === images.length) {
      return;
    }

    const oldTop = scrollTop();
    const oldMax = Math.max(maxScrollTop(), 1);
    const ratio = oldTop / oldMax;

    imageColumn.replaceChildren(...images.map(cloneImage));
    lastSourceKey = key;

    requestAnimationFrame(() => {
      if (oldTop > 0) {
        setScrollTop(ratio * Math.max(maxScrollTop(), 1));
      }
    });
  }

  function scheduleRebuild(force = false) {
    clearTimeout(rebuildTimer);
    rebuildTimer = setTimeout(() => rebuildReader(force), 100);
  }

  function imagesReady() {
    const imgs = [...document.querySelectorAll("#mdsr-images .mdsr-page")];
    return (
      imgs.length > 0 &&
      imgs.every(img =>
        img.dataset.ready === "1" ||
        (img.complete && img.naturalWidth > 0 && img.naturalHeight > 0)
      )
    );
  }

  function getNavigationLinks() {
    const anchors = [...document.querySelectorAll('a[href*="/chapter/"]')];

    let previous = null;
    let next = null;

    for (const a of anchors) {
      if (!a.href || a.href === location.href) continue;

      if (!previous && a.querySelector(".feather-chevron-left")) {
        previous = a.href;
      }

      if (!next && a.querySelector(".feather-chevron-right")) {
        next = a.href;
      }
    }

    return { previous, next };
  }

  async function navigate(direction, reason = "manual") {
    if (navigating) return;

    const { previous, next } = getNavigationLinks();
    const target = direction === "next" ? next : previous;
    if (!target) return;

    // Strong duplicate auto-navigation fuse.
    if (reason === "auto") {
      const now = Date.now();
      const key = "mdsr-v3-last-auto-nav";
      let previousAuto = null;

      try {
        previousAuto = JSON.parse(sessionStorage.getItem(key) || "null");
      } catch (_) {}

      if (previousAuto && now - previousAuto.time < 12000) {
        await setAutoScroll(false);
        return;
      }

      sessionStorage.setItem(
        key,
        JSON.stringify({ from: location.href, to: target, time: now })
      );
    }

    navigating = true;
    location.assign(target);
  }

  function minimumMovementRequired() {
    const max = maxScrollTop();

    // Must prove substantial actual movement on this chapter before auto-next.
    // This completely prevents "loaded at bottom / wrong scroller" loops.
    return Math.min(
      Math.max(300, max * 0.20),
      1200
    );
  }

  function canAutoAdvance(now) {
    if (!settings.autoScrollEnabled) return false;
    if (!imagesReady()) return false;

    const age = now - chapterOpenedAt;
    if (age < 5000) return false;

    const max = maxScrollTop();

    // If the page is not actually scrollable, never auto-next.
    // Manual Right Arrow still works.
    if (max < 100) return false;

    if (autoAccumulatedMovement < minimumMovementRequired()) return false;
    if (remainingScroll() > 2) return false;

    return true;
  }

  function stopAutoScroll() {
    if (timer !== null) clearInterval(timer);
    timer = null;
    bottomStableSince = 0;
  }

  function startAutoScroll() {
    stopAutoScroll();

    if (!settings.autoScrollEnabled) return;

    resetChapterSafety();

    // Integer movement on the real scrolling element.
    // 20 fps is smooth enough and avoids sub-pixel/rAF ambiguity.
    const intervalMs = 50;

    timer = setInterval(() => {
      if (!settings.autoScrollEnabled || navigating) {
        stopAutoScroll();
        return;
      }

      if (!imagesReady()) return;

      const speed = clamp(
        Number(settings.autoScrollSpeed) || DEFAULTS.autoScrollSpeed,
        5,
        2000
      );

      const el = scroller();
      if (!el) return;

      const before = el.scrollTop;
      const delta = Math.max(1, Math.round(speed * intervalMs / 1000));

      if (remainingScroll() > 1) {
        el.scrollTop = Math.min(maxScrollTop(), before + delta);

        const after = el.scrollTop;
        const moved = Math.max(0, after - before);

        autoAccumulatedMovement += moved;
        lastAutoScrollTop = after;
        bottomStableSince = 0;
        return;
      }

      const now = performance.now();

      if (!canAutoAdvance(now)) {
        // At bottom but safety conditions are not satisfied:
        // stay here instead of advancing.
        return;
      }

      if (!bottomStableSince) {
        bottomStableSince = now;
        return;
      }

      if (now - bottomStableSince >= 1500) {
        navigate("next", "auto");
      }
    }, intervalMs);
  }

  async function setAutoScroll(enabled) {
    settings.autoScrollEnabled = Boolean(enabled);
    await storeSet({ autoScrollEnabled: settings.autoScrollEnabled });

    if (settings.autoScrollEnabled) {
      startAutoScroll();
    } else {
      stopAutoScroll();
    }
  }

  async function toggleAutoScroll() {
    await setAutoScroll(!settings.autoScrollEnabled);
  }

  function keyScroll(direction) {
    const el = scroller();
    if (!el) return;

    const pixels = clamp(
      Number(settings.keyScrollPixels) || DEFAULTS.keyScrollPixels,
      50,
      3000
    );

    el.scrollBy({
      top: direction * pixels,
      behavior: "smooth"
    });
  }

  function isTypingTarget(target) {
    if (!(target instanceof Element)) return false;
    return Boolean(
      target.closest("input, textarea, select, [contenteditable='true'], [role='textbox']")
    );
  }

  function onKeyDown(event) {
    if (!isChapterPage()) return;
    if (isTypingTarget(event.target)) return;
    if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;

    if (event.key === "ArrowRight") {
      event.preventDefault();
      event.stopImmediatePropagation();
      navigate("next", "manual");
      return;
    }

    if (event.key === "ArrowLeft") {
      event.preventDefault();
      event.stopImmediatePropagation();
      navigate("previous", "manual");
      return;
    }

    if (event.key === "ArrowDown") {
      event.preventDefault();
      event.stopImmediatePropagation();
      keyScroll(1);
      return;
    }

    if (event.key === "ArrowUp") {
      event.preventDefault();
      event.stopImmediatePropagation();
      keyScroll(-1);
      return;
    }

    if (event.key === "Enter") {
      event.preventDefault();
      event.stopImmediatePropagation();
      toggleAutoScroll();
    }
  }

  function observeMangaDex() {
    const observer = new MutationObserver(mutations => {
      for (const m of mutations) {
        if (
          m.type === "childList" ||
          (m.type === "attributes" &&
            ["src", "srcset", "href"].includes(m.attributeName))
        ) {
          scheduleRebuild(false);
          break;
        }
      }
    });

    observer.observe(document.documentElement, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ["src", "srcset", "href"]
    });
  }

  async function init() {
    if (!isChapterPage()) return;

    const stored = await storeGet([
      "autoScrollEnabled",
      "autoScrollSpeed",
      "keyScrollPixels",
      "maxReaderWidth",
      "v3Initialized"
    ]);

    // Always neutralize old broken-version state on first v3 install.
    if (!stored.v3Initialized) {
      await storeSet({
        autoScrollEnabled: false,
        v3Initialized: true
      });
      stored.autoScrollEnabled = false;
      stored.v3Initialized = true;
    }

    settings = { ...DEFAULTS, ...stored };

    document.addEventListener("keydown", onKeyDown, true);
    observeMangaDex();

    ensureRoot();
    rebuildReader(true);
    resetChapterSafety();

    [150, 400, 900, 1800, 3200].forEach(ms =>
      setTimeout(() => scheduleRebuild(false), ms)
    );

    if (settings.autoScrollEnabled) {
      setTimeout(startAutoScroll, 800);
    }

    chrome.storage.onChanged.addListener((changes, area) => {
      if (area !== "local") return;

      if (changes.autoScrollSpeed) {
        settings.autoScrollSpeed = Number(changes.autoScrollSpeed.newValue);
      }

      if (changes.keyScrollPixels) {
        settings.keyScrollPixels = Number(changes.keyScrollPixels.newValue);
      }

      if (changes.maxReaderWidth) {
        settings.maxReaderWidth = Number(changes.maxReaderWidth.newValue);
        applyWidth();
      }

      if (changes.autoScrollEnabled) {
        settings.autoScrollEnabled = Boolean(changes.autoScrollEnabled.newValue);

        if (settings.autoScrollEnabled) startAutoScroll();
        else stopAutoScroll();
      }
    });
  }

  init();
})();
