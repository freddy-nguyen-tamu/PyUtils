(() => {
  "use strict";

  /**
   * YouTube Live Replay Cleaner
   *
   * Goal:
   * - Remove replay live chat on YouTube videos that used to be live.
   * - Remove Top Fans / leaderboard UI.
   * - Remove leftover blank/black parent containers after the chat is gone.
   *
   * Change these values if needed.
   */
  const SETTINGS = {
    // true = remove the entire live chat replay panel/container.
    // false = keep chat replay, only remove Top Fans / leaderboard UI.
    removeWholeChatReplay: true,

    // Remove the Top Fans / leaderboard button inside the replay chat header.
    removeTopFansButton: true,

    // Try clicking YouTube's own close button before removing DOM shells.
    clickCloseButtonAsFallback: true,

    // Set to true only when debugging in DevTools.
    debug: false
  };

  const log = (...args) => {
    if (SETTINGS.debug) console.log("[YT Replay Cleaner]", ...args);
  };

  function safeRemove(el, reason = "") {
    if (!el || !el.isConnected) return false;
    log("remove", reason, el);
    el.remove();
    return true;
  }

  function clickYouTubeCloseButton() {
    if (!SETTINGS.clickCloseButtonAsFallback) return;

    const closeButton =
      document.querySelector("#close-button button") ||
      document.querySelector('yt-live-chat-header-renderer button[aria-label="Close"]') ||
      document.querySelector('yt-live-chat-header-renderer button[aria-label="Đóng"]') ||
      document.querySelector('button[aria-label="Close"]') ||
      document.querySelector('button[aria-label="Đóng"]');

    if (closeButton) {
      log("click close button", closeButton);
      closeButton.click();
    }
  }

  function removeTopFansButton() {
    if (!SETTINGS.removeTopFansButton) return;

    const selectors = [
      "#viewer-leaderboard-entry-point",
      "ytvl-live-viewer-leaderboard-chat-entry-point-view-model"
    ];

    for (const selector of selectors) {
      document.querySelectorAll(selector).forEach((el) => {
        safeRemove(el, selector);
      });
    }

    // Text fallback for localized or changed YouTube DOM.
    // English + Vietnamese because the attached source used Vietnamese text.
    document.querySelectorAll("button").forEach((button) => {
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

        safeRemove(wrapper, "top fans text fallback");
      }
    });
  }

  function removeChatReplayContainers() {
    if (!SETTINGS.removeWholeChatReplay) return;

    clickYouTubeCloseButton();

    // Child and frame containers.
    const chatSelectors = [
      "ytd-live-chat-frame",
      "#chat-container",
      "#chatframe",
      "yt-live-chat-app",
      "yt-live-chat-renderer",
      "#chat.yt-live-chat-renderer",
      "#chat-messages.yt-live-chat-renderer"
    ];

    for (const selector of chatSelectors) {
      document.querySelectorAll(selector).forEach((el) => {
        safeRemove(el, selector);
      });
    }

    // The blank white/empty box the user saw:
    // <div id="contents" class="style-scope yt-live-chat-app"></div>
    document
      .querySelectorAll("yt-live-chat-app > #contents, div#contents.style-scope.yt-live-chat-app")
      .forEach((el) => {
        safeRemove(el.closest("yt-live-chat-app") || el, "yt-live-chat-app contents shell");
      });

    // The black box the user saw:
    // <div id="panels-full-bleed-container" class="style-scope ytd-watch-flexy"></div>
    document
      .querySelectorAll("#panels-full-bleed-container, div#panels-full-bleed-container.style-scope.ytd-watch-flexy")
      .forEach((el) => {
        safeRemove(el, "panels-full-bleed-container shell");
      });

    // Extra cleanup for empty shells YouTube sometimes leaves after DOM changes.
    document.querySelectorAll("yt-live-chat-app, #chat-container").forEach((el) => {
      if ((el.textContent || "").trim() === "" && el.children.length === 0) {
        safeRemove(el, "empty live chat shell");
      }
    });
  }

  function clean() {
    removeTopFansButton();
    removeChatReplayContainers();
  }

  clean();

  // YouTube loads/rebuilds page sections dynamically, so keep watching.
  const observer = new MutationObserver(() => clean());
  observer.observe(document.documentElement, {
    childList: true,
    subtree: true
  });

  // YouTube is a single-page app, so run again after internal navigation.
  window.addEventListener("yt-navigate-finish", () => setTimeout(clean, 100), true);
  window.addEventListener("popstate", () => setTimeout(clean, 250), true);
})();
