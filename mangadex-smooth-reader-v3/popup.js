const DEFAULTS = {
  autoScrollEnabled: false,
  autoScrollSpeed: 120,
  keyScrollPixels: 420,
  maxReaderWidth: 1100
};

const $ = id => document.getElementById(id);

function clamp(n, min, max) {
  return Math.max(min, Math.min(max, n));
}

function setPair(range, number, value) {
  range.value = value;
  number.value = value;
}

function save(key, value) {
  chrome.storage.local.set({ [key]: value });
}

function bindPair(range, number, key, min, max) {
  range.addEventListener("input", () => {
    const value = clamp(Number(range.value), min, max);
    number.value = value;
    save(key, value);
  });

  number.addEventListener("change", () => {
    const value = clamp(Number(number.value), min, max);
    number.value = value;
    range.value = value;
    save(key, value);
  });
}

chrome.storage.local.get(Object.keys(DEFAULTS), stored => {
  const s = { ...DEFAULTS, ...stored };

  $("enabled").checked = Boolean(s.autoScrollEnabled);
  setPair($("speed"), $("speedNumber"), s.autoScrollSpeed);
  setPair($("keyScroll"), $("keyScrollNumber"), s.keyScrollPixels);
  setPair($("width"), $("widthNumber"), s.maxReaderWidth);
});

$("enabled").addEventListener("change", e => {
  save("autoScrollEnabled", e.target.checked);
});

bindPair($("speed"), $("speedNumber"), "autoScrollSpeed", 5, 2000);
bindPair($("keyScroll"), $("keyScrollNumber"), "keyScrollPixels", 50, 3000);
bindPair($("width"), $("widthNumber"), "maxReaderWidth", 420, 2400);
