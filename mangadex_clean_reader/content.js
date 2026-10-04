(() => {
  'use strict';

  const ROOT_ID = 'smr-root';
  const PAGES_ID = 'smr-pages';
  const DEFAULT_SPEED = 120; // pixels per second
  const MIN_SPEED = 10;
  const MAX_SPEED = 1000;

  let speed = DEFAULT_SPEED;
  let autoScrollEnabled = false;
  let heldDirection = 0;
  let lastFrame = performance.now();
  let lastBuildAt = 0;
  let lastImageChangeAt = 0;
  let lastNavigationAt = 0;
  let lastSignature = '';
  let rebuildTimer = 0;

  const clampSpeed = (value) => {
    const n = Number(value);
    if (!Number.isFinite(n)) return DEFAULT_SPEED;
    return Math.max(MIN_SPEED, Math.min(MAX_SPEED, n));
  };

  function getStorage(defaults) {
    return new Promise((resolve) => chrome.storage.local.get(defaults, resolve));
  }

  function setStorage(values) {
    return new Promise((resolve) => chrome.storage.local.set(values, resolve));
  }

  function getSourceImages() {
    return Array.from(document.querySelectorAll('.md--reader-pages img'))
      .filter((img) => {
        const src = img.currentSrc || img.src || img.getAttribute('src') || '';
        return Boolean(src);
      });
  }

  function sourceSignature(images) {
    return images.map((img) => `${img.alt || ''}|${img.currentSrc || img.src || ''}`).join('\n');
  }

  function ensureRoot() {
    let root = document.getElementById(ROOT_ID);
    if (root) return root;

    document.documentElement.classList.add('smr-active');
    document.body.classList.add('smr-active');

    root = document.createElement('main');
    root.id = ROOT_ID;
    root.setAttribute('aria-label', '');

    const pages = document.createElement('div');
    pages.id = PAGES_ID;
    root.appendChild(pages);
    document.body.appendChild(root);

    // Do not add wheel listeners. The browser owns mouse-wheel scrolling.
    return root;
  }

  function rebuildReader(force = false) {
    const images = getSourceImages();
    if (!images.length) return false;

    const sig = sourceSignature(images);
    if (!force && sig === lastSignature) return true;

    const root = ensureRoot();
    const pages = root.querySelector(`#${PAGES_ID}`);
    if (!pages) return false;

    const previousY = window.scrollY;
    const fragment = document.createDocumentFragment();

    for (const source of images) {
      const clone = document.createElement('img');
      clone.alt = '';
      clone.decoding = 'async';
      clone.loading = 'eager';
      clone.draggable = false;
      clone.referrerPolicy = source.referrerPolicy || '';
      clone.src = source.currentSrc || source.src || source.getAttribute('src');
      fragment.appendChild(clone);
    }

    pages.replaceChildren(fragment);
    lastSignature = sig;
    lastBuildAt = Date.now();
    lastImageChangeAt = Date.now();

    // Keep the current reading position when MangaDex mutates its hidden app.
    requestAnimationFrame(() => {
      if (previousY > 0) window.scrollTo(0, previousY);
    });

    return true;
  }

  function scheduleRebuild() {
    clearTimeout(rebuildTimer);
    rebuildTimer = window.setTimeout(() => rebuildReader(false), 80);
  }

  function nextChapterAnchor() {
    const anchors = Array.from(document.querySelectorAll('a[href*="/chapter/"]'));

    // MangaDex's reader exposes a real anchor whose visible label is "Next Chapter".
    const byText = anchors.find((a) => /(^|\s)next\s+chapter(\s|$)/i.test(a.textContent || ''));
    if (byText) return byText;

    // Conservative fallbacks in case MangaDex changes the label but keeps semantic hints.
    return anchors.find((a) => /next/i.test(a.getAttribute('aria-label') || a.getAttribute('title') || '')) || null;
  }

  function allVisibleImagesReady() {
    const images = Array.from(document.querySelectorAll(`#${PAGES_ID} > img`));
    return images.length > 0 && images.every((img) => img.complete && img.naturalWidth > 0 && img.naturalHeight > 0);
  }

  function atDocumentBottom() {
    const doc = document.scrollingElement || document.documentElement;
    return window.scrollY + window.innerHeight >= doc.scrollHeight - 3;
  }

  function maybeContinueToNextChapter() {
    if (!autoScrollEnabled) return false;
    if (!atDocumentBottom()) return false;
    if (!allVisibleImagesReady()) return false;
    if (Date.now() - lastBuildAt < 1800) return false;
    if (Date.now() - lastImageChangeAt < 800) return false;
    if (Date.now() - lastNavigationAt < 2500) return true;

    const next = nextChapterAnchor();
    if (!next) return false;

    lastNavigationAt = Date.now();
    // Reuse MangaDex's own Next Chapter control rather than calculating chapter IDs.
    next.click();
    return true;
  }

  async function setAutoScroll(enabled) {
    autoScrollEnabled = Boolean(enabled);
    await setStorage({ autoScrollEnabled });
  }

  function isEditableTarget(target) {
    if (!(target instanceof Element)) return false;
    return Boolean(target.closest('input, textarea, select, [contenteditable="true"]'));
  }

  // IMPORTANT: LeftArrow and RightArrow are deliberately NOT handled here.
  // MangaDex already owns them, so its native previous/next behavior remains intact.
  window.addEventListener('keydown', (event) => {
    if (isEditableTarget(event.target)) return;

    if (event.key === 'ArrowUp') {
      event.preventDefault();
      event.stopImmediatePropagation();
      heldDirection = -1;
      return;
    }

    if (event.key === 'ArrowDown') {
      event.preventDefault();
      event.stopImmediatePropagation();
      heldDirection = 1;
      return;
    }

    if (event.key === 'Enter' && !event.repeat) {
      event.preventDefault();
      event.stopImmediatePropagation();
      void setAutoScroll(!autoScrollEnabled);
    }
  }, true);

  window.addEventListener('keyup', (event) => {
    if (event.key === 'ArrowUp' && heldDirection < 0) {
      event.preventDefault();
      event.stopImmediatePropagation();
      heldDirection = 0;
    } else if (event.key === 'ArrowDown' && heldDirection > 0) {
      event.preventDefault();
      event.stopImmediatePropagation();
      heldDirection = 0;
    }
  }, true);

  window.addEventListener('blur', () => {
    heldDirection = 0;
  });

  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName !== 'local') return;
    if (changes.scrollSpeed) speed = clampSpeed(changes.scrollSpeed.newValue);
    if (changes.autoScrollEnabled) autoScrollEnabled = Boolean(changes.autoScrollEnabled.newValue);
  });

  const observer = new MutationObserver(() => {
    const imgs = getSourceImages();
    if (!imgs.length) return;
    const sig = sourceSignature(imgs);
    if (sig !== lastSignature) {
      lastImageChangeAt = Date.now();
      scheduleRebuild();
    }
  });

  function animationFrame(now) {
    const dt = Math.min(64, now - lastFrame) / 1000;
    lastFrame = now;

    const direction = heldDirection || (autoScrollEnabled ? 1 : 0);
    if (direction !== 0) {
      const before = window.scrollY;
      window.scrollBy(0, direction * speed * dt);

      if (direction > 0 && before === window.scrollY) {
        maybeContinueToNextChapter();
      }
    } else if (autoScrollEnabled) {
      maybeContinueToNextChapter();
    }

    requestAnimationFrame(animationFrame);
  }

  async function start() {
    const stored = await getStorage({
      scrollSpeed: DEFAULT_SPEED,
      autoScrollEnabled: false
    });

    speed = clampSpeed(stored.scrollSpeed);
    autoScrollEnabled = Boolean(stored.autoScrollEnabled);

    // Wait for MangaDex to populate its blob-backed chapter images.
    const waitUntilReady = () => {
      if (rebuildReader(true)) {
        observer.observe(document.documentElement, {
          childList: true,
          subtree: true,
          attributes: true,
          attributeFilter: ['src']
        });
        requestAnimationFrame(animationFrame);
      } else {
        window.setTimeout(waitUntilReady, 120);
      }
    };

    waitUntilReady();
  }

  void start();
})();
