(() => {
  'use strict';

  // Deliberate design constraint:
  // This extension DOES NOT register a wheel/mousewheel/DOMMouseScroll listener.
  // It does not prevent, replace, synthesize, or otherwise alter mouse-wheel events.
  // Manual wheel/touchpad scrolling is performed entirely by the browser inside
  // the clean reader iframe's normal scrollable document.

  const DEFAULT_SPEED = 120; // pixels per second
  const MIN_SPEED = 10;
  const MAX_SPEED = 5000;
  const BOTTOM_TOLERANCE_PX = 3;
  const BOTTOM_HOLD_MS = 500;

  const clampSpeed = (value) => {
    const n = Number(value);
    if (!Number.isFinite(n)) return DEFAULT_SPEED;
    return Math.max(MIN_SPEED, Math.min(MAX_SPEED, n));
  };

  const toAbsoluteUrl = (value) => {
    if (!value) return null;
    try {
      return new URL(value, location.href).href;
    } catch {
      return null;
    }
  };

  const imageUrls = [...document.querySelectorAll('#viewer img.image, #viewer img')]
    .map((img) =>
      img.currentSrc ||
      img.getAttribute('src') ||
      img.getAttribute('data-src') ||
      img.getAttribute('data-original')
    )
    .map(toAbsoluteUrl)
    .filter(Boolean)
    .filter((url, index, all) => all.indexOf(url) === index);

  // Do nothing on non-reader MangaHome pages.
  if (imageUrls.length === 0) return;

  function findChapterUrl(kind) {
    const isNext = kind === 'next';
    const variableName = isNext ? 'nextchapterurl' : 'prechapterurl';

    // The reader page currently publishes these URLs in an inline script.
    for (const script of document.scripts) {
      const text = script.textContent || '';
      if (!text.includes(variableName)) continue;
      const match = text.match(
        new RegExp(`${variableName}\\s*=\\s*["']([^"']+)["']`, 'i')
      );
      if (match?.[1]) return toAbsoluteUrl(match[1]);
    }

    // Fallback: use the reader footer's chapter links.
    const footerLinks = [...document.querySelectorAll('.mangaread-footer a[href]')];
    const byText = footerLinks.find((a) => {
      const text = (a.textContent || '').trim().toLowerCase();
      return isNext ? text.includes('next chapter') : text.includes('prev chapter');
    });
    if (byText) return toAbsoluteUrl(byText.getAttribute('href'));

    // Fallback: use the current item in the chapter list.
    const current = document.querySelector('#readChapterLists a.current');
    const sibling = isNext
      ? current?.closest('li')?.nextElementSibling
      : current?.closest('li')?.previousElementSibling;
    const siblingLink = sibling?.querySelector('a[href]');
    return siblingLink ? toAbsoluteUrl(siblingLink.getAttribute('href')) : null;
  }

  const previousChapterUrl = findChapterUrl('previous');
  const nextChapterUrl = findChapterUrl('next');

  const escapeHtml = (value) =>
    String(value)
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#039;');

  const imageMarkup = imageUrls
    .map(
      (src, index) =>
        `<img src="${escapeHtml(src)}" alt="" decoding="async" loading="eager" ${index < 3 ? 'fetchpriority="high"' : ''}>`
    )
    .join('');

  const readerHtml = `<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    html, body {
      margin: 0;
      padding: 0;
      min-height: 100%;
      background: #111;
    }

    body {
      width: 100%;
    }

    main {
      display: block;
      width: 100%;
      margin: 0;
      padding: 0;
    }

    img {
      display: block;
      width: auto;
      max-width: 100%;
      height: auto;
      margin: 0 auto;
      padding: 0;
      border: 0;
    }
  </style>
</head>
<body>
  <main>${imageMarkup}</main>
</body>
</html>`;

  // Replace the visible site DOM with one opaque, isolated iframe. The site's
  // original document no longer participates in layout/scrolling, so its reader
  // CSS and scroll effects cannot affect the clean image stream.
  const host = document.createElement('mangahome-clean-reader');
  host.style.setProperty('all', 'initial', 'important');
  host.style.setProperty('position', 'fixed', 'important');
  host.style.setProperty('inset', '0', 'important');
  host.style.setProperty('display', 'block', 'important');
  host.style.setProperty('width', '100vw', 'important');
  host.style.setProperty('height', '100vh', 'important');
  host.style.setProperty('margin', '0', 'important');
  host.style.setProperty('padding', '0', 'important');
  host.style.setProperty('border', '0', 'important');
  host.style.setProperty('z-index', '2147483647', 'important');
  host.style.setProperty('background', '#111', 'important');

  const shadow = host.attachShadow({ mode: 'closed' });
  const frame = document.createElement('iframe');
  frame.setAttribute('title', '');
  frame.setAttribute('aria-hidden', 'true');
  frame.tabIndex = 0;
  frame.style.cssText = [
    'display:block',
    'width:100%',
    'height:100%',
    'margin:0',
    'padding:0',
    'border:0',
    'background:#111'
  ].join(';');
  frame.srcdoc = readerHtml;
  shadow.appendChild(frame);

  document.documentElement.style.setProperty('margin', '0', 'important');
  document.documentElement.style.setProperty('padding', '0', 'important');
  document.documentElement.style.setProperty('overflow', 'hidden', 'important');
  document.body.replaceChildren();
  document.body.style.setProperty('margin', '0', 'important');
  document.body.style.setProperty('padding', '0', 'important');
  document.body.style.setProperty('overflow', 'hidden', 'important');
  document.body.appendChild(host);

  let autoScrollEnabled = false;
  let scrollSpeed = DEFAULT_SPEED;
  let manualDirection = 0;
  let navigating = false;
  let readerWindow = null;
  let readerDocument = null;
  let animationFrameId = 0;
  let lastFrameTime = 0;
  let bottomReachedAt = null;
  let readerReady = false;

  function navigate(url) {
    if (!url || navigating) return;
    navigating = true;
    location.assign(url);
  }

  function isAtBottom() {
    if (!readerWindow || !readerDocument) return false;
    const root = readerDocument.documentElement;
    const body = readerDocument.body;
    const scrollHeight = Math.max(
      root?.scrollHeight || 0,
      body?.scrollHeight || 0
    );
    return (
      readerWindow.scrollY + readerWindow.innerHeight >=
      scrollHeight - BOTTOM_TOLERANCE_PX
    );
  }

  function handleKeyDown(event) {
    if (event.defaultPrevented) return;

    switch (event.key) {
      case 'ArrowRight':
        event.preventDefault();
        event.stopImmediatePropagation();
        if (!event.repeat) navigate(nextChapterUrl);
        break;

      case 'ArrowLeft':
        event.preventDefault();
        event.stopImmediatePropagation();
        if (!event.repeat) navigate(previousChapterUrl);
        break;

      case 'ArrowDown':
        event.preventDefault();
        event.stopImmediatePropagation();
        manualDirection = 1;
        break;

      case 'ArrowUp':
        event.preventDefault();
        event.stopImmediatePropagation();
        manualDirection = -1;
        bottomReachedAt = null;
        break;

      case 'Enter':
        event.preventDefault();
        event.stopImmediatePropagation();
        if (event.repeat) break;
        autoScrollEnabled = !autoScrollEnabled;
        bottomReachedAt = null;
        chrome.storage.local.set({ autoScrollEnabled });
        break;

      default:
        break;
    }
  }

  function handleKeyUp(event) {
    if (event.key === 'ArrowDown' && manualDirection === 1) {
      event.preventDefault();
      event.stopImmediatePropagation();
      manualDirection = 0;
    } else if (event.key === 'ArrowUp' && manualDirection === -1) {
      event.preventDefault();
      event.stopImmediatePropagation();
      manualDirection = 0;
    }
  }

  function animate(now) {
    if (!readerReady || !readerWindow) {
      animationFrameId = requestAnimationFrame(animate);
      return;
    }

    if (!lastFrameTime) lastFrameTime = now;
    const elapsedSeconds = Math.min((now - lastFrameTime) / 1000, 0.05);
    lastFrameTime = now;

    // Arrow-key movement temporarily overrides autoscroll while the key is held.
    // When released, persistent autoscroll resumes if it was enabled.
    const direction = manualDirection !== 0
      ? manualDirection
      : (autoScrollEnabled ? 1 : 0);

    if (direction !== 0) {
      readerWindow.scrollBy(0, direction * scrollSpeed * elapsedSeconds);
    }

    if (autoScrollEnabled && manualDirection === 0 && isAtBottom()) {
      if (bottomReachedAt === null) bottomReachedAt = now;
      if (now - bottomReachedAt >= BOTTOM_HOLD_MS) {
        navigate(nextChapterUrl);
      }
    } else {
      bottomReachedAt = null;
    }

    animationFrameId = requestAnimationFrame(animate);
  }

  frame.addEventListener('load', () => {
    readerWindow = frame.contentWindow;
    readerDocument = frame.contentDocument;
    if (!readerWindow || !readerDocument) return;

    // Keyboard only. There is intentionally no wheel listener anywhere.
    readerWindow.addEventListener('keydown', handleKeyDown, true);
    readerWindow.addEventListener('keyup', handleKeyUp, true);

    readerReady = true;
    frame.focus({ preventScroll: true });
    readerWindow.focus();
  }, { once: true });

  // Fallback if focus is ever moved back to the parent document.
  // These are keyboard handlers only; mouse-wheel input remains untouched.
  window.addEventListener('keydown', handleKeyDown, true);
  window.addEventListener('keyup', handleKeyUp, true);

  chrome.storage.local.get(
    {
      autoScrollEnabled: false,
      scrollSpeed: DEFAULT_SPEED
    },
    (stored) => {
      autoScrollEnabled = Boolean(stored.autoScrollEnabled);
      scrollSpeed = clampSpeed(stored.scrollSpeed);
      animationFrameId = requestAnimationFrame(animate);
    }
  );

  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName !== 'local') return;

    if (changes.scrollSpeed) {
      scrollSpeed = clampSpeed(changes.scrollSpeed.newValue);
    }

    if (changes.autoScrollEnabled) {
      autoScrollEnabled = Boolean(changes.autoScrollEnabled.newValue);
      bottomReachedAt = null;
    }
  });

  window.addEventListener('pagehide', () => {
    if (animationFrameId) cancelAnimationFrame(animationFrameId);
  }, { once: true });
})();
