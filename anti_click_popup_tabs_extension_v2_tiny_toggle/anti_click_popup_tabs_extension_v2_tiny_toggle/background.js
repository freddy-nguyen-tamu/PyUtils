const DEFAULTS = {
  enabled: true,
  aggressiveMode: true,
  showFloatingToggle: true,
  disabledHosts: {}
};

async function getSettings() {
  const data = await chrome.storage.local.get(DEFAULTS);
  return { ...DEFAULTS, ...data };
}

function hostFromUrl(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

function isInternalUrl(url) {
  return !url || /^(chrome|chrome-extension|edge|about|devtools):/i.test(url);
}

async function shouldProtectHost(host) {
  const settings = await getSettings();
  if (!settings.enabled) return false;
  if (!host) return false;
  if (settings.disabledHosts && settings.disabledHosts[host]) return false;
  return true;
}

async function broadcastSettings() {
  const settings = await getSettings();
  const tabs = await chrome.tabs.query({});
  for (const tab of tabs) {
    if (!tab.id || isInternalUrl(tab.url || "")) continue;
    try {
      await chrome.tabs.sendMessage(tab.id, { type: "SETTINGS_UPDATED", settings });
    } catch {}
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    if (message?.type === "GET_SETTINGS") {
      const settings = await getSettings();
      const host = sender?.tab?.url ? hostFromUrl(sender.tab.url) : "";
      sendResponse({ settings, host });
      return;
    }

    if (message?.type === "SET_GLOBAL_ENABLED") {
      await chrome.storage.local.set({ enabled: !!message.enabled });
      await broadcastSettings();
      sendResponse({ ok: true });
      return;
    }

    if (message?.type === "SET_AGGRESSIVE_MODE") {
      await chrome.storage.local.set({ aggressiveMode: !!message.enabled });
      await broadcastSettings();
      sendResponse({ ok: true });
      return;
    }

    if (message?.type === "SET_SHOW_FLOATING_TOGGLE") {
      await chrome.storage.local.set({ showFloatingToggle: !!message.enabled });
      await broadcastSettings();
      sendResponse({ ok: true });
      return;
    }

    if (message?.type === "SET_HOST_DISABLED") {
      const settings = await getSettings();
      const disabledHosts = settings.disabledHosts || {};
      const host = message.host;
      if (host) {
        if (message.disabled) disabledHosts[host] = true;
        else delete disabledHosts[host];
        await chrome.storage.local.set({ disabledHosts });
        await broadcastSettings();
      }
      sendResponse({ ok: true });
      return;
    }

    if (message?.type === "GET_POPUP_STATE") {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      const host = tab?.url ? hostFromUrl(tab.url) : "";
      const settings = await getSettings();
      sendResponse({ settings, host, tabUrl: tab?.url || "" });
      return;
    }
  })();

  return true;
});

chrome.tabs.onCreated.addListener(async (tab) => {
  const settings = await getSettings();
  if (!settings.enabled || !settings.aggressiveMode) return;
  if (!tab.openerTabId) return;

  let opener;
  try {
    opener = await chrome.tabs.get(tab.openerTabId);
  } catch {
    return;
  }

  const openerHost = hostFromUrl(opener?.url || "");
  const newUrl = tab.pendingUrl || tab.url || "";

  if (!(await shouldProtectHost(openerHost))) return;
  if (isInternalUrl(newUrl)) return;
  if (settings.disabledHosts?.[openerHost]) return;

  try {
    await chrome.tabs.remove(tab.id);
  } catch {}
});
