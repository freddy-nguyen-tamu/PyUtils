(() => {
  "use strict";

  const DEFAULT_SPEED = 700;
  const MIN_SPEED = 50;
  const MAX_SPEED = 3000;

  const speedInput = document.getElementById("speed");
  const slider = document.getElementById("speedSlider");
  const status = document.getElementById("status");
  let statusTimer = null;

  function clampSpeed(value) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return DEFAULT_SPEED;
    return Math.min(MAX_SPEED, Math.max(MIN_SPEED, Math.round(parsed)));
  }

  function showSaved(value) {
    status.textContent = `Saved: ${value} px/sec`;
    clearTimeout(statusTimer);
    statusTimer = setTimeout(() => {
      status.textContent = "Changes save automatically.";
    }, 1200);
  }

  function save(value) {
    const speed = clampSpeed(value);
    speedInput.value = String(speed);
    slider.value = String(speed);
    chrome.storage.local.set({ scrollSpeed: speed }, () => showSaved(speed));
  }

  chrome.storage.local.get({ scrollSpeed: DEFAULT_SPEED }, (settings) => {
    const speed = clampSpeed(settings.scrollSpeed);
    speedInput.value = String(speed);
    slider.value = String(speed);
  });

  speedInput.addEventListener("change", () => save(speedInput.value));
  speedInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      save(speedInput.value);
      speedInput.blur();
    }
  });

  slider.addEventListener("input", () => {
    const speed = clampSpeed(slider.value);
    speedInput.value = String(speed);
    chrome.storage.local.set({ scrollSpeed: speed });
  });

  slider.addEventListener("change", () => showSaved(clampSpeed(slider.value)));
})();
