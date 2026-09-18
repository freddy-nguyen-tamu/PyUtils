(() => {
  'use strict';

  const DEFAULT_SPEED = 90;
  const MIN_SPEED = 10;
  const MAX_SPEED = 500;
  const slider = document.getElementById('speed');
  const number = document.getElementById('speedNumber');
  const saved = document.getElementById('saved');
  let saveTimer = 0;

  function clamp(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return DEFAULT_SPEED;
    return Math.min(MAX_SPEED, Math.max(MIN_SPEED, Math.round(n / 5) * 5));
  }

  function showSaved() {
    saved.textContent = 'Saved';
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      saved.textContent = 'Saved automatically';
    }, 900);
  }

  async function sendToCurrentTab(speed) {
    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!tab?.id) return;
      await chrome.tabs.sendMessage(tab.id, {
        type: 'TG_SIMPLE_READER_SPEED',
        speed
      });
    } catch {
      // The active tab may not be a ToonGod chapter; storage still persists.
    }
  }

  async function setSpeed(raw) {
    const speed = clamp(raw);
    slider.value = String(speed);
    number.value = String(speed);
    await chrome.storage.sync.set({ autoScrollSpeed: speed });
    await sendToCurrentTab(speed);
    showSaved();
  }

  slider.addEventListener('input', () => {
    number.value = slider.value;
    void setSpeed(slider.value);
  });

  number.addEventListener('change', () => void setSpeed(number.value));
  number.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      number.blur();
      void setSpeed(number.value);
    }
  });

  chrome.storage.sync.get({ autoScrollSpeed: DEFAULT_SPEED }, (stored) => {
    const speed = clamp(stored.autoScrollSpeed);
    slider.value = String(speed);
    number.value = String(speed);
  });
})();
