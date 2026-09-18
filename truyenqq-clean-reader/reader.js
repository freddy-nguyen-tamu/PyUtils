(() => {
  'use strict';

  const DEFAULTS = {
    autoScroll: false,
    scrollRate: 120
  };

  let settings = { ...DEFAULTS };
  let nextUrl = null;
  let prevUrl = null;
  let autoFrame = 0;
  let lastFrameTime = 0;
  let endHoldStartedAt = 0;
  let navigating = false;
  let keyDirection = 0;
  let keyFrame = 0;
  let keyLastFrameTime = 0;

  const storageGet = (keys) => new Promise((resolve) => {
    chrome.storage.local.get(keys, resolve);
  });

  const storageSet = (obj) => new Promise((resolve) => {
    chrome.storage.local.set(obj, resolve);
  });

  function normalizeUrl(value) {
    if (!value) return null;
    try {
      return new URL(value, location.href).href;
    } catch {
      return null;
    }
  }

  function captureNavigation() {
    const nextAnchor = document.querySelector('.chapter_control a.next');
    const prevAnchor = document.querySelector('.chapter_control a.back');

    nextUrl = normalizeUrl(nextAnchor?.getAttribute('href'));
    prevUrl = normalizeUrl(prevAnchor?.getAttribute('href'));

    // Fallback to the chapter selector if the site's explicit links are absent.
    if (!nextUrl || !prevUrl) {
      const select = document.querySelector('.select-reading');
      if (select) {
        const options = Array.from(select.options);
        const selectedIndex = options.findIndex((o) => o.selected || normalizeUrl(o.value) === location.href);
        if (selectedIndex >= 0) {
          // TruyenQQ lists chapters newest -> oldest. Higher chapter = previous option.
          if (!nextUrl && selectedIndex > 0) {
            nextUrl = normalizeUrl(options[selectedIndex - 1]?.value);
          }
          if (!prevUrl && selectedIndex < options.length - 1) {
            prevUrl = normalizeUrl(options[selectedIndex + 1]?.value);
          }
        }
      }
    }
  }

  function collectImages() {
    const originalImages = Array.from(document.querySelectorAll('.reading-content img'));
    const unique = new Set();
    const images = [];

    for (const img of originalImages) {
      const source = img.getAttribute('data-src') || img.currentSrc || img.getAttribute('src');
      const url = normalizeUrl(source);
      if (!url || unique.has(url)) continue;
      unique.add(url);
      images.push({
        src: url,
        alt: img.getAttribute('alt') || ''
      });
    }

    return images;
  }

  function rebuildPage(images) {
    // Stop the original site from contributing layout, sticky headers, ads, scroll effects, etc.
    document.documentElement.innerHTML = '';

    const head = document.createElement('head');
    const metaCharset = document.createElement('meta');
    metaCharset.charset = 'utf-8';
    const metaViewport = document.createElement('meta');
    metaViewport.name = 'viewport';
    metaViewport.content = 'width=device-width, initial-scale=1';
    const title = document.createElement('title');
    title.textContent = 'Reader';
    const style = document.createElement('style');
    style.textContent = `
      :root {
        color-scheme: dark;
        background: #111;
      }

      * {
        box-sizing: border-box;
      }

      html {
        scroll-behavior: auto !important;
        overscroll-behavior: none !important;
        background: #111 !important;
      }

      body {
        margin: 0 !important;
        padding: 0 !important;
        min-height: 100vh;
        overflow-x: hidden !important;
        overflow-y: auto !important;
        overscroll-behavior: none !important;
        background: #111 !important;
      }

      #clean-reader {
        width: 100%;
        min-height: 100vh;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: flex-start;
        margin: 0;
        padding: 0;
      }

      #clean-reader img {
        display: block;
        width: auto;
        max-width: 100vw;
        height: auto;
        margin: 0 auto;
        padding: 0;
        border: 0;
        vertical-align: top;
        user-select: none;
        -webkit-user-drag: none;
      }
    `;

    head.append(metaCharset, metaViewport, title, style);

    const body = document.createElement('body');
    const root = document.createElement('main');
    root.id = 'clean-reader';

    for (const item of images) {
      const img = document.createElement('img');
      img.src = item.src;
      img.alt = item.alt;
      img.decoding = 'async';
      img.loading = 'eager';
      img.referrerPolicy = 'no-referrer-when-downgrade';
      root.appendChild(img);
    }

    body.appendChild(root);
    document.documentElement.append(head, body);
  }

  function maxScrollY() {
    const el = document.scrollingElement || document.documentElement;
    return Math.max(0, el.scrollHeight - el.clientHeight);
  }

  function atBottom() {
    const max = maxScrollY();
    return max <= 1 || window.scrollY >= max - 2;
  }

  function imagesSettled() {
    const images = document.querySelectorAll('#clean-reader img');
    if (!images.length) return false;
    return Array.from(images).every((img) => img.complete);
  }

  function navigate(url) {
    if (!url || navigating) return;
    navigating = true;
    location.href = url;
  }

  function autoStep(now) {
    if (!settings.autoScroll || navigating) {
      autoFrame = 0;
      lastFrameTime = 0;
      endHoldStartedAt = 0;
      return;
    }

    if (!lastFrameTime) lastFrameTime = now;
    const dt = Math.min(50, now - lastFrameTime) / 1000;
    lastFrameTime = now;

    if (!atBottom()) {
      endHoldStartedAt = 0;
      window.scrollBy(0, Math.max(0, Number(settings.scrollRate) || DEFAULTS.scrollRate) * dt);
    } else {
      if (!endHoldStartedAt) endHoldStartedAt = now;
      // Small dwell avoids jumping while the last image is still expanding during load.
      if (imagesSettled() && now - endHoldStartedAt >= 700) {
        if (nextUrl) {
          navigate(nextUrl);
          return;
        }
        // Final chapter: remain at the bottom with auto-scroll state preserved.
      }
    }

    autoFrame = requestAnimationFrame(autoStep);
  }

  function startAutoLoop() {
    if (autoFrame || !settings.autoScroll) return;
    lastFrameTime = 0;
    autoFrame = requestAnimationFrame(autoStep);
  }

  function stopAutoLoop() {
    if (autoFrame) cancelAnimationFrame(autoFrame);
    autoFrame = 0;
    lastFrameTime = 0;
    endHoldStartedAt = 0;
  }

  function keyScrollStep(now) {
    if (!keyDirection || navigating) {
      keyFrame = 0;
      keyLastFrameTime = 0;
      return;
    }

    if (!keyLastFrameTime) keyLastFrameTime = now;
    const dt = Math.min(50, now - keyLastFrameTime) / 1000;
    keyLastFrameTime = now;
    const rate = Math.max(1, Number(settings.scrollRate) || DEFAULTS.scrollRate);
    window.scrollBy(0, keyDirection * rate * dt);
    keyFrame = requestAnimationFrame(keyScrollStep);
  }

  function setKeyDirection(direction) {
    keyDirection = direction;
    if (direction && !keyFrame) {
      keyLastFrameTime = 0;
      keyFrame = requestAnimationFrame(keyScrollStep);
    }
    if (!direction && keyFrame) {
      cancelAnimationFrame(keyFrame);
      keyFrame = 0;
      keyLastFrameTime = 0;
    }
  }

  async function toggleAutoScroll() {
    settings.autoScroll = !settings.autoScroll;
    await storageSet({ autoScroll: settings.autoScroll });
    if (settings.autoScroll) startAutoLoop();
    else stopAutoLoop();
  }

  function installKeys() {
    window.addEventListener('keydown', (event) => {
      if (event.defaultPrevented) return;

      switch (event.code) {
        case 'ArrowRight':
          event.preventDefault();
          event.stopImmediatePropagation();
          if (!event.repeat) navigate(nextUrl);
          break;
        case 'ArrowLeft':
          event.preventDefault();
          event.stopImmediatePropagation();
          if (!event.repeat) navigate(prevUrl);
          break;
        case 'ArrowDown':
          event.preventDefault();
          event.stopImmediatePropagation();
          setKeyDirection(1);
          break;
        case 'ArrowUp':
          event.preventDefault();
          event.stopImmediatePropagation();
          setKeyDirection(-1);
          break;
        case 'Enter':
        case 'NumpadEnter':
          event.preventDefault();
          event.stopImmediatePropagation();
          if (!event.repeat) toggleAutoScroll();
          break;
      }
    }, { capture: true });

    window.addEventListener('keyup', (event) => {
      if ((event.code === 'ArrowDown' && keyDirection === 1) ||
          (event.code === 'ArrowUp' && keyDirection === -1)) {
        event.preventDefault();
        event.stopImmediatePropagation();
        setKeyDirection(0);
      }
    }, { capture: true });

    window.addEventListener('blur', () => setKeyDirection(0));
  }

  function watchSettings() {
    chrome.storage.onChanged.addListener((changes, area) => {
      if (area !== 'local') return;

      if (changes.scrollRate) {
        const value = Number(changes.scrollRate.newValue);
        if (Number.isFinite(value) && value > 0) settings.scrollRate = value;
      }

      if (changes.autoScroll) {
        settings.autoScroll = Boolean(changes.autoScroll.newValue);
        if (settings.autoScroll) startAutoLoop();
        else stopAutoLoop();
      }
    });
  }

  async function init() {
    captureNavigation();
    const images = collectImages();
    if (!images.length) return;

    const saved = await storageGet(DEFAULTS);
    settings = {
      autoScroll: Boolean(saved.autoScroll),
      scrollRate: Math.max(1, Number(saved.scrollRate) || DEFAULTS.scrollRate)
    };

    rebuildPage(images);
    installKeys();
    watchSettings();

    // Start at the top on each chapter. The auto-scroll preference persists independently.
    window.scrollTo(0, 0);

    // Let dimensions settle first so bottom detection is accurate.
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        if (settings.autoScroll) startAutoLoop();
      });
    });
  }

  init();
})();
