(() => {
  "use strict";

  const DEFAULT_SPEED = 700;
  const MIN_SPEED = 50;
  const MAX_SPEED = 5000;
  const BOTTOM_EPSILON = 2;
  const AUTO_BUTTON_ID = "comicland-auto-scroll-button";

  let speed = DEFAULT_SPEED;
  let manualDirection = 0;
  let autoScrollEnabled = false;
  let frameId = null;
  let lastTime = null;
  let clickedUrl = null;
  let previousUrl = location.href;
  let placementScheduled = false;

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
    return el.scrollTop + window.innerHeight >= el.scrollHeight - BOTTOM_EPSILON;
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
    if (manualDirection !== 0) return manualDirection;
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

    if (lastTime == null) lastTime = now;

    const elapsedSeconds = Math.min((now - lastTime) / 1000, 0.05);
    lastTime = now;

    const el = getScroller();
    const before = el.scrollTop;

    window.scrollBy(0, direction * speed * elapsedSeconds);

    const after = el.scrollTop;

    if (direction > 0 && after <= before + 0.01 && isAtBottom()) {
      clickNextChapterOnce();
    }

    frameId = requestAnimationFrame(tick);
  }

  const PLAY_ICON = `
    <svg xmlns="http://www.w3.org/2000/svg"
         width="20" height="20" viewBox="0 0 24 24"
         fill="none" stroke="currentColor" stroke-width="2"
         stroke-linecap="round" stroke-linejoin="round"
         class="lucide lucide-play md:w-6 md:h-6"
         aria-hidden="true">
      <polygon points="6 3 20 12 6 21 6 3"></polygon>
    </svg>
  `;

  const PAUSE_ICON = `
    <svg xmlns="http://www.w3.org/2000/svg"
         width="20" height="20" viewBox="0 0 24 24"
         fill="none" stroke="currentColor" stroke-width="2"
         stroke-linecap="round" stroke-linejoin="round"
         class="lucide lucide-pause md:w-6 md:h-6"
         aria-hidden="true">
      <rect x="6" y="4" width="4" height="16"></rect>
      <rect x="14" y="4" width="4" height="16"></rect>
    </svg>
  `;

  function updatePageButton() {
    const button = document.getElementById(AUTO_BUTTON_ID);
    if (!button) return;

    const desiredState = autoScrollEnabled ? "on" : "off";

    // CRITICAL: do nothing if the visual state is already correct.
    // This prevents our MutationObserver from reacting to our own DOM writes forever.
    if (button.dataset.autoScrollState === desiredState) return;

    button.dataset.autoScrollState = desiredState;
    button.title = autoScrollEnabled ? "Stop Auto Scroll" : "Start Auto Scroll";
    button.setAttribute("aria-label", button.title);
    button.setAttribute("aria-pressed", autoScrollEnabled ? "true" : "false");
    button.innerHTML = autoScrollEnabled ? PAUSE_ICON : PLAY_ICON;

    if (autoScrollEnabled) {
      button.style.outline = "2px solid #2563eb";
      button.style.outlineOffset = "2px";
    } else {
      button.style.outline = "";
      button.style.outlineOffset = "";
    }
  }

  function setAutoScroll(enabled) {
    autoScrollEnabled = Boolean(enabled);
    updatePageButton();

    if (autoScrollEnabled) ensureAnimation();

    return autoScrollEnabled;
  }

  function resetForNewChapter() {
    autoScrollEnabled = false;
    manualDirection = 0;
    clickedUrl = null;
    lastTime = null;
    updatePageButton();
  }

  function buildPageButton() {
    const button = document.createElement("button");
    button.id = AUTO_BUTTON_ID;
    button.type = "button";
    button.className =
      "bg-white text-gray-900 shadow-lg hover:shadow-xl rounded-full " +
      "p-3 md:p-4 hover:bg-gray-50 active:scale-95 transition-all duration-200";

    button.addEventListener("mousedown", (event) => {
      if (event.button === 0) {
        event.stopPropagation();
      }
    }, true);

    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      setAutoScroll(!autoScrollEnabled);
    }, true);

    return button;
  }

  function ensurePageButton() {
    const nextButton = findNextChapterButton();
    if (!nextButton) return;

    const controls = nextButton.parentElement;
    if (!controls) return;

    let button = document.getElementById(AUTO_BUTTON_ID);

    if (!button) {
      button = buildPageButton();
    }

    // Only move/insert it when actually needed.
    if (button.parentElement !== controls || button.nextSibling !== nextButton) {
      controls.insertBefore(button, nextButton);
    }

    updatePageButton();
  }

  function scheduleEnsurePageButton() {
    if (placementScheduled) return;
    placementScheduled = true;

    requestAnimationFrame(() => {
      placementScheduled = false;
      ensurePageButton();
    });
  }

  window.addEventListener("keydown", (event) => {
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
  }, true);

  window.addEventListener("keyup", (event) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      event.stopImmediatePropagation();
      if (manualDirection === 1) manualDirection = 0;
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      event.stopImmediatePropagation();
      if (manualDirection === -1) manualDirection = 0;
    }

    ensureAnimation();
  }, true);

  // Any left-click outside the Auto Scroll button stops Auto Scroll.
  window.addEventListener("mousedown", (event) => {
    if (
      event.button === 0 &&
      autoScrollEnabled &&
      !(
        event.target instanceof Element &&
        event.target.closest(`#${AUTO_BUTTON_ID}`)
      )
    ) {
      setAutoScroll(false);
    }
  }, true);

  window.addEventListener("blur", () => {
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
      sendResponse({ enabled: setAutoScroll(!autoScrollEnabled) });
      return;
    }

    if (message.type === "SET_AUTO_SCROLL") {
      sendResponse({ enabled: setAutoScroll(Boolean(message.enabled)) });
    }
  });

  // Observe React page replacement, but never blindly rewrite our own button.
  const observer = new MutationObserver(() => {
    if (location.href !== previousUrl) {
      previousUrl = location.href;
      resetForNewChapter();
    }

    // Schedule at most one placement check per animation frame.
    // ensurePageButton itself only writes when something is actually missing/moved.
    scheduleEnsurePageButton();
  });

  observer.observe(document.body, {
    childList: true,
    subtree: true
  });

  ensurePageButton();
})();
