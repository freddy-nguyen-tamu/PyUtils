(() => {
  "use strict";

  const speed = document.getElementById("speed");
  const speedValue = document.getElementById("speedValue");

  function clamp(value) {
    const n = Number(value);
    return Number.isFinite(n) ? Math.min(300, Math.max(10, n)) : 55;
  }

  function render(value) {
    const v = clamp(value);
    speed.value = String(v);
    speedValue.value = `${v} px/s`;
    speedValue.textContent = `${v} px/s`;
  }

  chrome.storage.local.get({ autoScrollSpeed: 55 }, (stored) => {
    render(stored.autoScrollSpeed);
  });

  speed.addEventListener("input", () => {
    const value = clamp(speed.value);
    render(value);
    chrome.storage.local.set({ autoScrollSpeed: value });
  });
})();
