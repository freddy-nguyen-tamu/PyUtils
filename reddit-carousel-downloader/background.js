'use strict';

// Downloads are done by Chrome rather than an in-page <a download> so that
// the original files, not screenshots / compressed previews, are saved.
const activeDownloads = new Map();

function permittedImageURL(raw) {
  try {
    const url = new URL(raw);
    if (url.protocol !== 'https:') return false;
    const hostname = url.hostname.toLowerCase();
    return hostname === 'i.redd.it' ||
      hostname === 'preview.redd.it' ||
      hostname.endsWith('.preview.redd.it') ||
      hostname === 'external-preview.redd.it' ||
      hostname === 'redditmedia.com' ||
      hostname.endsWith('.redditmedia.com') ||
      (hostname === 'www.reddit.com' && url.pathname === '/media');
  } catch {
    return false;
  }
}

function validDownloadPath(path) {
  return typeof path === 'string' && path.length <= 240 &&
    !path.startsWith('/') && !path.includes('..') &&
    !/[<>:"|?*\x00-\x1f]/.test(path) &&
    path.split('/').every(part => !!part && part.length <= 110 && part !== '.');
}

function waitUntilComplete(downloadId) {
  return new Promise(resolve => {
    let finished = false;
    const done = (result) => {
      if (finished) return;
      finished = true;
      chrome.downloads.onChanged.removeListener(onChanged);
      clearTimeout(timeoutId);
      activeDownloads.delete(downloadId);
      resolve(result);
    };
    const onChanged = (delta) => {
      if (delta.id !== downloadId) return;
      if (delta.state?.current === 'complete') {
        done({ ok: true, id: downloadId });
      } else if (delta.state?.current === 'interrupted') {
        done({ ok: false, error: `Download interrupted: ${delta.error?.current || 'unknown reason'}` });
      }
    };
    chrome.downloads.onChanged.addListener(onChanged);
    const timeoutId = setTimeout(() => {
      done({ ok: false, error: 'The file did not finish downloading within 150 seconds.' });
    }, 150000);
    activeDownloads.set(downloadId, done);
    // Close the race between chrome.downloads.download resolving and the listener attaching.
    chrome.downloads.search({ id: downloadId }).then(items => {
      if (finished) return;
      const item = items[0];
      if (item?.state === 'complete') done({ ok: true, id: downloadId });
      if (item?.state === 'interrupted') done({ ok: false, error: `Download interrupted: ${item.error || 'unknown reason'}` });
    }).catch(() => {});
  });
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || message.type !== 'RCD_DOWNLOAD') return false;

  (async () => {
    if (!sender.tab || !permittedImageURL(message.url)) {
      return { ok: false, error: 'Image URL is not from a supported Reddit image host.' };
    }
    if (!validDownloadPath(message.filename)) {
      return { ok: false, error: 'Invalid local filename.' };
    }
    const id = await chrome.downloads.download({
      url: message.url,
      filename: message.filename,
      conflictAction: 'uniquify',
      saveAs: false
    });
    if (typeof id !== 'number') return { ok: false, error: 'Chrome did not start the download.' };
    return waitUntilComplete(id);
  })().then(sendResponse).catch(error => {
    sendResponse({ ok: false, error: String(error?.message || error) });
  });
  return true;
});
