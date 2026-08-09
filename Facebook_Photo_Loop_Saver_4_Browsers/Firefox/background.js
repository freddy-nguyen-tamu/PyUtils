"use strict";

const DOWNLOAD_COMPLETE_TIMEOUT_MS = 5 * 60 * 1000;
const DOWNLOAD_POLL_MS = 300;

async function setBadge(tabId, text) {
  try {
    await browser.action.setBadgeText({ tabId, text: text || "" });
  } catch (_) {}
}

async function setTitle(tabId, title) {
  try {
    await browser.action.setTitle({ tabId, title });
  } catch (_) {}
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

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

async function waitForDownloadComplete(downloadId) {
  const deadline = Date.now() + DOWNLOAD_COMPLETE_TIMEOUT_MS;

  while (Date.now() < deadline) {
    const items = await browser.downloads.search({ id: downloadId });
    const item = items && items[0];

    if (!item) {
      throw new Error("Download disappeared from browser download history before it could be verified.");
    }

    if (item.state === "complete") {
      const bytes = Number(item.fileSize || item.totalBytes || 0);
      if (item.exists === false) {
        throw new Error("Browser reported the download complete, but the downloaded file does not exist.");
      }
      if (!(bytes > 0)) {
        throw new Error("Browser reported the download complete, but its file size is zero/unknown.");
      }

      return {
        ok: true,
        downloadId,
        state: item.state,
        exists: item.exists !== false,
        fileSize: bytes,
      };
    }

    if (item.state === "interrupted") {
      throw new Error(`Download was interrupted${item.error ? `: ${item.error}` : "."}`);
    }

    await sleep(DOWNLOAD_POLL_MS);
  }

  throw new Error("Timed out waiting for the image download to finish. Right Arrow was not pressed.");
}

browser.action.onClicked.addListener(async (tab) => {
  if (!tab.id) return;

  const url = tab.url || "";
  if (!/^https:\/\/([^.]+\.)?facebook\.com\//i.test(url) &&
      !/^https:\/\/www\.facebook\.com\//i.test(url)) {
    await setBadge(tab.id, "FB");
    await setTitle(tab.id, "Open a Facebook photo first");
    return;
  }

  try {
    // Works even if this Facebook tab existed before the extension was loaded.
    await browser.scripting.executeScript({
      target: { tabId: tab.id },
      files: ["content.js"],
    });
    await browser.tabs.sendMessage(tab.id, { type: "toggle-saver" });
  } catch (err) {
    await setBadge(tab.id, "ERR");
    await setTitle(tab.id, `Could not start: ${err?.message || err}`);
  }
});

browser.runtime.onMessage.addListener(async (message, sender) => {
  if (message?.type === "download-photo") {
    try {
      const downloadId = await browser.downloads.download({
        url: message.url,
        filename: message.filename,
        conflictAction: "uniquify",
        saveAs: false,
      });

      // Await actual completion. The content script awaits this response and
      // therefore cannot press Right Arrow before verification succeeds.
      return await waitForDownloadComplete(downloadId);
    } catch (err) {
      return { ok: false, error: err?.message || String(err) };
    }
  }

  if (message?.type === "get-downloaded-fbids") {
    try {
      const items = await browser.downloads.search({ state: "complete" });
      return { ok: true, fbids: completedDownloadFbids(items) };
    } catch (err) {
      return { ok: false, error: err?.message || String(err), fbids: [] };
    }
  }

  if (message?.type === "status") {
    const tabId = sender.tab?.id;
    if (typeof tabId === "number") {
      await setBadge(tabId, message.active ? "ON" : "");
      await setTitle(
        tabId,
        message.active
          ? `Facebook Photo Saver running — ${message.text || "working"}`
          : "Facebook Photo Saver — click to start/stop"
      );
    }
    return { ok: true };
  }
});
