(() => {
  "use strict";

  const DEFAULT_SPEED = 90;
  const MIN_SPEED = 10;
  const MAX_SPEED = 2000;

  const speedRange = document.getElementById("speed");
  const speedNumber = document.getElementById("speed-number");
  const state = document.getElementById("state");

  function clampSpeed(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return DEFAULT_SPEED;
    return Math.min(MAX_SPEED, Math.max(MIN_SPEED, Math.round(numeric)));
  }

  function renderState(enabled) {
    state.textContent = `Auto-scroll: ${enabled ? "ON" : "OFF"}`;
  }

  function renderSpeed(value) {
    const speed = clampSpeed(value);
    speedNumber.value = String(speed);
    speedRange.value = String(Math.min(Number(speedRange.max), speed));
  }

  function saveSpeed(value) {
    const speed = clampSpeed(value);
    renderSpeed(speed);
    chrome.storage.local.set({ autoScrollSpeed: speed });
  }

  chrome.storage.local.get(
    {
      autoScrollEnabled: false,
      autoScrollSpeed: DEFAULT_SPEED
    },
    (settings) => {
      renderState(Boolean(settings.autoScrollEnabled));
      renderSpeed(settings.autoScrollSpeed);
    }
  );

  speedRange.addEventListener("input", () => saveSpeed(speedRange.value));
  speedNumber.addEventListener("change", () => saveSpeed(speedNumber.value));

  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName !== "local") return;

    if (changes.autoScrollEnabled) {
      renderState(Boolean(changes.autoScrollEnabled.newValue));
    }

    if (changes.autoScrollSpeed) {
      renderSpeed(changes.autoScrollSpeed.newValue);
    }
  });
})();
