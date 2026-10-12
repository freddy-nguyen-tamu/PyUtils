'use strict';
const rate = document.getElementById('rate');
const value = document.getElementById('rateValue');
const enabled = document.getElementById('enabled');
const validRate = (v) => Number.isFinite(Number(v))
  ? Math.min(1200, Math.max(20, Number(v))) : 140;

chrome.storage.local.get({ ratePxPerSecond: 140, autoScrollEnabled: false })
  .then((settings) => {
    rate.value = String(validRate(settings.ratePxPerSecond));
    value.value = `${rate.value} px/s`;
    enabled.checked = settings.autoScrollEnabled === true;
  });

rate.addEventListener('input', () => {
  value.value = `${rate.value} px/s`;
  chrome.storage.local.set({ ratePxPerSecond: Number(rate.value) });
});
enabled.addEventListener('change', () => {
  chrome.storage.local.set({ autoScrollEnabled: enabled.checked });
});
chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== 'local') return;
  if (changes.ratePxPerSecond) {
    rate.value = String(validRate(changes.ratePxPerSecond.newValue));
    value.value = `${rate.value} px/s`;
  }
  if (changes.autoScrollEnabled) enabled.checked = changes.autoScrollEnabled.newValue === true;
});
