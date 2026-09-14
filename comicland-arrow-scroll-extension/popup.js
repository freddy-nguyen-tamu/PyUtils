(() => {
  "use strict";

  const DEFAULT_SPEED = 700;
  const MIN_SPEED = 50;
  const MAX_SPEED = 5000;

  const input = document.getElementById("speed");
  const button = document.getElementById("autoScroll");
  const status = document.getElementById("status");

  let statusTimer = null;
  let currentTabId = null;

  function normalize(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return DEFAULT_SPEED;
    return Math.min(MAX_SPEED, Math.max(MIN_SPEED, Math.round(n)));
  }

  function showStatus(text) {
    status.textContent = text;
    clearTimeout(statusTimer);
    statusTimer = setTimeout(() => {
      status.textContent = "";
    }, 1200);
  }

  function updateButton(enabled) {
    if (enabled) {
      button.textContent = "Stop Auto Scroll";
      button.classList.add("on");
    } else {
      button.textContent = "Start Auto Scroll";
      button.classList.remove("on");
    }
  }

  function saveSpeed() {
    const value = normalize(input.value);
    input.value = value;

    chrome.storage.sync.set({ scrollSpeed: value }, () => {
      showStatus("Speed saved");
    });
  }

  function sendToPage(message, callback) {
    if (currentTabId == null) {
      showStatus("Open a ComicLand chapter first");
      return;
    }

    chrome.tabs.sendMessage(currentTabId, message, (response) => {
      if (chrome.runtime.lastError) {
        updateButton(false);
        showStatus("Refresh the ComicLand chapter first");
        return;
      }

      if (callback) callback(response || {});
    });
  }

  chrome.storage.sync.get({ scrollSpeed: DEFAULT_SPEED }, ({ scrollSpeed }) => {
    input.value = normalize(scrollSpeed);
  });

  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    currentTabId = tabs[0]?.id ?? null;

    sendToPage({ type: "GET_AUTO_SCROLL_STATE" }, ({ enabled }) => {
      updateButton(Boolean(enabled));
    });
  });

  input.addEventListener("change", saveSpeed);
  input.addEventListener("blur", saveSpeed);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      saveSpeed();
      input.blur();
    }
  });

  button.addEventListener("click", () => {
    sendToPage({ type: "TOGGLE_AUTO_SCROLL" }, ({ enabled }) => {
      updateButton(Boolean(enabled));
      showStatus(enabled ? "Auto Scroll started" : "Auto Scroll stopped");
    });
  });
})();
