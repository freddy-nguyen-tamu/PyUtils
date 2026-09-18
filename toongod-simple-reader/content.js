(() => {
  'use strict';

  const HOST_ID = 'tg-simple-reader-host';
  const DEFAULT_SPEED = 90;
  const MIN_SPEED = 10;
  const MAX_SPEED = 500;

  if (document.getElementById(HOST_ID)) return;

  const originalImages = [...document.querySelectorAll('.reading-content img.wp-manga-chapter-img, img.wp-manga-chapter-img')];
  if (!originalImages.length) return;

  const pages = originalImages
    .map((img, index) => {
      const src = (img.currentSrc || img.src || img.dataset.src || '').trim();
      if (!src) return null;
      return {
        src,
        width: Number(img.getAttribute('width')) || 720,
        height: Number(img.getAttribute('height')) || 2200,
        alt: img.alt || `Page ${index + 1}`
      };
    })
    .filter(Boolean);

  if (!pages.length) return;

  const chapterSelect = document.querySelector('select.single-chapter-select');
  const chapterOptions = chapterSelect ? [...chapterSelect.options] : [];
  let currentIndex = chapterOptions.findIndex((option) => option.selected);

  if (currentIndex < 0) {
    const currentPath = location.pathname.replace(/\/$/, '');
    currentIndex = chapterOptions.findIndex((option) => {
      const url = option.dataset.redirect;
      if (!url) return false;
      try {
        return new URL(url, location.href).pathname.replace(/\/$/, '') === currentPath;
      } catch {
        return false;
      }
    });
  }

  function optionUrl(option) {
    if (!option) return null;
    const raw = option.dataset.redirect || option.value;
    if (!raw) return null;
    try {
      return new URL(raw, location.href).href;
    } catch {
      return null;
    }
  }

  // ToonGod's chapter selector is newest -> oldest. Therefore the option
  // before the current one is the next chapter and the option after it is
  // the previous chapter.
  let nextUrl = currentIndex > 0 ? optionUrl(chapterOptions[currentIndex - 1]) : null;
  let previousUrl = currentIndex >= 0 && currentIndex < chapterOptions.length - 1
    ? optionUrl(chapterOptions[currentIndex + 1])
    : null;

  // Fall back to the site's explicit navigation anchors if the selector
  // cannot resolve a direction on a particular chapter/template.
  nextUrl ||= document.querySelector('.nav-next a.next_page, .nav-next a')?.href || null;
  previousUrl ||= document.querySelector('.nav-previous a.prev_page, .nav-previous a, .nav-prev a.prev_page, .nav-prev a')?.href || null;

  const heading = (
    document.querySelector('#chapter-heading')?.textContent ||
    document.querySelector('h1')?.textContent ||
    document.title ||
    'Chapter'
  ).replace(/\s+/g, ' ').trim();

  const host = document.createElement('div');
  host.id = HOST_ID;
  host.style.cssText = 'position:fixed;inset:0;z-index:2147483647;display:block;';
  document.documentElement.appendChild(host);

  const shadow = host.attachShadow({ mode: 'open' });
  shadow.innerHTML = `
    <style>
      :host {
        all: initial;
        color-scheme: dark;
      }

      * { box-sizing: border-box; }

      #reader {
        position: fixed;
        inset: 0;
        overflow: hidden;
        background: #111;
        color: #f5f5f5;
        font: 13px/1.3 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }

      #scroller {
        position: absolute;
        inset: 0;
        overflow-x: hidden;
        overflow-y: auto;
        overscroll-behavior: none;
        scroll-behavior: auto !important;
        scrollbar-gutter: stable;
        -webkit-overflow-scrolling: auto;
        background: #111;
      }

      #pages {
        width: min(100%, 720px);
        margin: 0 auto;
        padding: 0;
        background: #000;
      }

      .page {
        display: block;
        width: 100%;
        margin: 0;
        padding: 0;
        contain: layout paint style;
      }

      .page img {
        display: block;
        width: 100%;
        height: auto;
        margin: 0;
        padding: 0;
        border: 0;
        vertical-align: top;
        background: #171717;
      }

      #help {
        position: fixed;
        right: 12px;
        bottom: 12px;
        z-index: 2;
        padding: 6px 8px;
        border-radius: 8px;
        background: rgba(12,12,12,.72);
        color: #aaa;
        font-size: 11px;
        opacity: .22;
        pointer-events: none;
      }

      @media (max-width: 760px) {
        #pages { width: 100%; }
        #help { display: none; }
      }

    </style>
    <main id="reader" aria-label="Simple chapter reader">
      <div id="scroller" tabindex="0">
        <div id="pages"></div>
      </div>
      <div id="help">← previous · → next · Space auto-scroll</div>
    </main>
  `;

  const reader = shadow.getElementById('reader');
  const scroller = shadow.getElementById('scroller');
  const pagesRoot = shadow.getElementById('pages');
  for (let i = 0; i < pages.length; i++) {
    const page = pages[i];
    const wrap = document.createElement('div');
    wrap.className = 'page';

    const img = document.createElement('img');
    img.src = page.src;
    img.alt = page.alt;
    img.width = page.width;
    img.height = page.height;
    img.decoding = 'async';
    img.loading = i < 2 ? 'eager' : 'lazy';
    img.fetchPriority = i === 0 ? 'high' : 'auto';
    img.draggable = false;

    wrap.appendChild(img);
    pagesRoot.appendChild(wrap);
  }

  // Freeze the original document. The extension reader itself is the only
  // scroll container, so theme scroll effects cannot move it around.
  const oldHtmlOverflow = document.documentElement.style.overflow;
  const oldBodyOverflow = document.body?.style.overflow || '';
  document.documentElement.style.overflow = 'hidden';
  if (document.body) document.body.style.overflow = 'hidden';

  let speed = DEFAULT_SPEED;
  let autoScrolling = false;
  let navigating = false;
  let rafId = 0;
  let lastTime = 0;

  function clampSpeed(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return DEFAULT_SPEED;
    return Math.min(MAX_SPEED, Math.max(MIN_SPEED, Math.round(n)));
  }

  function stopAutoScroll() {
    autoScrolling = false;
    lastTime = 0;
    if (rafId) cancelAnimationFrame(rafId);
    rafId = 0;
  }

  function saveAutoScrollEnabled(enabled) {
    chrome.storage.local.set({ autoScrollEnabled: Boolean(enabled) });
  }

  function tick(now) {
    if (!autoScrolling) return;
    if (!lastTime) lastTime = now;

    const dt = Math.min(50, now - lastTime);
    lastTime = now;
    scroller.scrollTop += speed * dt / 1000;

    const atBottom = scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 2;
    if (atBottom) {
      // Keep the persisted auto-scroll preference ON while moving between
      // chapters. The next chapter will read the preference and immediately
      // resume scrolling. At the final chapter there is nowhere to navigate,
      // so we simply stop the animation loop while leaving the preference ON.
      if (nextUrl && !navigating) {
        navigating = true;
        stopAutoScroll();
        location.assign(nextUrl);
        return;
      }

      stopAutoScroll();
      return;
    }

    rafId = requestAnimationFrame(tick);
  }

  function startAutoScroll() {
    if (autoScrolling) return;
    autoScrolling = true;
    lastTime = 0;
    rafId = requestAnimationFrame(tick);
  }

  function toggleAutoScroll() {
    if (autoScrolling) {
      stopAutoScroll();
      saveAutoScrollEnabled(false);
    } else {
      startAutoScroll();
      saveAutoScrollEnabled(true);
    }
  }

  function navigate(url) {
    if (!url || navigating) {
      return;
    }
    navigating = true;
    // Do not change the persisted auto-scroll preference here. If auto-scroll
    // was ON, a manually selected next/previous chapter should keep scrolling;
    // if it was OFF, it should stay OFF.
    stopAutoScroll();
    location.assign(url);
  }

  function isTypingTarget(target) {
    if (!(target instanceof Element)) return false;
    return Boolean(target.closest('input, textarea, select, [contenteditable="true"]'));
  }

  function onKeyDown(event) {
    if (isTypingTarget(event.target)) return;
    if (event.ctrlKey || event.altKey || event.metaKey) return;

    if (event.key === 'ArrowRight') {
      event.preventDefault();
      event.stopImmediatePropagation();
      if (!event.repeat) navigate(nextUrl);
      return;
    }

    if (event.key === 'ArrowLeft') {
      event.preventDefault();
      event.stopImmediatePropagation();
      if (!event.repeat) navigate(previousUrl);
      return;
    }

    if (event.code === 'Space') {
      event.preventDefault();
      event.stopImmediatePropagation();
      if (!event.repeat) toggleAutoScroll();
    }
  }

  // Capture keyboard shortcuts before the site's own handlers can use them.
  window.addEventListener('keydown', onKeyDown, true);

  // Prevent bubbling wheel/touch events from reaching theme handlers while
  // preserving the browser's native default scrolling inside #scroller.
  scroller.addEventListener('wheel', (event) => event.stopPropagation(), { passive: true });
  scroller.addEventListener('touchmove', (event) => event.stopPropagation(), { passive: true });

  chrome.storage.sync.get({ autoScrollSpeed: DEFAULT_SPEED }, (stored) => {
    speed = clampSpeed(stored.autoScrollSpeed);
  });

  chrome.storage.local.get({ autoScrollEnabled: false }, (stored) => {
    if (stored.autoScrollEnabled) startAutoScroll();
  });

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== 'sync' || !changes.autoScrollSpeed) return;
    speed = clampSpeed(changes.autoScrollSpeed.newValue);
  });

  chrome.runtime.onMessage.addListener((message) => {
    if (!message || message.type !== 'TG_SIMPLE_READER_SPEED') return;
    speed = clampSpeed(message.speed);
  });

  // Restore page overflow if the extension host is somehow removed without a
  // navigation (for example during extension development/reload).
  const removalObserver = new MutationObserver(() => {
    if (document.documentElement.contains(host)) return;
    window.removeEventListener('keydown', onKeyDown, true);
    document.documentElement.style.overflow = oldHtmlOverflow;
    if (document.body) document.body.style.overflow = oldBodyOverflow;
    removalObserver.disconnect();
  });
  removalObserver.observe(document.documentElement, { childList: true });

  scroller.focus({ preventScroll: true });
})();
