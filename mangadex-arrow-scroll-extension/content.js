(() => {
  "use strict";

  const DEFAULT_SPEED = 700; // pixels per second
  const MIN_SPEED = 50;
  const MAX_SPEED = 5000;
  const BOTTOM_EPSILON = 2;

  let speed = DEFAULT_SPEED;
  let direction = 0; // -1 = up, 0 = stopped, 1 = down
  let animationFrameId = null;
  let lastTimestamp = null;
  let nextChapterClickedForUrl = null;

  function clampSpeed(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return DEFAULT_SPEED;
    return Math.min(MAX_SPEED, Math.max(MIN_SPEED, n));
  }

  chrome.storage.sync.get({ scrollSpeed: DEFAULT_SPEED }, (result) => {
    speed = clampSpeed(result.scrollSpeed);
  });

  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName === "sync" && changes.scrollSpeed) {
      speed = clampSpeed(changes.scrollSpeed.newValue);
    }
  });

  function isEditableTarget(target) {
    if (!(target instanceof Element)) return false;
    return Boolean(
      target.closest(
        'input, textarea, select, [contenteditable="true"], [contenteditable=""], [role="textbox"]'
      )
    );
  }

  function getScrollElement() {
    return document.scrollingElement || document.documentElement;
  }

  function isAtBottom() {
    const scroller = getScrollElement();
    return (
      scroller.scrollTop + window.innerHeight >=
      scroller.scrollHeight - BOTTOM_EPSILON
    );
  }

  function findNextChapterLink() {
    const links = document.querySelectorAll('a[href*="/chapter/"]');

    for (const link of links) {
      const text = (link.textContent || "").replace(/\s+/g, " ").trim().toLowerCase();
      if (text === "next chapter") {
        return link;
      }
    }

    return null;
  }

  function clickNextChapterOnce() {
    const currentUrl = location.href;

    if (nextChapterClickedForUrl === currentUrl) {
      return;
    }

    const link = findNextChapterLink();
    if (!link) {
      return;
    }

    nextChapterClickedForUrl = currentUrl;
    link.click();
  }

  function tick(timestamp) {
    if (direction === 0) {
      animationFrameId = null;
      lastTimestamp = null;
      return;
    }

    if (lastTimestamp == null) {
      lastTimestamp = timestamp;
    }

    // Cap a huge frame gap so tab switching does not cause a giant jump.
    const elapsedSeconds = Math.min((timestamp - lastTimestamp) / 1000, 0.05);
    lastTimestamp = timestamp;

    const scroller = getScrollElement();
    const before = scroller.scrollTop;
    const distance = direction * speed * elapsedSeconds;

    window.scrollBy(0, distance);

    const after = scroller.scrollTop;

    // "Can't scroll down any further" means a downward attempt produced no
    // movement and the document is at its actual current bottom.
    if (direction > 0 && after <= before + 0.01 && isAtBottom()) {
      clickNextChapterOnce();
    }

    animationFrameId = requestAnimationFrame(tick);
  }

  function startScrolling(newDirection) {
    direction = newDirection;

    if (animationFrameId == null) {
      lastTimestamp = null;
      animationFrameId = requestAnimationFrame(tick);
    }
  }

  function stopScrolling(stoppedDirection) {
    if (direction === stoppedDirection) {
      direction = 0;
    }
  }

  window.addEventListener(
    "keydown",
    (event) => {
      if (isEditableTarget(event.target)) {
        return;
      }

      if (event.key === "ArrowDown") {
        event.preventDefault();
        event.stopImmediatePropagation();
        startScrolling(1);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        event.stopImmediatePropagation();
        startScrolling(-1);
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
        stopScrolling(1);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        event.stopImmediatePropagation();
        stopScrolling(-1);
      }
    },
    true
  );

  window.addEventListener("blur", () => {
    direction = 0;
  });

  // MangaDex can navigate chapters without a traditional full page reload.
  // Reset the one-click guard when the URL changes.
  let lastUrl = location.href;
  const urlObserver = new MutationObserver(() => {
    if (location.href !== lastUrl) {
      lastUrl = location.href;
      nextChapterClickedForUrl = null;
      direction = 0;
    }
  });

  urlObserver.observe(document.documentElement, {
    childList: true,
    subtree: true
  });
})();
