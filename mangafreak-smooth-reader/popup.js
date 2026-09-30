(() => {
  const DEFAULT_SPEED = 90;
  const speed = document.querySelector('#speed');
  const value = document.querySelector('#value');

  chrome.storage.local.get({ scrollSpeed: DEFAULT_SPEED }, (state) => {
    speed.value = String(state.scrollSpeed);
    value.textContent = String(state.scrollSpeed);
  });

  speed.addEventListener('input', () => {
    const next = Number(speed.value);
    value.textContent = String(next);
    chrome.storage.local.set({ scrollSpeed: next });
  });
})();
