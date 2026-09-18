(() => {
  "use strict";

  const DEFAULT_SPEED = 120;
  const MIN_SPEED = 10;
  const MAX_SPEED = 1000;

  const slider = document.getElementById("speed");
  const number = document.getElementById("speedNumber");

  const clamp = (value) => {
    const n = Number(value);
    if (!Number.isFinite(n)) return DEFAULT_SPEED;
    return Math.min(MAX_SPEED, Math.max(MIN_SPEED, Math.round(n / 5) * 5));
  };

  const render = (value) => {
    const speed = clamp(value);
    slider.value = String(speed);
    number.value = String(speed);
    return speed;
  };

  const save = async (value) => {
    const speed = render(value);
    await chrome.storage.local.set({ scrollSpeed: speed });
  };

  chrome.storage.local.get({ scrollSpeed: DEFAULT_SPEED }).then(({ scrollSpeed }) => {
    render(scrollSpeed);
  });

  slider.addEventListener("input", () => save(slider.value));
  number.addEventListener("change", () => save(number.value));
  number.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      save(number.value);
      number.blur();
    }
  });
})();
