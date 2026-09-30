(() => {
  'use strict';

  const DEFAULTS = {
    autoScroll: false,
    speed: 80,
    arrowMultiplier: 1
  };

  const MIN_SPEED = 10;
  const MAX_SPEED = 500;
  const NAV_BOTTOM_HOLD_MS = 1200;
  const NAV_COOLDOWN_MS = 2500;

  let settings = { ...DEFAULTS };
  let previousUrl = null;
  let nextUrl = null;
  let navigationLocked = false;
  let bottomSince = 0;
  let lastFrame = 0;
  let rafId = 0;
  let readerImages = [];
  let imagesReady = false;

  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

  function absoluteUrl(value) {
    if (!value) return null;
    try {
      return new URL(value, location.href).href;
    } catch {
      return null;
    }
  }

  function extractChapterNavigation() {
    const explicitPrevious = document.querySelector('.prev-chapter-link[href]');
    const explicitNext = document.querySelector('.next-chapter-link[href]');

    previousUrl = absoluteUrl(explicitPrevious?.getAttribute('href'));
    nextUrl = absoluteUrl(explicitNext?.getAttribute('href'));

    // Fallback: derive adjacent chapters from the site's chapter selector.
    if (!previousUrl || !nextUrl) {
      const select = document.querySelector('.mangosm-manga-nav-select');
      if (select) {
        const options = Array.from(select.options);
        const currentIndex = options.findIndex((option) => {
          const url = absoluteUrl(option.value);
          return url && normalizeUrl(url) === normalizeUrl(location.href);
        });

        if (currentIndex >= 0) {
          // The attached site lists newest -> oldest. Therefore the visually
          // previous option is the next chapter, and the following option is
          // the previous chapter.
          if (!nextUrl && currentIndex > 0) {
            nextUrl = absoluteUrl(options[currentIndex - 1]?.value);
          }
          if (!previousUrl && currentIndex < options.length - 1) {
            previousUrl = absoluteUrl(options[currentIndex + 1]?.value);
          }
        }
      }
    }
  }

  function normalizeUrl(url) {
    try {
      const parsed = new URL(url);
      parsed.hash = '';
      return parsed.href.replace(/\/$/, '');
    } catch {
      return String(url).replace(/\/$/, '');
    }
  }

  function extractImages() {
    const container = document.querySelector('.images-container');
    if (!container) return [];

    return Array.from(container.querySelectorAll('img'))
      .map((img, index) => {
        const src =
          img.currentSrc ||
          img.getAttribute('src') ||
          img.getAttribute('data-src') ||
          img.getAttribute('data-lazy-src');

        const url = absoluteUrl(src);
        if (!url) return null;

        return {
          src: url,
          alt: img.getAttribute('alt') || `Page ${index + 1}`
        };
      })
      .filter(Boolean);
  }

  function buildCleanReader(images) {
    document.documentElement.innerHTML = '';

    const head = document.createElement('head');
    const meta = document.createElement('meta');
    meta.name = 'viewport';
    meta.content = 'width=device-width, initial-scale=1';
    head.appendChild(meta);

    const style = document.createElement('style');
    style.textContent = `
      :root {
        color-scheme: dark;
        background: #111;
      }

      html {
        margin: 0 !important;
        padding: 0 !important;
        background: #111 !important;
        overflow-y: scroll !important;
        overflow-x: hidden !important;
        overscroll-behavior: none !important;
        scroll-behavior: auto !important;
        scrollbar-gutter: stable;
      }

      body {
        margin: 0 !important;
        padding: 0 !important;
        min-height: 100vh !important;
        background: #111 !important;
        overflow: visible !important;
        overscroll-behavior: none !important;
      }

      #simple-reader {
        width: 100%;
        margin: 0 auto;
        padding: 0;
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 0;
      }

      #simple-reader img {
        display: block !important;
        width: auto !important;
        height: auto !important;
        max-width: min(100vw, 1100px) !important;
        margin: 0 auto !important;
        padding: 0 !important;
        border: 0 !important;
        box-shadow: none !important;
        background: #111 !important;
        object-fit: contain !important;
        user-select: none;
        -webkit-user-drag: none;
      }
    `;
    head.appendChild(style);

    const body = document.createElement('body');
    const reader = document.createElement('main');
    reader.id = 'simple-reader';

    readerImages = images.map((item, index) => {
      const img = document.createElement('img');
      img.src = item.src;
      img.alt = item.alt;
      img.decoding = 'async';
      img.loading = index < 3 ? 'eager' : 'lazy';
      img.referrerPolicy = 'no-referrer-when-downgrade';
      reader.appendChild(img);
      return img;
    });

    body.appendChild(reader);
    document.documentElement.append(head, body);

    // Native wheel/touch scrolling remains untouched. We only suppress the
    // site's keyboard handlers by owning keydown in capture phase.
    document.documentElement.scrollTop = 0;
    document.body.scrollTop = 0;

    monitorImageReadiness();
  }

  function monitorImageReadiness() {
    if (!readerImages.length) {
      imagesReady = false;
      return;
    }

    const update = () => {
      imagesReady = readerImages.every((img) => img.complete);
    };

    for (const img of readerImages) {
      if (!img.complete) {
        img.addEventListener('load', update, { once: true });
        img.addEventListener('error', update, { once: true });
      }
    }
    update();
  }

  async function loadSettings() {
    const stored = await chrome.storage.local.get(DEFAULTS);
    settings.autoScroll = Boolean(stored.autoScroll);
    settings.speed = clamp(Number(stored.speed) || DEFAULTS.speed, MIN_SPEED, MAX_SPEED);
    settings.arrowMultiplier = Number(stored.arrowMultiplier) || 1;
  }

  function saveAutoScroll() {
    chrome.storage.local.set({ autoScroll: settings.autoScroll });
  }

  function toggleAutoScroll() {
    settings.autoScroll = !settings.autoScroll;
    bottomSince = 0;
    lastFrame = performance.now();
    saveAutoScroll();
  }

  function scrollByKeyboard(direction) {
    // One arrow press moves roughly 0.65 seconds worth of the configured
    // autoscroll rate, with a practical minimum so slow speeds still feel useful.
    const amount = Math.max(40, settings.speed * 0.65) * settings.arrowMultiplier;
    window.scrollBy({ top: direction * amount, left: 0, behavior: 'auto' });
    bottomSince = 0;
  }

  function navigate(url) {
    if (!url || navigationLocked) return;
    navigationLocked = true;
    settings.autoScroll = Boolean(settings.autoScroll);
    chrome.storage.local.set({ autoScroll: settings.autoScroll }, () => {
      location.href = url;
    });
  }

  function isAtBottom() {
    const root = document.documentElement;
    const scrollTop = window.scrollY || root.scrollTop || 0;
    const viewport = window.innerHeight || root.clientHeight || 0;
    const height = Math.max(root.scrollHeight, document.body?.scrollHeight || 0);
    return height > viewport + 100 && scrollTop + viewport >= height - 2;
  }

  function autoScrollFrame(now) {
    if (!lastFrame) lastFrame = now;
    const deltaSeconds = Math.min((now - lastFrame) / 1000, 0.1);
    lastFrame = now;

    if (settings.autoScroll && !navigationLocked) {
      const before = window.scrollY;
      window.scrollBy(0, settings.speed * deltaSeconds);
      const after = window.scrollY;

      if (imagesReady && isAtBottom()) {
        if (!bottomSince) bottomSince = now;
        const held = now - bottomSince;
        if (held >= NAV_BOTTOM_HOLD_MS && now >= NAV_COOLDOWN_MS) {
          navigate(nextUrl);
        }
      } else if (after !== before || !isAtBottom()) {
        bottomSince = 0;
      }
    } else {
      bottomSince = 0;
    }

    rafId = requestAnimationFrame(autoScrollFrame);
  }

  function onKeyDown(event) {
    if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return;

    const target = event.target;
    if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement || target?.isContentEditable) {
      return;
    }

    switch (event.key) {
      case 'ArrowRight':
        event.preventDefault();
        event.stopImmediatePropagation();
        navigate(nextUrl);
        break;
      case 'ArrowLeft':
        event.preventDefault();
        event.stopImmediatePropagation();
        navigate(previousUrl);
        break;
      case 'ArrowUp':
        event.preventDefault();
        event.stopImmediatePropagation();
        scrollByKeyboard(-1);
        break;
      case 'ArrowDown':
        event.preventDefault();
        event.stopImmediatePropagation();
        scrollByKeyboard(1);
        break;
      case 'Enter':
        event.preventDefault();
        event.stopImmediatePropagation();
        toggleAutoScroll();
        break;
      default:
        break;
    }
  }

  function onStorageChanged(changes, areaName) {
    if (areaName !== 'local') return;
    if (changes.speed) {
      settings.speed = clamp(Number(changes.speed.newValue) || DEFAULTS.speed, MIN_SPEED, MAX_SPEED);
    }
    if (changes.autoScroll && Boolean(changes.autoScroll.newValue) !== settings.autoScroll) {
      settings.autoScroll = Boolean(changes.autoScroll.newValue);
      bottomSince = 0;
    }
  }

  async function init() {
    const images = extractImages();
    if (!images.length) return;

    extractChapterNavigation();
    await loadSettings();

    // Register the key handler before replacing the page, and use capture so
    // it wins over bubbling handlers left behind by the original site scripts.
    window.addEventListener('keydown', onKeyDown, true);
    chrome.storage.onChanged.addListener(onStorageChanged);

    buildCleanReader(images);
    lastFrame = performance.now();
    rafId = requestAnimationFrame(autoScrollFrame);

    window.addEventListener('pagehide', () => {
      if (rafId) cancelAnimationFrame(rafId);
    }, { once: true });
  }

  init().catch((error) => {
    console.error('[Simple Manga Reader] Failed to initialize:', error);
  });
})();
