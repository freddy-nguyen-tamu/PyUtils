(() => {
  "use strict";

  const DEFAULT_SPEED = 700;
  const MIN_SPEED = 50;
  const MAX_SPEED = 5000;
  const BOTTOM_EPSILON = 2;

  let speed = DEFAULT_SPEED;

  // Manual key state:
  // -1 = Up Arrow held
  //  0 = no arrow held
  //  1 = Down Arrow held
  let manualDirection = 0;

  // Auto Scroll is intentionally NOT persisted.
  // Every newly loaded/navigated chapter starts OFF.
  let autoScrollEnabled = false;

  let frameId = null;
  let lastTime = null;
  let clickedUrl = null;
  let previousUrl = location.href;

  function clampSpeed(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return DEFAULT_SPEED;
    return Math.min(MAX_SPEED, Math.max(MIN_SPEED, n));
  }

  chrome.storage.sync.get({ scrollSpeed: DEFAULT_SPEED }, ({ scrollSpeed }) => {
    speed = clampSpeed(scrollSpeed);
  });

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "sync" && changes.scrollSpeed) {
      speed = clampSpeed(changes.scrollSpeed.newValue);
    }
  });

  function isEditable(target) {
    return target instanceof Element && Boolean(
      target.closest(
        'input, textarea, select, [contenteditable="true"], [contenteditable=""], [role="textbox"]'
      )
    );
  }

  function getScroller() {
    return document.scrollingElement || document.documentElement;
  }

  function isAtBottom() {
    const el = getScroller();
    return (
      el.scrollTop + window.innerHeight >=
      el.scrollHeight - BOTTOM_EPSILON
    );
  }

  function findNextChapterButton() {
    return document.querySelector('button[title="Next Chapter"]');
  }

  function clickNextChapterOnce() {
    if (clickedUrl === location.href) return false;

    const button = findNextChapterButton();
    if (!button || button.disabled) return false;

    clickedUrl = location.href;
    button.click();
    return true;
  }

  function effectiveDirection() {
    // Manual key input wins while held.
    if (manualDirection !== 0) return manualDirection;

    // Auto Scroll only moves downward.
    return autoScrollEnabled ? 1 : 0;
  }

  function ensureAnimation() {
    if (frameId == null && effectiveDirection() !== 0) {
      lastTime = null;
      frameId = requestAnimationFrame(tick);
    }
  }

  function tick(now) {
    const direction = effectiveDirection();

    if (direction === 0) {
      frameId = null;
      lastTime = null;
      return;
    }

    if (lastTime == null) {
      lastTime = now;
    }

    // Avoid a giant jump after browser/tab inactivity.
    const elapsedSeconds = Math.min((now - lastTime) / 1000, 0.05);
    lastTime = now;

    const el = getScroller();
    const before = el.scrollTop;

    window.scrollBy(0, direction * speed * elapsedSeconds);

    const after = el.scrollTop;

    // A downward attempt that causes no movement at the real bottom
    // advances to the next chapter once.
    if (
      direction > 0 &&
      after <= before + 0.01 &&
      isAtBottom()
    ) {
      clickNextChapterOnce();
    }

    frameId = requestAnimationFrame(tick);
  }

  function setAutoScroll(enabled) {
    autoScrollEnabled = Boolean(enabled);

    if (autoScrollEnabled) {
      ensureAnimation();
    }

    return autoScrollEnabled;
  }

  function resetForNewChapter() {
    autoScrollEnabled = false;
    manualDirection = 0;
    clickedUrl = null;
    lastTime = null;

    // Existing requestAnimationFrame will stop on its next tick.
  }

  window.addEventListener(
    "keydown",
    (event) => {
      if (isEditable(event.target)) return;

      if (event.key === "ArrowDown") {
        event.preventDefault();
        event.stopImmediatePropagation();
        manualDirection = 1;
        ensureAnimation();
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        event.stopImmediatePropagation();
        manualDirection = -1;
        ensureAnimation();
      }
    },
    true
  );

  window.addEventListener(
    "keyup",
    (event) => {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        event.stopImmediatePropagation();
        if (manualDirection === 1) manualDirection = 0;
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        event.stopImmediatePropagation();
        if (manualDirection === -1) manualDirection = 0;
      }

      // If Auto Scroll is on, continue automatically after arrow release.
      ensureAnimation();
    },
    true
  );

  window.addEventListener("blur", () => {
    // Don't leave a manual arrow "held" if focus is lost.
    manualDirection = 0;
    ensureAnimation();
  });

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (!message || typeof message.type !== "string") return;

    if (message.type === "GET_AUTO_SCROLL_STATE") {
      sendResponse({ enabled: autoScrollEnabled });
      return;
    }

    if (message.type === "TOGGLE_AUTO_SCROLL") {
      const enabled = setAutoScroll(!autoScrollEnabled);
      sendResponse({ enabled });
      return;
    }

    if (message.type === "SET_AUTO_SCROLL") {
      const enabled = setAutoScroll(Boolean(message.enabled));
      sendResponse({ enabled });
    }
  });

  // ComicLand is a React app. If it changes chapter client-side,
  // turn Auto Scroll OFF for the new chapter.
  const observer = new MutationObserver(() => {
    if (location.href !== previousUrl) {
      previousUrl = location.href;
      resetForNewChapter();
    }
  });

  observer.observe(document.documentElement, {
    childList: true,
    subtree: true
  });
})();
