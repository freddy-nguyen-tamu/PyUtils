(() => {
  "use strict";

  // Simple version:
  // - Does not remove elements.
  // - Does not block requests.
  // - Does not inject a toggle button.
  // - Only clicks YouTube's own live chat close button when it appears.
  // - It DOES click while fullscreen, then releases pointer/focus back to the player.
  // - It stops after success or after a short timeout.

  const CLICKED_ATTR = "data-yt-live-chat-auto-close-clicked";
  const MAX_TRIES = 80;          // 80 * 250ms = 20 seconds
  const CHECK_EVERY_MS = 250;

  let tries = 0;
  let interval = null;
  let observer = null;

  function getTopDocument() {
    try {
      return window.top && window.top.document ? window.top.document : document;
    } catch {
      return document;
    }
  }

  function findCloseButton() {
    const selectors = [
      "yt-live-chat-header-renderer #close-button button",
      "#close-button button[aria-label]",
      "button[aria-label='Close']",
      "button[aria-label='Đóng']"
    ];

    for (const selector of selectors) {
      const button = document.querySelector(selector);
      if (button && !button.hasAttribute(CLICKED_ATTR)) return button;
    }

    // Fallback for other UI languages: the live chat close button is inside
    // #close-button. Avoid clicking random buttons elsewhere.
    const closeHost = document.querySelector(
      "yt-live-chat-header-renderer #close-button, yt-live-chat-app #close-button"
    );

    const fallbackButton = closeHost?.querySelector("button");
    if (fallbackButton && !fallbackButton.hasAttribute(CLICKED_ATTR)) return fallbackButton;

    return null;
  }

  function cleanup() {
    if (interval !== null) {
      clearInterval(interval);
      interval = null;
    }

    if (observer !== null) {
      observer.disconnect();
      observer = null;
    }
  }

  function releaseMouseAndRestorePlayerFocus() {
    const topDoc = getTopDocument();

    try {
      const player =
        topDoc.querySelector("#movie_player") ||
        topDoc.querySelector(".html5-video-player");

      const video = topDoc.querySelector("video");

      // Release any stuck mouse/pointer state caused by the programmatic click.
      // Send to the current document, top document, player, and video when possible.
      const targets = [document, topDoc, player, video].filter(Boolean);

      for (const target of targets) {
        for (const type of ["pointerup", "mouseup", "click", "pointercancel", "mouseleave"]) {
          let event;

          if (type.startsWith("pointer") && typeof PointerEvent !== "undefined") {
            event = new PointerEvent(type, {
              bubbles: true,
              cancelable: true,
              pointerId: 1,
              pointerType: "mouse",
              isPrimary: true,
              buttons: 0,
              button: 0,
              view: window
            });
          } else {
            event = new MouseEvent(type, {
              bubbles: true,
              cancelable: true,
              buttons: 0,
              button: 0,
              view: window
            });
          }

          target.dispatchEvent(event);
        }
      }

      // Return keyboard/mouse focus to the player instead of the chat iframe/button.
      if (player && typeof player.focus === "function") {
        player.focus({ preventScroll: true });
      } else if (video && typeof video.focus === "function") {
        video.focus({ preventScroll: true });
      }

      // Clear text selection too, because YouTube controls can act weird if an iframe/button keeps selection/focus.
      const sel = topDoc.getSelection?.();
      if (sel && typeof sel.removeAllRanges === "function") {
        sel.removeAllRanges();
      }
    } catch {
      // Ignore cleanup errors.
    }
  }

  function clickLiveChatCloseButton() {
    const button = findCloseButton();
    if (!button) return false;

    button.setAttribute(CLICKED_ATTR, "true");
    button.click();

    // Run cleanup more than once because YouTube fullscreen controls update async.
    setTimeout(releaseMouseAndRestorePlayerFocus, 0);
    setTimeout(releaseMouseAndRestorePlayerFocus, 50);
    setTimeout(releaseMouseAndRestorePlayerFocus, 200);

    cleanup();
    return true;
  }

  function start() {
    cleanup();
    tries = 0;

    if (clickLiveChatCloseButton()) return;

    interval = setInterval(() => {
      tries += 1;

      if (clickLiveChatCloseButton() || tries >= MAX_TRIES) {
        cleanup();
      }
    }, CHECK_EVERY_MS);

    observer = new MutationObserver(() => {
      clickLiveChatCloseButton();
    });

    observer.observe(document.documentElement, {
      childList: true,
      subtree: true
    });
  }

  start();

  // YouTube is a single-page app, so restart the simple one-shot search
  // when moving to another video.
  window.addEventListener("yt-navigate-finish", () => {
    setTimeout(start, 500);
  });
})();
