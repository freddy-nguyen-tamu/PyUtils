'use strict';
const auto = document.getElementById('auto');
const speed = document.getElementById('speed');
const value = document.getElementById('speed-value');
const clamp = v => Math.max(20, Math.min(600, Number(v) || 130));
chrome.storage.local.get({ autoScroll: false, speed: 130 }, settings => {
  auto.checked = !!settings.autoScroll;
  speed.value = String(clamp(settings.speed));
  value.textContent = `${speed.value} px/s`;
});
auto.addEventListener('change', () => chrome.storage.local.set({ autoScroll: auto.checked }));
speed.addEventListener('input', () => {
  value.textContent = `${speed.value} px/s`;
  chrome.storage.local.set({ speed: clamp(speed.value) });
});
chrome.storage.onChanged.addListener((changes, namespace) => {
  if (namespace !== 'local') return;
  if (changes.autoScroll) auto.checked = !!changes.autoScroll.newValue;
  if (changes.speed) {
    speed.value = String(clamp(changes.speed.newValue));
    value.textContent = `${speed.value} px/s`;
  }
});
