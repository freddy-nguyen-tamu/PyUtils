'use strict';

const $ = id => document.getElementById(id);
let currentTab = null;
let refreshing = false;
let injectionInProgress = false;

async function findTab() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  currentTab = tabs[0] || null;
  const url = currentTab?.url || '';
  return currentTab && /^https:\/\/([a-z0-9-]+\.)?reddit\.com(?:\/|$)/i.test(url);
}

function send(message) {
  return new Promise(resolve => {
    try {
      chrome.tabs.sendMessage(currentTab.id, message, response => {
        if (chrome.runtime.lastError) return resolve(null);
        resolve(response || null);
      });
    } catch { resolve(null); }
  });
}

function showStatus(s) {
  $('downloaded').textContent = String(s.downloaded || 0);
  $('skipped').textContent = String(s.skipped || 0);
  $('position').textContent = s.slide ? `${s.slide}${s.total ? `/${s.total}` : ''}` : '—';
  $('status').textContent = s.status || 'Ready.';
  $('error').textContent = s.error || '';
  $('error').hidden = !s.error;
  $('start').hidden = !!s.running;
  $('stop').hidden = !s.running;
  $('delay').disabled = !!s.running;
}

async function refresh() {
  if (refreshing || injectionInProgress) return;
  refreshing = true;
  try {
    const supported = await findTab();
    if (!supported) {
      showStatus({status:'Open a Reddit post with an image gallery first.'});
      $('start').disabled = true;
      return;
    }
    $('start').disabled = false;
    const state = await send({ type: 'RCD_STATUS' });
    if (state) showStatus(state);
  } finally { refreshing = false; }
}

async function start() {
  if (injectionInProgress) return;
  injectionInProgress = true;
  $('start').disabled = true;
  $('status').textContent = 'Starting on the current Reddit tab…';
  $('error').hidden = true;
  try {
    if (!await findTab()) throw new Error('Open the Reddit gallery tab first.');
    await chrome.scripting.executeScript({ target: { tabId: currentTab.id }, files: ['content.js'] });
    const result = await send({ type: 'RCD_START', options: {delayMs: Number($('delay').value)} });
    if (!result?.ok) throw new Error('Cannot communicate with this tab. Reload Reddit and try again.');
    showStatus(result.state);
  } catch (error) {
    showStatus({status:'Could not start.', error:String(error?.message || error)});
  } finally {
    injectionInProgress = false;
    $('start').disabled = false;
  }
}

async function stop() {
  $('stop').disabled = true;
  $('status').textContent = 'Stopping after the active download…';
  await send({type:'RCD_STOP'});
  $('stop').disabled = false;
}

$('delay').addEventListener('input', () => {
  $('delayText').textContent = `${$('delay').value} ms`;
  chrome.storage.local.set({ rcdDelay: Number($('delay').value) });
});
$('start').addEventListener('click', start);
$('stop').addEventListener('click', stop);

(async () => {
  try {
    const prefs = await chrome.storage.local.get('rcdDelay');
    if (prefs.rcdDelay) $('delay').value = prefs.rcdDelay;
    $('delayText').textContent = `${$('delay').value} ms`;
  } catch {}
  await refresh();
  setInterval(refresh, 550);
})();
