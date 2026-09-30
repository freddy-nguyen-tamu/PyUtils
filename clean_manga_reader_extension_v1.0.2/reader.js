(() => {
  "use strict";

  const DEFAULTS = {
    autoScrollEnabled: false,
    autoScrollSpeed: 55
  };

  let initialized = false;
  let autoScrollEnabled = false;
  let scrollSpeed = DEFAULTS.autoScrollSpeed;
  let nextChapterUrl = null;
  let previousChapterUrl = null;
  let navigating = false;

  let readerFrame = null;
  let readerWindow = null;
  let readerDocument = null;
  let readerScroller = null;

  let animationFrame = 0;
  let lastFrameTime = 0;
  let fractionalPixels = 0;
  let manualDirection = 0;
  let imagesSettled = false;
  let layoutStableSince = performance.now();
  let lastScrollHeight = 0;
  let bottomSince = 0;
  let autoScrollMoved = false;

  // Prevent a flash of the site's normal reader while we collect its image URLs.
  const earlyStyle = document.createElement("style");
  earlyStyle.id = "cmar-early-style";
  earlyStyle.textContent = `
    html { background:#111 !important; }
    body { visibility:hidden !important; }
  `;
  (document.documentElement || document).appendChild(earlyStyle);

  function storageGet(keys) {
    return new Promise((resolve) => chrome.storage.local.get(keys, resolve));
  }

  function storageSet(values) {
    return new Promise((resolve) => chrome.storage.local.set(values, resolve));
  }

  function normalizeSpeed(value) {
    const n = Number(value);
    return Number.isFinite(n) ? Math.min(300, Math.max(10, n)) : DEFAULTS.autoScrollSpeed;
  }

  function absoluteUrl(raw) {
    if (!raw) return null;
    try {
      return new URL(raw, location.href).href;
    } catch {
      return null;
    }
  }

  function pickLargestSrcset(srcset) {
    if (!srcset) return null;
    const entries = srcset
      .split(",")
      .map((part) => part.trim())
      .filter(Boolean)
      .map((part) => {
        const m = part.match(/^(\S+)(?:\s+(\d+)(?:w|x))?$/);
        return m ? { url: m[1], score: Number(m[2] || 0) } : null;
      })
      .filter(Boolean)
      .sort((a, b) => b.score - a.score);
    return entries[0]?.url || null;
  }

  function imageUrl(img) {
    const candidates = [
      img.getAttribute("data-src"),
      img.getAttribute("data-lazy-src"),
      img.getAttribute("data-original"),
      pickLargestSrcset(img.getAttribute("data-srcset")),
      pickLargestSrcset(img.getAttribute("srcset")),
      img.getAttribute("src"),
      img.currentSrc
    ];

    for (const raw of candidates) {
      const url = absoluteUrl(raw);
      if (!url || /^data:/i.test(url) || /^blob:/i.test(url)) continue;
      return url;
    }
    return null;
  }

  function looksLikeMangaImage(img, url) {
    if (!url) return false;

    let parsed;
    try {
      parsed = new URL(url);
    } catch {
      return false;
    }

    const alt = (img.getAttribute("alt") || "").toLowerCase();
    const cls = String(img.className || "").toLowerCase();
    const path = parsed.pathname.toLowerCase();

    if (parsed.hostname === "official.lowee.us" && path.includes("/manga/")) return true;
    if (path.includes("/manga/kangoku-gakuen/")) return true;
    if (alt.includes("prison school chapter")) return true;
    if (alt.includes("kangoku gakuen chapter")) return true;
    if (cls.includes("mz-lazy") && /chapter/i.test(alt)) return true;

    return false;
  }

  function collectMangaImages() {
    const scope = document.querySelector(".entry-content") || document;
    const seen = new Set();
    const result = [];

    for (const img of scope.querySelectorAll("img")) {
      const url = imageUrl(img);
      if (!looksLikeMangaImage(img, url) || seen.has(url)) continue;
      seen.add(url);

      result.push({
        url,
        width: Number(img.getAttribute("width")) || 0,
        height: Number(img.getAttribute("height")) || 0
      });
    }

    return result;
  }

  function chapterNumberFromText(value) {
    if (!value) return null;
    const m = String(value).match(/chapter\s*[-–—:#]?\s*(\d+(?:\.\d+)?)/i);
    if (!m) return null;
    const n = Number(m[1]);
    return Number.isFinite(n) ? n : null;
  }

  function chapterNumberFromUrl(value) {
    if (!value) return null;
    let path;
    try {
      path = new URL(value, location.href).pathname;
    } catch {
      path = String(value);
    }

    const m = path.match(/chapter[-_/]?(\d+)(?:[.-](\d+))?/i);
    if (!m) return null;

    if (m[2]) {
      const n = Number(`${m[1]}.${m[2]}`);
      return Number.isFinite(n) ? n : null;
    }

    const n = Number(m[1]);
    return Number.isFinite(n) ? n : null;
  }

  function currentChapterNumber() {
    return (
      chapterNumberFromText(document.querySelector("h1")?.textContent) ??
      chapterNumberFromText(document.title) ??
      chapterNumberFromUrl(location.href)
    );
  }

  function collectChapterNavigation() {
    const current = currentChapterNumber();
    const candidates = [];
    const seen = new Set();

    const navLinks = [
      ...document.querySelectorAll('.post-navigation a[href*="/manga/"]'),
      ...document.querySelectorAll('a[rel="prev"][href*="/manga/"], a[rel="next"][href*="/manga/"]')
    ];

    for (const a of navLinks) {
      const url = absoluteUrl(a.getAttribute("href"));
      if (!url || seen.has(url) || url === location.href) continue;
      seen.add(url);

      const n =
        chapterNumberFromText(a.textContent) ??
        chapterNumberFromText(a.getAttribute("aria-label")) ??
        chapterNumberFromUrl(url);

      candidates.push({ url, n });
    }

    if (current != null) {
      const higher = candidates
        .filter((x) => x.n != null && x.n > current)
        .sort((a, b) => a.n - b.n);
      const lower = candidates
        .filter((x) => x.n != null && x.n < current)
        .sort((a, b) => b.n - a.n);

      nextChapterUrl = higher[0]?.url || null;
      previousChapterUrl = lower[0]?.url || null;
    }

    // WordPress labels these chronologically, so on this site nav-previous often
    // means the numerically NEXT manga chapter.
    if (!nextChapterUrl) {
      nextChapterUrl = absoluteUrl(
        document.querySelector(".post-navigation .nav-previous a")?.getAttribute("href")
      );
    }

    if (!previousChapterUrl) {
      previousChapterUrl = absoluteUrl(
        document.querySelector(".post-navigation .nav-next a")?.getAttribute("href")
      );
    }

    if (nextChapterUrl === location.href) nextChapterUrl = null;
    if (previousChapterUrl === location.href) previousChapterUrl = null;
  }

  function getScrollTop() {
    return readerScroller?.scrollTop || 0;
  }

  function getScrollHeight() {
    return readerScroller?.scrollHeight || 0;
  }

  function getClientHeight() {
    return readerScroller?.clientHeight || 0;
  }

  function isAtBottom() {
    if (!readerScroller) return false;
    return getScrollTop() >= Math.max(0, getScrollHeight() - getClientHeight()) - 2;
  }

  function navigate(url) {
    if (!url || navigating) return;
    navigating = true;
    location.href = url;
  }

  function navigateNext() {
    navigate(nextChapterUrl);
  }

  function navigatePrevious() {
    navigate(previousChapterUrl);
  }

  async function setAutoScrollEnabled(enabled) {
    autoScrollEnabled = Boolean(enabled);
    bottomSince = 0;
    fractionalPixels = 0;
    autoScrollMoved = false;
    await storageSet({ autoScrollEnabled });
  }

  function disableAutoScrollForManualControl() {
    if (!autoScrollEnabled) return;
    autoScrollEnabled = false;
    bottomSince = 0;
    fractionalPixels = 0;
    autoScrollMoved = false;
    storageSet({ autoScrollEnabled: false });
  }

  function monitorLayoutStability(now) {
    const height = getScrollHeight();
    if (height !== lastScrollHeight) {
      lastScrollHeight = height;
      layoutStableSince = now;
      bottomSince = 0;
    }
  }

  function maybeAdvanceAtBottom(now) {
    // Reaching the bottom manually NEVER changes chapters. Auto-next belongs
    // only to an explicitly enabled auto-scroll session that actually moved.
    if (
      !autoScrollEnabled ||
      !autoScrollMoved ||
      !nextChapterUrl ||
      !imagesSettled ||
      !isAtBottom()
    ) {
      bottomSince = 0;
      return;
    }

    if (now - layoutStableSince < 800) {
      bottomSince = 0;
      return;
    }

    if (!bottomSince) bottomSince = now;

    if (now - bottomSince >= 1200) {
      navigateNext();
    }
  }

  function applyScroll(delta) {
    if (!readerScroller || !delta) return;

    fractionalPixels += delta;
    const whole = fractionalPixels > 0 ? Math.floor(fractionalPixels) : Math.ceil(fractionalPixels);

    if (whole !== 0) {
      readerScroller.scrollTop += whole;
      fractionalPixels -= whole;
    }
  }

  function animationLoop(now) {
    if (!initialized || !readerScroller) {
      animationFrame = requestAnimationFrame(animationLoop);
      return;
    }

    if (!lastFrameTime) lastFrameTime = now;
    const dt = Math.min(80, Math.max(0, now - lastFrameTime));
    lastFrameTime = now;

    monitorLayoutStability(now);

    if (manualDirection !== 0) {
      applyScroll(manualDirection * scrollSpeed * (dt / 1000));
    } else if (autoScrollEnabled && !navigating) {
      const before = getScrollTop();
      applyScroll(scrollSpeed * (dt / 1000));
      if (getScrollTop() > before) autoScrollMoved = true;
    }

    maybeAdvanceAtBottom(now);
    animationFrame = requestAnimationFrame(animationLoop);
  }

  function shouldOwnKey(event) {
    if (!initialized) return false;
    const target = event.target;
    if (
      target &&
      (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT|BUTTON)$/i.test(target.tagName || ""))
    ) {
      return false;
    }
    return ["ArrowRight", "ArrowLeft", "ArrowUp", "ArrowDown", "Enter"].includes(event.key);
  }

  function onKeyDown(event) {
    if (!shouldOwnKey(event)) return;

    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();

    if (event.key === "ArrowRight") {
      if (!event.repeat) navigateNext();
      return;
    }

    if (event.key === "ArrowLeft") {
      if (!event.repeat) navigatePrevious();
      return;
    }

    if (event.key === "Enter") {
      if (!event.repeat) setAutoScrollEnabled(!autoScrollEnabled);
      return;
    }

    // Arrow scrolling is manual control, so it cancels unattended auto-scroll.
    if (event.key === "ArrowUp") {
      disableAutoScrollForManualControl();
      manualDirection = -1;
      bottomSince = 0;
      return;
    }

    if (event.key === "ArrowDown") {
      disableAutoScrollForManualControl();
      manualDirection = 1;
      bottomSince = 0;
    }
  }

  function onKeyUp(event) {
    if (!initialized) return;
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;

    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();
    manualDirection = 0;
    fractionalPixels = 0;
  }

  function installKeyboardHandlers() {
    window.addEventListener("keydown", onKeyDown, true);
    window.addEventListener("keyup", onKeyUp, true);
  }

  function installFrameManualInputHandlers() {
    if (!readerWindow || !readerDocument) return;

    // Once the user clicks inside the iframe, keyboard focus belongs to that
    // browsing context, so register the same shortcuts there too.
    readerWindow.addEventListener("keydown", onKeyDown, true);
    readerWindow.addEventListener("keyup", onKeyUp, true);

    // These listeners live INSIDE the clean iframe. Wheel/touch events never
    // enter the website's document, so the site's old reader cannot cancel them.
    readerWindow.addEventListener(
      "wheel",
      () => {
        disableAutoScrollForManualControl();
        bottomSince = 0;
      },
      { passive: true }
    );

    readerWindow.addEventListener(
      "touchstart",
      () => {
        disableAutoScrollForManualControl();
        bottomSince = 0;
      },
      { passive: true }
    );

    readerWindow.addEventListener(
      "touchmove",
      () => {
        disableAutoScrollForManualControl();
        bottomSince = 0;
      },
      { passive: true }
    );

    // Clicking/dragging inside the clean reader is also explicit manual control.
    readerDocument.addEventListener(
      "pointerdown",
      () => {
        if (autoScrollEnabled) disableAutoScrollForManualControl();
      },
      { passive: true }
    );
  }

  function buildIsolatedReader(images) {
    try {
      window.stop();
    } catch {}

    try {
      history.scrollRestoration = "manual";
    } catch {}

    const body = document.body || document.createElement("body");
    if (!document.body) document.documentElement.appendChild(body);

    // The parent website never scrolls. The iframe is the only scroll surface.
    const parentStyle = document.createElement("style");
    parentStyle.textContent = `
      html, body {
        margin: 0 !important;
        padding: 0 !important;
        width: 100% !important;
        height: 100% !important;
        overflow: hidden !important;
        background: #111 !important;
        visibility: visible !important;
      }
      #cmar-frame {
        position: fixed !important;
        inset: 0 !important;
        width: 100vw !important;
        height: 100vh !important;
        border: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
        display: block !important;
        z-index: 2147483647 !important;
        background: #111 !important;
      }
    `;
    (document.head || document.documentElement).appendChild(parentStyle);

    readerFrame = document.createElement("iframe");
    readerFrame.id = "cmar-frame";
    readerFrame.title = "Clean manga reader";
    readerFrame.setAttribute("scrolling", "yes");

    body.replaceChildren(readerFrame);

    readerWindow = readerFrame.contentWindow;
    readerDocument = readerFrame.contentDocument;

    readerDocument.open();
    readerDocument.write(`<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  :root {
    color-scheme: dark;
    background: #111;
  }
  html, body {
    margin: 0;
    padding: 0;
    width: 100%;
    min-height: 100%;
    background: #111;
    overflow-x: hidden;
    overflow-y: auto;
    overscroll-behavior: none;
    scroll-behavior: auto;
    scrollbar-gutter: stable;
    touch-action: pan-y;
  }
  #reader {
    width: 100%;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0;
    line-height: 0;
    font-size: 0;
  }
  #reader img {
    display: block;
    width: auto;
    height: auto;
    max-width: min(100vw, 1200px);
    margin: 0 auto;
    padding: 0;
    border: 0;
    outline: 0;
    box-shadow: none;
    vertical-align: top;
    object-fit: contain;
    user-select: none;
    -webkit-user-drag: none;
    background: transparent;
  }
  @media (max-width: 1200px) {
    #reader img { max-width: 100vw; }
  }
</style>
</head>
<body><main id="reader"></main></body>
</html>`);
    readerDocument.close();

    readerScroller = readerDocument.scrollingElement || readerDocument.documentElement;
    const reader = readerDocument.getElementById("reader");

    let pending = images.length;
    const markSettled = () => {
      pending -= 1;
      if (pending <= 0) {
        imagesSettled = true;
        layoutStableSince = performance.now();
        lastScrollHeight = getScrollHeight();
      }
    };

    for (const item of images) {
      const img = readerDocument.createElement("img");
      img.src = item.url;
      img.alt = "";
      img.decoding = "async";
      img.loading = "eager";
      img.referrerPolicy = "no-referrer-when-downgrade";

      if (item.width > 0 && item.height > 0) {
        img.width = item.width;
        img.height = item.height;
      }

      img.addEventListener("load", markSettled, { once: true });
      img.addEventListener("error", markSettled, { once: true });
      reader.appendChild(img);

      if (img.complete) markSettled();
    }

    if (images.length === 0) imagesSettled = true;

    installFrameManualInputHandlers();
    readerScroller.scrollTop = 0;
    lastScrollHeight = getScrollHeight();
    layoutStableSince = performance.now();

    earlyStyle.remove();
  }

  async function init() {
    if (initialized) return;

    const images = collectMangaImages();
    if (!images.length) {
      earlyStyle.remove();
      if (document.body) document.body.style.visibility = "";
      return;
    }

    collectChapterNavigation();

    const stored = await storageGet(["autoScrollEnabled", "autoScrollSpeed"]);
    autoScrollEnabled =
      typeof stored.autoScrollEnabled === "boolean"
        ? stored.autoScrollEnabled
        : DEFAULTS.autoScrollEnabled;
    scrollSpeed = normalizeSpeed(stored.autoScrollSpeed);

    buildIsolatedReader(images);
    initialized = true;
  }

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "local") return;

    if (changes.autoScrollSpeed) {
      scrollSpeed = normalizeSpeed(changes.autoScrollSpeed.newValue);
    }

    if (changes.autoScrollEnabled) {
      autoScrollEnabled = Boolean(changes.autoScrollEnabled.newValue);
      bottomSince = 0;
      fractionalPixels = 0;
      autoScrollMoved = false;
    }
  });

  installKeyboardHandlers();
  animationFrame = requestAnimationFrame(animationLoop);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
