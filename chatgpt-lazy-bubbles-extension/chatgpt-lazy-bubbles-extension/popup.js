const DEFAULTS = {
  enabled: true,
  keepRecentTurns: 10,
  viewportBufferScreens: 2.5,
  minTurnsBeforeVirtualize: 24,
  showStatus: true,
  debug: false
};

const ids = Object.keys(DEFAULTS);

function getFormValues() {
  return {
    enabled: document.getElementById("enabled").checked,
    keepRecentTurns: Number(document.getElementById("keepRecentTurns").value),
    viewportBufferScreens: Number(document.getElementById("viewportBufferScreens").value),
    minTurnsBeforeVirtualize: Number(document.getElementById("minTurnsBeforeVirtualize").value),
    showStatus: document.getElementById("showStatus").checked,
    debug: document.getElementById("debug").checked
  };
}

function fillForm(settings) {
  for (const id of ids) {
    const el = document.getElementById(id);
    if (typeof DEFAULTS[id] === "boolean") el.checked = Boolean(settings[id]);
    else el.value = settings[id];
  }
}

async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function updateStats() {
  const stats = document.getElementById("stats");
  try {
    const tab = await getActiveTab();
    if (!tab?.id || !/https:\/\/(chatgpt\.com|chat\.openai\.com)\//.test(tab.url || "")) {
      stats.textContent = "Open a ChatGPT conversation to see stats.";
      return;
    }
    chrome.tabs.sendMessage(tab.id, { type: "CLB_GET_STATS" }, (res) => {
      if (chrome.runtime.lastError || !res?.ok) {
        stats.textContent = "Reload the ChatGPT tab if stats do not appear.";
        return;
      }
      stats.textContent = `${res.hidden}/${res.total} turns are currently sleeping.`;
    });
  } catch {
    stats.textContent = "Stats unavailable on this tab.";
  }
}

async function save() {
  const settings = getFormValues();
  await chrome.storage.sync.set(settings);
  const tab = await getActiveTab();
  if (tab?.id) {
    chrome.tabs.sendMessage(tab.id, { type: "CLB_SETTINGS_UPDATED", settings });
  }
  document.getElementById("save").textContent = "Saved";
  setTimeout(() => (document.getElementById("save").textContent = "Save settings"), 900);
  updateStats();
}

chrome.storage.sync.get(DEFAULTS, (items) => {
  fillForm({ ...DEFAULTS, ...items });
  updateStats();
});

document.getElementById("save").addEventListener("click", save);
