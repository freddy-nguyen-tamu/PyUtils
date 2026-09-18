(() => {
  const STORAGE_KEY = "clockOutSchedule";
  const PANEL_ID = "clock-out-once-panel";

  let timerId = null;
  let observer = null;
  let attempting = false;

  const pad2 = n => String(n).padStart(2, "0");

  function formatTarget(ms) {
    const d = new Date(ms);
    return d.toLocaleString([], {
      weekday: "short",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
      second: "2-digit"
    });
  }

  function formatRemaining(ms) {
    if (ms <= 0) return "00:00:00";

    const total = Math.ceil(ms / 1000);
    const days = Math.floor(total / 86400);
    const hours = Math.floor((total % 86400) / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const seconds = total % 60;

    const hhmmss = `${pad2(hours)}:${pad2(minutes)}:${pad2(seconds)}`;
    return days > 0 ? `${days}d ${hhmmss}` : hhmmss;
  }

  function isVisible(el) {
    if (!el) return false;
    const style = getComputedStyle(el);
    return style.display !== "none" &&
      style.visibility !== "hidden" &&
      style.opacity !== "0" &&
      el.getClientRects().length > 0;
  }

  function getReadyClockOutButton() {
    const button = document.getElementById("checkout-btn");
    if (!button || button.disabled || !isVisible(button)) return null;

    const originalText = (button.dataset.originalText || "").toLowerCase();
    const currentText = (button.textContent || "").toLowerCase();

    if (!originalText.includes("clock out") && !currentText.includes("clock out")) {
      return null;
    }

    return button;
  }

  function ensurePanel() {
    let panel = document.getElementById(PANEL_ID);
    if (panel) return panel;

    const actions = document.querySelector(".checkin-actions");
    const shiftAction = actions?.parentElement || document.querySelector(".shift-action");
    if (!shiftAction) return null;

    panel = document.createElement("div");
    panel.id = PANEL_ID;
    panel.style.cssText = [
      "display:none",
      "margin-top:12px",
      "padding:11px 12px",
      "border:1px solid #d8e4e3",
      "border-radius:12px",
      "background:#f7fbfb",
      "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif",
      "color:#1d1d1f"
    ].join(";");

    panel.innerHTML = `
      <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;">
        <div style="min-width:0;">
          <div style="font-size:11px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;color:#667085;">
            Scheduled Clock Out
          </div>
          <div id="clock-out-once-target" style="font-size:12px;color:#667085;margin-top:2px;"></div>
        </div>

        <div style="display:flex;align-items:center;gap:10px;margin-left:auto;">
          <div id="clock-out-once-countdown"
               style="font-variant-numeric:tabular-nums;font-size:18px;font-weight:800;letter-spacing:-.02em;white-space:nowrap;">
            --:--:--
          </div>
          <button id="clock-out-once-cancel"
                  type="button"
                  style="border:1px solid #d0d5dd;background:#fff;color:#344054;border-radius:8px;padding:6px 9px;font-size:11px;font-weight:700;cursor:pointer;">
            Cancel
          </button>
        </div>
      </div>
      <div id="clock-out-once-status" style="font-size:11px;color:#667085;margin-top:6px;line-height:1.4;"></div>
    `;

    if (actions) actions.insertAdjacentElement("afterend", panel);
    else shiftAction.appendChild(panel);

    panel.querySelector("#clock-out-once-cancel")?.addEventListener("click", async () => {
      const data = await chrome.storage.local.get(STORAGE_KEY);
      const schedule = data[STORAGE_KEY];
      if (!schedule) return;

      await chrome.storage.local.set({
        [STORAGE_KEY]: {
          ...schedule,
          enabled: false,
          cancelledAtMs: Date.now()
        }
      });
    });

    return panel;
  }

  async function render() {
    const panel = ensurePanel();
    if (!panel) return;

    const data = await chrome.storage.local.get(STORAGE_KEY);
    const schedule = data[STORAGE_KEY];

    if (!schedule) {
      panel.style.display = "none";
      return;
    }

    const targetEl = panel.querySelector("#clock-out-once-target");
    const countdownEl = panel.querySelector("#clock-out-once-countdown");
    const statusEl = panel.querySelector("#clock-out-once-status");
    const cancelEl = panel.querySelector("#clock-out-once-cancel");

    if (targetEl && Number.isFinite(schedule.targetMs)) {
      targetEl.textContent = `Target: ${formatTarget(schedule.targetMs)}`;
    }

    if (schedule.fired) {
      panel.style.display = "";
      if (countdownEl) countdownEl.textContent = "CLOCKED OUT";
      if (statusEl) statusEl.textContent = "The scheduled one-time Clock Out was triggered.";
      if (cancelEl) cancelEl.style.display = "none";
      return;
    }

    if (!schedule.enabled) {
      panel.style.display = "none";
      return;
    }

    panel.style.display = "";
    if (cancelEl) cancelEl.style.display = "";

    const remaining = schedule.targetMs - Date.now();
    if (countdownEl) countdownEl.textContent = formatRemaining(remaining);

    const button = getReadyClockOutButton();
    if (statusEl) {
      statusEl.textContent = button
        ? "Armed. This will click Clock Out once when the countdown reaches zero."
        : "Armed. Waiting for the Clock Out button to be available.";
    }

    if (remaining <= 0) {
      await tryClockOut();
    }
  }

  async function tryClockOut() {
    if (attempting) return;

    // Do not claim the one-time schedule until the real site button is present,
    // visible, enabled, and still represents Clock Out.
    const button = getReadyClockOutButton();
    if (!button) return;

    attempting = true;
    try {
      const result = await chrome.runtime.sendMessage({ type: "CLAIM_CLOCK_OUT" });
      if (!result?.granted) return;

      button.click();

      const panel = ensurePanel();
      if (panel) {
        panel.style.display = "";
        const countdownEl = panel.querySelector("#clock-out-once-countdown");
        const statusEl = panel.querySelector("#clock-out-once-status");
        const cancelEl = panel.querySelector("#clock-out-once-cancel");
        if (countdownEl) countdownEl.textContent = "CLOCKED OUT";
        if (statusEl) statusEl.textContent = "Clock Out clicked once.";
        if (cancelEl) cancelEl.style.display = "none";
      }
    } finally {
      attempting = false;
    }
  }

  function start() {
    ensurePanel();
    render();

    if (!timerId) {
      timerId = setInterval(render, 1000);
    }

    if (!observer) {
      observer = new MutationObserver(() => ensurePanel());
      observer.observe(document.documentElement, { childList: true, subtree: true });
    }
  }

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "local" && changes[STORAGE_KEY]) render();
  });

  chrome.runtime.onMessage.addListener(message => {
    if (message?.type === "CLOCK_OUT_DUE") {
      tryClockOut();
      render();
    }
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
})();
