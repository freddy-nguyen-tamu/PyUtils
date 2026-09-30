(() => {
  'use strict';

  const DEFAULT_SPEED = 90; // pixels / second
  const MIN_SPEED = 10;
  const MAX_SPEED = 1000;
  const STORAGE_KEYS = {
    auto: 'autoScrollEnabled',
    speed: 'scrollSpeed'
  };

  let autoScrollEnabled = false;
  let scrollSpeed = DEFAULT_SPEED;
  let direction = 0; // -1 up, +1 down, 0 none
  let lastFrame = performance.now();
  let bottomSince = 0;
  let navigating = false;
  let rafId = 0;

  const clampSpeed = (value) => {
    const number = Number(value);
    if (!Number.isFinite(number)) return DEFAULT_SPEED;
    return Math.min(MAX_SPEED, Math.max(MIN_SPEED, number));
  };

  const storageGet = (defaults) => new Promise((resolve) => {
    chrome.storage.local.get(defaults, resolve);
  });

  const storageSet = (values) => new Promise((resolve) => {
    chrome.storage.local.set(values, resolve);
  });

  function enableLongStripMode() {
    const checkbox = document.querySelector('#viewing_checkbox');
    if (!checkbox || checkbox.checked) return;

    // Prefer a real user-like click so the site's own toggleViewing handler runs.
    const slider = checkbox.parentElement?.querySelector('.slider.round');
    if (slider instanceof HTMLElement) {
      slider.click();
    } else {
      checkbox.click();
    }

    // Some site revisions may swallow the synthetic click; make the state explicit.
    if (!checkbox.checked) {
      checkbox.checked = true;
      checkbox.dispatchEvent(new Event('change', { bubbles: true }));
    }
  }

  function getChapterUrl(rel) {
    const link = document.querySelector(`link[rel="${rel}"]`);
    if (link?.href) return link.href;

    const select = document.querySelector('.chapter_list select');
    if (!(select instanceof HTMLSelectElement)) return null;

    const index = select.selectedIndex;
    const targetIndex = rel === 'next' ? index + 1 : index - 1;
    const option = select.options[targetIndex];
    if (!option?.value) return null;
    return new URL(option.value, location.href).href;
  }

  function collectPages() {
    const seen = new Set();
    const pages = [];

    document.querySelectorAll('.slideshow-container img').forEach((img) => {
      const src = img.currentSrc || img.src;
      if (!src || seen.has(src)) return;
      seen.add(src);
      pages.push({
        src,
        alt: img.alt || '',
        width: Number(img.getAttribute('width')) || 0,
        height: Number(img.getAttribute('height')) || 0
      });
    });

    return pages;
  }

  function installMinimalReader(pages) {
    document.documentElement.className = '';
    document.body.className = '';

    // Stop the site's presentation JS from reacting to clicks/keys after we replace it.
    document.body.replaceChildren();

    const style = document.createElement('style');
    style.textContent = `
      :root {
        color-scheme: dark;
        background: #111;
      }
      html {
        margin: 0 !important;
        padding: 0 !important;
        min-height: 100% !important;
        overflow-x: hidden !important;
        overflow-y: scroll !important;
        scroll-behavior: auto !important;
        overscroll-behavior-y: none !important;
        background: #111 !important;
      }
      body {
        margin: 0 !important;
        padding: 0 !important;
        min-height: 100vh !important;
        overflow: visible !important;
        background: #111 !important;
      }
      #mfsr-reader {
        width: 100%;
        min-height: 100vh;
        display: flex;
        flex-direction: column;
        align-items: center;
        margin: 0;
        padding: 0;
      }
      #mfsr-reader img {
        display: block !important;
        width: auto !important;
        height: auto !important;
        max-width: min(100vw, 1600px) !important;
        margin: 0 auto !important;
        padding: 0 !important;
        border: 0 !important;
        transform: none !important;
        animation: none !important;
        transition: none !important;
        opacity: 1 !important;
        position: static !important;
        object-fit: contain;
        user-select: none;
        -webkit-user-drag: none;
      }
    `;

    const reader = document.createElement('main');
    reader.id = 'mfsr-reader';

    for (const page of pages) {
      const img = document.createElement('img');
      img.src = page.src;
      img.alt = page.alt;
      img.decoding = 'async';
      img.loading = 'eager';
      if (page.width) img.width = page.width;
      if (page.height) img.height = page.height;
      reader.appendChild(img);
    }

    document.head.appendChild(style);
    document.body.appendChild(reader);
  }

  function atBottom() {
    const root = document.scrollingElement || document.documentElement;
    return root.scrollTop + root.clientHeight >= root.scrollHeight - 2;
  }

  function goToChapter(rel) {
    if (navigating) return;
    const url = getChapterUrl(rel);
    if (!url) return;
    navigating = true;
    location.href = url;
  }

  async function toggleAutoScroll() {
    autoScrollEnabled = !autoScrollEnabled;
    bottomSince = 0;
    await storageSet({ [STORAGE_KEYS.auto]: autoScrollEnabled });
  }

  function frame(now) {
    const elapsed = Math.min(100, now - lastFrame) / 1000;
    lastFrame = now;

    const activeDirection = direction !== 0 ? direction : (autoScrollEnabled ? 1 : 0);
    if (activeDirection !== 0 && !navigating) {
      const before = window.scrollY;
      window.scrollBy(0, activeDirection * scrollSpeed * elapsed);

      if (activeDirection > 0 && atBottom()) {
        if (!bottomSince) bottomSince = now;

        // Let the browser settle after the final image/load before changing chapters.
        if (now - bottomSince >= 500) {
          const next = getChapterUrl('next');
          if (next) {
            navigating = true;
            location.href = next;
          }
        }
      } else {
        bottomSince = 0;
      }

      // If scrolling upward while already at the top, simply stay there.
      if (activeDirection < 0 && before === 0) bottomSince = 0;
    } else {
      bottomSince = 0;
    }

    rafId = requestAnimationFrame(frame);
  }

  function installKeyboard() {
    const handled = new Set(['ArrowRight', 'ArrowLeft', 'ArrowUp', 'ArrowDown', 'Enter']);

    window.addEventListener('keydown', (event) => {
      if (!handled.has(event.key)) return;
      event.preventDefault();
      event.stopImmediatePropagation();

      if (event.key === 'ArrowRight') {
        if (!event.repeat) goToChapter('next');
        return;
      }

      if (event.key === 'ArrowLeft') {
        if (!event.repeat) goToChapter('prev');
        return;
      }

      if (event.key === 'ArrowUp') {
        direction = -1;
        return;
      }

      if (event.key === 'ArrowDown') {
        direction = 1;
        return;
      }

      if (event.key === 'Enter' && !event.repeat) {
        void toggleAutoScroll();
      }
    }, true);

    window.addEventListener('keyup', (event) => {
      if (event.key === 'ArrowUp' && direction < 0) direction = 0;
      if (event.key === 'ArrowDown' && direction > 0) direction = 0;
    }, true);

    window.addEventListener('blur', () => {
      direction = 0;
    });
  }

  function installStorageListener() {
    chrome.storage.onChanged.addListener((changes, area) => {
      if (area !== 'local') return;
      if (changes[STORAGE_KEYS.speed]) {
        scrollSpeed = clampSpeed(changes[STORAGE_KEYS.speed].newValue);
      }
      if (changes[STORAGE_KEYS.auto]) {
        autoScrollEnabled = Boolean(changes[STORAGE_KEYS.auto].newValue);
        bottomSince = 0;
      }
    });
  }

  async function main() {
    const pages = collectPages();
    if (!pages.length) return;

    // The supplied page uses #viewing_checkbox + .slider.round for its long-strip mode.
    // Trigger that first on every chapter, then replace the laggy reader UI.
    enableLongStripMode();

    const state = await storageGet({
      [STORAGE_KEYS.auto]: false,
      [STORAGE_KEYS.speed]: DEFAULT_SPEED
    });

    autoScrollEnabled = Boolean(state[STORAGE_KEYS.auto]);
    scrollSpeed = clampSpeed(state[STORAGE_KEYS.speed]);

    // Capture chapter URLs before replacing the original DOM.
    const prevUrl = getChapterUrl('prev');
    const nextUrl = getChapterUrl('next');
    window.__MFSR_CHAPTERS__ = { prevUrl, nextUrl };

    // getChapterUrl normally reads the page. After replacement, use captured values.
    const originalGetChapterUrl = getChapterUrl;
    getChapterUrl = function(rel) { // eslint-disable-line no-func-assign
      return rel === 'next' ? window.__MFSR_CHAPTERS__.nextUrl : window.__MFSR_CHAPTERS__.prevUrl;
    };

    installMinimalReader(pages);
    installKeyboard();
    installStorageListener();

    cancelAnimationFrame(rafId);
    lastFrame = performance.now();
    rafId = requestAnimationFrame(frame);
  }

  void main();
})();
