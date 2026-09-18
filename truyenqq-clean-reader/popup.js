(() => {
  const DEFAULT_RATE = 120;
  const slider = document.getElementById('rate');
  const number = document.getElementById('number');

  function clamp(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return DEFAULT_RATE;
    return Math.max(1, Math.min(5000, Math.round(n)));
  }

  function save(value) {
    const rate = clamp(value);
    slider.value = String(Math.max(20, Math.min(1200, rate)));
    number.value = String(rate);
    chrome.storage.local.set({ scrollRate: rate });
  }

  chrome.storage.local.get({ scrollRate: DEFAULT_RATE }, ({ scrollRate }) => {
    const rate = clamp(scrollRate);
    slider.value = String(Math.max(20, Math.min(1200, rate)));
    number.value = String(rate);
  });

  slider.addEventListener('input', () => save(slider.value));
  number.addEventListener('change', () => save(number.value));
  number.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      save(number.value);
    }
  });
})();
