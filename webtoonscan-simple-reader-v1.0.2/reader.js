(() => {
  'use strict';

  const CHAPTER_PATH = /^\/manhwa\/[^/]+\/[^/]+\/?$/i;
  if (!CHAPTER_PATH.test(location.pathname)) return;

  const storage = globalThis.browser?.storage?.local ?? globalThis.chrome?.storage?.local;
  const storageEvents = globalThis.browser?.storage?.onChanged ?? globalThis.chrome?.storage?.onChanged;
  const DEFAULT_RATE = 120;
  const MIN_RATE = 20;
  const MAX_RATE = 2000;

  let autoScrollEnabled = false;
  let scrollRate = DEFAULT_RATE;
  let manualDirection = 0;
  let rafId = 0;
  let lastFrame = 0;
  let nextChapterUrl = null;
  let previousChapterUrl = null;
  let readerReady = false;
  let navigating = false;
  let bottomTimer = 0;
  let manualInputTimer = 0;
  let autoScrollSuspended = false;

  // Wheel/touch events are deliberately NOT intercepted. The browser owns
  // manual scrolling. These passive listeners only pause our own auto-scroll
  // briefly so it never fights the user's hand scrolling.
  window.addEventListener('wheel', onManualScrollInput, { passive: true });
  window.addEventListener('touchmove', onManualScrollInput, { passive: true });
  window.addEventListener('keydown', onKeyDown, { capture: true });
  window.addEventListener('keyup', onKeyUp, { capture: true });
  window.addEventListener('scroll', onScroll, { capture: true, passive: true });
  storageEvents?.addListener?.(onStorageChanged);

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot, { once: true });
  } else {
    boot();
  }

  async function boot() {
    const images = collectChapterImages();
    if (!images.length) {
      removeInterceptors();
      return;
    }

    ({ nextChapterUrl, previousChapterUrl } = collectChapterNavigation());

    try {
      const saved = await storageGet(['autoScrollEnabled', 'scrollRate']);
      autoScrollEnabled = Boolean(saved.autoScrollEnabled);
      scrollRate = sanitizeRate(saved.scrollRate);
    } catch {
      autoScrollEnabled = false;
      scrollRate = DEFAULT_RATE;
    }

    buildReader(images);
    readerReady = true;

    // Every chapter should start at its first image, including automatic chapter changes.
    try {
      history.scrollRestoration = 'manual';
    } catch {}

    requestAnimationFrame(() => {
      window.scrollTo({ top: 0, left: 0, behavior: 'auto' });
      updateAnimationLoop();
    });
  }


  function onStorageChanged(changes, areaName) {
    if (areaName !== 'local') return;

    if (changes.scrollRate) {
      scrollRate = sanitizeRate(changes.scrollRate.newValue);
    }

    if (changes.autoScrollEnabled) {
      autoScrollEnabled = Boolean(changes.autoScrollEnabled.newValue);
      updateAnimationLoop();
    }
  }

  function removeInterceptors() {
    window.removeEventListener('wheel', onManualScrollInput);
    window.removeEventListener('touchmove', onManualScrollInput);
    window.removeEventListener('keydown', onKeyDown, { capture: true });
    window.removeEventListener('keyup', onKeyUp, { capture: true });
    window.removeEventListener('scroll', onScroll, { capture: true });
    storageEvents?.removeListener?.(onStorageChanged);
  }

  function collectChapterImages() {
    const candidates = [
      ...document.querySelectorAll('.reading-content .page-break img'),
      ...document.querySelectorAll('.reading-content img.wp-manga-chapter-img')
    ];

    return [...new Set(candidates)].filter((img) => {
      const source = getBestImageSource(img);
      if (!source) return false;
      img.src = source;
      return true;
    });
  }

  function getBestImageSource(img) {
    const raw =
      img.currentSrc ||
      img.getAttribute('data-src') ||
      img.getAttribute('data-lazy-src') ||
      img.getAttribute('data-original') ||
      img.getAttribute('src') ||
      '';

    return raw.trim();
  }

  function collectChapterNavigation() {
    let next = document.querySelector('.nav-next a[href], a.next_page[href]')?.href ?? null;
    let previous = document.querySelector('.nav-previous a[href], .nav-prev a[href], a.prev_page[href]')?.href ?? null;

    // Fallback to the chapter dropdown. WebtoonScan lists newest -> oldest,
    // so the next chapter is one option before the current selection.
    const select = document.querySelector('.single-chapter-select');
    if (select) {
      const options = [...select.options];
      const selectedIndex = select.selectedIndex;

      if (!next && selectedIndex > 0) {
        next = optionUrl(options[selectedIndex - 1]);
      }
      if (!previous && selectedIndex >= 0 && selectedIndex < options.length - 1) {
        previous = optionUrl(options[selectedIndex + 1]);
      }
    }

    return {
      nextChapterUrl: normalizeChapterUrl(next),
      previousChapterUrl: normalizeChapterUrl(previous)
    };
  }

  function optionUrl(option) {
    return option?.dataset?.redirect || option?.value || null;
  }

  function normalizeChapterUrl(value) {
    if (!value) return null;
    try {
      const url = new URL(value, location.href);
      if (url.origin !== location.origin || !CHAPTER_PATH.test(url.pathname)) return null;
      return url.href;
    } catch {
      return null;
    }
  }

  function buildReader(images) {
    const style = document.createElement('style');
    style.id = 'simple-reader-style';
    style.textContent = `
      html.simple-reader-active {
        margin: 0 !important;
        padding: 0 !important;
        width: 100% !important;
        height: auto !important;
        min-height: 100% !important;
        position: static !important;
        overflow-x: hidden !important;
        overflow-y: scroll !important;
        background: #111 !important;
        scroll-behavior: auto !important;
        scroll-snap-type: none !important;
        touch-action: pan-y !important;
      }

      html.simple-reader-active body {
        margin: 0 !important;
        padding: 0 !important;
        width: 100% !important;
        height: auto !important;
        min-height: 100% !important;
        position: static !important;
        overflow: visible !important;
        background: #111 !important;
        scroll-behavior: auto !important;
        scroll-snap-type: none !important;
        touch-action: pan-y !important;
      }

      #simple-reader-root {
        width: 100% !important;
        min-height: 100vh !important;
        margin: 0 !important;
        padding: 0 !important;
        display: flex !important;
        flex-direction: column !important;
        align-items: center !important;
        justify-content: flex-start !important;
        gap: 0 !important;
        background: #111 !important;
      }

      #simple-reader-root .simple-reader-image {
        display: block !important;
        flex: 0 0 auto !important;
        width: auto !important;
        max-width: 100vw !important;
        height: auto !important;
        max-height: none !important;
        margin: 0 auto !important;
        padding: 0 !important;
        border: 0 !important;
        border-radius: 0 !important;
        box-shadow: none !important;
        transform: none !important;
        object-fit: contain !important;
        user-select: none !important;
        -webkit-user-drag: none !important;
      }
    `;

    document.documentElement.className = 'simple-reader-active';
    document.head.appendChild(style);

    const reader = document.createElement('main');
    reader.id = 'simple-reader-root';

    for (const img of images) {
      img.removeAttribute('id');
      img.removeAttribute('style');
      img.removeAttribute('width');
      img.removeAttribute('height');
      img.removeAttribute('loading');
      img.removeAttribute('onclick');
      img.className = 'simple-reader-image';
      img.draggable = false;
      img.decoding = 'async';
      reader.appendChild(img);
    }

    document.body.replaceChildren(reader);
  }

  function onManualScrollInput() {
    // Important: do not call preventDefault(), stopPropagation(), or
    // stopImmediatePropagation() here. Native browser scrolling must remain intact.
    // If auto-scroll is enabled, only pause our animation briefly so the user's
    // manual wheel/touchpad movement wins.
    if (readerReady && autoScrollEnabled && manualDirection === 0) {
      suspendAutoScrollForManualInput();
    }
  }

  function suspendAutoScrollForManualInput() {
    autoScrollSuspended = true;
    if (manualInputTimer) clearTimeout(manualInputTimer);

    if (rafId) {
      cancelAnimationFrame(rafId);
      rafId = 0;
      lastFrame = 0;
    }

    manualInputTimer = window.setTimeout(() => {
      manualInputTimer = 0;
      autoScrollSuspended = false;
      updateAnimationLoop();
    }, 700);
  }

  function onKeyDown(event) {
    if (!readerReady) {
      if (isReaderKey(event.key)) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
      return;
    }

    if (!isReaderKey(event.key)) return;

    event.preventDefault();
    event.stopImmediatePropagation();

    if (event.key === 'ArrowRight') {
      if (!event.repeat) navigate(nextChapterUrl);
      return;
    }

    if (event.key === 'ArrowLeft') {
      if (!event.repeat) navigate(previousChapterUrl);
      return;
    }

    if (event.key === 'Enter') {
      if (!event.repeat) toggleAutoScroll();
      return;
    }

    if (event.key === 'ArrowDown') {
      manualDirection = 1;
      updateAnimationLoop();
      return;
    }

    if (event.key === 'ArrowUp') {
      manualDirection = -1;
      updateAnimationLoop();
    }
  }

  function onKeyUp(event) {
    if (!readerReady) return;
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;

    event.preventDefault();
    event.stopImmediatePropagation();

    const releasedDirection = event.key === 'ArrowDown' ? 1 : -1;
    if (manualDirection === releasedDirection) manualDirection = 0;
    updateAnimationLoop();
  }

  function isReaderKey(key) {
    return key === 'ArrowRight' || key === 'ArrowLeft' || key === 'ArrowUp' || key === 'ArrowDown' || key === 'Enter';
  }

  async function toggleAutoScroll() {
    autoScrollEnabled = !autoScrollEnabled;
    try {
      await storageSet({ autoScrollEnabled });
    } catch {}
    updateAnimationLoop();
  }

  function updateAnimationLoop() {
    const shouldRun = readerReady && !navigating && (manualDirection !== 0 || (autoScrollEnabled && !autoScrollSuspended));

    if (shouldRun && !rafId) {
      lastFrame = performance.now();
      rafId = requestAnimationFrame(step);
    } else if (!shouldRun && rafId) {
      cancelAnimationFrame(rafId);
      rafId = 0;
      lastFrame = 0;
    }
  }

  function step(now) {
    rafId = 0;
    if (!readerReady || navigating) return;

    const direction = manualDirection || (autoScrollEnabled && !autoScrollSuspended ? 1 : 0);
    if (!direction) {
      lastFrame = 0;
      return;
    }

    const dt = Math.min(Math.max(now - lastFrame, 0), 50);
    lastFrame = now;
    const before = window.scrollY;

    window.scrollBy({
      top: direction * scrollRate * (dt / 1000),
      left: 0,
      behavior: 'auto'
    });

    if (direction > 0 && isAtBottom()) {
      if (nextChapterUrl) {
        scheduleBottomNavigation();
        return;
      }

      // Final chapter: stop spending animation frames, but keep the saved
      // auto-scroll preference unchanged so it remains the user's preference.
      if (!manualDirection) return;
    }

    // If an upward/downward key is held but the browser cannot move any farther,
    // avoid doing extra work at the edge until another input changes direction.
    if (manualDirection && window.scrollY === before && ((direction < 0 && window.scrollY <= 0) || (direction > 0 && isAtBottom()))) {
      return;
    }

    rafId = requestAnimationFrame(step);
  }

  function onScroll() {
    if (!readerReady || navigating) return;
    if (isAtBottom() && nextChapterUrl) scheduleBottomNavigation();
    else clearBottomNavigation();
  }

  function isAtBottom() {
    const root = document.documentElement;
    const body = document.body;
    const pageHeight = Math.max(
      root.scrollHeight,
      root.offsetHeight,
      body?.scrollHeight ?? 0,
      body?.offsetHeight ?? 0
    );
    return Math.ceil(window.scrollY + window.innerHeight) >= pageHeight - 2;
  }

  function scheduleBottomNavigation() {
    if (bottomTimer || navigating || !nextChapterUrl) return;
    bottomTimer = window.setTimeout(() => {
      bottomTimer = 0;
      if (isAtBottom()) navigate(nextChapterUrl);
    }, 220);
  }

  function clearBottomNavigation() {
    if (!bottomTimer) return;
    clearTimeout(bottomTimer);
    bottomTimer = 0;
  }

  function navigate(url) {
    if (!url || navigating) return;
    navigating = true;
    clearBottomNavigation();
    if (manualInputTimer) {
      clearTimeout(manualInputTimer);
      manualInputTimer = 0;
    }
    if (rafId) cancelAnimationFrame(rafId);
    rafId = 0;
    location.assign(url);
  }

  function sanitizeRate(value) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return DEFAULT_RATE;
    return Math.min(MAX_RATE, Math.max(MIN_RATE, Math.round(parsed)));
  }

  function storageGet(keys) {
    if (!storage) return Promise.resolve({});
    const result = storage.get(keys);
    return result && typeof result.then === 'function'
      ? result
      : new Promise((resolve, reject) => {
          storage.get(keys, (items) => {
            const err = globalThis.chrome?.runtime?.lastError;
            if (err) reject(err);
            else resolve(items || {});
          });
        });
  }

  function storageSet(values) {
    if (!storage) return Promise.resolve();
    const result = storage.set(values);
    return result && typeof result.then === 'function'
      ? result
      : new Promise((resolve, reject) => {
          storage.set(values, () => {
            const err = globalThis.chrome?.runtime?.lastError;
            if (err) reject(err);
            else resolve();
          });
        });
  }
})();
