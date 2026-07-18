"use strict";

const DEFAULTS = {
  enabled: true,
  hideFeed: true,
  hideReels: true,
  detectionMode: "balanced",
  showPlaceholders: false
};

const ids = Object.keys(DEFAULTS);
const status = document.querySelector("#status");
let statusTimer = null;

function setStatus(message) {
  status.textContent = message;
  clearTimeout(statusTimer);
  statusTimer = setTimeout(() => {
    status.textContent = "Settings apply immediately.";
  }, 1500);
}

async function restore() {
  try {
    const settings = await chrome.storage.sync.get(DEFAULTS);

    for (const id of ids) {
      const control = document.getElementById(id);
      if (!control) continue;

      if (control.type === "checkbox") {
        control.checked = Boolean(settings[id]);
      } else {
        control.value = settings[id];
      }
    }
  } catch (error) {
    console.error(error);
    setStatus("Could not load settings.");
  }
}

async function save(event) {
  const control = event.currentTarget;
  const value = control.type === "checkbox" ? control.checked : control.value;

  try {
    await chrome.storage.sync.set({ [control.id]: value });
    setStatus("Saved.");
  } catch (error) {
    console.error(error);
    setStatus("Could not save setting.");
  }
}

for (const id of ids) {
  document.getElementById(id)?.addEventListener("change", save);
}

restore();
