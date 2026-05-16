(() => {
  "use strict";

  const STORAGE_KEY = "ytLiveReplayCleanerEnabled";
  const DEFAULT_ENABLED = true;

  const RULE_IDS = [7001, 7002, 7003, 7004];

  // These requests are what normally load the replay chat iframe/app.
  // Blocking them prevents the chat replay from being downloaded and run.
  const BLOCK_RULES = [
    {
      id: 7001,
      priority: 1,
      action: { type: "block" },
      condition: {
        regexFilter: "^https://(www\\.)?youtube\\.com/live_chat_replay(\\?|$)",
        resourceTypes: ["main_frame", "sub_frame", "xmlhttprequest", "other"]
      }
    },
    {
      id: 7002,
      priority: 1,
      action: { type: "block" },
      condition: {
        regexFilter: "^https://(www\\.)?youtube\\.com/live_chat(\\?|$)",
        resourceTypes: ["main_frame", "sub_frame", "xmlhttprequest", "other"]
      }
    },
    {
      id: 7003,
      priority: 1,
      action: { type: "block" },
      condition: {
        regexFilter: "^https://(www\\.)?youtube\\.com/live_chat/get_live_chat_replay(\\?|$)",
        resourceTypes: ["xmlhttprequest", "other"]
      }
    },
    {
      id: 7004,
      priority: 1,
      action: { type: "block" },
      condition: {
        regexFilter: "^https://(www\\.)?youtube\\.com/youtubei/v1/live_chat/",
        resourceTypes: ["xmlhttprequest", "other"]
      }
    }
  ];

  async function getEnabled() {
    const result = await chrome.storage.local.get({ [STORAGE_KEY]: DEFAULT_ENABLED });
    return Boolean(result[STORAGE_KEY]);
  }

  async function setEnabled(enabled) {
    await chrome.storage.local.set({ [STORAGE_KEY]: Boolean(enabled) });
    await applyRequestBlocking(Boolean(enabled));
  }

  async function applyRequestBlocking(enabled) {
    await chrome.declarativeNetRequest.updateDynamicRules({
      removeRuleIds: RULE_IDS,
      addRules: enabled ? BLOCK_RULES : []
    });
  }

  chrome.runtime.onInstalled.addListener(async () => {
    const existing = await chrome.storage.local.get(STORAGE_KEY);
    const enabled = Object.prototype.hasOwnProperty.call(existing, STORAGE_KEY)
      ? Boolean(existing[STORAGE_KEY])
      : DEFAULT_ENABLED;

    if (!Object.prototype.hasOwnProperty.call(existing, STORAGE_KEY)) {
      await chrome.storage.local.set({ [STORAGE_KEY]: enabled });
    }

    await applyRequestBlocking(enabled);
  });

  chrome.runtime.onStartup.addListener(async () => {
    await applyRequestBlocking(await getEnabled());
  });

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    (async () => {
      if (!message || typeof message.type !== "string") return;

      if (message.type === "getCleanerState") {
        sendResponse({ enabled: await getEnabled() });
        return;
      }

      if (message.type === "setCleanerState") {
        const enabled = Boolean(message.enabled);
        await setEnabled(enabled);
        sendResponse({ enabled });
        return;
      }
    })().catch((error) => {
      sendResponse({ error: String(error && error.message ? error.message : error) });
    });

    return true;
  });
})();
