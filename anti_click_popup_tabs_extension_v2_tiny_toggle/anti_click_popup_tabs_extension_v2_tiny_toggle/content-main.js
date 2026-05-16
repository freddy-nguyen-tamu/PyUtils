(() => {
  "use strict";

  const FLAG = "__ANTI_CLICK_POPUP_TABS_INSTALLED__";
  if (window[FLAG]) return;
  Object.defineProperty(window, FLAG, { value: true });

  let enabled = true;
  let disabledHosts = {};

  function currentHost() {
    return location.hostname.replace(/^www\./, "");
  }

  function computeEnabled(settings) {
    disabledHosts = settings?.disabledHosts || {};
    enabled = !!settings?.enabled && !disabledHosts[currentHost()];
  }

  function refreshEnabled() {
    try {
      chrome.runtime.sendMessage({ type: "GET_SETTINGS" }, (response) => {
        if (response?.settings) computeEnabled(response.settings);
      });
    } catch {
      enabled = true;
    }
  }

  refreshEnabled();
  setInterval(refreshEnabled, 3000);

  function isModifiedClick(event) {
    return event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button === 1;
  }

  function normalizeTarget(el) {
    if (!el || !enabled) return;

    if (el.tagName === "A") {
      if (el.target && el.target.toLowerCase() !== "_self") {
        el.target = "_self";
      }
      el.removeAttribute("rel");
    }

    if (el.tagName === "FORM") {
      if (el.target && el.target.toLowerCase() !== "_self") {
        el.target = "_self";
      }
    }
  }

  function sweepTargets(root = document) {
    if (!enabled) return;
    try {
      root.querySelectorAll?.("a[target], form[target], area[target], base[target]").forEach((el) => {
        if (el.tagName === "BASE") {
          el.removeAttribute("target");
        } else {
          normalizeTarget(el);
        }
      });
    } catch {}
  }

  const originalOpen = window.open;
  Object.defineProperty(window, "open", {
    configurable: true,
    writable: true,
    value: function(url, target, features) {
      if (enabled) {
        console.warn("[Anti Click Popup Tabs] Blocked window.open:", url || "");
        return null;
      }
      return originalOpen.call(window, url, target, features);
    }
  });

  document.addEventListener("click", (event) => {
    if (!enabled || isModifiedClick(event)) return;

    const link = event.target?.closest?.("a, area");
    if (link) {
      normalizeTarget(link);

      const href = String(link.getAttribute("href") || "");
      if (/^\s*javascript:/i.test(href) && /window\.open|open\s*\(/i.test(href)) {
        event.preventDefault();
        event.stopImmediatePropagation();
        console.warn("[Anti Click Popup Tabs] Blocked javascript popup link.");
      }
    }
  }, true);

  document.addEventListener("submit", (event) => {
    if (!enabled) return;
    const form = event.target;
    if (form && form.tagName === "FORM") {
      normalizeTarget(form);
    }
  }, true);

  const observer = new MutationObserver((mutations) => {
    if (!enabled) return;
    for (const m of mutations) {
      if (m.type === "attributes" && (m.attributeName === "target" || m.attributeName === "rel")) {
        normalizeTarget(m.target);
      }
      for (const node of m.addedNodes || []) {
        if (node.nodeType === 1) sweepTargets(node);
      }
    }
  });

  function start() {
    sweepTargets();
    observer.observe(document.documentElement || document, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["target", "rel"]
    });
  }

  if (document.documentElement) start();
  else document.addEventListener("DOMContentLoaded", start, { once: true });
})();
