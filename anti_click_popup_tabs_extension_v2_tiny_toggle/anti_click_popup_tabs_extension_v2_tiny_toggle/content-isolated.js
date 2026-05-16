(() => {
  "use strict";

  let settings = null;
  let button = null;

  function currentHost() {
    return location.hostname.replace(/^www\./, "");
  }

  function isProtected() {
    if (!settings) return true;
    return !!settings.enabled && !settings.disabledHosts?.[currentHost()];
  }

  function buttonText() {
    return isProtected() ? "ON" : "OFF";
  }

  function buttonTitle() {
    return isProtected()
      ? "Anti Click Popup Tabs is ON for this site. Click to turn OFF."
      : "Anti Click Popup Tabs is OFF for this site. Click to turn ON.";
  }

  function updateButton() {
    if (!button || !settings) return;

    button.textContent = buttonText();
    button.title = buttonTitle();

    const on = isProtected();
    button.style.background = on ? "rgba(20, 160, 75, 0.88)" : "rgba(180, 45, 45, 0.88)";
    button.style.color = "#fff";
    button.style.border = "1px solid rgba(255,255,255,.35)";
  }

  async function setSiteDisabled(disabled) {
    await chrome.runtime.sendMessage({
      type: "SET_HOST_DISABLED",
      host: currentHost(),
      disabled
    });

    settings.disabledHosts = settings.disabledHosts || {};
    if (disabled) settings.disabledHosts[currentHost()] = true;
    else delete settings.disabledHosts[currentHost()];
    updateButton();
  }

  function makeTinyToggle() {
    if (button || !settings?.showFloatingToggle) return;

    button = document.createElement("button");
    button.id = "anti-click-popup-tabs-tiny-toggle";
    button.type = "button";

    Object.assign(button.style, {
      position: "fixed",
      right: "6px",
      bottom: "6px",
      zIndex: "2147483647",
      width: "34px",
      height: "18px",
      minWidth: "34px",
      minHeight: "18px",
      padding: "0",
      margin: "0",
      borderRadius: "999px",
      fontSize: "9px",
      lineHeight: "16px",
      fontFamily: "Arial, sans-serif",
      fontWeight: "700",
      textAlign: "center",
      cursor: "pointer",
      opacity: "0.55",
      boxShadow: "0 1px 4px rgba(0,0,0,.35)",
      userSelect: "none",
      pointerEvents: "auto"
    });

    button.addEventListener("mouseenter", () => {
      button.style.opacity = "1";
    });

    button.addEventListener("mouseleave", () => {
      button.style.opacity = "0.55";
    });

    button.addEventListener("click", async (event) => {
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
      await setSiteDisabled(isProtected());
    }, true);

    updateButton();

    const add = () => {
      if (!document.documentElement) return;
      if (!button.isConnected) {
        (document.body || document.documentElement).appendChild(button);
      }
    };

    add();

    const keepAlive = new MutationObserver(add);
    keepAlive.observe(document.documentElement, { childList: true, subtree: true });
  }

  function removeTinyToggle() {
    if (button) {
      button.remove();
      button = null;
    }
  }

  async function loadSettings() {
    const response = await chrome.runtime.sendMessage({ type: "GET_SETTINGS" });
    settings = response?.settings || {
      enabled: true,
      aggressiveMode: true,
      showFloatingToggle: true,
      disabledHosts: {}
    };

    if (settings.showFloatingToggle) {
      makeTinyToggle();
      updateButton();
    } else {
      removeTinyToggle();
    }
  }

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message?.type === "PING") {
      sendResponse({ ok: true });
      return;
    }

    if (message?.type === "SETTINGS_UPDATED") {
      settings = message.settings;
      if (settings?.showFloatingToggle) {
        makeTinyToggle();
        updateButton();
      } else {
        removeTinyToggle();
      }
    }
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", loadSettings, { once: true });
  } else {
    loadSettings();
  }
})();
