const STORAGE_KEY = "clockOutSchedule";

const targetInput = document.getElementById("target");
const setButton = document.getElementById("set");
const cancelButton = document.getElementById("cancel");
const statusBox = document.getElementById("status");
const statusLabel = document.getElementById("status-label");
const countdown = document.getElementById("countdown");
const targetLabel = document.getElementById("target-label");
const message = document.getElementById("message");

let renderTimer = null;

const pad2 = n => String(n).padStart(2, "0");

function toLocalInputValue(date) {
  return [
    date.getFullYear(),
    "-",
    pad2(date.getMonth() + 1),
    "-",
    pad2(date.getDate()),
    "T",
    pad2(date.getHours()),
    ":",
    pad2(date.getMinutes()),
    ":",
    pad2(date.getSeconds())
  ].join("");
}

function formatTarget(ms) {
  return new Date(ms).toLocaleString([], {
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
  const h = Math.floor((total % 86400) / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const clock = `${pad2(h)}:${pad2(m)}:${pad2(s)}`;
  return days ? `${days}d ${clock}` : clock;
}

async function getSchedule() {
  const data = await chrome.storage.local.get(STORAGE_KEY);
  return data[STORAGE_KEY] || null;
}

async function render() {
  const schedule = await getSchedule();

  if (!schedule) {
    statusBox.classList.add("hidden");
    cancelButton.disabled = true;
    return;
  }

  cancelButton.disabled = !schedule.enabled;

  if (schedule.fired) {
    statusBox.classList.remove("hidden");
    statusLabel.textContent = "Triggered";
    countdown.textContent = "CLOCKED OUT";
    targetLabel.textContent = Number.isFinite(schedule.targetMs)
      ? formatTarget(schedule.targetMs)
      : "";
    return;
  }

  if (!schedule.enabled) {
    statusBox.classList.add("hidden");
    return;
  }

  statusBox.classList.remove("hidden");
  statusLabel.textContent = "Armed";
  countdown.textContent = formatRemaining(schedule.targetMs - Date.now());
  targetLabel.textContent = formatTarget(schedule.targetMs);
}

setButton.addEventListener("click", async () => {
  message.className = "message";
  message.textContent = "";

  const targetMs = new Date(targetInput.value).getTime();

  if (!Number.isFinite(targetMs)) {
    message.classList.add("error");
    message.textContent = "Choose a valid date and time.";
    return;
  }

  if (targetMs <= Date.now()) {
    message.classList.add("error");
    message.textContent = "Choose a time in the future.";
    return;
  }

  await chrome.storage.local.set({
    [STORAGE_KEY]: {
      targetMs,
      enabled: true,
      fired: false,
      createdAtMs: Date.now()
    }
  });

  message.textContent = "Scheduled. Keep Intern Hub open and logged in so its Clock Out button can be clicked.";
  await render();
});

cancelButton.addEventListener("click", async () => {
  const schedule = await getSchedule();
  if (!schedule) return;

  await chrome.storage.local.set({
    [STORAGE_KEY]: {
      ...schedule,
      enabled: false,
      cancelledAtMs: Date.now()
    }
  });

  message.className = "message";
  message.textContent = "Schedule cancelled.";
  await render();
});

(async function init() {
  const defaultTime = new Date(Date.now() + 60 * 60 * 1000);
  defaultTime.setMilliseconds(0);
  targetInput.value = toLocalInputValue(defaultTime);

  await render();
  renderTimer = setInterval(render, 1000);
})();
