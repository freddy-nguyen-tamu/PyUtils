(() => {
  "use strict";

  // This extension only tries for this long, then gives up.
  // Change this number if you want it to wait longer/shorter.
  const GIVE_UP_AFTER_MS = 10000;
  const CHECK_EVERY_MS = 500;

  let runToken = 0;
  let intervalId = null;
  let observer = null;
  let stopTimer = null;

  function log(...args) {
    // Uncomment for debugging:
    // console.log("[YT Live Chat Auto Close]", ...args);
  }

  function isYouTubeWatchOrChatPage() {
    const href = location.href;
    return (
      href.includes("youtube.com/watch") ||
      href.includes("youtube.com/live_chat") ||
      href.includes("youtube.com/live_chat_replay")
    );
  }

  function findCloseButton() {
    const selectors = [
      "yt-live-chat-header-renderer #close-button button",
      "yt-live-chat-app #close-button button",
      "#close-button button"
    ];

    for (const selector of selectors) {
      const button = document.querySelector(selector);
      if (!button) continue;

      const label = (button.getAttribute("aria-label") || "").toLowerCase();

      // In your Vietnamese YouTube source, the close button label is "Đóng".
      // This also supports English and a few common variants.
      const labelLooksRight =
        !label ||
        label.includes("đóng") ||
        label.includes("dong") ||
        label.includes("close") ||
        label.includes("hide") ||
        label.includes("ẩn") ||
        label.includes("an");

      if (labelLooksRight) return button;
    }

    return null;
  }

  function cleanup() {
    if (intervalId !== null) {
      clearInterval(intervalId);
      intervalId = null;
    }

    if (observer !== null) {
      observer.disconnect();
      observer = null;
    }

    if (stopTimer !== null) {
      clearTimeout(stopTimer);
      stopTimer = null;
    }
  }

  function tryClickCloseButton() {
    const button = findCloseButton();
    if (!button) return false;

    log("Clicking live chat close button.");
    button.click();
    cleanup();
    return true;
  }

  function startLimitedSearch() {
    cleanup();

    if (!isYouTubeWatchOrChatPage()) return;

    const myToken = ++runToken;
    const stopAt = Date.now() + GIVE_UP_AFTER_MS;

    function stopIfExpired() {
      if (myToken !== runToken) return true;

      if (Date.now() >= stopAt) {
        log("Gave up looking for live chat close button.");
        cleanup();
        return true;
      }

      return false;
    }

    // Try immediately.
    if (tryClickCloseButton()) return;

    // Then try on a short interval.
    intervalId = setInterval(() => {
      if (stopIfExpired()) return;
      tryClickCloseButton();
    }, CHECK_EVERY_MS);

    // Also react when YouTube inserts the chat later.
    observer = new MutationObserver(() => {
      if (stopIfExpired()) return;
      tryClickCloseButton();
    });

    observer.observe(document.documentElement || document.body, {
      childList: true,
      subtree: true
    });

    // Hard stop so it never keeps running forever on normal non-live videos.
    stopTimer = setTimeout(() => {
      if (myToken !== runToken) return;
      log("Timeout reached; stopping search.");
      cleanup();
    }, GIVE_UP_AFTER_MS);
  }

  // Initial load.
  startLimitedSearch();

  // YouTube is a single-page app, so start one new limited search
  // after YouTube navigates to another video.
  window.addEventListener("yt-navigate-finish", () => {
    setTimeout(startLimitedSearch, 500);
  });
})();
