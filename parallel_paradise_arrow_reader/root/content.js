(() => {
  "use strict";

  const DEFAULT_SPEED = 700; // pixels per second
  const MIN_SPEED = 50;
  const MAX_SPEED = 3000;

  let scrollSpeed = DEFAULT_SPEED;
  const heldScrollKeys = new Set();
  let activeScrollKey = null;
  let animationFrame = null;
  let previousFrameTime = null;

  function clampSpeed(value) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return DEFAULT_SPEED;
    return Math.min(MAX_SPEED, Math.max(MIN_SPEED, parsed));
  }

  chrome.storage.local.get({ scrollSpeed: DEFAULT_SPEED }, (settings) => {
    scrollSpeed = clampSpeed(settings.scrollSpeed);
  });

  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName === "local" && changes.scrollSpeed) {
      scrollSpeed = clampSpeed(changes.scrollSpeed.newValue);
    }
  });

  function isTypingTarget(target) {
    if (!(target instanceof Element)) return false;
    return Boolean(
      target.closest(
        'input, textarea, select, [contenteditable=""], [contenteditable="true"], [role="textbox"]'
      )
    );
  }

  function hasModifier(event) {
    return event.altKey || event.ctrlKey || event.metaKey || event.shiftKey;
  }

  function currentDirection() {
    if (activeScrollKey === "ArrowDown" && heldScrollKeys.has("ArrowDown")) return 1;
    if (activeScrollKey === "ArrowUp" && heldScrollKeys.has("ArrowUp")) return -1;
    if (heldScrollKeys.has("ArrowDown")) return 1;
    if (heldScrollKeys.has("ArrowUp")) return -1;
    return 0;
  }

  function stopScrolling() {
    if (animationFrame !== null) {
      cancelAnimationFrame(animationFrame);
      animationFrame = null;
    }
    previousFrameTime = null;
  }

  function scrollFrame(now) {
    const direction = currentDirection();
    if (direction === 0) {
      stopScrolling();
      return;
    }

    if (previousFrameTime === null) {
      previousFrameTime = now;
    }

    // Cap the time step so switching back to a backgrounded tab cannot cause a huge jump.
    const elapsedMs = Math.min(50, now - previousFrameTime);
    previousFrameTime = now;

    window.scrollBy(0, direction * scrollSpeed * (elapsedMs / 1000));
    animationFrame = requestAnimationFrame(scrollFrame);
  }

  function startScrolling() {
    if (animationFrame === null) {
      previousFrameTime = null;
      animationFrame = requestAnimationFrame(scrollFrame);
    }
  }

  function parseChapterNumber(text) {
    const match = String(text).match(/chapter\s+(\d+(?:\.\d+)?)/i);
    return match ? Number(match[1]) : null;
  }

  function navigateViaChapterSelect(direction) {
    const select = document.querySelector("select.mangosm-manga-nav-select");
    if (!select) return null;

    const current = parseChapterNumber(select.options[select.selectedIndex]?.textContent || "");
    if (!Number.isFinite(current)) return null;

    const candidates = Array.from(select.options)
      .map((option) => ({
        href: option.value,
        chapter: parseChapterNumber(option.textContent || "")
      }))
      .filter((item) => item.href && Number.isFinite(item.chapter));

    const valid = candidates.filter((item) =>
      direction > 0 ? item.chapter > current : item.chapter < current
    );

    // The selector is usable, but there is no chapter in that direction: we are at a boundary.
    if (valid.length === 0) return false;

    valid.sort((a, b) =>
      direction > 0 ? a.chapter - b.chapter : b.chapter - a.chapter
    );

    window.location.assign(valid[0].href);
    return true;
  }

  function navigateViaUrlNumber(direction) {
    const url = new URL(window.location.href);
    const match = url.pathname.match(/^(.*parallel-paradise-chapter-)(\d+)(\/?)$/i);
    if (!match) return false;

    const current = Number(match[2]);
    const next = current + direction;
    if (!Number.isInteger(next) || next < 1) return false;

    url.pathname = `${match[1]}${next}${match[3] || "/"}`;
    window.location.assign(url.href);
    return true;
  }

  function navigateChapter(direction) {
    // Prefer the site's own chapter links, because they also handle special chapter numbering.
    const selector = direction > 0 ? ".next-chapter-link[href]" : ".prev-chapter-link[href]";
    const explicitLink = document.querySelector(selector);
    if (explicitLink?.href) {
      window.location.assign(explicitLink.href);
      return;
    }

    // If the explicit link is absent, use the site's chapter selector. A false result means
    // the selector is valid but we are already at the first/latest chapter, so stop there.
    const selectResult = navigateViaChapterSelect(direction);
    if (selectResult !== null) return;

    // Final fallback only when the site's chapter navigation data is unavailable.
    navigateViaUrlNumber(direction);
  }

  document.addEventListener(
    "keydown",
    (event) => {
      if (isTypingTarget(event.target) || hasModifier(event)) return;

      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        heldScrollKeys.add(event.key);
        activeScrollKey = event.key;
        startScrolling();
        return;
      }

      if ((event.key === "ArrowRight" || event.key === "ArrowLeft") && !event.repeat) {
        event.preventDefault();
        navigateChapter(event.key === "ArrowRight" ? 1 : -1);
      }
    },
    { passive: false }
  );

  document.addEventListener("keyup", (event) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;

    heldScrollKeys.delete(event.key);
    if (activeScrollKey === event.key) {
      activeScrollKey = heldScrollKeys.has("ArrowDown")
        ? "ArrowDown"
        : heldScrollKeys.has("ArrowUp")
          ? "ArrowUp"
          : null;
    }

    if (heldScrollKeys.size === 0) stopScrolling();
  });

  window.addEventListener("blur", () => {
    heldScrollKeys.clear();
    activeScrollKey = null;
    stopScrolling();
  });

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      heldScrollKeys.clear();
      activeScrollKey = null;
      stopScrolling();
    }
  });
})();
