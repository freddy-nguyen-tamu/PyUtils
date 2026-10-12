'use strict';
const rate = document.getElementById('rate');
const speed = document.getElementById('speed');
const automatic = document.getElementById('automatic');

function displaySpeed() {
  speed.textContent = `${rate.value} px/s`;
}

chrome.storage.local.get({ rate: 180, automatic: false }, values => {
  rate.value = String(values.rate);
  automatic.checked = Boolean(values.automatic);
  displaySpeed();
});

rate.addEventListener('input', () => {
  displaySpeed();
  chrome.storage.local.set({ rate: Number(rate.value) });
});

automatic.addEventListener('change', () => {
  chrome.storage.local.set({ automatic: automatic.checked });
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== 'local') return;
  if (changes.automatic) automatic.checked = Boolean(changes.automatic.newValue);
  if (changes.rate && document.activeElement !== rate) {
    rate.value = String(changes.rate.newValue);
    displaySpeed();
  }
});
