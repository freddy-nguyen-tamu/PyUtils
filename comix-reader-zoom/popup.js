(() => {
  "use strict";

  const DEFAULT_SCROLL_SPEED = 1400;
  const MIN_SCROLL_SPEED = 50;
  const MAX_SCROLL_SPEED = 10000;

  const input = document.getElementById("scrollSpeed");
  const status = document.getElementById("status");
  let statusTimer = 0;

  function clamp(value) {
    return Math.min(MAX_SCROLL_SPEED, Math.max(MIN_SCROLL_SPEED, value));
  }

  function showStatus(text) {
    status.textContent = text;
    clearTimeout(statusTimer);
    statusTimer = setTimeout(() => {
      status.textContent = "";
    }, 1200);
  }

  function save() {
    const parsed = Number(input.value);
    if (!Number.isFinite(parsed)) return;

    const value = clamp(Math.round(parsed));
    input.value = String(value);
    chrome.storage.sync.set({ scrollSpeed: value }, () => {
      showStatus("Saved");
    });
  }

  chrome.storage.sync.get({ scrollSpeed: DEFAULT_SCROLL_SPEED }, (settings) => {
    input.value = String(clamp(Number(settings.scrollSpeed) || DEFAULT_SCROLL_SPEED));
  });

  input.addEventListener("change", save);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      save();
      input.blur();
    }
  });
})();
