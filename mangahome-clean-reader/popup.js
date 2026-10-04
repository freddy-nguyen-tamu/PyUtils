(() => {
  'use strict';

  const DEFAULT_SPEED = 120;
  const MIN_SPEED = 10;
  const SLIDER_MAX = 1000;
  const ABSOLUTE_MAX = 5000;

  const slider = document.getElementById('speed');
  const number = document.getElementById('speedNumber');

  function clamp(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return DEFAULT_SPEED;
    return Math.max(MIN_SPEED, Math.min(ABSOLUTE_MAX, Math.round(n)));
  }

  function save(value) {
    const speed = clamp(value);
    number.value = String(speed);
    slider.value = String(Math.min(speed, SLIDER_MAX));
    chrome.storage.local.set({ scrollSpeed: speed });
  }

  chrome.storage.local.get({ scrollSpeed: DEFAULT_SPEED }, ({ scrollSpeed }) => {
    const speed = clamp(scrollSpeed);
    number.value = String(speed);
    slider.value = String(Math.min(speed, SLIDER_MAX));
  });

  slider.addEventListener('input', () => save(slider.value));
  number.addEventListener('change', () => save(number.value));
  number.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      save(number.value);
      number.blur();
    }
  });
})();
