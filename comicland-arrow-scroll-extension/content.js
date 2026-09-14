(() => {
  "use strict";

  const DEFAULT_SPEED = 700;
  const MIN_SPEED = 50;
  const MAX_SPEED = 5000;
  const BOTTOM_EPSILON = 2;

  let speed = DEFAULT_SPEED;
  let direction = 0;
  let frameId = null;
  let lastTime = null;
  let clickedUrl = null;

  const clampSpeed = (value) => {
    const n = Number(value);
    return Number.isFinite(n)
      ? Math.min(MAX_SPEED, Math.max(MIN_SPEED, n))
      : DEFAULT_SPEED;
  };

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
      target.closest('input, textarea, select, [contenteditable="true"], [contenteditable=""], [role="textbox"]')
    );
  }

  function scroller() {
    return document.scrollingElement || document.documentElement;
  }

  function atBottom() {
    const el = scroller();
    return el.scrollTop + window.innerHeight >= el.scrollHeight - BOTTOM_EPSILON;
  }

  function clickNextChapterOnce() {
    if (clickedUrl === location.href) return;

    const button = document.querySelector('button[title="Next Chapter"]');
    if (!button || button.disabled) return;

    clickedUrl = location.href;
    button.click();
  }

  function tick(now) {
    if (direction === 0) {
      frameId = null;
      lastTime = null;
      return;
    }

    if (lastTime == null) lastTime = now;

    const elapsed = Math.min((now - lastTime) / 1000, 0.05);
    lastTime = now;

    const el = scroller();
    const before = el.scrollTop;

    window.scrollBy(0, direction * speed * elapsed);

    const after = el.scrollTop;

    if (direction > 0 && after <= before + 0.01 && atBottom()) {
      clickNextChapterOnce();
    }

    frameId = requestAnimationFrame(tick);
  }

  function start(dir) {
    direction = dir;
    if (frameId == null) {
      lastTime = null;
      frameId = requestAnimationFrame(tick);
    }
  }

  function stop(dir) {
    if (direction === dir) direction = 0;
  }

  window.addEventListener("keydown", (event) => {
    if (isEditable(event.target)) return;

    if (event.key === "ArrowDown") {
      event.preventDefault();
      event.stopImmediatePropagation();
      start(1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      event.stopImmediatePropagation();
      start(-1);
    }
  }, true);

  window.addEventListener("keyup", (event) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      event.stopImmediatePropagation();
      stop(1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      event.stopImmediatePropagation();
      stop(-1);
    }
  }, true);

  window.addEventListener("blur", () => {
    direction = 0;
  });

  let previousUrl = location.href;
  const observer = new MutationObserver(() => {
    if (location.href !== previousUrl) {
      previousUrl = location.href;
      clickedUrl = null;
      direction = 0;
      lastTime = null;
    }
  });

  observer.observe(document.documentElement, {
    childList: true,
    subtree: true
  });
})();