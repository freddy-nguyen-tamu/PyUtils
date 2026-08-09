(() => {
  'use strict';

  if (globalThis.__gmailMergedTxtExporterLoaded) return;
  globalThis.__gmailMergedTxtExporterLoaded = true;

  const DEFAULT_OPTIONS = {
    startFromFirst: true,
    maxPages: 0,
    delayMs: 900,
    includePreview: true
  };

  const state = {
    running: false,
    abortRequested: false,
    status: 'Ready.',
    overlay: null,
    statusNode: null,
    stopButton: null
  };

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type === 'GMAIL_EXPORT_STATUS') {
      sendResponse({ running: state.running, status: state.status });
      return;
    }

    if (message?.type === 'GMAIL_EXPORT_STOP') {
      state.abortRequested = true;
      setStatus('Stop requested. Preparing the partial TXT file…');
      sendResponse({ ok: true });
      return;
    }

    if (message?.type === 'GMAIL_EXPORT_START') {
      if (state.running) {
        sendResponse({ ok: false, error: 'An export is already running in this tab.' });
        return;
      }

      const options = normalizeOptions(message.options);
      state.running = true;
      state.abortRequested = false;
      ensureOverlay();
      sendResponse({ ok: true });

      runExport(options)
        .catch((error) => {
          console.error('[Gmail Merged TXT Exporter]', error);
          setStatus(`Export failed: ${error.message}`);
          window.alert(`Gmail export failed: ${error.message}`);
        })
        .finally(() => {
          state.running = false;
          state.abortRequested = false;
          if (state.stopButton) state.stopButton.disabled = true;
        });

      return;
    }
  });

  function normalizeOptions(options = {}) {
    return {
      startFromFirst: options.startFromFirst ?? DEFAULT_OPTIONS.startFromFirst,
      maxPages: Math.max(0, Number.parseInt(options.maxPages, 10) || 0),
      delayMs: Math.min(10000, Math.max(300, Number.parseInt(options.delayMs, 10) || DEFAULT_OPTIONS.delayMs)),
      includePreview: options.includePreview ?? DEFAULT_OPTIONS.includePreview
    };
  }

  async function runExport(options) {
    const startedAt = new Date();
    const sourceUrl = location.href;
    const conversations = [];
    const seenThreadKeys = new Set();
    const seenMessageKeys = new Set();
    const warnings = [];
    let pagesScanned = 0;

    setStatus('Preparing Gmail…');

    if (options.startFromFirst) {
      await navigateToFirstPageIfNeeded();
    }

    await waitForThreadList(20000);

    while (!state.abortRequested) {
      if (options.maxPages > 0 && pagesScanned >= options.maxPages) break;

      pagesScanned += 1;
      let pageConversationCount = 0;
      setStatus(`Page ${pagesScanned}: finding conversations…`);

      while (!state.abortRequested) {
        const rows = getThreadRows();
        const nextEntry = rows
          .map((row, index) => ({ row, index, key: getThreadKey(row, index) }))
          .find((entry) => !seenThreadKeys.has(entry.key));

        if (!nextEntry) break;

        const { row, key } = nextEntry;
        const listPreview = cleanText(row.innerText);
        const unread = isUnreadRow(row);
        seenThreadKeys.add(key);

        const ordinal = conversations.length + 1;
        setStatus(`Page ${pagesScanned}: opening conversation ${pageConversationCount + 1}…`);

        try {
          clickThreadRow(row);
          await waitForThreadView(20000);
          await sleep(options.delayMs);
          await expandConversation();
          await sleep(Math.min(1200, options.delayMs));

          const extracted = await extractConversation({
            ordinal,
            sourcePage: pagesScanned,
            listPreview: options.includePreview ? listPreview : '',
            unread,
            seenMessageKeys
          });

          if (extracted.messages.length > 0) {
            conversations.push(extracted);
          } else {
            warnings.push(`No message body found for conversation opened from page ${pagesScanned}: ${listPreview.slice(0, 160)}`);
          }
        } catch (error) {
          warnings.push(`Conversation on page ${pagesScanned} could not be fully exported: ${error.message}`);
          console.warn('[Gmail Merged TXT Exporter] Conversation error', error);
        }

        pageConversationCount += 1;

        if (state.abortRequested) break;

        setStatus(`Page ${pagesScanned}: returning to the message list…`);
        await returnToThreadList();
        await sleep(options.delayMs);
      }

      if (state.abortRequested) break;
      if (options.maxPages > 0 && pagesScanned >= options.maxPages) break;

      const beforeSignature = getListSignature();
      const nextPageButton = findNextPageButton();
      if (!nextPageButton || isDisabled(nextPageButton)) break;

      setStatus(`Moving to page ${pagesScanned + 1}…`);
      clickElement(nextPageButton);

      try {
        await waitUntil(() => {
          const signature = getListSignature();
          return signature && signature !== beforeSignature;
        }, 20000, 250);
      } catch (error) {
        warnings.push(`Could not confirm that Gmail moved past page ${pagesScanned}. Export stopped to avoid duplicates.`);
        break;
      }

      await sleep(options.delayMs);
    }

    const finishedAt = new Date();
    const text = buildTxtExport({
      sourceUrl,
      startedAt,
      finishedAt,
      pagesScanned,
      conversations,
      warnings,
      stoppedEarly: state.abortRequested
    });

    setStatus('Creating the merged TXT download…');
    downloadText(text, makeFilename(finishedAt));

    const messageCount = conversations.reduce((sum, conversation) => sum + conversation.messages.length, 0);
    const ending = state.abortRequested ? 'Stopped; partial export downloaded.' : 'Finished; export downloaded.';
    setStatus(`${ending} ${conversations.length} conversations, ${messageCount} messages, ${pagesScanned} page(s).`);

    if (state.stopButton) state.stopButton.disabled = true;
  }

  async function navigateToFirstPageIfNeeded() {
    const currentHash = location.hash;
    const firstPageHash = currentHash.replace(/\/p\d+(?=\/?(?:\?|$))/i, '').replace(/\/p\d+$/i, '');

    if (firstPageHash === currentHash) return;

    setStatus('Returning to page 1…');
    location.hash = firstPageHash;
    await waitForThreadList(20000);
    await sleep(700);
  }

  function getThreadRows() {
    const selectors = [
      'tr[role="main"]',
      'tr.zA',
      'div[role="main"] tr'
    ];

    const candidates = Array.from(document.querySelectorAll(selectors.join(',')));
    return uniqueElements(candidates).filter((row) => {
      if (!isVisible(row)) return false;
      if (!cleanText(row.innerText)) return false;

      const hasCheckbox = Boolean(row.querySelector('[role="checkbox"], input[type="checkbox"]'));
      const hasSubjectLikeTarget = Boolean(row.querySelector('[role="link"], .bog, .y6, [data-thread-id]'));
      return hasCheckbox && hasSubjectLikeTarget;
    });
  }

  function getThreadKey(row, index) {
    const possibleIds = [
      row.getAttribute('data-legacy-thread-id'),
      row.getAttribute('data-thread-id'),
      row.getAttribute('data-legacy-message-id'),
      row.getAttribute('data-message-id'),
      row.id
    ].filter(Boolean);

    if (possibleIds.length > 0) return `id:${possibleIds.join(':')}`;

    const descendantId = row.querySelector('[data-legacy-thread-id], [data-thread-id], [data-message-id]');
    if (descendantId) {
      return `desc:${descendantId.getAttribute('data-legacy-thread-id') || descendantId.getAttribute('data-thread-id') || descendantId.getAttribute('data-message-id')}`;
    }

    return `fallback:${simpleHash(`${cleanText(row.innerText)}|${index}`)}`;
  }

  function clickThreadRow(row) {
    const target = row.querySelector('.bog, .y6, [role="link"], td[role="gridcell"]') || row;
    clickElement(target);
  }

  async function waitForThreadView(timeoutMs) {
    await waitUntil(() => {
      return getVisibleMessageBodies().length > 0 || Boolean(findBackToListButton()) || Boolean(findThreadSubject());
    }, timeoutMs, 200);
  }

  async function waitForThreadList(timeoutMs) {
    await waitUntil(() => getThreadRows().length > 0, timeoutMs, 250);
  }

  async function expandConversation() {
    const expandAll = findActionByLabels(['Expand all', 'Expand all messages']);
    if (expandAll && !isDisabled(expandAll)) {
      clickElement(expandAll);
      await sleep(700);
    }

    const trimmedButtons = Array.from(document.querySelectorAll('[aria-label], [data-tooltip]'))
      .filter(isVisible)
      .filter((element) => {
        const label = getElementLabel(element).toLowerCase();
        return label.includes('show trimmed content') || label.includes('view entire message');
      });

    for (const button of trimmedButtons) {
      clickElement(button);
      await sleep(150);
    }
  }

  async function extractConversation({ ordinal, sourcePage, listPreview, unread, seenMessageKeys }) {
    const subject = cleanText(findThreadSubject()?.innerText) || inferSubjectFromPreview(listPreview) || '(no subject)';
    const threadUrl = location.href;
    const messages = [];
    const bodyElements = getVisibleMessageBodies();

    for (let index = 0; index < bodyElements.length; index += 1) {
      const bodyElement = bodyElements[index];
      const block = findMessageBlock(bodyElement);
      const message = await extractMessage(block, bodyElement, index + 1);
      const messageKey = message.messageId || `fallback:${simpleHash(`${message.from}|${message.date}|${message.body}`)}`;

      if (seenMessageKeys.has(messageKey)) continue;
      seenMessageKeys.add(messageKey);
      messages.push(message);
    }

    return {
      ordinal,
      sourcePage,
      subject,
      threadUrl,
      listPreview,
      wasUnreadInList: unread,
      messages
    };
  }

  function getVisibleMessageBodies() {
    const candidates = Array.from(document.querySelectorAll([
      '.a3s',
      '[data-message-id] .ii.gt',
      '.adn.ads .ii.gt'
    ].join(',')));

    return uniqueElements(candidates).filter((element) => {
      if (!isVisible(element)) return false;
      const text = cleanText(element.innerText);
      return text.length > 0;
    }).filter((element, index, all) => {
      return !all.some((other, otherIndex) => otherIndex !== index && other.contains(element));
    });
  }

  function findMessageBlock(bodyElement) {
    return bodyElement.closest('[data-message-id], .adn.ads, div[role="listitem"]') || bodyElement.parentElement || bodyElement;
  }

  async function extractMessage(block, bodyElement, ordinal) {
    const messageIdElement = block.matches('[data-message-id]') ? block : block.querySelector('[data-message-id]');
    const messageId = messageIdElement?.getAttribute('data-message-id') || '';

    const senderElement = block.querySelector('.gD[email], [email]');
    const senderEmail = normalizeEmail(senderElement?.getAttribute('email')) || firstEmail(senderElement?.getAttribute('title')) || firstEmail(senderElement?.innerText);
    const senderName = cleanText(senderElement?.getAttribute('name') || senderElement?.innerText);

    const headerDetails = await readHeaderDetails(block);
    const allAddresses = uniqueStrings([
      ...getEmailsFromElements(block.querySelectorAll('[email]')),
      ...headerDetails.addresses,
      ...extractEmails(headerDetails.text)
    ]);

    const parsed = parseLabeledHeaderAddresses(headerDetails.text);
    const from = uniqueStrings([
      senderEmail,
      ...parsed.from
    ]).filter(Boolean);

    let to = parsed.to;
    let cc = parsed.cc;
    let bcc = parsed.bcc;

    if (to.length === 0 && cc.length === 0 && bcc.length === 0) {
      const senderSet = new Set(from.map((email) => email.toLowerCase()));
      to = allAddresses.filter((email) => !senderSet.has(email.toLowerCase()));
    }

    const dateElement = block.querySelector('.g3[title], [data-tooltip*="202"], span[title]');
    const date = cleanText(dateElement?.getAttribute('title') || dateElement?.innerText);
    const body = normalizeBodyText(bodyElement.innerText);

    return {
      ordinal,
      messageId,
      senderName,
      from,
      to: uniqueStrings(to),
      cc: uniqueStrings(cc),
      bcc: uniqueStrings(bcc),
      date,
      body
    };
  }

  async function readHeaderDetails(block) {
    const button = Array.from(block.querySelectorAll('[aria-label], [data-tooltip], [role="button"]'))
      .find((element) => {
        if (!isVisible(element)) return false;
        const label = getElementLabel(element).toLowerCase();
        return label.includes('show details') || label.includes('message details') || label === 'details';
      });

    if (!button) return { text: '', addresses: [] };

    const existingPanels = new Set(getVisibleDetailPanels());
    clickElement(button);
    await sleep(300);

    const panels = getVisibleDetailPanels().filter((panel) => !existingPanels.has(panel));
    const selectedPanels = panels.length > 0 ? panels : getVisibleDetailPanels();
    const text = selectedPanels.map((panel) => cleanText(panel.innerText)).filter(Boolean).join('\n');
    const addresses = uniqueStrings(selectedPanels.flatMap((panel) => getEmailsFromElements(panel.querySelectorAll('[email]'))));

    clickElement(button);
    await sleep(100);

    return { text, addresses };
  }

  function getVisibleDetailPanels() {
    const candidates = Array.from(document.querySelectorAll([
      '[role="dialog"]',
      '[role="tooltip"]',
      '.ajA',
      '.aHo',
      '.aHl'
    ].join(',')));

    return uniqueElements(candidates).filter((panel) => {
      if (!isVisible(panel)) return false;
      const text = cleanText(panel.innerText).toLowerCase();
      return text.includes('from:') || text.includes('to:') || panel.querySelector('[email]');
    });
  }

  function parseLabeledHeaderAddresses(text) {
    const result = { from: [], to: [], cc: [], bcc: [] };
    if (!text) return result;

    const normalized = text.replace(/\r/g, '');
    const pattern = /(?:^|\n)\s*(from|to|cc|bcc)\s*:\s*([\s\S]*?)(?=\n\s*(?:from|to|cc|bcc|date|subject|mailed-by|signed-by|reply-to|security)\s*:|$)/gi;
    let match;

    while ((match = pattern.exec(normalized)) !== null) {
      const label = match[1].toLowerCase();
      result[label].push(...extractEmails(match[2]));
    }

    for (const key of Object.keys(result)) {
      result[key] = uniqueStrings(result[key]);
    }

    return result;
  }

  async function returnToThreadList() {
    const button = findBackToListButton();
    if (button) {
      clickElement(button);
    } else {
      history.back();
    }

    await waitForThreadList(20000);
  }

  function findBackToListButton() {
    return findActionByLabels([
      'Back to Inbox',
      'Back to Sent',
      'Back to Drafts',
      'Back to All Mail',
      'Back to Spam',
      'Back to Trash',
      'Back to search results',
      'Back'
    ], true);
  }

  function findNextPageButton() {
    const candidates = Array.from(document.querySelectorAll('[aria-label], [data-tooltip], [title]')).filter(isVisible);

    return candidates.find((element) => {
      const label = getElementLabel(element).trim().toLowerCase();
      if (!label) return false;
      return label === 'older' || label === 'next page' || label === 'next' || label.startsWith('older ');
    }) || null;
  }

  function findActionByLabels(labels, prefixMatch = false) {
    const normalizedLabels = labels.map((label) => label.toLowerCase());
    const candidates = Array.from(document.querySelectorAll('[aria-label], [data-tooltip], [title], [role="button"]')).filter(isVisible);

    return candidates.find((element) => {
      const value = getElementLabel(element).toLowerCase();
      return normalizedLabels.some((label) => prefixMatch ? value.startsWith(label) : value === label || value.includes(label));
    }) || null;
  }

  function findThreadSubject() {
    const selectors = [
      'h2.hP',
      'h2[data-thread-perm-id]',
      'div[role="main"] h2'
    ];

    return Array.from(document.querySelectorAll(selectors.join(','))).find((element) => isVisible(element) && cleanText(element.innerText)) || null;
  }

  function getListSignature() {
    return getThreadRows().slice(0, 8).map((row, index) => getThreadKey(row, index)).join('|');
  }

  function inferSubjectFromPreview(preview) {
    if (!preview) return '';
    const lines = preview.split('\n').map(cleanText).filter(Boolean);
    return lines.slice(1, 3).join(' — ').slice(0, 300);
  }

  function isUnreadRow(row) {
    const className = String(row.className || '');
    const ariaLabel = String(row.getAttribute('aria-label') || '').toLowerCase();
    return /\bzE\b/.test(className) || ariaLabel.includes('unread') || Boolean(row.querySelector('[aria-label*="unread" i]'));
  }

  function isDisabled(element) {
    return element.getAttribute('aria-disabled') === 'true' ||
      element.hasAttribute('disabled') ||
      String(element.className || '').includes('T-I-JO');
  }

  function clickElement(element) {
    if (!element) throw new Error('The expected Gmail control was not found.');

    element.scrollIntoView({ block: 'center', inline: 'center' });
    for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
      element.dispatchEvent(new MouseEvent(type, {
        bubbles: true,
        cancelable: true,
        view: window,
        button: 0
      }));
    }
  }

  function getElementLabel(element) {
    return cleanText(
      element.getAttribute('aria-label') ||
      element.getAttribute('data-tooltip') ||
      element.getAttribute('title') ||
      element.innerText
    );
  }

  function getEmailsFromElements(elements) {
    return uniqueStrings(Array.from(elements).flatMap((element) => {
      return [
        normalizeEmail(element.getAttribute('email')),
        ...extractEmails(element.getAttribute('title') || ''),
        ...extractEmails(element.innerText || '')
      ].filter(Boolean);
    }));
  }

  function firstEmail(value) {
    return extractEmails(value || '')[0] || '';
  }

  function extractEmails(value) {
    const matches = String(value || '').match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi) || [];
    return uniqueStrings(matches.map(normalizeEmail).filter(Boolean));
  }

  function normalizeEmail(value) {
    return String(value || '').trim().replace(/^mailto:/i, '').replace(/[<>;,]+$/g, '').toLowerCase();
  }

  function normalizeBodyText(value) {
    return String(value || '')
      .replace(/\u00a0/g, ' ')
      .replace(/[ \t]+\n/g, '\n')
      .replace(/\n{4,}/g, '\n\n\n')
      .trim();
  }

  function buildTxtExport({ sourceUrl, startedAt, finishedAt, pagesScanned, conversations, warnings, stoppedEarly }) {
    const totalMessages = conversations.reduce((sum, conversation) => sum + conversation.messages.length, 0);
    const lines = [
      'GMAIL MERGED TXT EXPORT',
      `Generated: ${finishedAt.toISOString()}`,
      `Started: ${startedAt.toISOString()}`,
      `Source URL at start: ${sourceUrl}`,
      `Pages scanned: ${pagesScanned}`,
      `Conversations exported: ${conversations.length}`,
      `Messages exported: ${totalMessages}`,
      `Stopped early: ${stoppedEarly ? 'Yes' : 'No'}`,
      '',
      '='.repeat(96),
      ''
    ];

    conversations.forEach((conversation, conversationIndex) => {
      lines.push(
        `CONVERSATION ${conversationIndex + 1}`,
        `Subject: ${conversation.subject}`,
        `Thread URL: ${conversation.threadUrl}`,
        `Source page: ${conversation.sourcePage}`,
        `Originally unread in list: ${conversation.wasUnreadInList ? 'Yes' : 'No'}`
      );

      if (conversation.listPreview) {
        lines.push('Inbox/list preview:', conversation.listPreview);
      }

      lines.push('', '-'.repeat(96), '');

      conversation.messages.forEach((message, messageIndex) => {
        const senderDisplay = formatSender(message.senderName, message.from);
        lines.push(
          `MESSAGE ${conversationIndex + 1}.${messageIndex + 1}`,
          `Message ID: ${message.messageId || '(not available from Gmail DOM)'}`,
          `From: ${senderDisplay || '(not found)'}`,
          `To: ${message.to.join(', ') || '(not found)'}`,
          `Cc: ${message.cc.join(', ') || '(none found)'}`,
          `Bcc: ${message.bcc.join(', ') || '(none found)'}`,
          `Date: ${message.date || '(not found)'}`,
          '',
          'Body:',
          message.body || '(empty body)',
          '',
          '-'.repeat(96),
          ''
        );
      });

      lines.push('='.repeat(96), '');
    });

    if (warnings.length > 0) {
      lines.push('EXPORT WARNINGS', ...warnings.map((warning, index) => `${index + 1}. ${warning}`), '');
    }

    lines.push(
      'NOTE',
      'This file was produced by reading the Gmail web interface. Gmail can change its page structure at any time, so verify important exports.',
      ''
    );

    return lines.join('\n');
  }

  function formatSender(name, fromAddresses) {
    const addresses = uniqueStrings(fromAddresses || []);
    if (name && addresses.length === 1 && !name.toLowerCase().includes(addresses[0].toLowerCase())) {
      return `${name} <${addresses[0]}>`;
    }
    if (addresses.length > 0) return addresses.join(', ');
    return name || '';
  }

  function makeFilename(date) {
    const timestamp = date.toISOString().replace(/[:.]/g, '-');
    return `gmail-merged-export-${timestamp}.txt`;
  }

  function downloadText(text, filename) {
    const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    anchor.style.display = 'none';
    document.documentElement.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 60000);
  }

  function ensureOverlay() {
    if (state.overlay?.isConnected) {
      state.stopButton.disabled = false;
      return;
    }

    const overlay = document.createElement('section');
    overlay.id = 'gmail-merged-exporter-progress';
    Object.assign(overlay.style, {
      position: 'fixed',
      right: '18px',
      bottom: '18px',
      zIndex: '2147483647',
      width: '330px',
      padding: '14px',
      border: '1px solid rgba(60,64,67,.25)',
      borderRadius: '10px',
      background: '#ffffff',
      boxShadow: '0 4px 18px rgba(60,64,67,.3)',
      color: '#202124',
      fontFamily: 'Arial, Helvetica, sans-serif',
      fontSize: '13px',
      lineHeight: '1.4'
    });

    const title = document.createElement('strong');
    title.textContent = 'Gmail TXT export';
    title.style.display = 'block';
    title.style.marginBottom = '7px';

    const status = document.createElement('div');
    status.textContent = state.status;
    status.style.wordBreak = 'break-word';

    const stopButton = document.createElement('button');
    stopButton.textContent = 'Stop and download partial export';
    Object.assign(stopButton.style, {
      marginTop: '10px',
      width: '100%',
      padding: '8px',
      border: '0',
      borderRadius: '6px',
      background: '#fce8e6',
      color: '#b3261e',
      fontWeight: '700',
      cursor: 'pointer'
    });
    stopButton.addEventListener('click', () => {
      state.abortRequested = true;
      stopButton.disabled = true;
      setStatus('Stop requested. Preparing the partial TXT file…');
    });

    overlay.append(title, status, stopButton);
    document.documentElement.appendChild(overlay);

    state.overlay = overlay;
    state.statusNode = status;
    state.stopButton = stopButton;
  }

  function setStatus(message) {
    state.status = message;
    if (state.statusNode) state.statusNode.textContent = message;
  }

  function isVisible(element) {
    if (!(element instanceof Element)) return false;
    const style = window.getComputedStyle(element);
    if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return false;
    const rect = element.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }

  function uniqueElements(elements) {
    return Array.from(new Set(elements));
  }

  function uniqueStrings(values) {
    const seen = new Set();
    const output = [];

    for (const value of values || []) {
      const cleaned = cleanText(value);
      if (!cleaned) continue;
      const key = cleaned.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      output.push(cleaned);
    }

    return output;
  }

  function cleanText(value) {
    return String(value || '').replace(/\u00a0/g, ' ').replace(/[ \t]+/g, ' ').trim();
  }

  function simpleHash(value) {
    let hash = 2166136261;
    const text = String(value || '');
    for (let index = 0; index < text.length; index += 1) {
      hash ^= text.charCodeAt(index);
      hash = Math.imul(hash, 16777619);
    }
    return (hash >>> 0).toString(16);
  }

  function sleep(milliseconds) {
    return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
  }

  function waitUntil(predicate, timeoutMs = 15000, intervalMs = 200) {
    return new Promise((resolve, reject) => {
      const started = Date.now();

      const check = () => {
        try {
          if (predicate()) {
            resolve();
            return;
          }
        } catch (error) {
          reject(error);
          return;
        }

        if (Date.now() - started >= timeoutMs) {
          reject(new Error(`Timed out after ${Math.round(timeoutMs / 1000)} seconds while waiting for Gmail.`));
          return;
        }

        window.setTimeout(check, intervalMs);
      };

      check();
    });
  }
})();
