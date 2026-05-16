function hostFromUrl(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

async function send(message) {
  return await chrome.runtime.sendMessage(message);
}

async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function load() {
  const state = await send({ type: "GET_POPUP_STATE" });
  const settings = state.settings;
  const host = state.host || hostFromUrl(state.tabUrl || "");

  document.getElementById("host").textContent = host ? `Current site: ${host}` : "Current site: unavailable";

  const globalEnabled = document.getElementById("globalEnabled");
  const siteEnabled = document.getElementById("siteEnabled");
  const aggressiveMode = document.getElementById("aggressiveMode");
  const showFloatingToggle = document.getElementById("showFloatingToggle");

  globalEnabled.checked = !!settings.enabled;
  siteEnabled.checked = host ? !settings.disabledHosts?.[host] : false;
  siteEnabled.disabled = !host;
  aggressiveMode.checked = !!settings.aggressiveMode;
  showFloatingToggle.checked = settings.showFloatingToggle !== false;

  globalEnabled.onchange = async () => {
    await send({ type: "SET_GLOBAL_ENABLED", enabled: globalEnabled.checked });
  };

  siteEnabled.onchange = async () => {
    if (!host) return;
    await send({
      type: "SET_HOST_DISABLED",
      host,
      disabled: !siteEnabled.checked
    });
  };

  aggressiveMode.onchange = async () => {
    await send({ type: "SET_AGGRESSIVE_MODE", enabled: aggressiveMode.checked });
  };

  showFloatingToggle.onchange = async () => {
    await send({ type: "SET_SHOW_FLOATING_TOGGLE", enabled: showFloatingToggle.checked });
  };

  document.getElementById("reload").onclick = async () => {
    const tab = await getActiveTab();
    if (tab?.id) chrome.tabs.reload(tab.id);
    window.close();
  };
}

load();
