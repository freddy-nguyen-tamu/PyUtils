"use strict";

const DEFAULTS = { enabled: true, edgeGap: 0, tryNativeZoom: true };
const enabled = document.getElementById("enabled");
const nativeZoom = document.getElementById("tryNativeZoom");
const edgeGap = document.getElementById("edgeGap");
const gapValue = document.getElementById("edgeGapValue");

function showGap() {
  gapValue.textContent = `${edgeGap.value} px`;
}

chrome.storage.sync.get(DEFAULTS, values => {
  if (chrome.runtime.lastError) return;
  enabled.checked = !!values.enabled;
  nativeZoom.checked = !!values.tryNativeZoom;
  edgeGap.value = String(Math.max(0, Math.min(32, Number(values.edgeGap) || 0)));
  showGap();
});

enabled.addEventListener("change", () => chrome.storage.sync.set({ enabled: enabled.checked }));
nativeZoom.addEventListener("change", () => chrome.storage.sync.set({ tryNativeZoom: nativeZoom.checked }));
edgeGap.addEventListener("input", showGap);
edgeGap.addEventListener("change", () => chrome.storage.sync.set({ edgeGap: Number(edgeGap.value) }));
