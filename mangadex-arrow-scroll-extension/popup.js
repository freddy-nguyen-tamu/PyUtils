(() => {
  "use strict";

  const DEFAULT_SPEED = 700;
  const MIN_SPEED = 50;
  const MAX_SPEED = 5000;

  const input = document.getElementById("speed");
  const status = document.getElementById("status");
  let statusTimer = null;

  function normalize(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return DEFAULT_SPEED;
    return Math.min(MAX_SPEED, Math.max(MIN_SPEED, Math.round(n)));
  }

  function showSaved() {
    status.textContent = "Saved";
    clearTimeout(statusTimer);
    statusTimer = setTimeout(() => {
      status.textContent = "";
    }, 900);
  }

  chrome.storage.sync.get({ scrollSpeed: DEFAULT_SPEED }, (result) => {
    input.value = normalize(result.scrollSpeed);
  });

  function save() {
    const value = normalize(input.value);
    input.value = value;
    chrome.storage.sync.set({ scrollSpeed: value }, showSaved);
  }

  input.addEventListener("change", save);
  input.addEventListener("blur", save);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      save();
      input.blur();
    }
  });
})();
