(() => {
  'use strict';

  const DEFAULT_SPEED = 900;
  const MIN_SPEED = 100;
  const MAX_SPEED = 5000;

  const slider = document.getElementById('speed');
  const numberInput = document.getElementById('speedNumber');
  const label = document.getElementById('speedLabel');

  function clamp(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return DEFAULT_SPEED;
    return Math.min(MAX_SPEED, Math.max(MIN_SPEED, Math.round(number / 50) * 50));
  }

  function render(value) {
    const speed = clamp(value);
    slider.value = String(speed);
    numberInput.value = String(speed);
    label.textContent = `${speed.toLocaleString()} px/second`;
    return speed;
  }

  function save(value) {
    const speed = render(value);
    chrome.storage.sync.set({ scrollSpeed: speed });
  }

  chrome.storage.sync.get({ scrollSpeed: DEFAULT_SPEED }, ({ scrollSpeed }) => {
    render(scrollSpeed);
  });

  slider.addEventListener('input', () => save(slider.value));
  numberInput.addEventListener('change', () => save(numberInput.value));
  numberInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      save(numberInput.value);
      numberInput.blur();
    }
  });
})();
