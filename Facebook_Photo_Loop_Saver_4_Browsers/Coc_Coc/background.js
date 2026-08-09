"use strict";

const DOWNLOAD_COMPLETE_TIMEOUT_MS = 5 * 60 * 1000;
const DOWNLOAD_POLL_MS = 300;

function setBadge(tabId, text) {
  chrome.action.setBadgeText({ tabId, text: text || "" }, () => void chrome.runtime.lastError);
}

function setTitle(tabId, title) {
  chrome.action.setTitle({ tabId, title }, () => void chrome.runtime.lastError);
}

function completedDownloadFbids(items) {
  const ids = new Set();

  for (const item of items || []) {
    if (!item || item.state !== "complete" || item.exists === false) continue;

    const postDecompressedBytes = Number(item.fileSize);
    const transferBytes = Number(item.totalBytes);
    if (!(postDecompressedBytes > 0) && !(transferBytes > 0)) continue;

    const filename = String(item.filename || "").replace(/\\/g, "/");
    if (!filename.includes("/Facebook_Photo_Loop_Saver/")) continue;

    const match = filename.match(/_fbid_(\d+)\.(?:jpe?g|png|webp|gif|avif)$/i);
    if (match) ids.add(match[1]);
  }

  return Array.from(ids);
}

function waitForDownloadComplete(downloadId, callback) {
  const deadline = Date.now() + DOWNLOAD_COMPLETE_TIMEOUT_MS;
  let finished = false;

  const finish = (result) => {
    if (finished) return;
    finished = true;
    callback(result);
  };

  const poll = () => {
    if (finished) return;

    chrome.downloads.search({ id: downloadId }, (items) => {
      const err = chrome.runtime.lastError;
      if (err) {
        finish({ ok: false, error: `Could not verify download: ${err.message}` });
        return;
      }

      const item = items && items[0];
      if (!item) {
        finish({ ok: false, error: "Download disappeared from browser download history before it could be verified." });
        return;
      }

      if (item.state === "complete") {
        const postDecompressedBytes = Number(item.fileSize);
        const transferBytes = Number(item.totalBytes);
        const bytes = postDecompressedBytes > 0
          ? postDecompressedBytes
          : (transferBytes > 0 ? transferBytes : 0);
        if (item.exists === false) {
          finish({ ok: false, error: "Browser reported the download complete, but the downloaded file does not exist." });
          return;
        }
        if (!(bytes > 0)) {
          finish({ ok: false, error: "Browser reported the download complete, but its file size is zero/unknown." });
          return;
        }

        finish({
          ok: true,
          downloadId,
          state: item.state,
          exists: item.exists !== false,
          fileSize: bytes,
        });
        return;
      }

      if (item.state === "interrupted") {
        finish({
          ok: false,
          error: `Download was interrupted${item.error ? `: ${item.error}` : "."}`,
        });
        return;
      }

      if (Date.now() >= deadline) {
        finish({ ok: false, error: "Timed out waiting for the image download to finish. Right Arrow was not pressed." });
        return;
      }

      // Polling also keeps an MV3 service worker alive while the message is pending.
      setTimeout(poll, DOWNLOAD_POLL_MS);
    });
  };

  poll();
}

chrome.action.onClicked.addListener((tab) => {
  if (!tab.id) return;

  const url = tab.url || "";
  if (!/^https:\/\/([^.]+\.)?facebook\.com\//i.test(url) &&
      !/^https:\/\/www\.facebook\.com\//i.test(url)) {
    setBadge(tab.id, "FB");
    setTitle(tab.id, "Open a Facebook photo first");
    return;
  }

  // Always inject on click so the extension also works on Facebook tabs that
  // were already open before the extension was installed/reloaded.
  chrome.scripting.executeScript(
    { target: { tabId: tab.id }, files: ["content.js"] },
    () => {
      const injectionError = chrome.runtime.lastError;
      if (injectionError) {
        setBadge(tab.id, "ERR");
        setTitle(tab.id, `Injection failed: ${injectionError.message}`);
        return;
      }

      chrome.tabs.sendMessage(tab.id, { type: "toggle-saver" }, () => {
        const messageError = chrome.runtime.lastError;
        if (messageError) {
          setBadge(tab.id, "ERR");
          setTitle(tab.id, `Could not start: ${messageError.message}`);
        }
      });
    }
  );
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === "download-photo") {
    chrome.downloads.download(
      {
        url: message.url,
        filename: message.filename,
        conflictAction: "uniquify",
        saveAs: false,
      },
      (downloadId) => {
        const err = chrome.runtime.lastError;
        if (err || typeof downloadId !== "number") {
          sendResponse({ ok: false, error: err?.message || "Browser did not create a download." });
          return;
        }

        // IMPORTANT: do not answer the content script yet. It awaits this
        // response, so it cannot press Right Arrow until the browser reports
        // state=complete, the file exists, and it has non-zero size.
        waitForDownloadComplete(downloadId, sendResponse);
      }
    );
    return true;
  }

  if (message?.type === "get-downloaded-fbids") {
    chrome.downloads.search({ state: "complete" }, (items) => {
      const err = chrome.runtime.lastError;
      if (err) sendResponse({ ok: false, error: err.message, fbids: [] });
      else sendResponse({ ok: true, fbids: completedDownloadFbids(items) });
    });
    return true;
  }

  if (message?.type === "status") {
    const tabId = sender.tab?.id;
    if (typeof tabId === "number") {
      setBadge(tabId, message.active ? "ON" : "");
      setTitle(
        tabId,
        message.active
          ? `Facebook Photo Saver running — ${message.text || "working"}`
          : "Facebook Photo Saver — click to start/stop"
      );
    }
    sendResponse({ ok: true });
  }
});
