(() => {
  'use strict';

  const SETTINGS_KEY = 'reader_settings';
  const originalUrl = new URL(location.href);
  const openedOnNumberedPage = /\/p\/\d+\/?$/i.test(originalUrl.pathname);

  let allPagesActivated = false;
  let topResetFinished = false;
  let retryTimer = null;

  function forceAllPagesSetting() {
    let settings = {};

    try {
      const raw = localStorage.getItem(SETTINGS_KEY);
      if (raw) settings = JSON.parse(raw) || {};
    } catch (_) {
      settings = {};
    }

    settings.gototop = 'off';
    settings.page_mode = 'all-pages';
    settings.scaling_mode = settings.scaling_mode || 'horizontal';
    settings.hide_tutorial = 'on';

    try {
      localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
    } catch (_) {
      // The reader can still be switched through its DOM controls below.
    }
  }

  function normalizeUrlToChapterRoot() {
    if (!openedOnNumberedPage) return;

    const url = new URL(location.href);
    const normalizedPath = url.pathname.replace(/\/p\/\d+\/?$/i, '/');

    if (normalizedPath !== url.pathname) {
      url.pathname = normalizedPath;
      history.replaceState(history.state, '', url.href);
    }
  }

  function addInfiniteScrollCss() {
    if (document.getElementById('hr-infinite-scroll-style')) return;

    const style = document.createElement('style');
    style.id = 'hr-infinite-scroll-style';
    style.textContent = `
      /* The click zones are only useful in single-page mode. */
      #btnGoLeft,
      #btnGoRight {
        display: none !important;
        pointer-events: none !important;
      }

      /* Keep the reader as a normal vertical document. */
      #readingContent,
      #readingWrapper,
      #readingImages {
        overflow: visible !important;
        height: auto !important;
        max-height: none !important;
      }

      #readingImages {
        display: flex !important;
        flex-direction: column !important;
        align-items: center !important;
      }

      #readingImages > .reading-page:not(.hidden) {
        width: 100% !important;
        flex: 0 0 auto !important;
      }

      #readingImages > .reading-page:not(.hidden) > img {
        display: block !important;
        width: 100% !important;
        max-width: 1280px !important;
        height: auto !important;
        max-height: none !important;
        object-fit: contain !important;
      }
    `;

    (document.head || document.documentElement).appendChild(style);
  }

  function dispatchReaderChange(control) {
    // Different site builds have listened to one or more of these events.
    control.dispatchEvent(new Event('input', { bubbles: true }));
    control.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function activateNativeAllPages() {
    forceAllPagesSetting();
    addInfiniteScrollCss();

    const allPages = document.getElementById('all-pages-mode');
    const singlePage = document.getElementById('single-page-mode');
    const slider = document.getElementById('slider-mode');
    const readingImages = document.getElementById('readingImages');

    if (!allPages || !readingImages) return false;

    if (singlePage) singlePage.checked = false;
    if (slider) slider.checked = false;

    if (!allPages.checked) {
      allPages.checked = true;
      dispatchReaderChange(allPages);

      // A real click is useful for builds whose reader uses click handlers
      // instead of change handlers. Guard it so we do not create loops.
      if (!allPagesActivated) {
        try {
          allPages.click();
        } catch (_) {
          // Ignore and keep the storage/change-event fallback.
        }
      }
    }

    document.body?.classList.remove('reading-mode-single-page', 'reading-mode-slider');
    document.body?.classList.add('reading-mode-all-pages');

    allPagesActivated = true;
    return true;
  }

  function resetToPageOne() {
    if (!openedOnNumberedPage || topResetFinished) return;

    // Single-page mode can try to restore the old /p/N/ position while its
    // deferred reader script initializes. A few top resets beat that race,
    // then stop completely so normal manual scrolling is untouched.
    const delays = [0, 100, 350, 800, 1500];
    for (const delay of delays) {
      setTimeout(() => window.scrollTo({ top: 0, left: 0, behavior: 'auto' }), delay);
    }
    setTimeout(() => {
      topResetFinished = true;
    }, 1800);
  }

  function boot() {
    forceAllPagesSetting();
    normalizeUrlToChapterRoot();
    addInfiniteScrollCss();

    const activated = activateNativeAllPages();
    if (activated) resetToPageOne();

    if (retryTimer === null) {
      let attempts = 0;
      retryTimer = setInterval(() => {
        attempts += 1;
        const ok = activateNativeAllPages();
        if (ok) resetToPageOne();

        // The site uses deferred scripts; 10 seconds is far beyond normal
        // initialization but avoids a permanent interval on broken pages.
        if (attempts >= 100) {
          clearInterval(retryTimer);
          retryTimer = null;
        }
      }, 100);
    }
  }

  // Set storage before HentaiRead's deferred reader JavaScript executes.
  forceAllPagesSetting();
  normalizeUrlToChapterRoot();

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot, { once: true });
  } else {
    boot();
  }

  // HentaiRead can rewrite reader DOM state after startup. Keep only the mode
  // invariant, not the user's scroll position.
  window.addEventListener('load', () => {
    activateNativeAllPages();
    resetToPageOne();
  }, { once: true });
})();
