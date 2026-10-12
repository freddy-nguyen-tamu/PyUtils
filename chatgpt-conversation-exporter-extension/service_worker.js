'use strict';

const conversationPath = /\/c\/[0-9a-f-]{32,36}(?:\/|$)/i;

chrome.action.onClicked.addListener(async (tab) => {
  const tabId = tab && tab.id;
  if (typeof tabId !== 'number') return;

  let url;
  try {
    url = new URL(tab.url || '');
  } catch (_) {
    await showError(tabId, 'URL', 'Could not read this tab URL. Open a ChatGPT conversation and click again.');
    return;
  }

  if (url.hostname !== 'chatgpt.com' || !conversationPath.test(url.pathname)) {
    await showError(
      tabId,
      'URL',
      'Open https://chatgpt.com/c/<conversation-id> first, then click the extension icon.'
    );
    return;
  }

  try {
    await chrome.action.setBadgeBackgroundColor({ tabId, color: '#2563eb' });
    await chrome.action.setBadgeText({ tabId, text: '...' });
    await chrome.action.setTitle({
      tabId,
      title: 'Injecting exporter. Check this tab’s DevTools Console for progress.'
    });

    await chrome.scripting.executeScript({
      target: { tabId },
      files: ['exporter.js'],
      world: 'MAIN'
    });

    await chrome.action.setBadgeBackgroundColor({ tabId, color: '#15803d' });
    await chrome.action.setBadgeText({ tabId, text: 'RUN' });
    await chrome.action.setTitle({
      tabId,
      title: 'Exporter injected. Check the page console for progress; a TXT download starts when complete.'
    });
  } catch (error) {
    await showError(tabId, 'ERR', `Could not inject exporter: ${error && error.message ? error.message : String(error)}`);
  }
});

async function showError(tabId, badge, title) {
  try {
    await chrome.action.setBadgeBackgroundColor({ tabId, color: '#b45309' });
    await chrome.action.setBadgeText({ tabId, text: badge });
    await chrome.action.setTitle({ tabId, title });
  } catch (_) {
    // The tab may have closed or navigated while the extension was running.
  }
}
