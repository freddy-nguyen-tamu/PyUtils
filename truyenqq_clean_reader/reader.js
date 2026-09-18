(() => {
  "use strict";

  // Only activate on URLs that look like chapter pages.
  if (!/\/chapter-[^/?#]+\/?$/.test(location.pathname)) return;

  const root = document.documentElement;
  root.setAttribute("data-tqq-reader-booting", "1");

  const DEFAULT_SPEED = 120;
  const MIN_SPEED = 10;
  const MAX_SPEED = 1000;
  const BOTTOM_EPSILON = 3;
  const AUTO_NEXT_DELAY_MS = 450;

  let autoScrollEnabled = false;
  let scrollSpeed = DEFAULT_SPEED;
  let manualDirection = 0;
  let manualKey = null;
  let pendingImages = 0;
  let nextUrl = null;
  let prevUrl = null;
  let navigating = false;
  let bottomSince = 0;
  let lastFrame = 0;
  let manualScrollPauseUntil = 0;

  const clampSpeed = (value) => {
    const number = Number(value);
    if (!Number.isFinite(number)) return DEFAULT_SPEED;
    return Math.min(MAX_SPEED, Math.max(MIN_SPEED, number));
  };

  const absoluteUrl = (value) => {
    if (!value) return null;
    try {
      return new URL(value, location.href).href;
    } catch {
      return null;
    }
  };

  const extractNavigation = () => {
    const next =
      document.querySelector(".chapter_control a.next") ||
      document.querySelector('a[rel="next"]');
    const prev =
      document.querySelector(".chapter_control a.back") ||
      document.querySelector('a[rel="prev"]');

    nextUrl = absoluteUrl(next?.getAttribute("href"));
    prevUrl = absoluteUrl(prev?.getAttribute("href"));

    // Fallback to the chapter selector if a page ever omits the buttons.
    const select = document.querySelector(".select-reading");
    if (select) {
      const options = Array.from(select.options);
      const currentIndex = options.findIndex(
        (option) => option.selected || absoluteUrl(option.value) === location.href
      );

      if (!nextUrl && currentIndex > 0) {
        nextUrl = absoluteUrl(options[currentIndex - 1]?.value);
      }
      if (!prevUrl && currentIndex >= 0 && currentIndex < options.length - 1) {
        prevUrl = absoluteUrl(options[currentIndex + 1]?.value);
      }
    }
  };

  const extractImageUrls = () => {
    const candidates = Array.from(
      document.querySelectorAll(".reading-content .inner img, .reading-content img")
    );

    const seen = new Set();
    const urls = [];

    for (const img of candidates) {
      const raw =
        img.getAttribute("data-src") ||
        img.getAttribute("data-original") ||
        img.currentSrc ||
        img.getAttribute("src");

      const url = absoluteUrl(raw);
      if (!url || /\/images\/loading\.svg(?:[?#]|$)/i.test(url) || seen.has(url)) {
        continue;
      }

      seen.add(url);
      urls.push({
        url,
        alt: img.getAttribute("alt") || ""
      });
    }

    return urls;
  };

  const atBottom = () => {
    const doc = document.documentElement;
    const body = document.body;
    const scrollHeight = Math.max(
      doc?.scrollHeight || 0,
      body?.scrollHeight || 0
    );
    return window.scrollY + window.innerHeight >= scrollHeight - BOTTOM_EPSILON;
  };

  const navigate = (url) => {
    if (!url || navigating) return;
    navigating = true;
    location.href = url;
  };

  const maybeAdvance = (now, forceAttempt = false) => {
    if (!nextUrl || pendingImages > 0 || !atBottom()) {
      bottomSince = 0;
      return;
    }

    if (!(autoScrollEnabled || manualDirection > 0 || forceAttempt)) {
      bottomSince = 0;
      return;
    }

    if (!bottomSince) bottomSince = now;

    if (now - bottomSince >= AUTO_NEXT_DELAY_MS) {
      navigate(nextUrl);
    }
  };

  const frame = (now) => {
    if (!lastFrame) lastFrame = now;
    const dt = Math.min(0.05, Math.max(0, (now - lastFrame) / 1000));
    lastFrame = now;

    const direction = manualDirection !== 0
      ? manualDirection
      : (autoScrollEnabled && now >= manualScrollPauseUntil ? 1 : 0);

    if (direction !== 0 && !navigating) {
      const before = window.scrollY;
      window.scrollBy(0, direction * scrollSpeed * dt);
      const after = window.scrollY;

      if (direction > 0 && Math.abs(after - before) < 0.01) {
        maybeAdvance(now);
      } else {
        bottomSince = 0;
      }
    } else {
      bottomSince = 0;
    }

    requestAnimationFrame(frame);
  };

  const setAutoScroll = async (enabled) => {
    autoScrollEnabled = Boolean(enabled);
    bottomSince = 0;
    try {
      await chrome.storage.local.set({ autoScrollEnabled });
    } catch {
      // Keep the in-page state even if extension storage is temporarily unavailable.
    }
  };

  const onKeyDown = (event) => {
    if (event.key === "ArrowRight") {
      event.preventDefault();
      event.stopImmediatePropagation();
      navigate(nextUrl);
      return;
    }

    if (event.key === "ArrowLeft") {
      event.preventDefault();
      event.stopImmediatePropagation();
      navigate(prevUrl);
      return;
    }

    if (event.key === "ArrowDown") {
      event.preventDefault();
      event.stopImmediatePropagation();
      manualDirection = 1;
      manualKey = "ArrowDown";
      return;
    }

    if (event.key === "ArrowUp") {
      event.preventDefault();
      event.stopImmediatePropagation();
      manualDirection = -1;
      manualKey = "ArrowUp";
      return;
    }

    if (event.key === "Enter") {
      event.preventDefault();
      event.stopImmediatePropagation();
      if (!event.repeat) setAutoScroll(!autoScrollEnabled);
    }
  };

  const onKeyUp = (event) => {
    if (event.key === manualKey) {
      event.preventDefault();
      event.stopImmediatePropagation();
      manualDirection = 0;
      manualKey = null;
      bottomSince = 0;
    } else if (
      event.key === "ArrowLeft" ||
      event.key === "ArrowRight" ||
      event.key === "ArrowUp" ||
      event.key === "ArrowDown" ||
      event.key === "Enter"
    ) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  };

  // Install keyboard interception before the site's own DOM-ready handlers.
  // This prevents its original chapter hotkeys from competing with ours.
  window.addEventListener("keydown", onKeyDown, true);
  window.addEventListener("keyup", onKeyUp, true);

  const installNativeScrollProtection = () => {
    // The site's JavaScript may leave wheel/touch listeners attached to
    // window/document even after we replace the visible DOM. Intercept those
    // events first, stop the site's handlers, but DO NOT preventDefault().
    // That leaves the browser's own native wheel/touch scrolling intact.
    const pauseForManualScroll = () => {
      manualScrollPauseUntil = performance.now() + 900;
      bottomSince = 0;
    };

    window.addEventListener(
      "wheel",
      (event) => {
        event.stopImmediatePropagation();
        pauseForManualScroll();

        // A downward wheel attempt while already at the bottom advances.
        if (event.deltaY > 0 && !navigating) {
          window.setTimeout(() => {
            if (atBottom()) {
              const started = performance.now();
              if (!bottomSince) bottomSince = started;
              window.setTimeout(
                () => maybeAdvance(performance.now(), true),
                AUTO_NEXT_DELAY_MS
              );
            }
          }, 0);
        }
      },
      { passive: true, capture: true }
    );

    window.addEventListener(
      "touchstart",
      (event) => {
        event.stopImmediatePropagation();
        pauseForManualScroll();
      },
      { passive: true, capture: true }
    );

    window.addEventListener(
      "touchmove",
      (event) => {
        event.stopImmediatePropagation();
        pauseForManualScroll();
      },
      { passive: true, capture: true }
    );

    // Scrollbar dragging, mouse-wheel scrolling, PageUp/PageDown from browser
    // UI, and other native scroll mechanisms all trigger this. Keep auto-scroll
    // from immediately fighting the user's new position.
    window.addEventListener(
      "scroll",
      () => {
        if (manualDirection === 0) {
          manualScrollPauseUntil = Math.max(
            manualScrollPauseUntil,
            performance.now() + 250
          );
        }
      },
      { passive: true, capture: true }
    );
  };

  const buildReader = (images) => {
    const reader = document.createElement("main");
    reader.id = "tqq-clean-reader";

    pendingImages = images.length;

    for (const item of images) {
      const img = document.createElement("img");
      img.alt = item.alt;
      img.decoding = "async";
      img.loading = "eager";
      img.draggable = false;

      const settled = () => {
        pendingImages = Math.max(0, pendingImages - 1);
      };

      img.addEventListener("load", settled, { once: true });
      img.addEventListener("error", settled, { once: true });
      img.src = item.url;
      reader.appendChild(img);
    }

    document.body.replaceChildren(reader);

    root.removeAttribute("data-tqq-reader-booting");
    root.setAttribute("data-tqq-reader-ready", "1");
  };

  const init = async () => {
    const images = extractImageUrls();

    if (!images.length) {
      root.removeAttribute("data-tqq-reader-booting");
      return;
    }

    extractNavigation();

    try {
      const saved = await chrome.storage.local.get({
        autoScrollEnabled: false,
        scrollSpeed: DEFAULT_SPEED
      });
      autoScrollEnabled = Boolean(saved.autoScrollEnabled);
      scrollSpeed = clampSpeed(saved.scrollSpeed);
    } catch {
      autoScrollEnabled = false;
      scrollSpeed = DEFAULT_SPEED;
    }

    buildReader(images);

    window.addEventListener("blur", () => {
      manualDirection = 0;
      manualKey = null;
      bottomSince = 0;
    });

    installNativeScrollProtection();

    chrome.storage.onChanged.addListener((changes, areaName) => {
      if (areaName !== "local") return;
      if (changes.scrollSpeed) {
        scrollSpeed = clampSpeed(changes.scrollSpeed.newValue);
      }
      if (changes.autoScrollEnabled) {
        autoScrollEnabled = Boolean(changes.autoScrollEnabled.newValue);
        bottomSince = 0;
      }
    });

    requestAnimationFrame(frame);
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
