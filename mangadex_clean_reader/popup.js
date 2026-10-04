(() => {
  const DEFAULT_SPEED = 120;
  const speedInput = document.getElementById('speed');
  const value = document.getElementById('value');

  function render(n) {
    value.textContent = `${n} px/sec`;
  }

  chrome.storage.local.get({ scrollSpeed: DEFAULT_SPEED }, ({ scrollSpeed }) => {
    const n = Number(scrollSpeed) || DEFAULT_SPEED;
    speedInput.value = String(n);
    render(n);
  });

  speedInput.addEventListener('input', () => {
    const n = Number(speedInput.value);
    render(n);
    chrome.storage.local.set({ scrollSpeed: n });
  });
})();
