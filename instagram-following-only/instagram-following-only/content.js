(() => {
  "use strict";

  const DEFAULTS = Object.freeze({
    enabled: true,
    hideFeed: true,
    hideReels: true,
    detectionMode: "balanced",
    showPlaceholders: false
  });

  const FOLLOW_LABELS = new Set([
    "follow", "seguir", "suivre", "segui", "folgen", "volgen",
    "obserwuj", "подписаться", "フォロー", "팔로우", "关注", "關注",
    "追蹤", "متابعة", "takip et", "följ", "følg"
  ]);

  const SUGGESTED_PHRASES = [
    "suggested for you",
    "suggested post",
    "suggested reel",
    "because you follow",
    "because you watched",
    "recommended for you",
    "sugerido para ti",
    "sugerencias para ti",
    "suggerito per te",
    "suggéré pour vous",
    "vorgeschlagen für dich",
    "empfohlen für dich",
    "おすすめ",
    "추천",
    "为你推荐",
    "為你推薦"
  ];

  const AGGRESSIVE_PHRASES = [
    ...SUGGESTED_PHRASES,
    "suggested",
    "recommended",
    "sponsored",
    "patrocinado",
    "gesponsert",
    "sponsorisé"
  ];

  const RESERVED_PATHS = new Set([
    "about", "accounts", "api", "challenge", "developer", "direct",
    "directory", "emails", "explore", "legal", "p", "privacy",
    "reel", "reels", "stories", "terms", "web"
  ]);

  let settings = { ...DEFAULTS };
  let scanTimer = null;
  let lastUrl = location.href;
  const placeholderFor = new WeakMap();

  function normalizeText(value) {
    return String(value || "")
      .replace(/\s+/g, " ")
      .trim()
      .toLocaleLowerCase();
  }

  function pageKind() {
    const path = location.pathname;
    if (/^\/reels?(\/|$)/.test(path)) return "reels";
    if (path === "/") return "feed";
    return "other";
  }

  function isEnabledFor(kind) {
    if (!settings.enabled) return false;
    if (kind === "feed") return settings.hideFeed;
    if (kind === "reels") return settings.hideReels;
    return false;
  }

  function isVisible(element) {
    if (!(element instanceof HTMLElement)) return false;
    const style = getComputedStyle(element);
    return (
      style.display !== "none" &&
      style.visibility !== "hidden" &&
      element.getClientRects().length > 0
    );
  }

  function isProfileHref(href) {
    if (!href) return false;
    let url;
    try {
      url = new URL(href, location.origin);
    } catch {
      return false;
    }
    if (url.origin !== location.origin) return false;

    const parts = url.pathname.split("/").filter(Boolean);
    return parts.length === 1 && !RESERVED_PATHS.has(parts[0].toLocaleLowerCase());
  }

  function hasNearbyProfileLink(element) {
    let node = element;
    for (let depth = 0; node && depth < 5; depth += 1, node = node.parentElement) {
      const links = node.querySelectorAll?.("a[href]") || [];
      for (const link of links) {
        if (isProfileHref(link.getAttribute("href"))) return true;
      }
    }
    return false;
  }

  function hasFollowAction(candidate) {
    const controls = candidate.querySelectorAll(
      "button, [role='button'], a[role='button']"
    );

    for (const control of controls) {
      if (candidate.dataset.ifoHidden !== "true" && !isVisible(control)) continue;

      const label = normalizeText(
        control.getAttribute("aria-label") ||
        control.textContent
      );

      if (!FOLLOW_LABELS.has(label)) continue;
      if (hasNearbyProfileLink(control)) return true;

      const candidateRect = candidate.getBoundingClientRect();
      const controlRect = control.getBoundingClientRect();
      const relativeTop = controlRect.top - candidateRect.top;

      // Feed headers normally place Follow near the top of the card.
      if (relativeTop >= -20 && relativeTop <= 260) return true;
    }

    return false;
  }

  function hasSuggestedLabel(candidate, mode, kind) {
    if (mode === "conservative") return false;

    const phrases = mode === "aggressive"
      ? AGGRESSIVE_PHRASES
      : SUGGESTED_PHRASES;

    const candidateRect = candidate.getBoundingClientRect();
    const nodes = candidate.querySelectorAll(
      "span, div[role='button'], div[role='heading'], h1, h2, h3"
    );

    let inspected = 0;
    for (const node of nodes) {
      if (inspected >= 180) break;
      inspected += 1;
      if (candidate.dataset.ifoHidden !== "true" && !isVisible(node)) continue;

      const text = normalizeText(node.textContent);
      if (!text || text.length > 120) continue;
      if (!phrases.some((phrase) => text === phrase || text.startsWith(`${phrase} `))) {
        continue;
      }

      const rect = node.getBoundingClientRect();
      const relativeTop = rect.top - candidateRect.top;

      if (kind === "reels" || (relativeTop >= -20 && relativeTop <= 320)) {
        return true;
      }
    }

    return false;
  }

  function classify(candidate, kind) {
    if (candidate.dataset.ifoShowOnce === "true") {
      return { hide: false, reason: "" };
    }

    if (hasFollowAction(candidate)) {
      return { hide: true, reason: "Account is not followed" };
    }

    if (hasSuggestedLabel(candidate, settings.detectionMode, kind)) {
      return { hide: true, reason: "Suggested content" };
    }

    return { hide: false, reason: "" };
  }

  function findReelContainer(video) {
    const article = video.closest("article");
    if (article) return article;

    let node = video.parentElement;
    let best = null;

    for (let depth = 0; node && depth < 12; depth += 1, node = node.parentElement) {
      if (node.matches("html, body, main")) break;

      const rect = node.getBoundingClientRect();
      const videoCount = node.querySelectorAll("video").length;
      const hasProfileLink = [...node.querySelectorAll("a[href]")]
        .some((link) => isProfileHref(link.getAttribute("href")));

      const plausibleWidth = rect.width >= 260 && rect.width <= 1100;
      const plausibleHeight =
        rect.height >= Math.min(360, window.innerHeight * 0.45) &&
        rect.height <= window.innerHeight * 1.7;

      if (videoCount === 1 && hasProfileLink && plausibleWidth && plausibleHeight) {
        best = node;
      }
    }

    return best;
  }

  function collectCandidates(kind) {
    const candidates = new Set();

    // Keep previously hidden virtualized containers in the scan set so they
    // can be reevaluated if Instagram reuses them for different content.
    document.querySelectorAll("[data-ifo-hidden='true']")
      .forEach((node) => candidates.add(node));

    if (kind === "feed") {
      document.querySelectorAll("article").forEach((node) => candidates.add(node));
    }

    if (kind === "reels") {
      document.querySelectorAll("article").forEach((node) => candidates.add(node));
      document.querySelectorAll("video").forEach((video) => {
        const container = findReelContainer(video);
        if (container) candidates.add(container);
      });
    }

    return [...candidates].filter((candidate) => {
      if (!(candidate instanceof HTMLElement)) return false;
      if (candidate.closest("[data-ifo-placeholder='true']")) return false;
      if (candidate.matches("main, body, html")) return false;
      return true;
    });
  }

  function removePlaceholder(candidate) {
    const placeholder = placeholderFor.get(candidate);
    if (placeholder?.isConnected) placeholder.remove();
    placeholderFor.delete(candidate);
  }

  function ensurePlaceholder(candidate, reason) {
    let placeholder = placeholderFor.get(candidate);
    if (placeholder?.isConnected) {
      const reasonNode = placeholder.querySelector(".ifo-placeholder__reason");
      if (reasonNode) reasonNode.textContent = reason;
      return;
    }

    placeholder = document.createElement("div");
    placeholder.className = "ifo-placeholder";
    placeholder.dataset.ifoPlaceholder = "true";
    placeholder.innerHTML = `
      <div class="ifo-placeholder__text">
        <strong>Hidden by Instagram Following Only</strong>
        <span class="ifo-placeholder__reason"></span>
      </div>
      <button type="button" class="ifo-placeholder__button">Show once</button>
    `;

    const reasonNode = placeholder.querySelector(".ifo-placeholder__reason");
    if (reasonNode) reasonNode.textContent = reason;

    placeholder.querySelector(".ifo-placeholder__button")?.addEventListener("click", () => {
      candidate.dataset.ifoShowOnce = "true";
      candidate.classList.remove("ifo-hidden");
      removePlaceholder(candidate);
    });

    candidate.before(placeholder);
    placeholderFor.set(candidate, placeholder);
  }

  function setHidden(candidate, hide, reason = "") {
    if (hide) {
      candidate.classList.add("ifo-hidden");
      candidate.dataset.ifoHidden = "true";
      candidate.dataset.ifoReason = reason;

      if (settings.showPlaceholders) {
        ensurePlaceholder(candidate, reason);
      } else {
        removePlaceholder(candidate);
      }
      return;
    }

    candidate.classList.remove("ifo-hidden");
    delete candidate.dataset.ifoHidden;
    delete candidate.dataset.ifoReason;
    removePlaceholder(candidate);
  }

  function revealEverything() {
    document.querySelectorAll(".ifo-hidden").forEach((candidate) => {
      setHidden(candidate, false);
    });
    document.querySelectorAll("[data-ifo-placeholder='true']").forEach((node) => node.remove());
  }

  function scan() {
    scanTimer = null;
    const kind = pageKind();

    if (!isEnabledFor(kind)) {
      revealEverything();
      return;
    }

    for (const candidate of collectCandidates(kind)) {
      const verdict = classify(candidate, kind);
      setHidden(candidate, verdict.hide, verdict.reason);
    }
  }

  function scheduleScan(delay = 120) {
    if (scanTimer !== null) clearTimeout(scanTimer);
    scanTimer = setTimeout(scan, delay);
  }

  async function loadSettings() {
    try {
      const stored = await chrome.storage.sync.get(DEFAULTS);
      settings = { ...DEFAULTS, ...stored };
    } catch (error) {
      console.warn("[Instagram Following Only] Could not load settings:", error);
      settings = { ...DEFAULTS };
    }
    scheduleScan(0);
  }

  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName !== "sync") return;

    for (const [key, change] of Object.entries(changes)) {
      if (key in DEFAULTS) settings[key] = change.newValue;
    }

    document.querySelectorAll("[data-ifo-show-once='true']").forEach((node) => {
      delete node.dataset.ifoShowOnce;
    });

    scheduleScan(0);
  });

  const observer = new MutationObserver(() => scheduleScan());
  observer.observe(document.documentElement, {
    childList: true,
    subtree: true,
    characterData: true
  });

  setInterval(() => {
    if (location.href !== lastUrl) {
      lastUrl = location.href;
      document.querySelectorAll("[data-ifo-show-once='true']").forEach((node) => {
        delete node.dataset.ifoShowOnce;
      });
      scheduleScan(0);
    }
  }, 800);

  loadSettings();
})();
