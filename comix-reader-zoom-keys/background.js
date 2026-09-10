// Service worker: supplies the exact browser zoom factor to the content script
// and pushes updates whenever the user changes browser zoom.

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type !== "COMIX_GET_TAB_ZOOM") return;

  const tabId = sender.tab?.id;
  if (typeof tabId !== "number") {
    sendResponse({ zoom: 1 });
    return;
  }

  chrome.tabs.getZoom(tabId)
    .then((zoom) => sendResponse({ zoom }))
    .catch(() => sendResponse({ zoom: 1 }));

  return true;
});

chrome.tabs.onZoomChange.addListener((info) => {
  chrome.tabs.sendMessage(info.tabId, {
    type: "COMIX_ZOOM_CHANGED",
    zoom: info.newZoomFactor
  }).catch(() => {
    // Expected on tabs where this extension has no content script.
  });
});
