(() => {
  'use strict';

  const slider = document.getElementById('speed');
  const output = document.getElementById('speedValue');

  function render(value) {
    output.textContent = `${value} px/s`;
  }

  chrome.storage.local.get({ speed: 80 }, ({ speed }) => {
    const value = Math.max(10, Math.min(500, Number(speed) || 80));
    slider.value = String(value);
    render(value);
  });

  slider.addEventListener('input', () => {
    const value = Number(slider.value);
    render(value);
    chrome.storage.local.set({ speed: value });
  });
})();
