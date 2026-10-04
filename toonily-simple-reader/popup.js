(() => {
  "use strict";

  const DEFAULTS = {
    autoScrollEnabled: false,
    autoScrollSpeed: 120,
    arrowScrollStep: 260
  };

  const enabled = document.getElementById("autoScrollEnabled");
  const speed = document.getElementById("autoScrollSpeed");
  const step = document.getElementById("arrowScrollStep");
  const saved = document.getElementById("saved");
  let saveTimer = 0;

  chrome.storage.local.get(DEFAULTS, (settings) => {
    enabled.checked = Boolean(settings.autoScrollEnabled);
    speed.value = settings.autoScrollSpeed;
    step.value = settings.arrowScrollStep;
  });

  enabled.addEventListener("change", () => {
    chrome.storage.local.set({ autoScrollEnabled: enabled.checked }, showSaved);
  });

  speed.addEventListener("change", saveNumbers);
  step.addEventListener("change", saveNumbers);
  speed.addEventListener("input", debounceSave);
  step.addEventListener("input", debounceSave);

  function clamp(value, min, max, fallback) {
    const n = Number(value);
    if (!Number.isFinite(n)) return fallback;
    return Math.min(max, Math.max(min, n));
  }

  function saveNumbers() {
    const autoScrollSpeed = clamp(speed.value, 10, 3000, DEFAULTS.autoScrollSpeed);
    const arrowScrollStep = clamp(step.value, 20, 3000, DEFAULTS.arrowScrollStep);

    speed.value = autoScrollSpeed;
    step.value = arrowScrollStep;

    chrome.storage.local.set(
      { autoScrollSpeed, arrowScrollStep },
      showSaved
    );
  }

  function debounceSave() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(saveNumbers, 250);
  }

  function showSaved() {
    saved.textContent = "Saved";
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      saved.textContent = "";
    }, 700);
  }
})();
