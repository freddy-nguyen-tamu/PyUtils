(() => {
  "use strict";

  // Simple version:
  // - Does not remove elements.
  // - Does not block requests.
  // - Does not inject a toggle button.
  // - Only clicks YouTube's own live chat close button when it appears.

  const CLICKED_ATTR = "data-yt-live-chat-auto-close-clicked";

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
    const closeHost = document.querySelector("yt-live-chat-header-renderer #close-button, #close-button");
    const fallbackButton = closeHost?.querySelector("button");
    if (fallbackButton && !fallbackButton.hasAttribute(CLICKED_ATTR)) return fallbackButton;

    return null;
  }

  function clickLiveChatCloseButton() {
    const button = findCloseButton();
    if (!button) return false;

    button.setAttribute(CLICKED_ATTR, "true");
    button.click();
    return true;
  }

  // Try a few times because YouTube creates the live chat iframe/header late.
  let tries = 0;
  const maxTries = 80;

  const interval = setInterval(() => {
    tries += 1;

    if (clickLiveChatCloseButton() || tries >= maxTries) {
      clearInterval(interval);
    }
  }, 250);

  // Also react to YouTube SPA navigation and late DOM changes.
  const observer = new MutationObserver(() => {
    clickLiveChatCloseButton();
  });

  observer.observe(document.documentElement, {
    childList: true,
    subtree: true
  });
})();
