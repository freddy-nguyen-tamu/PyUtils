(() => {
  "use strict";

  const DEFAULT_SCROLL_SPEED = 1400; // pixels per second
  const MIN_SCROLL_SPEED = 50;
  const MAX_SCROLL_SPEED = 10000;

  // Reader controls are revealed from the right-most quarter of the viewport.
  // This matches the placement of the controls on the supplied reader page.
  const CONTROL_REVEAL_FRACTION = 0.25;
  const CONTROL_SELECTOR = ".rpage-floatctl, .rpage-bottombar";
  const CONTROL_VISIBILITY_ATTR = "data-comix-ext-controls-visible";
  const CONTROL_STYLE_ID = "comix-reader-zoom-keys-autohide-style";

  let browserZoom = 1;
  let baseReaderWidthPercent = null;
  let scrollSpeed = DEFAULT_SCROLL_SPEED;
  let animationFrame = 0;
  let lastFrameTime = 0;
  const heldScrollKeys = new Set();
  let applyQueued = false;

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function isEditableTarget(target) {
    if (!(target instanceof Element)) return false;
    return Boolean(
      target.closest(
        'input, textarea, select, [contenteditable=""], [contenteditable="true"], [role="textbox"]'
      )
    );
  }

  function readSiteWidthPercent() {
    const stretchButton = document.querySelector(
      'button[aria-label="Stretch to fit width"]'
    );
    if (stretchButton?.getAttribute("aria-pressed") === "true") {
      return 100;
    }

    const label = document.querySelector(".rpage-zoom__value");
    if (label) {
      const match = label.textContent?.match(/([0-9]+(?:\.[0-9]+)?)\s*%/);
      if (match) {
        const value = Number(match[1]);
        if (Number.isFinite(value) && value > 0) return value;
      }
    }

    return null;
  }

  function readInitialInlineWidth(reader) {
    const raw = reader?.style?.getPropertyValue("--rpage-max-w")?.trim();
    const match = raw?.match(/^([0-9]+(?:\.[0-9]+)?)vw$/i);
    if (!match) return null;

    const value = Number(match[1]);
    return Number.isFinite(value) && value > 0 ? value : null;
  }

  function applyZoomResponsiveWidth() {
    applyQueued = false;

    const reader = document.querySelector(".rpage");
    if (!reader) return;

    // Prefer the site's own visible width setting (for example 50%). This lets
    // the extension continue to cooperate with the site's +/- image-width UI.
    const siteWidth = readSiteWidthPercent();
    if (siteWidth !== null) {
      baseReaderWidthPercent = siteWidth;
    } else if (baseReaderWidthPercent === null) {
      baseReaderWidthPercent = readInitialInlineWidth(reader) ?? 50;
    }

    // Example with the supplied page's 50% base width:
    // 100% browser zoom -> 50vw
    // 125% browser zoom -> 62.5vw
    // 150% browser zoom -> 75vw
    // 200%+ browser zoom -> 100vw
    const effectiveWidth = clamp(
      baseReaderWidthPercent * browserZoom,
      10,
      100
    );
    const cssValue = `${Number(effectiveWidth.toFixed(3))}vw`;

    if (
      reader.style.getPropertyValue("--rpage-max-w").trim() !== cssValue ||
      reader.style.getPropertyPriority("--rpage-max-w") !== "important"
    ) {
      reader.style.setProperty("--rpage-max-w", cssValue, "important");
    }
  }

  function queueApplyZoomResponsiveWidth() {
    if (applyQueued) return;
    applyQueued = true;
    requestAnimationFrame(applyZoomResponsiveWidth);
  }

  function installControlAutoHideStyles() {
    if (document.getElementById(CONTROL_STYLE_ID)) return;

    const style = document.createElement("style");
    style.id = CONTROL_STYLE_ID;
    style.textContent = `
      ${CONTROL_SELECTOR} {
        opacity: 0 !important;
        visibility: hidden !important;
        pointer-events: none !important;
        transition:
          opacity 120ms ease,
          visibility 0s linear 120ms !important;
      }

      html[${CONTROL_VISIBILITY_ATTR}="true"] ${CONTROL_SELECTOR} {
        opacity: 1 !important;
        visibility: visible !important;
        pointer-events: auto !important;
        transition:
          opacity 120ms ease,
          visibility 0s linear 0s !important;
      }
    `;
    (document.head || document.documentElement).appendChild(style);
  }

  function setReaderControlsVisible(visible) {
    document.documentElement.setAttribute(
      CONTROL_VISIBILITY_ATTR,
      visible ? "true" : "false"
    );
  }

  function pointerIsOverReaderControls(target) {
    return target instanceof Element && Boolean(target.closest(CONTROL_SELECTOR));
  }

  function onPointerMove(event) {
    // Show all reader controls when the pointer enters the right-most 1/4 of
    // the screen. Once visible, keep them visible while the pointer is over
    // any of those controls, even if that control extends left of the quarter.
    const revealBoundary = window.innerWidth * (1 - CONTROL_REVEAL_FRACTION);
    const inRevealQuarter = event.clientX >= revealBoundary;
    const overControls = pointerIsOverReaderControls(event.target);
    setReaderControlsVisible(inRevealQuarter || overControls);
  }

  function onPointerLeaveWindow(event) {
    if (event.relatedTarget === null) {
      setReaderControlsVisible(false);
    }
  }

  function getChapterButton(direction) {
    const label = direction === "next" ? "Next chapter" : "Previous chapter";
    const preferred = document.querySelector(
      `.rpage-floatctl button[aria-label="${label}"]`
    );
    if (preferred && !preferred.disabled) return preferred;

    return [...document.querySelectorAll(`button[aria-label="${label}"]`)]
      .find((button) => !button.disabled) ?? null;
  }

  function clickChapter(direction) {
    const button = getChapterButton(direction);
    if (!button) return false;
    button.click();
    return true;
  }

  function stopNativeArrowBehavior(event) {
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();
  }

  function currentScrollDirection() {
    return (heldScrollKeys.has("ArrowDown") ? 1 : 0) -
      (heldScrollKeys.has("ArrowUp") ? 1 : 0);
  }

  function scrollFrame(timestamp) {
    const direction = currentScrollDirection();
    if (direction === 0) {
      animationFrame = 0;
      lastFrameTime = 0;
      return;
    }

    if (!lastFrameTime) lastFrameTime = timestamp;
    const elapsedSeconds = Math.min((timestamp - lastFrameTime) / 1000, 0.05);
    lastFrameTime = timestamp;

    window.scrollBy({
      top: direction * scrollSpeed * elapsedSeconds,
      left: 0,
      behavior: "auto"
    });

    animationFrame = requestAnimationFrame(scrollFrame);
  }

  function ensureScrollAnimation() {
    if (!animationFrame) {
      lastFrameTime = 0;
      animationFrame = requestAnimationFrame(scrollFrame);
    }
  }

  function onKeyDown(event) {
    if (event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
    if (isEditableTarget(event.target)) return;

    if (event.key === "ArrowRight") {
      stopNativeArrowBehavior(event);
      if (!event.repeat) clickChapter("next");
      return;
    }

    if (event.key === "ArrowLeft") {
      stopNativeArrowBehavior(event);
      if (!event.repeat) clickChapter("previous");
      return;
    }

    if (event.key === "ArrowUp" || event.key === "ArrowDown") {
      stopNativeArrowBehavior(event);
      heldScrollKeys.add(event.key);
      ensureScrollAnimation();
    }
  }

  function onKeyUp(event) {
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
    heldScrollKeys.delete(event.key);
  }

  function loadScrollSpeed() {
    chrome.storage.sync.get({ scrollSpeed: DEFAULT_SCROLL_SPEED }, (settings) => {
      const value = Number(settings.scrollSpeed);
      if (Number.isFinite(value)) {
        scrollSpeed = clamp(value, MIN_SCROLL_SPEED, MAX_SCROLL_SPEED);
      }
    });
  }

  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName !== "sync" || !changes.scrollSpeed) return;
    const value = Number(changes.scrollSpeed.newValue);
    if (Number.isFinite(value)) {
      scrollSpeed = clamp(value, MIN_SCROLL_SPEED, MAX_SCROLL_SPEED);
    }
  });

  chrome.runtime.onMessage.addListener((message) => {
    if (message?.type !== "COMIX_ZOOM_CHANGED") return;
    const value = Number(message.zoom);
    if (!Number.isFinite(value) || value <= 0) return;
    browserZoom = value;
    queueApplyZoomResponsiveWidth();
  });

  // Comix is an SPA and can replace reader elements while navigating chapters.
  // Re-apply the effective width after relevant DOM updates.
  const observer = new MutationObserver(queueApplyZoomResponsiveWidth);
  observer.observe(document.documentElement, {
    subtree: true,
    childList: true,
    characterData: true,
    attributes: true,
    attributeFilter: ["style", "aria-pressed"]
  });

  installControlAutoHideStyles();
  setReaderControlsVisible(false);

  document.addEventListener("mousemove", onPointerMove, true);
  document.addEventListener("mouseout", onPointerLeaveWindow, true);
  document.addEventListener("keydown", onKeyDown, true);
  document.addEventListener("keyup", onKeyUp, true);
  window.addEventListener("blur", () => {
    heldScrollKeys.clear();
    setReaderControlsVisible(false);
  });

  loadScrollSpeed();

  chrome.runtime.sendMessage({ type: "COMIX_GET_TAB_ZOOM" }, (response) => {
    if (chrome.runtime.lastError) {
      browserZoom = 1;
    } else {
      const value = Number(response?.zoom);
      browserZoom = Number.isFinite(value) && value > 0 ? value : 1;
    }
    queueApplyZoomResponsiveWidth();
  });

  queueApplyZoomResponsiveWidth();
})();
