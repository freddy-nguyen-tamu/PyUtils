/* Gmail UI adapters. Fails closed when elements or navigation cannot be verified. */
(function(root) {
  'use strict';
  const SELECTORS = {
    rows: 'tr.zA[data-legacy-thread-id],tr.zA[data-thread-id],[role="main"] tr.zA',
    select: ['.oZ-jc[role="checkbox"]', 'td.oZ-x3 [role="checkbox"]', 'td:first-child [role="checkbox"]', '.oZ-jc'],
    delete: [
      '[gh="mtb"] [data-tooltip="Delete"]',
      '[gh="mtb"] [aria-label="Delete"]',
      '[role="main"] [gh="mtb"] [data-tooltip="Delete"]',
      '[role="main"] [gh="mtb"] [aria-label="Delete"]',
      '[gh="mtb"] [act="10"]'
    ],
    older: [
      '[role="main"] [data-tooltip="Older"]', '[role="main"] [aria-label="Older"]',
      '[data-tooltip="Older"]', '[aria-label="Older"]',
      '[role="main"] [data-tooltip="Next page"]', '[role="main"] [aria-label="Next page"]'
    ]
  };
  function searchHash(query) {return '#search/' + encodeURIComponent(query);}
  function currentAccountPath(loc = root.location) {
    const match = String(loc.pathname).match(/\/(mail\/u\/[^/]+)\//);
    if (!match) throw Error('Open a standard Gmail tab (mail.google.com/mail/u/0/).');
    return match[1];
  }
  function accountKey(loc = root.location) {return `${loc.origin}/${currentAccountPath(loc)}`;}
  function pageRows(doc = root.document) {
    const nodes = Array.from(doc.querySelectorAll(SELECTORS.rows));
    const used = new Set();
    return nodes.filter(row => {
      const id = row.getAttribute('data-legacy-thread-id') || row.getAttribute('data-thread-id');
      if (!id || !/^[a-zA-Z0-9:_.-]{6,150}$/.test(id) || used.has(id)) return false;
      used.add(id);
      return true;
    }).map(row => ({
      id: row.getAttribute('data-legacy-thread-id') || row.getAttribute('data-thread-id'),
      node: row,
      sender: (row.querySelector('.yW [email]')?.getAttribute('email') || row.querySelector('.yW')?.textContent || '').trim().slice(0,120),
      subject: (row.querySelector('.bog')?.textContent || row.querySelector('.y6')?.textContent || '').trim().slice(0,180)
    }));
  }
  function visible(element) {
    if (!element || !element.isConnected) return false;
    const r = element.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(element).visibility !== 'hidden';
  }
  function firstVisible(doc, selectors) {
    for (const selector of selectors) {
      const list = doc.querySelectorAll(selector);
      for (const element of list) if (visible(element)) return element;
    }
    return null;
  }
  function isDisabled(el) {
    return !el || el.getAttribute('aria-disabled') === 'true' || el.hasAttribute('disabled') ||
      el.classList.contains('T-I-JW') || el.classList.contains('amJ-JW');
  }
  function rowCheckbox(row) {
    const checkbox = firstVisible(row, SELECTORS.select);
    if (!checkbox) throw Error('Gmail row checkbox not found; stopping to protect your mail.');
    const label = (checkbox.getAttribute('aria-label') || '').toLowerCase();
    if (label.includes('star') || label.includes('important')) throw Error('Unsafe checkbox match; nothing deleted.');
    return checkbox;
  }
  function checked(cb) {
    const value = cb.getAttribute('aria-checked');
    if (value === 'true') return true;
    if (value === 'false') return false;
    return cb.classList.contains('T-Jo-Jp') || Boolean(cb.querySelector('.T-Jo-Jp'));
  }
  function canVerifyChecked(cb) {
    return cb.hasAttribute('aria-checked') || cb.classList.contains('T-Jo') || cb.classList.contains('oZ-jc');
  }
  function verifyCanSelect(rows) {
    for (const {node} of rows) {
      const checkbox = rowCheckbox(node);
      if (!canVerifyChecked(checkbox)) throw Error('Gmail checkbox state is unavailable; stopping.');
      if (checked(checkbox)) throw Error('A conversation is already selected in Gmail. Deselect it before continuing.');
    }
  }
  async function selectExactly(targetIds, rows, sleeper) {
    const target = new Set(targetIds);
    verifyCanSelect(rows);
    for (const {id, node} of rows) {
      if (!target.has(id)) continue;
      const checkbox = rowCheckbox(node);
      checkbox.click();
      await sleeper(60);
      if (!checked(checkbox)) throw Error('Gmail did not select the expected row. Nothing was trashed.');
    }
    for (const {id, node} of rows) {
      const cb = rowCheckbox(node);
      if (checked(cb) !== target.has(id)) throw Error('Gmail selection did not match the safe thread list. Refusing to delete.');
    }
  }
  function deleteButton(doc = root.document) {
    return firstVisible(doc, SELECTORS.delete);
  }
  function olderButton(doc = root.document) {
    const el = firstVisible(doc, SELECTORS.older);
    return isDisabled(el) ? null : el;
  }
  function isEmpty(doc = root.document) {
    const main = doc.querySelector('[role="main"]');
    if (!main) return false;
    const txt = (main.textContent || '').replace(/\s+/g, ' ');
    // A missing page is never assumed empty: only known Gmail empty-state messages.
    return /no conversations found|no results found|no messages matched your search|no conversations in|there are no conversations|your search.*did not match any/i.test(txt);
  }
  function paginationStatus(doc = root.document) {
    const main = doc.querySelector('[role="main"]');
    if (!main) return null;
    const text = (main.textContent || '').replace(/\s+/g, ' ');
    const matches = [...text.matchAll(/([\d,]+)\s*[–—-]\s*([\d,]+)\s+of\s+([\d,]+|many)/gi)];
    if (!matches.length) return null;
    const match = matches[0];
    const toNumber = s => Number(String(s).replace(/,/g, ''));
    return {start:toNumber(match[1]), end:toNumber(match[2]), total:match[3].toLowerCase()==='many'?null:toNumber(match[3])};
  }
  function pageSignature(rows = pageRows()) {return rows.map(row=>row.id).join('|');}
  const api = {SELECTORS, searchHash, accountKey, currentAccountPath, pageRows, visible, firstVisible, isDisabled, rowCheckbox,
    checked, canVerifyChecked, verifyCanSelect, selectExactly, deleteButton, olderButton, isEmpty, paginationStatus, pageSignature};
  root.GKGmail = api;
  if (typeof module !== 'undefined') module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
