(() => {
  'use strict';

  const EXT_ATTR = 'data-yt-shorts-x-channel-button';
  const POLL_MS = 80;
  const MENU_TIMEOUT_MS = 3500;

  const X_ICON_PATH = 'M18.3 5.71a1 1 0 0 0-1.41 0L12 10.59 7.11 5.7A1 1 0 0 0 5.7 7.11L10.59 12 5.7 16.89a1 1 0 1 0 1.41 1.41L12 13.41l4.89 4.89a1 1 0 0 0 1.41-1.41L13.41 12l4.89-4.89a1 1 0 0 0 0-1.4Z';

  const buttonToReel = new WeakMap();

  function isShortsPage() {
    return location.pathname.startsWith('/shorts') || Boolean(document.querySelector('ytd-shorts'));
  }

  function textOf(el) {
    return (el?.textContent || '').replace(/\s+/g, ' ').trim();
  }

  function isOnScreen(el) {
    if (!el || !el.isConnected) return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.right > 0 && rect.top < innerHeight && rect.left < innerWidth;
  }

  function closestReel(el) {
    return el?.closest?.('ytd-reel-video-renderer');
  }

  function makeXButton(reel) {
    const wrap = document.createElement('div');
    wrap.className = 'yt-shorts-x-channel-button-container style-scope ytd-shorts-player-controls';
    wrap.setAttribute(EXT_ATTR, 'true');

    const button = document.createElement('button');
    button.className = 'ytSpecButtonShapeNextHost ytSpecButtonShapeNextTonal ytSpecButtonShapeNextOverlayDark ytSpecButtonShapeNextSizeL ytSpecButtonShapeNextIconButton';
    button.type = 'button';
    button.title = '';
    button.setAttribute('aria-label', "Don't recommend this channel");
    button.style.color = 'rgb(255, 255, 255)';
    button.style.backgroundColor = 'transparent';

    const icon = document.createElement('div');
    icon.className = 'ytSpecButtonShapeNextIcon';
    icon.setAttribute('aria-hidden', 'true');
    icon.innerHTML = `
      <span class="ytIconWrapperHost" style="width: 24px; height: 24px;">
        <span class="yt-icon-shape ytSpecIconShapeHost">
          <div style="width: 100%; height: 100%; display: block; filter: drop-shadow(0px 1px 4px rgba(0, 0, 0, 0.3)); fill: currentcolor;">
            <svg xmlns="http://www.w3.org/2000/svg" height="24" viewBox="0 0 24 24" width="24" focusable="false" aria-hidden="true" style="pointer-events: none; display: inherit; width: 100%; height: 100%;">
              <path d="${X_ICON_PATH}"></path>
            </svg>
          </div>
        </span>
      </span>`;

    const feedback = document.createElement('yt-touch-feedback-shape');
    feedback.className = 'ytSpecTouchFeedbackShapeHost ytSpecTouchFeedbackShapeOverlayTouchResponseInverse';
    feedback.setAttribute('aria-hidden', 'true');
    feedback.innerHTML = '<div class="ytSpecTouchFeedbackShapeStroke"></div><div class="ytSpecTouchFeedbackShapeFill"></div>';

    button.append(icon, feedback);
    wrap.append(button);
    buttonToReel.set(button, reel);

    return wrap;
  }

  function findMoreActionsButton(reel) {
    const candidates = [
      ...reel.querySelectorAll('ytd-menu-renderer yt-button-shape button[aria-label="More actions"], ytd-menu-renderer button[aria-label="More actions"], ytd-menu-renderer button[aria-label*="More actions"], ytd-menu-renderer yt-icon-button button, ytd-menu-renderer button')
    ];

    return candidates.find((button) => {
      const label = button.getAttribute('aria-label') || '';
      if (/more actions/i.test(label) && isOnScreen(button)) return true;
      const path = button.querySelector('svg path')?.getAttribute('d') || '';
      return isOnScreen(button) && path.includes('M12 4') && path.includes('M12 10') && path.includes('M12 16');
    }) || null;
  }

  function isDontRecommendChannelItem(el) {
    const label = el.getAttribute?.('aria-label') || '';
    return label === "Don't recommend this channel" || textOf(el) === "Don't recommend this channel";
  }

  function findDontRecommendChannelButton() {
    const menuRoots = [
      ...document.querySelectorAll('ytd-popup-container, tp-yt-iron-dropdown, ytd-menu-popup-renderer, ytd-sheet-view-model')
    ];

    for (const root of menuRoots) {
      const exactItem = [...root.querySelectorAll('yt-list-item-view-model, ytd-menu-service-item-renderer, tp-yt-paper-item, [role="listitem"], button')]
        .find(isDontRecommendChannelItem);
      if (exactItem) return exactItem.querySelector('button') || exactItem;
    }

    return [...document.querySelectorAll('yt-list-item-view-model, ytd-menu-service-item-renderer, tp-yt-paper-item, [role="listitem"], button')]
      .filter(isOnScreen)
      .map((el) => isDontRecommendChannelItem(el) ? (el.querySelector('button') || el) : null)
      .find(Boolean) || null;
  }

  function clickDontRecommendChannel(reel) {
    const moreButton = findMoreActionsButton(reel);
    if (!moreButton) return;

    moreButton.click();

    const started = Date.now();
    const timer = window.setInterval(() => {
      const target = findDontRecommendChannelButton();
      if (target) {
        window.clearInterval(timer);
        target.click();
        return;
      }
      if (Date.now() - started > MENU_TIMEOUT_MS) {
        window.clearInterval(timer);
      }
    }, POLL_MS);
  }

  function handleXClick(event) {
    const button = event.target?.closest?.(`[${EXT_ATTR}="true"] button, [${EXT_ATTR}="true"]`);
    if (!button) return;

    const container = button.closest(`[${EXT_ATTR}="true"]`) || button;
    const actualButton = container.querySelector('button') || button;
    const reel = buttonToReel.get(actualButton) || closestReel(container);

    if (!reel) return;

    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();

    clickDontRecommendChannel(reel);
  }

  function findRightControls(reel) {
    return reel.querySelector('ytd-shorts-player-controls #right-controls');
  }

  function findMenuContainer(rightControls) {
    return rightControls?.querySelector('#menu-button');
  }

  function installButton(reel) {
    if (!reel) return;

    const rightControls = findRightControls(reel);
    const menuContainer = findMenuContainer(rightControls);
    if (!rightControls || !menuContainer) return;

    let existing = reel.querySelector(`[${EXT_ATTR}="true"]`);

    if (existing && existing.parentElement !== rightControls) {
      existing.remove();
      existing = null;
    }

    if (!existing) {
      existing = makeXButton(reel);
    }

    const actualButton = existing.querySelector('button');
    if (actualButton) buttonToReel.set(actualButton, reel);

    if (existing.nextElementSibling !== menuContainer) {
      rightControls.insertBefore(existing, menuContainer);
    }
  }

  function scan() {
    if (!isShortsPage()) {
      document.querySelectorAll(`[${EXT_ATTR}="true"]`).forEach((el) => el.remove());
      return;
    }

    document.querySelectorAll('ytd-reel-video-renderer').forEach(installButton);
  }

  let scanQueued = false;
  function queueScan() {
    if (scanQueued) return;
    scanQueued = true;
    requestAnimationFrame(() => {
      scanQueued = false;
      scan();
    });
  }

  document.addEventListener('pointerdown', handleXClick, true);
  document.addEventListener('click', handleXClick, true);

  const observer = new MutationObserver(queueScan);
  observer.observe(document.documentElement, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ['class', 'show-player-controls', 'style', 'hidden']
  });

  window.addEventListener('yt-navigate-finish', queueScan, true);
  window.addEventListener('popstate', queueScan, true);
  window.addEventListener('resize', queueScan, true);
  window.addEventListener('scroll', queueScan, true);
  document.addEventListener('visibilitychange', queueScan, true);

  scan();
  window.setInterval(scan, 500);
})();