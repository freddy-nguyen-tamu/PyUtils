(() => {
  "use strict";

  const STORAGE_KEY = "ytLiveReplayCleanerEnabled";
  const STYLE_ID = "yt-live-replay-cleaner-style";
  const BUTTON_ID = "yt-live-replay-cleaner-player-button";

  let cachedEnabled = true;
  let observer = null;
  let bootedStorageListener = false;
  let bootedNavigationListener = false;

  const HIDE_CSS = `
    ytd-live-chat-frame,
    #chat-container,
    #chatframe,
    #panels-full-bleed-container,
    div#panels-full-bleed-container.style-scope.ytd-watch-flexy,
    yt-live-chat-app,
    yt-live-chat-app > #contents,
    div#contents.style-scope.yt-live-chat-app,
    yt-live-chat-renderer,
    #chat.yt-live-chat-renderer,
    #chat-messages.yt-live-chat-renderer,
    #viewer-leaderboard-entry-point,
    ytvl-live-viewer-leaderboard-chat-entry-point-view-model {
      display: none !important;
      visibility: hidden !important;
      opacity: 0 !important;
      pointer-events: none !important;
      height: 0 !important;
      min-height: 0 !important;
      max-height: 0 !important;
      width: 0 !important;
      min-width: 0 !important;
      max-width: 0 !important;
      margin: 0 !important;
      padding: 0 !important;
      border: 0 !important;
      overflow: hidden !important;
    }

    #${BUTTON_ID}.ytp-button {
      width: 48px !important;
      min-width: 48px !important;
      max-width: 48px !important;
      padding: 0 !important;
      opacity: .9 !important;
    }

    #${BUTTON_ID}.ytp-button:hover {
      opacity: 1 !important;
    }

    #${BUTTON_ID} svg {
      width: 100% !important;
      height: 100% !important;
      pointer-events: none !important;
      display: block !important;
    }
  `;

  function sendMessage(message) {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage(message, (response) => {
          if (chrome.runtime.lastError) {
            resolve({ error: chrome.runtime.lastError.message });
            return;
          }
          resolve(response || {});
        });
      } catch (error) {
        resolve({ error: String(error && error.message ? error.message : error) });
      }
    });
  }

  async function getEnabled() {
    const response = await sendMessage({ type: "getCleanerState" });
    if (typeof response.enabled === "boolean") return response.enabled;

    return new Promise((resolve) => {
      chrome.storage.local.get({ [STORAGE_KEY]: true }, (result) => {
        resolve(Boolean(result[STORAGE_KEY]));
      });
    });
  }

  async function setEnabled(enabled) {
    const response = await sendMessage({ type: "setCleanerState", enabled });
    return typeof response.enabled === "boolean" ? response.enabled : Boolean(enabled);
  }

  function ensureStyle() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = HIDE_CSS;
    (document.head || document.documentElement).appendChild(style);
  }

  function removeStyle() {
    const style = document.getElementById(STYLE_ID);
    if (style) style.remove();
  }

  function killLoadedChatNow() {
    const selectors = [
      "#panels-full-bleed-container",
      "#chat-container",
      "ytd-live-chat-frame",
      "iframe#chatframe",
      "yt-live-chat-app",
      "yt-live-chat-renderer"
    ];

    for (const selector of selectors) {
      document.querySelectorAll(selector).forEach((el) => {
        if (el.id === BUTTON_ID || el.contains(document.getElementById(BUTTON_ID))) return;
        if (el.tagName === "IFRAME") {
          try {
            el.src = "about:blank";
            el.removeAttribute("src");
          } catch (_) {}
        }
        el.remove();
      });
    }

    document.querySelectorAll("button").forEach((button) => {
      if (button.id === BUTTON_ID) return;

      const text = (button.textContent || "").trim().toLowerCase();
      const aria = (button.getAttribute("aria-label") || "").trim().toLowerCase();
      const combined = `${text} ${aria}`;

      if (
        combined.includes("top fans") ||
        combined.includes("người hâm mộ hàng đầu") ||
        combined.includes("nguoi ham mo hang dau")
      ) {
        const wrapper =
          button.closest("#viewer-leaderboard-entry-point") ||
          button.closest("ytvl-live-viewer-leaderboard-chat-entry-point-view-model") ||
          button.closest("button-view-model") ||
          button;
        wrapper.remove();
      }
    });
  }

  function applyCleaner(enabled) {
    cachedEnabled = Boolean(enabled);

    if (cachedEnabled) {
      ensureStyle();
      killLoadedChatNow();
    } else {
      removeStyle();
    }

    ensurePlayerControlButton();
    updatePlayerControlButton();
  }

  function makeIconSvg(enabled) {
    if (enabled) {
      // Chat blocked/off: speech bubble with orange slash.
      return `
        <svg viewBox="0 0 36 36" aria-hidden="true" focusable="false">
          <path d="M8 10.5c0-1.7 1.3-3 3-3h14c1.7 0 3 1.3 3 3v9c0 1.7-1.3 3-3 3H16l-5.8 4.8c-.5.4-1.2.1-1.2-.6v-4.2c-.6-.1-1-.4-1.4-.8-.4-.5-.6-1.1-.6-1.7v-9.5z" fill="white" opacity=".95"/>
          <circle cx="14" cy="15" r="1.4" fill="#111" opacity=".95"/>
          <circle cx="18" cy="15" r="1.4" fill="#111" opacity=".95"/>
          <circle cx="22" cy="15" r="1.4" fill="#111" opacity=".95"/>
          <path d="M9 28L29 8" stroke="#ff4e1f" stroke-width="3.6" stroke-linecap="round"/>
        </svg>`;
    }

    // Chat allowed/on: normal speech bubble with tiny check.
    return `
      <svg viewBox="0 0 36 36" aria-hidden="true" focusable="false">
        <path d="M8 10.5c0-1.7 1.3-3 3-3h14c1.7 0 3 1.3 3 3v9c0 1.7-1.3 3-3 3H16l-5.8 4.8c-.5.4-1.2.1-1.2-.6v-4.2c-.6-.1-1-.4-1.4-.8-.4-.5-.6-1.1-.6-1.7v-9.5z" fill="white" opacity=".95"/>
        <circle cx="14" cy="15" r="1.4" fill="#111" opacity=".95"/>
        <circle cx="18" cy="15" r="1.4" fill="#111" opacity=".95"/>
        <circle cx="22" cy="15" r="1.4" fill="#111" opacity=".95"/>
        <path d="M21.4 25.7l2.4 2.4 4.8-5.6" stroke="#22c55e" stroke-width="2.7" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>`;
  }

  function ensurePlayerControlButton() {
    if (window.top !== window) return null;

    const rightControls = document.querySelector(".html5-video-player .ytp-right-controls, .ytp-right-controls");
    if (!rightControls) return null;

    let button = document.getElementById(BUTTON_ID);
    if (!button) {
      button = document.createElement("button");
      button.id = BUTTON_ID;
      button.type = "button";
      button.className = "ytp-button yt-live-replay-cleaner-button";
      button.setAttribute("data-priority", "6");
      button.setAttribute("aria-keyshortcuts", "");

      button.addEventListener("click", async (event) => {
        event.preventDefault();
        event.stopPropagation();
        event.stopImmediatePropagation();

        const nextEnabled = !cachedEnabled;
        button.disabled = true;
        button.setAttribute(
          "data-tooltip-title",
          nextEnabled ? "Turning live chat replay off..." : "Turning live chat replay on..."
        );

        await setEnabled(nextEnabled);
        applyCleaner(nextEnabled);

        if (!nextEnabled) {
          // Chat was blocked/removed, so a reload is needed to let YouTube rebuild it.
          setTimeout(() => window.location.reload(), 120);
        } else {
          button.disabled = false;
        }
      }, true);
    }

    if (button.parentElement !== rightControls) {
      const settingsButton = rightControls.querySelector(".ytp-settings-button");
      const subtitlesButton = rightControls.querySelector(".ytp-subtitles-button");

      if (settingsButton) {
        rightControls.insertBefore(button, settingsButton);
      } else if (subtitlesButton && subtitlesButton.nextSibling) {
        rightControls.insertBefore(button, subtitlesButton.nextSibling);
      } else {
        rightControls.appendChild(button);
      }
    }

    return button;
  }

  function updatePlayerControlButton() {
    if (window.top !== window) return;
    const button = ensurePlayerControlButton();
    if (!button) return;

    button.disabled = false;
    button.innerHTML = makeIconSvg(cachedEnabled);

    if (cachedEnabled) {
      button.title = "Live chat replay is OFF";
      button.setAttribute("aria-label", "Live chat replay is blocked. Click to turn live chat on. This reloads the page.");
      button.setAttribute("data-tooltip-title", "Live chat: OFF");
    } else {
      button.title = "Live chat replay is ON";
      button.setAttribute("aria-label", "Live chat replay is allowed. Click to turn live chat off.");
      button.setAttribute("data-tooltip-title", "Live chat: ON");
    }
  }

  async function boot() {
    const enabled = await getEnabled();
    applyCleaner(enabled);

    if (!observer) {
      observer = new MutationObserver(() => {
        if (cachedEnabled) {
          killLoadedChatNow();
        }
        ensurePlayerControlButton();
        updatePlayerControlButton();
      });

      observer.observe(document.documentElement, {
        childList: true,
        subtree: true
      });
    }

    if (!bootedStorageListener) {
      bootedStorageListener = true;
      chrome.storage.onChanged.addListener((changes, areaName) => {
        if (areaName !== "local" || !changes[STORAGE_KEY]) return;
        applyCleaner(Boolean(changes[STORAGE_KEY].newValue));
      });
    }

    if (!bootedNavigationListener) {
      bootedNavigationListener = true;
      window.addEventListener("yt-navigate-finish", () => {
        setTimeout(boot, 100);
      }, true);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
    getEnabled().then(applyCleaner);
  } else {
    boot();
  }
})();
