(() => {
  'use strict';

  const storage = globalThis.browser?.storage?.local ?? globalThis.chrome?.storage?.local;
  const DEFAULT_RATE = 120;
  const MIN_RATE = 20;
  const MAX_RATE = 2000;

  const slider = document.getElementById('rate');
  const number = document.getElementById('rateNumber');

  init();

  async function init() {
    const saved = await storageGet(['scrollRate']);
    const rate = sanitize(saved.scrollRate);
    setInputs(rate);

    slider.addEventListener('input', () => saveRate(slider.value));
    number.addEventListener('change', () => saveRate(number.value));
    number.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        saveRate(number.value);
        number.blur();
      }
    });
  }

  async function saveRate(value) {
    const rate = sanitize(value);
    setInputs(rate);
    await storageSet({ scrollRate: rate });
  }

  function setInputs(rate) {
    slider.value = String(rate);
    number.value = String(rate);
  }

  function sanitize(value) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return DEFAULT_RATE;
    return Math.min(MAX_RATE, Math.max(MIN_RATE, Math.round(parsed / 10) * 10));
  }

  function storageGet(keys) {
    if (!storage) return Promise.resolve({});
    const result = storage.get(keys);
    return result && typeof result.then === 'function'
      ? result
      : new Promise((resolve, reject) => {
          storage.get(keys, (items) => {
            const err = globalThis.chrome?.runtime?.lastError;
            if (err) reject(err);
            else resolve(items || {});
          });
        });
  }

  function storageSet(values) {
    if (!storage) return Promise.resolve();
    const result = storage.set(values);
    return result && typeof result.then === 'function'
      ? result
      : new Promise((resolve, reject) => {
          storage.set(values, () => {
            const err = globalThis.chrome?.runtime?.lastError;
            if (err) reject(err);
            else resolve();
          });
        });
  }
})();
