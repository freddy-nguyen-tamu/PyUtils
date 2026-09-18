(() => {
  'use strict';

  const DEFAULT_SPEED = 900; // pixels per second while holding ArrowUp/ArrowDown
  const MIN_SPEED = 100;
  const MAX_SPEED = 5000;

  let scrollSpeed = DEFAULT_SPEED;
  let scrollDirection = 0;
  let animationFrame = null;
  let lastFrameTime = 0;
  let chapterNavigationLocked = false;

  function clampSpeed(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return DEFAULT_SPEED;
    return Math.min(MAX_SPEED, Math.max(MIN_SPEED, number));
  }

  chrome.storage.sync.get({ scrollSpeed: DEFAULT_SPEED }, ({ scrollSpeed: saved }) => {
    scrollSpeed = clampSpeed(saved);
  });

  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName === 'sync' && changes.scrollSpeed) {
      scrollSpeed = clampSpeed(changes.scrollSpeed.newValue);
    }
  });

  function isTypingTarget(target) {
    if (!(target instanceof Element)) return false;
    return Boolean(
      target.closest('input, textarea, [contenteditable="true"], [role="textbox"]')
    );
  }

  function stopSiteHandler(event) {
    event.preventDefault();
    event.stopImmediatePropagation();
  }

  function scrollFrame(now) {
    if (!scrollDirection) {
      animationFrame = null;
      lastFrameTime = 0;
      return;
    }

    if (!lastFrameTime) lastFrameTime = now;
    const deltaMs = Math.min(50, Math.max(0, now - lastFrameTime));
    lastFrameTime = now;

    window.scrollBy(0, scrollDirection * scrollSpeed * (deltaMs / 1000));
    animationFrame = requestAnimationFrame(scrollFrame);
  }

  function startScrolling(direction) {
    if (scrollDirection === direction && animationFrame !== null) return;
    scrollDirection = direction;
    lastFrameTime = 0;
    if (animationFrame === null) {
      animationFrame = requestAnimationFrame(scrollFrame);
    }
  }

  function stopScrolling(direction) {
    if (scrollDirection !== direction) return;
    scrollDirection = 0;
  }

  function normalizePath(value) {
    try {
      return new URL(value, location.href).pathname.replace(/\/+$/, '') || '/';
    } catch {
      return String(value || '').replace(/[?#].*$/, '').replace(/\/+$/, '') || '/';
    }
  }

  function chapterNumber(option) {
    const text = option?.textContent || '';
    const match = text.match(/chapter\s*([0-9]+(?:\.[0-9]+)?)/i);
    return match ? Number(match[1]) : NaN;
  }

  function getChapterSelect() {
    const selects = [...document.querySelectorAll('select.select-reading')];
    if (!selects.length) return null;

    const currentPath = normalizePath(location.pathname);
    return (
      selects.find((select) =>
        [...select.options].some((option) => normalizePath(option.value) === currentPath)
      ) || selects[0]
    );
  }

  function getCurrentOption(select) {
    const currentPath = normalizePath(location.pathname);
    return (
      [...select.options].find((option) => normalizePath(option.value) === currentPath) ||
      select.options[select.selectedIndex] ||
      null
    );
  }

  function findChapterByNumber(select, currentOption, direction) {
    const currentNumber = chapterNumber(currentOption);
    if (!Number.isFinite(currentNumber)) return null;

    const candidates = [...select.options]
      .map((option) => ({ option, number: chapterNumber(option) }))
      .filter(({ number }) =>
        Number.isFinite(number) && (direction > 0 ? number > currentNumber : number < currentNumber)
      )
      .sort((a, b) =>
        direction > 0 ? a.number - b.number : b.number - a.number
      );

    return candidates[0]?.option || null;
  }

  function findChapterByDomOrder(select, currentOption, direction) {
    const options = [...select.options];
    const index = options.indexOf(currentOption);
    if (index < 0) return null;

    // The attached TruyenQQ chapter form is newest -> oldest, so a newer/next
    // chapter is normally one option upward and previous is one option downward.
    const targetIndex = direction > 0 ? index - 1 : index + 1;
    return options[targetIndex] || null;
  }

  function goToChapter(direction) {
    if (chapterNavigationLocked) return;

    const select = getChapterSelect();
    if (!select) return;

    const currentOption = getCurrentOption(select);
    if (!currentOption) return;

    const targetOption =
      findChapterByNumber(select, currentOption, direction) ||
      findChapterByDomOrder(select, currentOption, direction);

    if (!targetOption?.value) return;

    chapterNavigationLocked = true;
    const targetUrl = new URL(targetOption.value, location.href).href;
    location.assign(targetUrl);
  }

  function onKeyDown(event) {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    if (isTypingTarget(event.target)) return;

    if (event.key === 'ArrowDown') {
      stopSiteHandler(event);
      startScrolling(1);
      return;
    }

    if (event.key === 'ArrowUp') {
      stopSiteHandler(event);
      startScrolling(-1);
      return;
    }

    if (event.key === 'ArrowRight') {
      stopSiteHandler(event);
      if (!event.repeat) goToChapter(1);
      return;
    }

    if (event.key === 'ArrowLeft') {
      stopSiteHandler(event);
      if (!event.repeat) goToChapter(-1);
    }
  }

  function onKeyUp(event) {
    if (event.key === 'ArrowDown') {
      stopSiteHandler(event);
      stopScrolling(1);
    } else if (event.key === 'ArrowUp') {
      stopSiteHandler(event);
      stopScrolling(-1);
    } else if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      stopSiteHandler(event);
    }
  }

  function stopOnBlur() {
    scrollDirection = 0;
  }

  // Capture on window so TruyenQQ's own document-level keyboard listener never
  // receives these arrow-key events.
  window.addEventListener('keydown', onKeyDown, { capture: true, passive: false });
  window.addEventListener('keyup', onKeyUp, { capture: true, passive: false });
  window.addEventListener('blur', stopOnBlur, true);
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) stopOnBlur();
  });
})();
