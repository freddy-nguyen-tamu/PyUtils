(() => {
  'use strict';

  // No 'wheel' or 'mousewheel' listener is ever registered by this extension.
  // Native mouse/trackpad scrolling takes place in a separate, script-free frame.
  if (window.top !== window || document.getElementById('qcr-frame')) return;

  const originalContent = document.querySelector('#chapter-content');
  if (!originalContent) return; // Do not change the homepage, series page, etc.

  const absoluteURL = (raw) => {
    if (!raw || /^(?:none|#|javascript:)/i.test(raw.trim())) return null;
    try {
      const url = new URL(raw.trim(), location.href);
      return url.protocol === 'https:' || url.protocol === 'http:' ? url.href : null;
    } catch {
      return null;
    }
  };

  const imageSources = [...originalContent.querySelectorAll('img')]
    .map((original) => {
      const raw = original.getAttribute('data-src') ||
        original.getAttribute('data-original') ||
        original.getAttribute('data-lazy-src') ||
        original.getAttribute('data-url') ||
        original.getAttribute('src') || original.currentSrc;
      return absoluteURL(raw);
    })
    .filter(Boolean);
  if (!imageSources.length) return;

  // Chapter IDs can have nontrivial suffixes (e.g. chapter-74-end.html).
  // Use the site's actual option values, never fabricated chapter URLs.
  function chapterNavigation() {
    const select = document.querySelector('#chaptersList');
    const urlKey = (value) => {
      try {
        const url = new URL(value);
        return `${url.origin}${url.pathname.replace(/\/$/, '')}`;
      } catch {
        return '';
      }
    };
    const chapters = select ? [...select.querySelectorAll('option[value]')]
      .map((opt) => ({ href: absoluteURL(opt.value), label: opt.textContent || '' }))
      .filter((item) => item.href && new URL(item.href).origin === location.origin) : [];
    const current = chapters.findIndex((chapter) => urlKey(chapter.href) === urlKey(location.href));
    if (current >= 0 && chapters.length > 1) {
      const numberOf = (chapter) => {
        const url = new URL(chapter.href);
        const matches = url.pathname.match(/(?:chapter|chap|chuong|chương)[-_ ]*(\d+(?:[.,]\d+)?)/i);
        if (matches) return Number(matches[1].replace(',', '.'));
        const label = chapter.label.match(/(?:chapter|chap|chuong|chương)\s*(\d+(?:[.,]\d+)?)/i);
        return label ? Number(label[1].replace(',', '.')) : null;
      };
      let ascending = true;
      for (let i = 1; i < chapters.length; i++) {
        const a = numberOf(chapters[i - 1]);
        const b = numberOf(chapters[i]);
        if (a !== null && b !== null && a !== b) {
          ascending = b > a;
          break;
        }
      }
      return {
        previous: chapters[current + (ascending ? -1 : 1)]?.href || null,
        next: chapters[current + (ascending ? 1 : -1)]?.href || null
      };
    }

    // Fallback for chapters without a complete select list.
    const links = [...document.querySelectorAll('a[href]')].map((anchor) => ({
      href: absoluteURL(anchor.getAttribute('href')),
      text: (anchor.textContent || '').trim().replace(/\s+/g, ' ')
    })).filter(({ href }) => href && new URL(href).origin === location.origin);
    return {
      previous: links.find(({ text }) => /^(?:trước|previous|prev)(?:\b|$)/i.test(text))?.href || null,
      next: links.find(({ text }) => /^(?:tiếp|next)(?:\b|$)/i.test(text))?.href || null
    };
  }

  const chapters = chapterNavigation();
  const INITIAL_RATE = 140;
  const MIN_RATE = 20;
  const MAX_RATE = 1200;
  const validRate = (value) => {
    const number = Number(value);
    return Number.isFinite(number) ? Math.max(MIN_RATE, Math.min(MAX_RATE, number)) : INITIAL_RATE;
  };

  // Site styles, scripts, click handlers and scroll effects are OUTSIDE this
  // sandboxed about:srcdoc document. Scripts are deliberately NOT permitted.
  // Its only visible contents are the chapter images themselves.
  const frameHTML = `<!doctype html><html lang="en"><head>
    <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <style>
      html { background:#101010; scroll-behavior:auto !important;
        overscroll-behavior:none; scrollbar-width:none; }
      html::-webkit-scrollbar, body::-webkit-scrollbar { display:none; }
      body { margin:0; padding:0; background:#101010; min-height:100vh;
        overflow-x:hidden; overflow-y:visible; scroll-behavior:auto !important; }
      #pages { display:flex; flex-direction:column; align-items:center;
        width:100%; padding:0; margin:0; gap:0; }
      .page { box-sizing:border-box; width:100%; display:flex;
        align-items:flex-start; justify-content:center;
        min-height:min(80vh,850px); padding:0; margin:0; }
      .page.ready { min-height:0; }
      .page img { display:block; width:auto; height:auto; max-width:100%;
        margin:0; padding:0; border:0; }
      .page img:not(.ready) { visibility:hidden; }
      .page.failed { display:none; }
    </style></head><body><main id="pages"></main></body></html>`;

  const frame = document.createElement('iframe');
  frame.id = 'qcr-frame';
  frame.setAttribute('sandbox', 'allow-same-origin'); // no allow-scripts
  frame.setAttribute('title', 'Chapter images');
  frame.setAttribute('scrolling', 'yes');
  frame.setAttribute('referrerpolicy', 'strict-origin-when-cross-origin');
  frame.style.setProperty('position', 'fixed', 'important');
  frame.style.setProperty('inset', '0', 'important');
  frame.style.setProperty('display', 'block', 'important');
  frame.style.setProperty('width', '100vw', 'important');
  frame.style.setProperty('height', '100vh', 'important');
  frame.style.setProperty('height', '100dvh', 'important');
  frame.style.setProperty('background', '#101010', 'important');
  frame.style.setProperty('border', '0', 'important');
  frame.style.setProperty('padding', '0', 'important');
  frame.style.setProperty('margin', '0', 'important');
  frame.style.setProperty('z-index', '2147483647', 'important');

  // This reset prevents the underlying site's bouncing page layout from
  // becoming a second scrollbar. No wheel events are altered or intercepted.
  document.documentElement.style.setProperty('overflow', 'hidden', 'important');
  document.documentElement.style.setProperty('scroll-behavior', 'auto', 'important');
  document.body.style.setProperty('overflow', 'hidden', 'important');
  document.body.style.setProperty('margin', '0', 'important');
  document.body.style.setProperty('padding', '0', 'important');
  document.body.style.setProperty('background', '#101010', 'important');

  let scrollOn = false;
  let rate = INITIAL_RATE;
  let heldDirection = 0;
  const heldKeys = new Set();
  let raf = 0;
  let lastFrameTime = 0;
  let lastLoadCheck = 0;
  let loadingAhead = false;
  let endReachedAt = 0;
  let changingChapter = false;
  let settingsReady = false;
  let reader = null;
  let pages = [];
  let latestWrite = Promise.resolve();
  let firstImageLoaded = false;

  function scrollingElement() {
    return reader?.scrollingElement || reader?.documentElement || null;
  }

  function queueAnimation() {
    if (!raf && reader && settingsReady && (scrollOn || heldDirection)) {
      raf = requestAnimationFrame(animate);
    }
  }

  function stopAnimation() {
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
    lastFrameTime = 0;
    endReachedAt = 0;
  }

  function setAuto(enabled, persist = true) {
    scrollOn = Boolean(enabled);
    if (persist) {
      latestWrite = chrome.storage.local.set({ autoScrollEnabled: scrollOn })
        .catch((error) => console.warn('Quiet Reader: cannot save setting', error));
    }
    if (scrollOn || heldDirection) queueAnimation();
    else stopAnimation();
  }

  function navigate(url) {
    if (!url || changingChapter) return;
    changingChapter = true;
    stopAnimation();
    // If Enter and Right are pressed immediately together, finish saving
    // the on/off state before opening the next chapter.
    Promise.resolve(latestWrite).finally(() => location.assign(url));
  }

  function loadingNearViewport() {
    const scroll = scrollingElement();
    if (!scroll) return true;
    // Throttle layout reads; do not measure every image at 60 fps.
    const limit = scroll.scrollTop + scroll.clientHeight + 100;
    for (const page of pages) {
      if (!page.done && page.node.offsetTop <= limit) return true;
    }
    return false;
  }

  function animate(now) {
    raf = 0;
    if (changingChapter || !reader || (!scrollOn && !heldDirection)) return;
    const scroll = scrollingElement();
    if (!scroll) return;

    const seconds = lastFrameTime ? Math.min((now - lastFrameTime) / 1000, 0.12) : 0;
    lastFrameTime = now;

    if (now - lastLoadCheck > 180) {
      loadingAhead = loadingNearViewport();
      lastLoadCheck = now;
    }

    // While ArrowUp/ArrowDown is held it overrides, but does not disable,
    // the user's saved auto-scroll mode. A single tap also makes a step.
    const direction = heldDirection || (scrollOn ? 1 : 0);
    if (direction && (heldDirection || !loadingAhead)) {
      scroll.scrollTop += direction * rate * seconds;
    }

    if (scrollOn && !heldDirection && !loadingAhead) {
      const atBottom = scroll.scrollTop + scroll.clientHeight >= scroll.scrollHeight - 2;
      const allFinished = pages.every((page) => page.done);
      if (allFinished && !firstImageLoaded) {
        // Never advance through blank or entirely failed chapters.
        setAuto(false);
        return;
      }
      if (atBottom && allFinished && firstImageLoaded) {
        if (!endReachedAt) endReachedAt = now;
        // Wait for layout to settle rather than navigating on a brief resize.
        if (now - endReachedAt > 650) {
          if (chapters.next) {
            navigate(chapters.next);
            return;
          }
          // Final chapter. Stop safely instead of repeatedly reloading.
          setAuto(false);
          return;
        }
      } else {
        endReachedAt = 0;
      }
    } else {
      endReachedAt = 0;
    }
    queueAnimation();
  }

  function onKeyboard(event) {
    if (event.defaultPrevented || event.isComposing || event.altKey ||
        event.ctrlKey || event.metaKey || event.shiftKey) return;
    if (!['ArrowRight', 'ArrowLeft', 'ArrowUp', 'ArrowDown', 'Enter'].includes(event.key)) return;
    const tag = event.target?.tagName?.toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select' || event.target?.isContentEditable) return;
    event.preventDefault();
    event.stopImmediatePropagation();

    if (event.key === 'Enter') {
      if (!event.repeat) setAuto(!scrollOn);
    } else if (event.key === 'ArrowRight') {
      if (!event.repeat) navigate(chapters.next);
    } else if (event.key === 'ArrowLeft') {
      if (!event.repeat) navigate(chapters.previous);
    } else {
      const direction = event.key === 'ArrowUp' ? -1 : 1;
      if (!heldKeys.has(event.key)) {
        heldKeys.add(event.key);
        // A short key tap should visibly scroll even if released before RAF.
        const scroll = scrollingElement();
        if (scroll) scroll.scrollTop += direction * Math.max(20, rate * 0.17);
      }
      heldDirection = direction;
      queueAnimation();
    }
  }

  function onKeyUp(event) {
    if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
    event.preventDefault();
    event.stopImmediatePropagation();
    heldKeys.delete(event.key);
    heldDirection = heldKeys.has('ArrowUp') ? -1 : heldKeys.has('ArrowDown') ? 1 : 0;
    if (!scrollOn && !heldDirection) stopAnimation();
  }

  function attachKeys(target) {
    target.addEventListener('keydown', onKeyboard, true);
    target.addEventListener('keyup', onKeyUp, true);
  }

  // Key presses work whether the frame or the tab itself has keyboard focus.
  attachKeys(window);
  window.addEventListener('blur', () => {
    heldKeys.clear();
    heldDirection = 0;
    if (!scrollOn) stopAnimation();
  });

  frame.addEventListener('load', () => {
    // The frame's document contains no website scripts or styles.
    reader = frame.contentDocument;
    if (!reader) return;
    const column = reader.getElementById('pages');
    if (!column) return;
    attachKeys(frame.contentWindow);
    const fragment = reader.createDocumentFragment();

    pages = imageSources.map((src, index) => {
      const slot = reader.createElement('div');
      slot.className = 'page';
      const image = reader.createElement('img');
      image.alt = ''; // No captions, broken-image text, or other letters.
      image.decoding = 'async';
      image.loading = index < 2 ? 'eager' : 'lazy';
      if (index === 0) image.fetchPriority = 'high';
      const state = { node: slot, done: false };
      const finish = (success) => {
        if (state.done) return;
        state.done = true;
        if (success) {
          firstImageLoaded = true;
          image.classList.add('ready');
          slot.classList.add('ready');
        } else {
          slot.classList.add('failed');
        }
        loadingAhead = loadingNearViewport();
        endReachedAt = 0;
        queueAnimation();
      };
      image.addEventListener('load', () => finish(image.naturalWidth > 0), { once: true });
      image.addEventListener('error', () => finish(false), { once: true });
      image.src = src;
      slot.appendChild(image);
      fragment.appendChild(slot);
      return state;
    });
    column.appendChild(fragment);
    loadingAhead = true;
    if (settingsReady) queueAnimation();
    // Give keyboard focus to the clean scroll view, not hidden site links.
    frame.contentWindow.focus();
  }, { once: true });

  // Register the load listener before attaching the iframe, avoiding the
  // initial about:blank / srcdoc load race in some Chromium versions.
  // Cancel outstanding *original page* requests before creating the reader;
  // the reader's own image requests start only after the frame is attached.
  try { window.stop(); } catch { /* Nothing pending. */ }
  frame.srcdoc = frameHTML;
  document.body.replaceChildren(frame);

  chrome.storage.local.get({ autoScrollEnabled: false, ratePxPerSecond: INITIAL_RATE })
    .then((saved) => {
      rate = validRate(saved.ratePxPerSecond);
      scrollOn = saved.autoScrollEnabled === true;
      settingsReady = true;
      queueAnimation();
    })
    .catch(() => { settingsReady = true; queueAnimation(); });

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== 'local') return;
    if (changes.ratePxPerSecond) rate = validRate(changes.ratePxPerSecond.newValue);
    if (changes.autoScrollEnabled) setAuto(changes.autoScrollEnabled.newValue, false);
  });
})();
