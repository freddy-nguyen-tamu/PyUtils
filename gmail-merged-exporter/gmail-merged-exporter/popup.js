const startButton = document.getElementById('start');
const stopButton = document.getElementById('stop');
const statusElement = document.getElementById('status');
const startFromFirstInput = document.getElementById('startFromFirst');
const maxPagesInput = document.getElementById('maxPages');
const delayMsInput = document.getElementById('delayMs');
const includePreviewInput = document.getElementById('includePreview');

function setStatus(message, isError = false) {
  statusElement.textContent = message;
  statusElement.style.color = isError ? '#b3261e' : '#202124';
}

async function getActiveGmailTab() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  const tab = tabs[0];

  if (!tab?.id || !tab.url?.startsWith('https://mail.google.com/')) {
    throw new Error('Open Gmail in the active tab first.');
  }

  return tab;
}

async function saveOptions(options) {
  await chrome.storage.local.set({ exporterOptions: options });
}

async function loadOptions() {
  const stored = await chrome.storage.local.get('exporterOptions');
  const options = stored.exporterOptions;
  if (!options) return;

  startFromFirstInput.checked = options.startFromFirst ?? true;
  maxPagesInput.value = options.maxPages ?? 0;
  delayMsInput.value = options.delayMs ?? 900;
  includePreviewInput.checked = options.includePreview ?? true;
}

function currentOptions() {
  return {
    startFromFirst: startFromFirstInput.checked,
    maxPages: Math.max(0, Number.parseInt(maxPagesInput.value, 10) || 0),
    delayMs: Math.min(10000, Math.max(300, Number.parseInt(delayMsInput.value, 10) || 900)),
    includePreview: includePreviewInput.checked
  };
}

async function refreshStatus() {
  try {
    const tab = await getActiveGmailTab();
    const response = await chrome.tabs.sendMessage(tab.id, { type: 'GMAIL_EXPORT_STATUS' });
    const running = Boolean(response?.running);
    startButton.disabled = running;
    stopButton.disabled = !running;
    setStatus(running ? (response.status || 'Export is running in the Gmail tab.') : 'Ready.');
  } catch (error) {
    startButton.disabled = false;
    stopButton.disabled = true;
    setStatus(error.message, true);
  }
}

startButton.addEventListener('click', async () => {
  try {
    const tab = await getActiveGmailTab();
    const options = currentOptions();
    await saveOptions(options);

    const response = await chrome.tabs.sendMessage(tab.id, {
      type: 'GMAIL_EXPORT_START',
      options
    });

    if (!response?.ok) {
      throw new Error(response?.error || 'Could not start the exporter. Reload Gmail and try again.');
    }

    startButton.disabled = true;
    stopButton.disabled = false;
    setStatus('Started. Progress is shown inside the Gmail tab.');
  } catch (error) {
    setStatus(error.message, true);
  }
});

stopButton.addEventListener('click', async () => {
  try {
    const tab = await getActiveGmailTab();
    await chrome.tabs.sendMessage(tab.id, { type: 'GMAIL_EXPORT_STOP' });
    setStatus('Stopping after the current step; partial results will be downloaded.');
  } catch (error) {
    setStatus(error.message, true);
  }
});

loadOptions().then(refreshStatus).catch((error) => setStatus(error.message, true));
