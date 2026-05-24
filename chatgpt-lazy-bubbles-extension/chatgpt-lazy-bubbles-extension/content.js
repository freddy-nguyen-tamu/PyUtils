(() => {
  "use strict";

  const DEFAULTS = {
    enabled: true,
    keepRecentTurns: 10,
    viewportBufferScreens: 2.5,
    minTurnsBeforeVirtualize: 24,
    showStatus: true,
    debug: false
  };

  let settings = { ...DEFAULTS };
  let scheduled = false;
  let observer = null;
  let resizeObserver = null;
  let lastUrl = location.href;
  const measuredHeights = new WeakMap();

  const log = (...args) => {
    if (settings.debug) console.debug("[ChatGPT Lazy Bubbles]", ...args);
  };

  const storageGet = () => new Promise((resolve) => {
    try {
      chrome.storage.sync.get(DEFAULTS, (items) => resolve({ ...DEFAULTS, ...items }));
    } catch {
      resolve({ ...DEFAULTS });
    }
  });

  const isConversationPage = () => {
    return location.hostname.includes("chatgpt.com") || location.hostname.includes("chat.openai.com");
  };

  const getTurnNodes = () => {
    const byTestId = Array.from(document.querySelectorAll('[data-testid^="conversation-turn-"]'));
    const byTurn = Array.from(document.querySelectorAll('section[data-turn][data-turn-id]'));
    const byContainer = Array.from(document.querySelectorAll('[data-turn-id-container]'))
      .filter((node) => node.querySelector?.('section[data-turn], [data-testid^="conversation-turn-"]'));

    const seen = new Set();
    const result = [];

    for (const node of [...byContainer, ...byTestId, ...byTurn]) {
      const turn = node.closest('[data-turn-id-container]') || node;
      if (!turn || seen.has(turn)) continue;
      if (!document.documentElement.contains(turn)) continue;
      seen.add(turn);
      result.push(turn);
    }

    return result.sort((a, b) => {
      if (a === b) return 0;
      return a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1;
    });
  };

  const setHeightVar = (node) => {
    if (node.classList.contains("clb-virtualized-turn")) return;
    const rect = node.getBoundingClientRect();
    const current = Math.max(1, Math.round(rect.height || node.offsetHeight || 0));
    if (current > 1) {
      measuredHeights.set(node, current);
      node.style.setProperty("--clb-preserved-height", `${current}px`);
    }
  };

  const unvirtualize = (node) => {
    node.classList.remove("clb-virtualized-turn");
    node.classList.add("clb-nearby-turn");
    node.style.removeProperty("height");
    node.style.removeProperty("max-height");
    node.style.removeProperty("min-height");
  };

  const virtualize = (node) => {
    const cached = measuredHeights.get(node);
    if (!cached) setHeightVar(node);
    const height = measuredHeights.get(node) || Math.max(96, Math.round(node.getBoundingClientRect().height || node.offsetHeight || 96));
    node.style.setProperty("--clb-preserved-height", `${height}px`);
    node.classList.remove("clb-nearby-turn");
    node.classList.add("clb-virtualized-turn");
  };

  const updateStatus = (total, hidden) => {
    let pill = document.getElementById("clb-status-pill");
    if (!settings.showStatus || !settings.enabled || total < settings.minTurnsBeforeVirtualize) {
      if (pill) pill.classList.add("clb-hidden");
      return;
    }

    if (!pill) {
      pill = document.createElement("div");
      pill.id = "clb-status-pill";
      const text = document.createElement("span");
      text.id = "clb-status-text";
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = "Disable";
      btn.addEventListener("click", async () => {
        settings.enabled = false;
        await chrome.storage.sync.set({ enabled: false });
        applyVirtualization(true);
      });
      pill.append(text, btn);
      document.documentElement.appendChild(pill);
    }

    pill.classList.remove("clb-hidden");
    const text = pill.querySelector("#clb-status-text");
    if (text) text.textContent = `${hidden}/${total} bubbles sleeping`;
  };

  const applyVirtualization = (forceShowAll = false) => {
    scheduled = false;

    if (!isConversationPage()) return;

    const turns = getTurnNodes();
    if (!settings.enabled || forceShowAll || turns.length < settings.minTurnsBeforeVirtualize) {
      for (const node of turns) unvirtualize(node);
      updateStatus(turns.length, 0);
      return;
    }

    const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 800;
    const bufferPx = viewportHeight * Number(settings.viewportBufferScreens || DEFAULTS.viewportBufferScreens);
    const topLimit = -bufferPx;
    const bottomLimit = viewportHeight + bufferPx;
    const keepRecentStart = Math.max(0, turns.length - Number(settings.keepRecentTurns || DEFAULTS.keepRecentTurns));
    let hidden = 0;

    for (let i = 0; i < turns.length; i++) {
      const node = turns[i];

      if (i >= keepRecentStart) {
        unvirtualize(node);
        setHeightVar(node);
        continue;
      }

      if (!node.classList.contains("clb-virtualized-turn")) setHeightVar(node);
      const rect = node.getBoundingClientRect();
      const farAbove = rect.bottom < topLimit;
      const farBelow = rect.top > bottomLimit;

      if (farAbove || farBelow) {
        virtualize(node);
        hidden++;
      } else {
        unvirtualize(node);
        setHeightVar(node);
      }
    }

    updateStatus(turns.length, hidden);
    log({ total: turns.length, hidden });
  };

  const scheduleApply = () => {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => applyVirtualization(false));
  };

  const watchUrlChanges = () => {
    setInterval(() => {
      if (lastUrl !== location.href) {
        lastUrl = location.href;
        for (const node of getTurnNodes()) unvirtualize(node);
        setTimeout(scheduleApply, 350);
      }
    }, 750);
  };

  const initObservers = () => {
    observer?.disconnect();
    resizeObserver?.disconnect();

    observer = new MutationObserver(() => scheduleApply());
    observer.observe(document.documentElement, { childList: true, subtree: true });

    resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const node = entry.target;
        if (!node.classList.contains("clb-virtualized-turn")) {
          const h = Math.max(1, Math.round(entry.contentRect.height || node.offsetHeight || 0));
          if (h > 1) {
            measuredHeights.set(node, h);
            node.style.setProperty("--clb-preserved-height", `${h}px`);
          }
        }
      }
    });

    setInterval(() => {
      for (const node of getTurnNodes()) {
        try { resizeObserver.observe(node); } catch {}
      }
    }, 2000);

    window.addEventListener("scroll", scheduleApply, { passive: true });
    window.addEventListener("resize", scheduleApply, { passive: true });
    document.addEventListener("visibilitychange", scheduleApply, { passive: true });
  };

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type === "CLB_SETTINGS_UPDATED") {
      settings = { ...settings, ...message.settings };
      applyVirtualization(!settings.enabled);
      sendResponse?.({ ok: true });
    }
    if (message?.type === "CLB_GET_STATS") {
      const turns = getTurnNodes();
      sendResponse?.({
        ok: true,
        total: turns.length,
        hidden: turns.filter((n) => n.classList.contains("clb-virtualized-turn")).length,
        settings
      });
    }
    return true;
  });

  const main = async () => {
    settings = await storageGet();
    initObservers();
    watchUrlChanges();
    setTimeout(scheduleApply, 300);
    setTimeout(scheduleApply, 1200);
  };

  main();
})();
