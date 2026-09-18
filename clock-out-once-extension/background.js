const STORAGE_KEY = "clockOutSchedule";
const ALARM_NAME = "oneTimeClockOut";

chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.clear(ALARM_NAME);
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "local" || !changes[STORAGE_KEY]) return;
  syncAlarm(changes[STORAGE_KEY].newValue);
});

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name !== ALARM_NAME) return;

  // Wake all Intern Hub content scripts. Only the tab that successfully
  // claims the schedule in this service worker may click the button.
  const tabs = await chrome.tabs.query({});
  await Promise.allSettled(
    tabs
      .filter(tab => Number.isInteger(tab.id))
      .map(tab => chrome.tabs.sendMessage(tab.id, { type: "CLOCK_OUT_DUE" }))
  );
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type !== "CLAIM_CLOCK_OUT") return;

  claimClockOut()
    .then(sendResponse)
    .catch(error => sendResponse({ granted: false, error: String(error) }));

  return true;
});

async function syncAlarm(schedule) {
  await chrome.alarms.clear(ALARM_NAME);

  if (!schedule?.enabled || schedule?.fired || !Number.isFinite(schedule?.targetMs)) {
    return;
  }

  chrome.alarms.create(ALARM_NAME, { when: schedule.targetMs });
}

async function claimClockOut() {
  const data = await chrome.storage.local.get(STORAGE_KEY);
  const schedule = data[STORAGE_KEY];

  if (!schedule?.enabled || schedule?.fired) {
    return { granted: false, reason: "not-armed" };
  }

  if (!Number.isFinite(schedule.targetMs) || Date.now() < schedule.targetMs) {
    return { granted: false, reason: "not-due" };
  }

  // Service-worker message handlers are serialized enough for this simple
  // single-extension lock: mark it fired before authorizing the page click.
  const fired = {
    ...schedule,
    enabled: false,
    fired: true,
    firedAtMs: Date.now()
  };

  await chrome.storage.local.set({ [STORAGE_KEY]: fired });
  await chrome.alarms.clear(ALARM_NAME);

  return { granted: true, schedule: fired };
}
