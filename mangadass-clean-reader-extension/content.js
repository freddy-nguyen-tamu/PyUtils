(() => {
  "use strict";

  const DEFAULT_SPEED = 90;
  const MIN_SPEED = 10;
  const MAX_SPEED = 2000;
  const BOTTOM_TOLERANCE_PX = 3;
  const BOTTOM_DWELL_MS = 450;

  let autoScrollEnabled = false;
  let autoScrollSpeed = DEFAULT_SPEED;
  let animationFrameId = 0;
  let lastFrameTime = null;
  let fractionalPixels = 0;
  let bottomSince = null;
  let navigationInProgress = false;
  let readerImages = [];
  let nextChapterUrl = null;
  let previousChapterUrl = null;
  let cleanupObserver = null;

  function clampSpeed(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return DEFAULT_SPEED;
    return Math.min(MAX_SPEED, Math.max(MIN_SPEED, numeric));
  }

  function storageGet(defaults) {
    return new Promise((resolve) => {
      chrome.storage.local.get(defaults, resolve);
    });
  }

  function storageSet(values) {
    return new Promise((resolve) => {
      chrome.storage.local.set(values, resolve);
    });
  }

  function absoluteUrl(href) {
    if (!href) return null;
    try {
      return new URL(href, location.href).href;
    } catch {
      return null;
    }
  }

  function chapterUrlFromData(seriesBaseUrl, chapterData) {
    if (!seriesBaseUrl || !chapterData) return null;
    try {
      return new URL(`${chapterData}`, `${seriesBaseUrl}/`).href;
    } catch {
      return null;
    }
  }

  function discoverChapterNavigation() {
    const chapterLinks = Array.from(
      document.querySelectorAll(".chap-navigation a[href], .navi-change-chapter-btn a[href]")
    );

    const nextLink = chapterLinks.find((link) =>
      /\bnext\b/i.test(link.textContent || "") ||
      /(?:^|\s)navi-change-chapter-btn-next(?:\s|$)/.test(link.className || "")
    );

    const previousLink = chapterLinks.find((link) =>
      /\b(prev|previous)\b/i.test(link.textContent || "") ||
      /(?:^|\s)navi-change-chapter-btn-prev(?:\s|$)/.test(link.className || "")
    );

    let discoveredNext = absoluteUrl(nextLink?.getAttribute("href"));
    let discoveredPrevious = absoluteUrl(previousLink?.getAttribute("href"));

    const selector = document.querySelector(".navi-change-chapter");
    const options = selector
      ? Array.from(selector.querySelectorAll("option[data-c]"))
      : [];

    if (options.length > 0) {
      const selected =
        options.find((option) => option.selected) ||
        options.find((option) => {
          const data = option.getAttribute("data-c");
          return data && location.pathname.endsWith(`/${data}`);
        });

      const selectedIndex = selected ? options.indexOf(selected) : -1;
      const seriesPath = location.pathname.replace(/\/chapter-[^/]+\/?$/i, "");
      const seriesBaseUrl = `${location.origin}${seriesPath}`;

      // MangaDass lists newest chapters first. The entry immediately before the
      // selected one is the next chapter; the entry immediately after it is the
      // previous chapter.
      if (selectedIndex >= 0) {
        if (!discoveredNext && selectedIndex > 0) {
          discoveredNext = chapterUrlFromData(
            seriesBaseUrl,
            options[selectedIndex - 1].getAttribute("data-c")
          );
        }

        if (!discoveredPrevious && selectedIndex < options.length - 1) {
          discoveredPrevious = chapterUrlFromData(
            seriesBaseUrl,
            options[selectedIndex + 1].getAttribute("data-c")
          );
        }
      }
    }

    nextChapterUrl = discoveredNext;
    previousChapterUrl = discoveredPrevious;
  }

  function prepareImages() {
    const sourceImages = Array.from(document.querySelectorAll(".read-content img"));
    if (sourceImages.length === 0) return null;

    const reader = document.createElement("main");
    reader.id = "md-clean-reader";
    reader.setAttribute("role", "main");

    for (const [index, image] of sourceImages.entries()) {
      const dataSource = image.getAttribute("data-src");
      const currentSource = image.getAttribute("src");

      if (dataSource && (!currentSource || currentSource === "#" || currentSource.startsWith("data:image/gif"))) {
        image.setAttribute("src", dataSource);
      }

      image.removeAttribute("class");
      image.removeAttribute("style");
      image.removeAttribute("width");
      image.removeAttribute("height");
      image.removeAttribute("title");
      image.removeAttribute("onclick");
      image.removeAttribute("onload");
      image.removeAttribute("onerror");
      image.setAttribute("alt", "");
      image.setAttribute("draggable", "false");
      image.setAttribute("decoding", "async");
      image.setAttribute("loading", index < 3 ? "eager" : "lazy");

      reader.appendChild(image);
    }

    readerImages = sourceImages;
    return reader;
  }

  function removeInjectedBodyContent(reader) {
    if (cleanupObserver) cleanupObserver.disconnect();

    cleanupObserver = new MutationObserver((mutations) => {
      for (const mutation of mutations) {
        for (const node of mutation.addedNodes) {
          if (!(node instanceof Element)) continue;
          if (node === reader || reader.contains(node)) continue;

          if (node.parentNode && document.body.contains(node)) {
            node.remove();
          }
        }
      }
    });

    cleanupObserver.observe(document.body, {
      childList: true,
      subtree: true
    });
  }

  function buildCleanReader() {
    discoverChapterNavigation();

    const reader = prepareImages();
    if (!reader) {
      document.documentElement.classList.add("md-clean-reader-unavailable");
      document.documentElement.classList.add("md-clean-reader-ready");
      return false;
    }

    document.body.replaceChildren(reader);

    // Remove attributes/classes that can keep the site's layout CSS active.
    document.body.removeAttribute("class");
    document.body.removeAttribute("style");

    removeInjectedBodyContent(reader);
    document.documentElement.classList.add("md-clean-reader-ready");

    return true;
  }

  function allImagesSettled() {
    return readerImages.length > 0 && readerImages.every((image) => image.complete);
  }

  function isAtBottom() {
    const root = document.scrollingElement || document.documentElement;
    return root.scrollTop + window.innerHeight >= root.scrollHeight - BOTTOM_TOLERANCE_PX;
  }

  function cancelAutoScrollLoop() {
    if (animationFrameId) {
      cancelAnimationFrame(animationFrameId);
      animationFrameId = 0;
    }
    lastFrameTime = null;
    fractionalPixels = 0;
    bottomSince = null;
  }

  function navigate(url) {
    if (!url || navigationInProgress) return;
    navigationInProgress = true;
    cancelAutoScrollLoop();
    location.assign(url);
  }

  function autoScrollFrame(timestamp) {
    animationFrameId = 0;

    if (!autoScrollEnabled || navigationInProgress) {
      cancelAutoScrollLoop();
      return;
    }

    if (lastFrameTime === null) {
      lastFrameTime = timestamp;
    }

    const elapsedMs = Math.min(100, Math.max(0, timestamp - lastFrameTime));
    lastFrameTime = timestamp;

    fractionalPixels += (autoScrollSpeed * elapsedMs) / 1000;
    const wholePixels = Math.floor(fractionalPixels);

    if (wholePixels > 0) {
      fractionalPixels -= wholePixels;
      window.scrollBy(0, wholePixels);
    }

    if (isAtBottom() && allImagesSettled()) {
      if (bottomSince === null) {
        bottomSince = timestamp;
      } else if (timestamp - bottomSince >= BOTTOM_DWELL_MS) {
        if (nextChapterUrl) {
          navigate(nextChapterUrl);
          return;
        }

        // End of the available chapter list. Keep the saved ON state so that
        // navigating to another chapter later still resumes auto-scroll.
        cancelAutoScrollLoop();
        return;
      }
    } else {
      bottomSince = null;
    }

    animationFrameId = requestAnimationFrame(autoScrollFrame);
  }

  function startAutoScrollLoop() {
    cancelAutoScrollLoop();
    if (!autoScrollEnabled || navigationInProgress) return;
    animationFrameId = requestAnimationFrame(autoScrollFrame);
  }

  async function setAutoScrollEnabled(enabled) {
    autoScrollEnabled = Boolean(enabled);
    await storageSet({ autoScrollEnabled });

    if (autoScrollEnabled) {
      startAutoScrollLoop();
    } else {
      cancelAutoScrollLoop();
    }
  }

  function onKeyDown(event) {
    if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return;

    // ArrowUp and ArrowDown are intentionally NOT handled here. The browser
    // keeps complete control of their normal scrolling behavior.
    if (event.key === "ArrowRight") {
      event.preventDefault();
      if (nextChapterUrl) navigate(nextChapterUrl);
      return;
    }

    if (event.key === "ArrowLeft") {
      event.preventDefault();
      if (previousChapterUrl) navigate(previousChapterUrl);
      return;
    }

    if (event.key === "Enter") {
      event.preventDefault();
      void setAutoScrollEnabled(!autoScrollEnabled);
    }
  }

  function blockPageClickHandlers(event) {
    // The clean reader has no clickable controls. Stopping ordinary left-click
    // propagation prevents the removed page's popup/ad click handlers from
    // firing, while leaving wheel, touch, keyboard scrolling and context menus
    // untouched.
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopImmediatePropagation();
  }

  async function initialize() {
    const settings = await storageGet({
      autoScrollEnabled: false,
      autoScrollSpeed: DEFAULT_SPEED
    });

    autoScrollEnabled = Boolean(settings.autoScrollEnabled);
    autoScrollSpeed = clampSpeed(settings.autoScrollSpeed);

    const readerReady = buildCleanReader();
    if (!readerReady) return;

    document.addEventListener("keydown", onKeyDown, true);
    document.addEventListener("click", blockPageClickHandlers, true);

    chrome.storage.onChanged.addListener((changes, areaName) => {
      if (areaName !== "local") return;

      if (changes.autoScrollSpeed) {
        autoScrollSpeed = clampSpeed(changes.autoScrollSpeed.newValue);
      }

      if (changes.autoScrollEnabled) {
        autoScrollEnabled = Boolean(changes.autoScrollEnabled.newValue);
        if (autoScrollEnabled) {
          startAutoScrollLoop();
        } else {
          cancelAutoScrollLoop();
        }
      }
    });

    if (autoScrollEnabled) {
      startAutoScrollLoop();
    }
  }

  function bootWhenReady() {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", () => {
        void initialize();
      }, { once: true });
    } else {
      void initialize();
    }
  }

  bootWhenReady();
})();
