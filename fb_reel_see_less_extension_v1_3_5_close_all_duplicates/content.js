(() => {
  "use strict";

  const BUTTON_CLASS = "qacer-fb-reel-see-less-button";
  const STATUS_ID = "qacer-fb-reel-see-less-status";
  const PLAYER_SELECTOR = '[aria-label="Video player"][role="group"]';
  const PATH_RE = /^\/reel\/[^/]+\/?/;
  const SEEN_PLAYER_ATTR = "data-qacer-see-less-button";

  // Classes copied from Facebook's reel media-control button style in your sample.
  const FB_CONTROL_BUTTON_CLASSES = [
    "x1i10hfl", "x1qjc9v5", "xjbqb8w", "xjqpnuy", "xc5r6h4", "xqeqjp1",
    "x1phubyo", "x13fuv20", "x18b5jzi", "x1q0q8m5", "x1t7ytsu", "x972fbf",
    "x10w94by", "x1qhh985", "x14e42zd", "x9f619", "x1ypdohk", "xdl72j9",
    "x2lah0s", "x3ct3a4", "xdj266r", "x14z9mp", "xat24cr", "x1lziwak",
    "x2lwn1j", "xeuugli", "x1n2onr6", "x16tdsg8", "x1hl2dhg", "xggy1nq",
    "x1ja2u2z", "x1t137rt", "x1fmog5m", "xu25z0z", "x140muxe", "xo1y3bh",
    "x3nfvp2", "x1q0g3np", "x87ps6o", "x1lku1pv", "x1a2a7pz", "xz9dl7a",
    "xpdmqnj", "xsag5q8", "x1g0dm76"
  ];

  function isReelUrl() {
    return location.hostname === "www.facebook.com" && PATH_RE.test(location.pathname);
  }

  function isVisible(el) {
    if (!(el instanceof Element)) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" &&
      style.display !== "none" && Number(style.opacity || "1") > 0;
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function showStatus(message, ok = true) {
    // No in-page popup: Facebook already shows its own confirmation after “See less”.
    // Keep quiet on success/progress and only log failures to the console for debugging.
    if (!ok) console.warn("[FB Reel See Less]", message);
  }

  function fireRealishClick(el) {
    if (!el) return false;
    try { el.scrollIntoView?.({ block: "center", inline: "center", behavior: "instant" }); } catch {}
    const rect = el.getBoundingClientRect();
    const opts = {
      bubbles: true,
      cancelable: true,
      composed: true,
      view: window,
      clientX: rect.left + rect.width / 2,
      clientY: rect.top + rect.height / 2
    };
    for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
      try {
        el.dispatchEvent(type.startsWith("pointer") ? new PointerEvent(type, opts) : new MouseEvent(type, opts));
      } catch {
        el.dispatchEvent(new MouseEvent(type, opts));
      }
    }
    try { el.click(); } catch {}
    return true;
  }

  function clickableAncestor(el) {
    if (!el) return null;
    return el.closest('[aria-haspopup="menu"], [aria-label], [role="button"], [role="menuitem"], a, button, [tabindex="0"]') || el;
  }

  function rectDistance(a, b) {
    const ax = a.left + a.width / 2;
    const ay = a.top + a.height / 2;
    const bx = b.left + b.width / 2;
    const by = b.top + b.height / 2;
    return Math.hypot(ax - bx, ay - by);
  }

  function textOf(el) {
    return (el?.innerText || el?.textContent || "").replace(/\s+/g, " ").trim();
  }



  const FB_HIDDEN_REEL_ALERT_TEXT = "This reel has been hidden in the Reels section. You'll see fewer videos from";

  function looksLikeCapturedClone(el) {
    // Ignore frozen debugging clones produced by the console capture script.
    return !!el?.closest?.('[data-captured-facebook-toast]');
  }

  function findToastAncestorFromAlert(host) {
    let best = host;
    for (let node = host; node && node !== document.body; node = node.parentElement) {
      if (looksLikeCapturedClone(node)) return null;
      const text = textOf(node);
      if (!text.includes(FB_HIDDEN_REEL_ALERT_TEXT)) continue;
      const rect = node.getBoundingClientRect();
      const hasClose = !!node.querySelector?.('[aria-label="Close"][role="button"], [aria-label="Close"], [role="button"] svg path[d*="M9.942 2.942"]');
      const toastSized = rect.width >= 220 && rect.width <= Math.min(window.innerWidth * 0.98, 1000) &&
        rect.height >= 30 && rect.height <= Math.min(window.innerHeight * 0.75, 420);
      if (toastSized) best = node;
      if (toastSized && hasClose) return node;
    }
    return best;
  }

  function findCloseButtonsNearAlert(host) {
    const alertRect = host.getBoundingClientRect();
    const buttons = [];

    // Close button is sometimes inside a toast ancestor, sometimes just a sibling
    // inside the parent shell. Capture all matching close buttons near the alert.
    const toast = findToastAncestorFromAlert(host);
    if (toast) {
      for (const close of Array.from(toast.querySelectorAll?.('[aria-label="Close"][role="button"], [aria-label="Close"], [role="button"] svg path[d*="M9.942 2.942"]') || [])) {
        const btn = clickableAncestor(close);
        if (btn && isVisible(btn) && !/Undo/i.test(textOf(btn)) && !looksLikeCapturedClone(btn)) buttons.push(btn);
      }
    }

    for (const close of Array.from(document.querySelectorAll('[aria-label="Close"][role="button"], [aria-label="Close"], [role="button"] svg path[d*="M9.942 2.942"]'))) {
      const btn = clickableAncestor(close);
      if (!btn || !isVisible(btn) || looksLikeCapturedClone(btn)) continue;
      if (/Undo/i.test(textOf(btn))) continue;
      const br = btn.getBoundingClientRect();
      const verticalNear = br.bottom >= alertRect.top - 140 && br.top <= alertRect.bottom + 140;
      const horizontalNear = br.left >= alertRect.left - 160 && br.left <= alertRect.right + 360;
      const notFacebookChrome = br.top > 70 || alertRect.top > 70;
      if (verticalNear && horizontalNear && notFacebookChrome) buttons.push(btn);
    }

    return Array.from(new Set(buttons))
      .sort((a, b) => rectDistance(a.getBoundingClientRect(), alertRect) - rectDistance(b.getBoundingClientRect(), alertRect));
  }

  function findFacebookHiddenReelCloseButtons(root = document) {
    const closeButtons = [];

    // Always scan the full document. Duplicate Facebook toasts can be inserted in
    // separate sibling containers after the first close button was already clicked.
    const hosts = [];
    for (const scanRoot of [root, document]) {
      if (scanRoot instanceof Element) hosts.push(scanRoot);
      hosts.push(...Array.from(scanRoot.querySelectorAll?.('[role="alert"], [aria-atomic="true"], div, span') || []));
    }

    for (const host of Array.from(new Set(hosts))) {
      if (!(host instanceof Element) || !isVisible(host) || looksLikeCapturedClone(host)) continue;
      if (!textOf(host).includes(FB_HIDDEN_REEL_ALERT_TEXT)) continue;
      closeButtons.push(...findCloseButtonsNearAlert(host));
    }

    return Array.from(new Set(closeButtons));
  }

  function clickCloseButtonAggressively(closeButton) {
    if (!closeButton || !isVisible(closeButton) || looksLikeCapturedClone(closeButton)) return;

    // Do not mark it as permanently handled before it disappears. If Facebook ignores
    // one synthetic click, later burst ticks should try the same visible button again.
    closeButton.dataset.qacerAutoCloseAttempts = String((Number(closeButton.dataset.qacerAutoCloseAttempts || "0") || 0) + 1);
    for (const delay of [0, 35, 90, 180]) {
      setTimeout(() => {
        if (isVisible(closeButton) && !looksLikeCapturedClone(closeButton)) fireRealishClick(closeButton);
      }, delay);
    }
  }

  function autoCloseFacebookHiddenReelAlerts(root = document) {
    const buttons = findFacebookHiddenReelCloseButtons(root);
    for (const closeButton of buttons) clickCloseButtonAggressively(closeButton);
  }

  function runAutoCloseBurst(durationMs = 6500) {
    const start = Date.now();
    autoCloseFacebookHiddenReelAlerts(document);
    const timer = setInterval(() => {
      autoCloseFacebookHiddenReelAlerts(document);
      if (Date.now() - start > durationMs) clearInterval(timer);
    }, 60);
  }


  function getVisiblePlayers() {
    return Array.from(document.querySelectorAll(PLAYER_SELECTOR)).filter(isVisible);
  }

  function findReelRoot(player) {
    // The video player is usually nested inside a larger reel container that also includes
    // the profile link and the three-dot menu. Pick the smallest visible ancestor that has both.
    let best = null;
    for (let node = player.parentElement; node && node !== document.body; node = node.parentElement) {
      const hasPlayer = node.querySelector?.(PLAYER_SELECTOR);
      const hasProfile = node.querySelector?.('a[aria-label="See Owner Profile"]');
      const hasMenuPath = node.querySelector?.('svg path[d*="M5 14"][d*="a2 2"]');
      const rect = node.getBoundingClientRect();
      if (hasPlayer && (hasProfile || hasMenuPath) && rect.width >= player.getBoundingClientRect().width) {
        best = node;
        break;
      }
    }
    return best || player.closest('[role="main"]') || document;
  }

  function findThreeDotButton(player) {
    const playerRect = player.getBoundingClientRect();
    const root = findReelRoot(player);
    const pools = [root, document];
    const candidates = [];

    for (const pool of pools) {
      // Exact 3-dot SVG path from the provided Facebook markup.
      for (const path of Array.from(pool.querySelectorAll?.('svg path[d^="M5 14a2"], svg path[d*="M5 14a2"]') || [])) {
        const clickable = clickableAncestor(path);
        if (clickable && isVisible(clickable)) candidates.push(clickable);
      }

      // Fallbacks for Facebook variants: aria-label/options/menu buttons near the reel.
      for (const el of Array.from(pool.querySelectorAll?.('[aria-haspopup="menu"], [aria-haspopup="dialog"][aria-label="Menu"], [aria-label="Menu"], [aria-label*="More"], [aria-label*="more"], [aria-label*="Options"], [aria-label*="options"]') || [])) {
        const clickable = clickableAncestor(el);
        if (clickable && isVisible(clickable)) candidates.push(clickable);
      }

      if (candidates.length) break;
    }

    const unique = Array.from(new Set(candidates));
    unique.sort((a, b) => {
      const ar = a.getBoundingClientRect();
      const br = b.getBoundingClientRect();

      // Prefer buttons on the same vertical band as this reel and toward the right side.
      const aBandPenalty = ar.bottom < playerRect.top - 80 || ar.top > playerRect.bottom + 80 ? 100000 : 0;
      const bBandPenalty = br.bottom < playerRect.top - 80 || br.top > playerRect.bottom + 80 ? 100000 : 0;
      const aText = (a.getAttribute("aria-label") || "") + " " + textOf(a);
      const bText = (b.getAttribute("aria-label") || "") + " " + textOf(b);
      const aExactMenuBonus = aText.trim() === "Menu" ? -50000 : 0;
      const bExactMenuBonus = bText.trim() === "Menu" ? -50000 : 0;
      const aHasDotsBonus = a.querySelector?.('svg path[d^="M5 14a2"], svg path[d*="M5 14a2"]') ? -50000 : 0;
      const bHasDotsBonus = b.querySelector?.('svg path[d^="M5 14a2"], svg path[d*="M5 14a2"]') ? -50000 : 0;
      const aAccountMenuPenalty = /Facebook menu|Account Controls|Notifications|Your profile/i.test(aText) ? 200000 : 0;
      const bAccountMenuPenalty = /Facebook menu|Account Controls|Notifications|Your profile/i.test(bText) ? 200000 : 0;
      const aScore = aBandPenalty + aAccountMenuPenalty + aExactMenuBonus + aHasDotsBonus + rectDistance(ar, playerRect) - ar.right * 0.25;
      const bScore = bBandPenalty + bAccountMenuPenalty + bExactMenuBonus + bHasDotsBonus + rectDistance(br, playerRect) - br.right * 0.25;
      return aScore - bScore;
    });

    return unique[0] || null;
  }

  function findSeeLessMenuItem() {
    const possible = Array.from(document.querySelectorAll('[role="menuitem"], [role="button"], div[tabindex="0"], span, div'));
    for (const el of possible) {
      if (!isVisible(el)) continue;
      const text = textOf(el);
      if (text === "See less" || (text.startsWith("See less") && text.includes("Hide reel"))) {
        const item = el.closest('[role="menuitem"], [tabindex="0"], [role="button"]') || el;
        if (isVisible(item)) return item;
      }
    }
    const span = Array.from(document.querySelectorAll("span")).find((el) => isVisible(el) && textOf(el) === "See less");
    return span ? clickableAncestor(span) : null;
  }

  async function waitForSeeLess(timeoutMs = 4500) {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      const item = findSeeLessMenuItem();
      if (item) return item;
      await sleep(120);
    }
    return null;
  }

  async function clickSeeLessFlow(player, button) {
    if (!isReelUrl()) {
      showStatus("This button only works on facebook.com/reel/* pages.", false);
      return;
    }

    button?.setAttribute("aria-disabled", "true");
    try {
      showStatus("Opening Reel menu…");
      const menu = findThreeDotButton(player);
      if (!menu) {
        showStatus("Could not find the Reel menu near this reel. Hover the reel once; if it still fails, Facebook changed the menu DOM again.", false);
        return;
      }

      fireRealishClick(menu);
      const seeLess = await waitForSeeLess();
      if (!seeLess) {
        showStatus("Menu opened, but See less was not found.", false);
        return;
      }

      showStatus("Clicking See less…");
      fireRealishClick(seeLess);
      runAutoCloseBurst(8000);
      await sleep(300);
      showStatus("See less clicked.");
    } finally {
      button?.setAttribute("aria-disabled", "false");
    }
  }

  function buildButton(player) {
    const btn = document.createElement("div");
    btn.className = `${BUTTON_CLASS} ${FB_CONTROL_BUTTON_CLASSES.join(" ")}`;
    btn.setAttribute("role", "button");
    btn.setAttribute("tabindex", "0");
    btn.setAttribute("aria-label", "See less");
    btn.setAttribute("aria-disabled", "false");
    btn.title = "See less";

    btn.style.position = "absolute";
    btn.style.top = "12px";
    btn.style.right = "12px";
    btn.style.zIndex = "2147483647";
    btn.style.width = "48px";
    btn.style.height = "48px";
    btn.style.borderRadius = "999px";
    btn.style.background = "rgba(0, 0, 0, .55)";
    btn.style.backdropFilter = "blur(8px)";
    btn.style.color = "white";
    btn.style.display = "flex";
    btn.style.alignItems = "center";
    btn.style.justifyContent = "center";
    btn.style.cursor = "pointer";
    btn.style.userSelect = "none";
    btn.style.boxShadow = "0 2px 8px rgba(0,0,0,.35)";
    btn.style.font = "900 30px/1 Arial, Helvetica, sans-serif";
    btn.style.textAlign = "center";

    const x = document.createElement("span");
    x.textContent = "X";
    x.setAttribute("aria-hidden", "true");
    x.style.transform = "translateY(-1px)";
    x.style.pointerEvents = "none";
    btn.appendChild(x);

    const overlay = document.createElement("div");
    overlay.setAttribute("role", "none");
    overlay.setAttribute("data-visualcompletion", "ignore");
    overlay.style.position = "absolute";
    overlay.style.inset = "0";
    overlay.style.borderRadius = "999px";
    overlay.style.pointerEvents = "none";
    btn.appendChild(overlay);

    btn.addEventListener("mouseenter", () => (btn.style.background = "rgba(0, 0, 0, .72)"));
    btn.addEventListener("mouseleave", () => (btn.style.background = "rgba(0, 0, 0, .55)"));
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      clickSeeLessFlow(player, btn);
    });
    btn.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        event.stopPropagation();
        clickSeeLessFlow(player, btn);
      }
    });

    return btn;
  }

  function injectButtons() {
    if (!isReelUrl()) return;
    for (const player of getVisiblePlayers()) {
      const existing = player.querySelector(`:scope > .${BUTTON_CLASS}`);
      if (existing) continue;
      const style = window.getComputedStyle(player);
      if (style.position === "static") player.style.position = "relative";
      player.setAttribute(SEEN_PLAYER_ATTR, "true");
      player.appendChild(buildButton(player));
    }
  }

  function cleanupIfNotReel() {
    if (isReelUrl()) return;
    document.querySelectorAll(`.${BUTTON_CLASS}`).forEach((btn) => btn.remove());
  }

  function refresh() {
    cleanupIfNotReel();
    injectButtons();
    autoCloseFacebookHiddenReelAlerts();
  }

  let refreshTimer = null;
  function scheduleRefresh() {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(refresh, 80);
  }

  refresh();
  window.addEventListener("scroll", scheduleRefresh, true);
  window.addEventListener("resize", scheduleRefresh);
  window.addEventListener("mousemove", scheduleRefresh, { passive: true });

  const observer = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      for (const node of mutation.addedNodes || []) {
        if (node instanceof Element) autoCloseFacebookHiddenReelAlerts(node);
      }
    }
    scheduleRefresh();
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });

  const originalPushState = history.pushState;
  const originalReplaceState = history.replaceState;
  history.pushState = function (...args) {
    const result = originalPushState.apply(this, args);
    setTimeout(refresh, 50);
    return result;
  };
  history.replaceState = function (...args) {
    const result = originalReplaceState.apply(this, args);
    setTimeout(refresh, 50);
    return result;
  };
  window.addEventListener("popstate", () => setTimeout(refresh, 50));
})();
