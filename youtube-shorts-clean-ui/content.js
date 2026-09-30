(() => {
  'use strict';

  const ROOT = document.documentElement;
  const ACTIVE = 'qs-shorts-active';
  const COMPACT = 'qs-shorts-compact';
  const UI_ID = 'qs-shorts-ui';
  const ITEM_SELECTOR = 'ytd-shorts .reel-video-in-sequence-new';
  const CONTAINER_SELECTOR = 'ytd-shorts #shorts-container';
  const OVERLAY_SELECTOR = 'ytd-shorts #reel-overlay-container';
  const RENDERER_SELECTOR = 'ytd-shorts #reel-video-renderer';

  let active = false;
  let lastHref = location.href;
  let lastCurrentId = null;
  let resizeTimer = 0;
  let fitRAF = 0;
  let mutationRAF = 0;
  let wheelAccum = 0;
  let wheelResetTimer = 0;
  let navigationLockedUntil = 0;

  function onShortsRoute() {
    return location.pathname === '/shorts' || location.pathname.startsWith('/shorts/');
  }

  function viewportHeight() {
    return Math.round(window.visualViewport?.height || window.innerHeight || 0);
  }

  function viewportWidth() {
    return Math.round(window.visualViewport?.width || window.innerWidth || 0);
  }

  // F11 is browser fullscreen, not the Fullscreen API. In Chrome, the inner
  // viewport becomes essentially the screen size. We deliberately restore the
  // native YouTube layout there, as requested.
  function isBrowserFullscreen() {
    if (document.fullscreenElement) return true;

    const w = viewportWidth();
    const h = viewportHeight();
    const sw = Math.round(window.screen?.width || 0);
    const sh = Math.round(window.screen?.height || 0);

    if (!w || !h || !sw || !sh) return false;

    const innerMatchesScreen = Math.abs(w - sw) <= 6 && Math.abs(h - sh) <= 8;
    const outerMatchesScreen =
      Math.abs((window.outerWidth || w) - sw) <= 8 &&
      Math.abs((window.outerHeight || h) - sh) <= 8;

    return innerMatchesScreen || (outerMatchesScreen && Math.abs(h - sh) <= 16);
  }

  function currentOverlay() {
    return document.querySelector(OVERLAY_SELECTOR);
  }

  function currentItem() {
    return currentOverlay()?.closest('.reel-video-in-sequence-new') || null;
  }

  function rememberCurrent() {
    const item = currentItem();
    if (item?.id) lastCurrentId = item.id;
    return item;
  }

  function currentContainer() {
    return document.querySelector(CONTAINER_SELECTOR);
  }

  function itemById(id) {
    if (id == null) return null;
    const items = document.querySelectorAll(ITEM_SELECTOR);
    for (const item of items) {
      if (item.id === String(id)) return item;
    }
    return null;
  }

  function itemTopInsideContainer(item, container) {
    const inner = container.querySelector('#shorts-inner-container');
    if (inner && item.offsetParent === inner) return item.offsetTop;

    // Fallback that works even if YouTube changes offsetParent.
    const itemRect = item.getBoundingClientRect();
    const containerRect = container.getBoundingClientRect();
    return container.scrollTop + (itemRect.top - containerRect.top);
  }

  function alignToItem(id = lastCurrentId) {
    if (!active) return;
    const container = currentContainer();
    const item = itemById(id) || currentItem();
    if (!container || !item) return;

    lastCurrentId = item.id || lastCurrentId;
    const top = itemTopInsideContainer(item, container);
    if (Number.isFinite(top)) {
      container.scrollTo({ top, left: 0, behavior: 'auto' });
    }
  }

  function setViewportVariable() {
    ROOT.style.setProperty('--qs-vh', `${viewportHeight()}px`);
  }

  function fitCurrentRenderer() {
    cancelAnimationFrame(fitRAF);
    fitRAF = requestAnimationFrame(() => {
      if (!active || !ROOT.classList.contains(COMPACT)) {
        ROOT.style.setProperty('--qs-reel-scale', '1');
        return;
      }

      const renderer = document.querySelector(RENDERER_SELECTOR);
      if (!renderer) return;

      ROOT.style.setProperty('--qs-reel-scale', '1');

      requestAnimationFrame(() => {
        if (!ROOT.classList.contains(COMPACT) || !renderer.isConnected) return;

        // offsetWidth/offsetHeight ignore CSS transforms, which lets us measure
        // the native YouTube renderer before applying our fit scale.
        const naturalW = Math.max(renderer.offsetWidth, 1);
        const naturalH = Math.max(renderer.offsetHeight, 1);
        const availableW = Math.max(viewportWidth() - 104, 320);
        const availableH = Math.max(viewportHeight() - 16, 320);

        let scale = Math.min(1, availableW / naturalW, availableH / naturalH);
        if (!Number.isFinite(scale) || scale <= 0) scale = 1;

        ROOT.style.setProperty('--qs-reel-scale', scale.toFixed(4));
      });
    });
  }

  function applyMode({ realign = true } = {}) {
    if (!active) return;

    rememberCurrent();
    setViewportVariable();

    const compact = !isBrowserFullscreen();
    ROOT.classList.toggle(COMPACT, compact);

    // Keep the same logical Short selected across the geometry change. Multiple
    // passes handle Chrome F11 + YouTube reacting to resize on different frames.
    if (realign) {
      requestAnimationFrame(() => alignToItem());
      setTimeout(() => alignToItem(), 70);
      setTimeout(() => alignToItem(), 220);
    }

    fitCurrentRenderer();
  }

  function makeUi() {
    let ui = document.getElementById(UI_ID);
    if (ui) return ui;

    ui = document.createElement('div');
    ui.id = UI_ID;
    ui.setAttribute('aria-hidden', 'false');
    ui.innerHTML = `
      <div class="qs-nav" aria-label="Short navigation">
        <button type="button" data-dir="-1" aria-label="Previous Short" title="Previous Short (wheel up / Arrow Up)">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4 4.5 11.5 5.9 12.9 11 7.8V20h2V7.8l5.1 5.1 1.4-1.4Z"/></svg>
        </button>
        <button type="button" data-dir="1" aria-label="Next Short" title="Next Short (wheel down / Arrow Down)">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 4v12.2l-5.1-5.1-1.4 1.4L12 20l7.5-7.5-1.4-1.4-5.1 5.1V4Z"/></svg>
        </button>
      </div>`;

    ui.addEventListener('click', (event) => {
      const button = event.target.closest('button[data-dir]');
      if (!button) return;
      navigate(Number(button.dataset.dir));
    });

    document.documentElement.appendChild(ui);
    return ui;
  }

  function nativeNavButton(direction) {
    const id = direction > 0 ? '#navigation-button-down' : '#navigation-button-up';
    return document.querySelector(`ytd-shorts ${id} button`);
  }

  function adjacentItem(direction) {
    const item = currentItem() || itemById(lastCurrentId);
    if (!item) return null;

    let node = item;
    while ((node = direction > 0 ? node.nextElementSibling : node.previousElementSibling)) {
      if (node.classList?.contains('reel-video-in-sequence-new')) return node;
    }
    return null;
  }

  function settleAfterNavigation(previousId) {
    const started = performance.now();

    const check = () => {
      if (!active) return;
      const item = currentItem();
      if (item?.id && item.id !== previousId) {
        lastCurrentId = item.id;
        alignToItem(lastCurrentId);
        fitCurrentRenderer();
        return;
      }

      if (performance.now() - started < 900) {
        requestAnimationFrame(check);
      } else {
        rememberCurrent();
        alignToItem();
        fitCurrentRenderer();
      }
    };

    requestAnimationFrame(check);
  }

  function navigate(direction) {
    if (!active || (direction !== 1 && direction !== -1)) return;

    const now = performance.now();
    if (now < navigationLockedUntil) return;
    navigationLockedUntil = now + 340;

    const before = rememberCurrent();
    const beforeId = before?.id || lastCurrentId;
    const nativeButton = nativeNavButton(direction);

    if (nativeButton && !nativeButton.disabled) {
      nativeButton.click();
      settleAfterNavigation(beforeId);
      return;
    }

    // Fallback for a future YouTube DOM variant: perform one exact item jump.
    const target = adjacentItem(direction);
    const container = currentContainer();
    if (target && container) {
      lastCurrentId = target.id || lastCurrentId;
      container.scrollTo({
        top: itemTopInsideContainer(target, container),
        left: 0,
        behavior: 'auto'
      });
      settleAfterNavigation(beforeId);
    }
  }

  function isEditable(target) {
    if (!(target instanceof Element)) return false;
    return Boolean(
      target.closest('input, textarea, select, [contenteditable="true"], [role="textbox"]')
    );
  }

  function nestedScrollableCanConsume(target, deltaY) {
    if (!(target instanceof Element)) return false;
    const shorts = currentContainer();

    for (let el = target; el && el !== document.body && el !== document.documentElement; el = el.parentElement) {
      if (el === shorts) return false;

      const style = getComputedStyle(el);
      const overflowY = style.overflowY;
      if ((overflowY === 'auto' || overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 3) {
        const canUp = el.scrollTop > 1;
        const canDown = el.scrollTop + el.clientHeight < el.scrollHeight - 1;
        if ((deltaY < 0 && canUp) || (deltaY > 0 && canDown)) return true;
      }
    }

    return false;
  }

  function onWheel(event) {
    if (!active || event.ctrlKey || event.metaKey) return;
    if (Math.abs(event.deltaY) < Math.abs(event.deltaX)) return;
    if (nestedScrollableCanConsume(event.target, event.deltaY)) return;

    event.preventDefault();

    wheelAccum += event.deltaY;
    clearTimeout(wheelResetTimer);
    wheelResetTimer = setTimeout(() => {
      wheelAccum = 0;
    }, 130);

    if (performance.now() < navigationLockedUntil) return;
    if (Math.abs(wheelAccum) < 28) return;

    const direction = wheelAccum > 0 ? 1 : -1;
    wheelAccum = 0;
    navigate(direction);
  }

  function onKeyDown(event) {
    if (!active || event.defaultPrevented || isEditable(event.target)) return;
    if (event.altKey || event.ctrlKey || event.metaKey) return;

    let direction = 0;
    if (event.key === 'ArrowDown' || event.key === 'PageDown') direction = 1;
    if (event.key === 'ArrowUp' || event.key === 'PageUp') direction = -1;
    if (!direction) return;

    event.preventDefault();
    event.stopPropagation();
    navigate(direction);
  }

  function onResize() {
    if (!active) return;
    rememberCurrent();
    clearTimeout(resizeTimer);

    // First pass immediately, then a second after Chrome/YouTube have finished
    // changing viewport metrics. This is what prevents F11 enter/exit bouncing.
    applyMode({ realign: true });
    resizeTimer = setTimeout(() => applyMode({ realign: true }), 180);
  }

  function activate() {
    if (active) {
      applyMode({ realign: false });
      return;
    }

    active = true;
    ROOT.classList.add(ACTIVE);
    makeUi();
    applyMode({ realign: false });

    // The live renderer is inserted/moved by YouTube as the selected Short
    // changes. A throttled observer remembers that identity and refits the card.
    observer.observe(document.documentElement, { childList: true, subtree: true });
    setTimeout(() => {
      rememberCurrent();
      alignToItem();
      fitCurrentRenderer();
    }, 120);
  }

  function deactivate() {
    if (!active) return;
    active = false;
    ROOT.classList.remove(ACTIVE, COMPACT);
    ROOT.style.removeProperty('--qs-vh');
    ROOT.style.removeProperty('--qs-reel-scale');
    observer.disconnect();
  }

  function syncRoute() {
    const shouldBeActive = onShortsRoute();
    if (shouldBeActive) activate();
    else deactivate();
  }

  const observer = new MutationObserver(() => {
    if (!active || mutationRAF) return;
    mutationRAF = requestAnimationFrame(() => {
      mutationRAF = 0;
      const oldId = lastCurrentId;
      rememberCurrent();
      if (lastCurrentId !== oldId) {
        alignToItem(lastCurrentId);
        fitCurrentRenderer();
      }
    });
  });

  window.addEventListener('wheel', onWheel, { capture: true, passive: false });
  window.addEventListener('keydown', onKeyDown, true);
  window.addEventListener('resize', onResize, { passive: true });
  window.visualViewport?.addEventListener('resize', onResize, { passive: true });
  document.addEventListener('fullscreenchange', onResize, true);
  window.addEventListener('yt-navigate-finish', () => {
    lastHref = location.href;
    syncRoute();
    setTimeout(() => {
      rememberCurrent();
      applyMode({ realign: true });
    }, 80);
  });

  // YouTube is an SPA and does not guarantee a standard navigation event for
  // every internal transition. This tiny URL watcher is intentionally cheap.
  setInterval(() => {
    if (location.href === lastHref) return;
    lastHref = location.href;
    syncRoute();
  }, 300);

  syncRoute();
})();
