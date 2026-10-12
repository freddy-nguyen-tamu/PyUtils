(() => {
  'use strict';

  // Mouse-wheel events are entirely browser-owned. This code never listens
  // to, cancels, dispatches, simulates, or otherwise alters a wheel event.
  if (!/^\/manhwa\/[^/]+\/chapter-[^/]+\/?$/i.test(location.pathname)) return;

  const root = document.documentElement;
  root.classList.add('mcr-pending');

  const DEFAULT_RATE = 180;
  const MIN_RATE = 20;
  const MAX_RATE = 600;
  const BOTTOM_TOLERANCE = 3;
  let rate = DEFAULT_RATE;
  let automatic = false;
  let mounted = false;
  let navigating = false;
  let scroller = null;
  let images = null;
  let nextUrl = null;
  let prevUrl = null;
  let heldUp = false;
  let heldDown = false;
  let animationId = 0;
  let lastFrame = 0;
  let bottomTimer = 0;
  let scrollIntent = false;
  let pausedUntil = 0;
  let scriptScrollExpected = null;

  function clampRate(value) {
    const n = Number(value);
    return Number.isFinite(n) ? Math.max(MIN_RATE, Math.min(MAX_RATE, n)) : DEFAULT_RATE;
  }

  const cleanPath = path => path.replace(/\/+$/, '') || '/';

  function resolveNavigation() {
    const links = [...document.querySelectorAll('#chaptersList a.chapter-item[href]')]
      .map(a => {
        try {
          const url = new URL(a.getAttribute('href'), location.href);
          if (url.origin !== location.origin || !/^\/manhwa\/[^/]+\/chapter-/i.test(url.pathname)) return null;
          return { url, current: a.classList.contains('current') };
        } catch (_) {
          return null;
        }
      })
      .filter(Boolean);

    let current = links.findIndex(link => cleanPath(link.url.pathname) === cleanPath(location.pathname));
    if (current < 0) current = links.findIndex(link => link.current);
    prevUrl = current > 0 ? links[current - 1].url.href : null;
    nextUrl = current >= 0 && current < links.length - 1 ? links[current + 1].url.href : null;
    // Deliberately never infer chapter numbers: e.g. chapter-147-the-end.
  }

  function maximum() {
    return scroller ? Math.max(0, scroller.scrollHeight - scroller.clientHeight) : 0;
  }

  function atBottom() {
    return !!scroller && maximum() - scroller.scrollTop <= BOTTOM_TOLERANCE;
  }

  function allPanelsReady() {
    const list = images?.querySelectorAll('.image-page img');
    return !!(list?.length && [...list].every(img =>
      !!(img.currentSrc || img.getAttribute('src')) && img.complete && img.naturalWidth > 0
    ));
  }

  function cancelBottom() {
    if (bottomTimer) clearTimeout(bottomTimer);
    bottomTimer = 0;
  }

  function saveAutomatic() {
    chrome.storage.local.set({ automatic });
  }

  function navigate(url) {
    if (!url || navigating) return;
    navigating = true;
    cancelBottom();
    if (animationId) cancelAnimationFrame(animationId);
    animationId = 0;
    location.assign(url);
  }

  function finishChapter() {
    if (!mounted || navigating || !atBottom() || !allPanelsReady()) return;
    if (nextUrl) {
      navigate(nextUrl);
    } else {
      if (automatic) {
        automatic = false;
        saveAutomatic();
      }
      stopAnimationIfIdle();
    }
  }

  function checkBottom() {
    cancelBottom();
    if (!mounted || navigating || !scrollIntent || !atBottom()) return;
    bottomTimer = setTimeout(() => {
      bottomTimer = 0;
      if (atBottom()) finishChapter();
    }, automatic ? 800 : 1500);
  }

  function stopAnimationIfIdle() {
    if (automatic || heldUp || heldDown) return;
    if (animationId) cancelAnimationFrame(animationId);
    animationId = 0;
    lastFrame = 0;
  }

  // Only changes the element scrollTop for the explicit keyboard/auto mode.
  // Browser mouse/touchpad wheel scrolling follows its independent native path.
  function keyboardScroll(delta) {
    if (!scroller) return;
    scriptScrollExpected = Math.max(0, Math.min(maximum(), scroller.scrollTop + delta));
    scroller.scrollTop = scriptScrollExpected;
  }

  function animate(time) {
    animationId = 0;
    if (!mounted || navigating || !(automatic || heldUp || heldDown)) {
      lastFrame = 0;
      return;
    }
    if (!lastFrame) lastFrame = time;
    const dt = Math.max(0, Math.min((time - lastFrame) / 1000, 0.05));
    lastFrame = time;

    if (document.visibilityState !== 'hidden') {
      const direction = heldUp ? -1 : (heldDown ? 1 : automatic ? 1 : 0);
      if (direction !== 0 && (heldUp || heldDown || time >= pausedUntil)) {
        scrollIntent = true;
        keyboardScroll(direction * rate * dt);
      }
      if (direction > 0 && atBottom()) {
        if (!bottomTimer) checkBottom();
      } else if (direction < 0 || !atBottom()) {
        cancelBottom();
      }
    }
    animationId = requestAnimationFrame(animate);
  }

  function ensureAnimation() {
    if (!mounted || animationId || !(automatic || heldUp || heldDown)) return;
    lastFrame = 0;
    animationId = requestAnimationFrame(animate);
  }

  function setAutomatic(value, persist = false) {
    automatic = !!value;
    if (persist) saveAutomatic();
    cancelBottom();
    if (automatic) {
      scrollIntent = true;
      ensureAnimation();
      if (atBottom()) checkBottom();
    } else {
      stopAnimationIfIdle();
    }
  }

  function keydown(event) {
    if (!mounted || navigating || event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.target?.closest?.('input,textarea,select,[contenteditable="true"]')) return;
    if (!['ArrowRight', 'ArrowLeft', 'ArrowUp', 'ArrowDown', 'Enter'].includes(event.key)) return;
    event.preventDefault();
    event.stopImmediatePropagation();

    if (event.key === 'ArrowRight') {
      if (!event.repeat) navigate(nextUrl);
    } else if (event.key === 'ArrowLeft') {
      if (!event.repeat) navigate(prevUrl);
    } else if (event.key === 'Enter') {
      if (!event.repeat) setAutomatic(!automatic, true);
    } else {
      const down = event.key === 'ArrowDown';
      const wasHeld = down ? heldDown : heldUp;
      if (down) heldDown = true;
      else heldUp = true;
      scrollIntent = true;
      pausedUntil = 0;
      if (!wasHeld) keyboardScroll((down ? 1 : -1) * Math.max(35, rate * 0.12));
      ensureAnimation();
    }
  }

  function keyup(event) {
    if (!mounted || !['ArrowUp', 'ArrowDown'].includes(event.key)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (event.key === 'ArrowDown') heldDown = false;
    else heldUp = false;
    stopAnimationIfIdle();
  }

  // Observe scroll *position*, never wheel input. If browser-native scrolling
  // diverges from the last scripted position, temporarily give manual control
  // priority over automatic scrolling without toggling its saved state.
  function onScroll() {
    if (!mounted || navigating) return;
    scrollIntent = true;
    const actual = scroller.scrollTop;
    if (scriptScrollExpected === null || Math.abs(actual - scriptScrollExpected) > 2) {
      if (automatic && !heldUp && !heldDown) pausedUntil = performance.now() + 2000;
    }
    scriptScrollExpected = actual;
    if (atBottom()) {
      if (!bottomTimer) checkBottom();
    } else {
      cancelBottom();
    }
  }

  function hideChromeWithoutMovingReader(reader) {
    // Preserve the complete ancestor chain: no reparenting of blob images,
    // no broken site lazy-loader references and no observer disconnections.
    let kept = reader;
    for (let parent = reader.parentElement; parent; parent = parent.parentElement) {
      if (parent === document.documentElement) break;
      if (parent !== document.body) parent.classList.add('mcr-ancestor');
      for (const sibling of parent.children) {
        if (sibling !== kept) sibling.classList.add('mcr-hidden');
      }
      kept = parent;
    }
  }

  function mount() {
    if (mounted || !document.body) return false;
    const reader = document.getElementById('reader');
    const foundImages = document.getElementById('imagesList');
    const content = document.getElementById('readingContent');
    if (!reader || !foundImages || !content || !reader.contains(content)
        || !content.contains(foundImages) || !foundImages.querySelector('.image-page img')) return false;

    // Site chapter navigation and lazy image loading remain in the same DOM.
    resolveNavigation();
    scroller = reader;
    images = foundImages;
    hideChromeWithoutMovingReader(reader);
    root.classList.remove('mcr-pending');
    root.classList.add('mcr-active');
    mounted = true;

    window.addEventListener('keydown', keydown, true);
    window.addEventListener('keyup', keyup, true);
    window.addEventListener('blur', () => {
      heldUp = false;
      heldDown = false;
      stopAnimationIfIdle();
    });
    scroller.addEventListener('scroll', onScroll, { passive: true });
    images.addEventListener('load', () => {
      if (scrollIntent && atBottom() && !bottomTimer) checkBottom();
    }, true);

    // WP Rocket may postpone the site's loader until a regular interaction.
    // One ordinary mousemove wakes that loader WITHOUT any wheel event.
    setTimeout(() => {
      if (mounted && document.querySelector('script[type="rocketlazyloadscript"]')) {
        window.dispatchEvent(new MouseEvent('mousemove', { bubbles: true }));
      }
    }, 500);

    chrome.storage.local.get({ rate: DEFAULT_RATE, automatic: false }, values => {
      rate = clampRate(values.rate);
      setAutomatic(values.automatic);
    });

    chrome.storage.onChanged.addListener((changes, area) => {
      if (area !== 'local') return;
      if (changes.rate) rate = clampRate(changes.rate.newValue);
      if (changes.automatic) setAutomatic(changes.automatic.newValue);
    });
    return true;
  }

  function initialize() {
    if (mount()) return;
    const observer = new MutationObserver(() => {
      if (mount()) observer.disconnect();
    });
    observer.observe(document.documentElement, { childList: true, subtree: true });
    setTimeout(() => {
      observer.disconnect();
      if (!mounted) root.classList.remove('mcr-pending');
    }, 15000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initialize, { once: true });
  } else {
    initialize();
  }
})();
