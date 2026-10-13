(() => {
  'use strict';

  // Deliberately NO wheel/mousewheel listeners, event synthesizers, or wheel
  // transformations. #readerarea scrolls through ordinary Chromium input.
  if (window.top !== window) return;
  if (globalThis.__quietChapterReaderStarted) return;
  globalThis.__quietChapterReaderStarted = true;

  const DEFAULTS = Object.freeze({ autoScroll: false, speed: 130 });
  const MAX_FRAME_MS = 80;
  const BOTTOM_EPSILON = 3;
  const STABLE_BOTTOM_MS = 2100;
  const USER_SCROLL_PAUSE_MS = 1350;
  let area = null;
  let enabled = false;
  let speed = DEFAULTS.speed;
  let animationFrame = 0;
  let lastFrame = 0;
  let held = new Set();
  let isLeaving = false;
  let manuallyPausedUntil = 0;
  let lastInternalPosition = null;
  let lastInternalWriteAt = 0;
  let bottomSince = 0;
  let lastContentChange = 0;
  let lastContentHeight = 0;
  let lastImageCount = 0;
  let observer = null;
  let resizeObserver = null;
  let tabVisible = !document.hidden;

  function clampSpeed(value) {
    const number = Number(value);
    return Number.isFinite(number) ? Math.max(20, Math.min(600, number)) : DEFAULTS.speed;
  }
  function now() { return performance.now(); }
  function scheduleFrame() {
    if (!animationFrame && area && !isLeaving) animationFrame = requestAnimationFrame(tick);
  }
  function saveAuto(value) {
    enabled = !!value;
    bottomSince = 0;
    try { chrome.storage.local.set({ autoScroll: enabled }); } catch (_) { /* unloading */ }
    scheduleFrame();
  }

  function canonical(value) {
    try {
      const u = new URL(value, location.href);
      if (!['http:', 'https:'].includes(u.protocol) || u.origin !== location.origin) return null;
      return u.origin + u.pathname.replace(/\/+$/, '') + '/';
    } catch (_) { return null; }
  }
  const currentUrl = canonical(location.href);
  function validTarget(raw) {
    const parsed = canonical(raw);
    if (!parsed || parsed === currentUrl) return null;
    return new URL(raw, location.href).href;
  }

  // Options are in the WEBSITE's own order, newest-to-oldest in this HTML.
  // Prefer true site prev/next anchors, and use adjacency in the real selector
  // only as fallback. Never calculate/guess a chapter number or URL.
  function chapterOptions() {
    const select = [...document.querySelectorAll('select#chapter, select[name="chapter"]')]
      .find(s => [...s.options].some(o => canonical(o.value) === currentUrl));
    if (!select) return { options: [], index: -1 };
    const options = [...select.options].filter(o => canonical(o.value));
    return { options, index: options.findIndex(o => canonical(o.value) === currentUrl) };
  }
  function getChapterTarget(direction) {
    const data = chapterOptions();
    const index = data.index;
    let fromSelect = null;
    if (index !== -1) {
      // Most WordPress/mangareader lists have newest first. Determine order
      // from the site's own explicit prev/next links when both exist.
      const nextAnchor = document.querySelector('a.ch-next-btn[href]:not(.disabled), a[rel="next"].ch-next-btn[href]');
      const prevAnchor = document.querySelector('a.ch-prev-btn[href]:not(.disabled), a[rel="prev"].ch-prev-btn[href]');
      const nextIndex = data.options.findIndex(o => canonical(o.value) === canonical(nextAnchor?.getAttribute('href')));
      const prevIndex = data.options.findIndex(o => canonical(o.value) === canonical(prevAnchor?.getAttribute('href')));
      // If there's no usable anchor, select labels are ordered by the website;
      // its first option is the latest chapter on the supplied target website.
      const orientation = nextIndex >= 0 && nextIndex !== index ? Math.sign(nextIndex - index)
        : prevIndex >= 0 && prevIndex !== index ? -Math.sign(prevIndex - index)
        : -1;
      const adjacentIndex = index + (direction === 'next' ? orientation : -orientation);
      if (adjacentIndex >= 0 && adjacentIndex < data.options.length)
        fromSelect = validTarget(data.options[adjacentIndex].value);
      // If an explicit website anchor exists and disagrees with the selector's
      // order, it remains authoritative rather than predicting chapter IDs.
    }
    const selectors = direction === 'next'
      ? ['a.ch-next-btn:not(.disabled)[href]', '.nextprev a[rel="next"]:not(.disabled)[href]']
      : ['a.ch-prev-btn:not(.disabled)[href]', '.nextprev a[rel="prev"]:not(.disabled)[href]'];
    for (const selector of selectors) {
      for (const anchor of document.querySelectorAll(selector)) {
        const href = validTarget(anchor.getAttribute('href'));
        if (href && (index === -1 || data.options.some(o => canonical(o.value) === canonical(href))))
          return href;
      }
    }
    return fromSelect;
  }

  function goChapter(direction) {
    if (isLeaving) return false;
    const next = getChapterTarget(direction);
    if (!next) {
      // Auto-run ends at the last actual chapter (and remains off on reload).
      if (direction === 'next' && enabled) saveAuto(false);
      return false;
    }
    isLeaving = true;
    held.clear();
    if (animationFrame) cancelAnimationFrame(animationFrame);
    animationFrame = 0;
    location.assign(next);
    return true;
  }

  function chapterImages() {
    return area ? [...area.querySelectorAll('img.ts-main-image, picture img, img[data-index], img')]
      .filter((im, idx, arr) => arr.indexOf(im) === idx) : [];
  }
  function imagesReady() {
    const images = chapterImages();
    if (!images.length) return false;
    return images.every(img => {
      if (!img.complete || img.naturalWidth <= 0 || img.naturalHeight <= 0) return false;
      const deferred = img.getAttribute('data-src') || img.getAttribute('data-lazy-src') ||
        img.getAttribute('data-original');
      // A successful placeholder must never count as the chapter image.
      if (deferred && !img.currentSrc.startsWith('blob:')) {
        const active = img.currentSrc || img.src;
        try {
          if (new URL(deferred, location.href).href !== active &&
              new URL(deferred, location.href).href !== img.src) return false;
        } catch (_) { return false; }
      }
      return true;
    });
  }
  function markContentChange() {
    lastContentChange = now();
    bottomSince = 0;
  }
  function onAreaScroll() {
    // This is a passive native 'scroll' observer, NOT a wheel listener. It
    // never changes the scroll position or cancels any input. When the native
    // position differs from our own most recent RAF write, pause auto-run so
    // native upward/downward manual input wins for a generous period.
    if (!area || isLeaving) return;
    const p = area.scrollTop;
    if (lastInternalPosition === null || Math.abs(p - lastInternalPosition) > 2 ||
        now() - lastInternalWriteAt > 180) {
      if (enabled) manuallyPausedUntil = now() + USER_SCROLL_PAUSE_MS;
      bottomSince = 0;
    }
  }
  function scrollRelative(amount) {
    if (!area || amount === 0) return;
    const before = area.scrollTop;
    area.scrollTop = before + amount;
    lastInternalPosition = area.scrollTop;
    lastInternalWriteAt = now();
  }
  function tick(time) {
    animationFrame = 0;
    if (!area || isLeaving) return;
    const dt = lastFrame ? Math.max(0, Math.min(MAX_FRAME_MS, time - lastFrame)) : 0;
    lastFrame = time;
    const height = area.scrollHeight;
    const count = chapterImages().length;
    if (height !== lastContentHeight || count !== lastImageCount) {
      lastContentHeight = height;
      lastImageCount = count;
      markContentChange();
    }
    const keyboardDirection = (held.has('ArrowDown') ? 1 : 0) - (held.has('ArrowUp') ? 1 : 0);
    // No synthetic wheel. Keyboard and auto-run may programmatically modify
    // the native container's scrollTop; manual input stays browser-native.
    if (tabVisible && dt > 0) {
      if (keyboardDirection) scrollRelative(keyboardDirection * speed * dt / 1000);
      else if (enabled && time >= manuallyPausedUntil) scrollRelative(speed * dt / 1000);
    }
    const atBottom = area.scrollHeight - area.clientHeight - area.scrollTop <= BOTTOM_EPSILON;
    if (enabled && !keyboardDirection && tabVisible && time >= manuallyPausedUntil &&
        atBottom && imagesReady() && time - lastContentChange >= STABLE_BOTTOM_MS) {
      if (!bottomSince) bottomSince = time;
      if (time - bottomSince >= STABLE_BOTTOM_MS) {
        goChapter('next');
        return;
      }
    } else bottomSince = 0;
    // Only animate while something requires it; a passive heartbeat when
    // idle checks image/layout changes without continuously writing scrollTop.
    if (enabled || keyboardDirection) scheduleFrame();
    else lastFrame = 0;
  }
  function onKeyDown(event) {
    if (event.altKey || event.ctrlKey || event.metaKey || event.isComposing) return;
    const active = document.activeElement;
    if (active && (active.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(active.tagName))) return;
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Enter'].includes(event.key)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (isLeaving) return;
    if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
      if (!event.repeat) goChapter(event.key === 'ArrowRight' ? 'next' : 'prev');
    } else if (event.key === 'Enter') {
      if (!event.repeat) saveAuto(!enabled);
    } else {
      held.add(event.key);
      scheduleFrame();
    }
  }
  function onKeyUp(event) {
    if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
    held.delete(event.key);
    event.preventDefault();
    event.stopImmediatePropagation();
  }
  function setup() {
    if (area) return;
    const element = document.getElementById('readerarea');
    if (!element || !element.querySelector('img')) return;
    area = element;
    document.documentElement.setAttribute('data-qcr-active', 'true');
    // We leave the site's original DOM nodes and lazy-loader metadata intact.
    // Do not overwrite img.src/srcset/decoding/loading or blob object URLs.
    area.addEventListener('scroll', onAreaScroll, { passive: true });
    area.addEventListener('load', markContentChange, true);
    area.addEventListener('error', markContentChange, true);
    window.addEventListener('keydown', onKeyDown, true);
    window.addEventListener('keyup', onKeyUp, true);
    window.addEventListener('blur', () => { held.clear(); lastFrame = 0; });
    document.addEventListener('visibilitychange', () => {
      tabVisible = !document.hidden;
      lastFrame = 0;
      if (tabVisible) scheduleFrame();
    });
    observer = new MutationObserver(markContentChange);
    observer.observe(area, { subtree: true, childList: true, attributes: true,
      attributeFilter: ['src', 'srcset', 'data-src', 'data-lazy-src', 'data-original', 'style', 'class'] });
    if ('ResizeObserver' in window) {
      resizeObserver = new ResizeObserver(markContentChange);
      for (const img of chapterImages()) resizeObserver.observe(img);
    }
    lastContentHeight = area.scrollHeight;
    lastImageCount = chapterImages().length;
    markContentChange();
    // Settings are shared across pages; the next chapter reuses stored state.
    chrome.storage.local.get(DEFAULTS, result => {
      if (chrome.runtime.lastError) return;
      speed = clampSpeed(result.speed);
      enabled = !!result.autoScroll;
      if (enabled) scheduleFrame();
    });
    chrome.storage.onChanged.addListener((changes, namespace) => {
      if (namespace !== 'local') return;
      if (changes.speed) speed = clampSpeed(changes.speed.newValue);
      if (changes.autoScroll) {
        enabled = !!changes.autoScroll.newValue;
        bottomSince = 0;
        lastFrame = 0;
        if (enabled) scheduleFrame();
      }
    });
  }
  // CSS can begin hiding site chrome before heavy site JS finishes.
  if (/chapter/i.test(location.pathname))
    document.documentElement?.setAttribute('data-qcr-active', 'true');
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setup, { once: true });
  } else setup();
})();
