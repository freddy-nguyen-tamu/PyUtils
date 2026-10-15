/* Entirely UI-based Gmail thread protection + trash. No OAuth, API, or fetch calls. */
(() => {
  'use strict';
  if (window.top !== window || window.__GK_BROWSER_CLEANER__) return;
  window.__GK_BROWSER_CLEANER__ = true;
  const Gmail = globalThis.GKGmail;
  const {parseRules} = globalThis.GKRules;
  const DEFAULT_RULES = 'friend@example.com\n@mycompany.com\nsubject:"important project"';
  const STORAGE_PREFIX = 'gmail_keepclean_v2:';
  const TTL = 20 * 60 * 1000;
  const MAX_PAGES = 20000;
  let running = false;
  let stopRequested = false;
  let preview = null;
  let screen = null;
  let accountKey = null;
  let logEntries = [];
  let extensionSelectionActive = false;

  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  function abortIfStopped() {
    if (stopRequested) throw new Error('Paused. No further Gmail actions will be performed.');
    if (Gmail.accountKey() !== accountKey) throw new Error('Gmail account tab changed. Stopped.');
  }
  function ensureNotModal() {
    for (const el of document.querySelectorAll('[role="alertdialog"], dialog[open]')) {
      if (Gmail.visible(el)) throw new Error('Gmail has a confirmation dialog open. Review it manually; automation stopped.');
    }
  }
  const esc = html => String(html).replace(/[&<>"']/g, v => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[v]));
  const short = (n) => Number(n).toLocaleString();
  function log(text, type='info') {
    logEntries.push({text, type, at:new Date().toLocaleTimeString()});
    if (logEntries.length > 120) logEntries.shift();
    if (!screen) return;
    const target = screen.querySelector('#gk-log');
    if (target) {
      target.textContent = '';
      for (const msg of logEntries.slice(-8)) {
        const line = document.createElement('div');
        line.className = 'gk-log-' + msg.type;
        line.textContent = `${msg.at}  ${msg.text}`;
        target.append(line);
      }
      target.scrollTop = target.scrollHeight;
    }
  }
  function status(value) {
    if (screen) screen.querySelector('#gk-status').textContent = value;
  }
  function buttonState() {
    if (!screen) return;
    const fresh = preview && preview.path === accountKey && preview.rules === screen.querySelector('#gk-rules').value && preview.scope === screen.querySelector('#gk-scope').value && Date.now() - preview.createdAt < TTL;
    screen.querySelector('#gk-scan').disabled = running;
    screen.querySelector('#gk-trash').disabled = running || !fresh;
    screen.querySelector('#gk-stop').disabled = !running;
    screen.querySelector('#gk-rules').disabled = running;
    screen.querySelector('#gk-scope').disabled = running;
    screen.querySelector('#gk-close').disabled = running;
    screen.querySelector('#gk-ack').disabled = running;
    screen.querySelector('#gk-confirm').disabled = running;
  }
  function setPreview(data) {
    preview = data;
    const box = screen.querySelector('#gk-preview');
    if (!data) {box.hidden = true; buttonState(); return;}
    box.hidden = false;
    screen.querySelector('#gk-preview-time').textContent = new Date(data.createdAt).toLocaleTimeString();
    screen.querySelector('#gk-protected').textContent = short(data.protectedCount);
    screen.querySelector('#gk-deletable').textContent = short(data.deletableCount);
    screen.querySelector('#gk-total').textContent = short(data.totalCount);
    const list = screen.querySelector('#gk-samples');
    list.textContent = '';
    for (const x of data.samples) {
      const el = document.createElement('div');
      el.className = 'gk-sample';
      el.textContent = `${x.sender || 'Unknown sender'} — ${x.subject || '(no subject)'}`;
      list.append(el);
    }
    buttonState();
  }
  async function loadSettings() {
    const saved = await chrome.storage.local.get(STORAGE_PREFIX + accountKey);
    const data = saved[STORAGE_PREFIX + accountKey];
    if (!screen || !data) return;
    screen.querySelector('#gk-rules').value = data.rules ?? '';
    screen.querySelector('#gk-scope').value = data.scope || 'all';
  }
  async function saveSettings() {
    await chrome.storage.local.set({[STORAGE_PREFIX + accountKey]:{
      rules:screen.querySelector('#gk-rules').value,
      scope:screen.querySelector('#gk-scope').value
    }});
  }
  function mount() {
    if (document.querySelector('#gk-root')) return;
    const chip = document.createElement('button');
    chip.id = 'gk-launcher';
    chip.type = 'button';
    chip.textContent = '✦ Keep & Clean';
    chip.addEventListener('click', () => toggle());
    document.documentElement.appendChild(chip);
    screen = document.createElement('section');
    screen.id = 'gk-root';
    screen.hidden = true;
    screen.setAttribute('aria-label', 'Gmail Keep & Clean controls');
    // Gmail enforces Trusted Types. Build extension UI without innerHTML.
    const element = (tag, className='', text='', attrs={}) => {
      const n = document.createElement(tag);
      if (className) n.className = className;
      if (text) n.textContent = text;
      for (const [name,value] of Object.entries(attrs)) {
        if (name === 'id') n.id = value;
        else n.setAttribute(name, value);
      }
      return n;
    };
    const append = (parent, ...children) => {parent.append(...children); return parent;};
    const heading = element('header','gk-head');
    const brand = append(element('div','gk-title'), element('span','gk-symbol','✦'),append(element('div'), element('strong','','Keep & Clean'),element('small','','Runs inside your signed-in Gmail tab')));
    append(heading, brand, element('button','gk-icon-button','×',{type:'button',id:'gk-close','aria-label':'Close'}));
    const body = element('div','gk-content');
    const notice = element('div','gk-notice');
    append(notice,element('b','','No OAuth or API setup. '),document.createTextNode('This extension uses Gmail’s own controls and protects whole conversations by thread ID instead of relying on unsafe negative search exclusions.'));
    const label = (target,title) => element('label','gk-label',title,{for:target});
    const textarea = element('textarea','', '', {id:'gk-rules', spellcheck:'false',placeholder:'person@example.com\n@company.com\nsubject:"important"\nquery:label:Receipts'});
    const helper = element('p','gk-help','One rule per line. Email addresses match sender or recipient; @domain, subject:, label:, is:starred, and query:… are supported.');
    const scope = append(element('select','', '',{id:'gk-scope'}), element('option','','All Mail — inbox, sent and archives',{value:'all'}),element('option','','Inbox only',{value:'inbox'}));
    const draftCheck = element('input','', '',{type:'checkbox',id:'gk-drafts',checked:'',disabled:''});
    const draftLine = append(element('label','gk-toggle'),draftCheck,document.createTextNode(' Always protect conversations with drafts'));
    const buttons = append(element('div','gk-buttons'),element('button','gk-primary','1. Scan & preview',{type:'button',id:'gk-scan'}),element('button','gk-muted','Pause',{type:'button',id:'gk-stop',disabled:''}));
    const statusLine = append(element('div','gk-statusline'),element('span','gk-dot'),element('span','','Ready. Your mail has not been changed.',{id:'gk-status'}));
    const pv = element('div','', '',{id:'gk-preview',hidden:''});
    const pvHeading = append(element('div','gk-preview-heading'),element('span','','Preview'),append(element('small'),document.createTextNode('scanned '),element('span','','',{id:'gk-preview-time'})));
    const stats = element('div','gk-stats');
    for (const [id,labelText] of [['gk-protected','Protected'],['gk-deletable','To Trash'],['gk-total','Checked']]) {
      append(stats,append(element('div'),element('strong','','0',{id}),element('span','',labelText)));
    }
    const warning = element('p','gk-warning','Review carefully. Scans are snapshots: mail arriving after the preview may not be protected. Gmail normally removes mail from Trash after 30 days. The extension never permanently deletes anything.');
    const ack = append(element('label','gk-toggle gk-confirm-line'),element('input','', '',{type:'checkbox',id:'gk-ack'}),document.createTextNode(' I checked my rules and preview, and want to move the other conversations to Trash.'));
    const typing = element('input','', '',{type:'text',id:'gk-confirm',autocomplete:'off',placeholder:'MOVE TO TRASH'});
    append(pv,pvHeading,stats,element('div','gk-sample-heading','Examples that would be trashed'),element('div','', '',{id:'gk-samples'}),warning,ack,label('gk-confirm','Type MOVE TO TRASH to proceed'),typing,element('button','gk-danger','2. Trash unprotected threads',{type:'button',id:'gk-trash'}));
    append(body,notice,label('gk-rules','Keep entire conversations matching these rules'),textarea,helper,label('gk-scope','Where to clean'),scope,draftLine,buttons,statusLine,pv,element('div','gk-log-heading','Activity'),element('div','', '',{id:'gk-log',role:'status','aria-live':'polite'}),element('footer','','Gmail conversation view must be ON. Leave this tab open and do not click in Gmail while running. No external services.'));
    append(screen,heading,body);
    document.documentElement.append(screen);
    screen.querySelector('#gk-close').addEventListener('click', () => toggle(false));
    screen.querySelector('#gk-stop').addEventListener('click', () => {stopRequested = true; status('Pausing after the current safe step…'); log('Pause requested.');});
    screen.querySelector('#gk-scan').addEventListener('click', () => executePreview());
    screen.querySelector('#gk-trash').addEventListener('click', () => executeDeletion());
    screen.querySelector('#gk-rules').addEventListener('input', () => {setPreview(null); saveSettings().catch(()=>{});});
    screen.querySelector('#gk-scope').addEventListener('change', () => {setPreview(null); saveSettings().catch(()=>{});});
    try {accountKey = Gmail.accountKey(); loadSettings().catch(e => log(e.message, 'error'));}
    catch (e) {status(e.message);}
    buttonState();
  }
  function toggle(force) {
    if (!screen) mount();
    if (running && force === false) return;
    const open = (force === undefined) ? screen.hidden : force;
    screen.hidden = !open;
    document.querySelector('#gk-launcher').hidden = open;
  }
  async function until(check, message, limit = 30000) {
    const started = Date.now();
    let old = null;
    let stableSince = 0;
    while (Date.now() - started < limit) {
      abortIfStopped();
      ensureNotModal();
      const state = check();
      if (state) {
        const signature = state.signature || JSON.stringify(state);
        if (signature === old) {
          if (Date.now() - stableSince > 420) return state;
        } else {old = signature; stableSince = Date.now();}
      } else {old = null; stableSince = 0;}
      await wait(130);
    }
    throw new Error(message + ' — Gmail did not load as expected; stopped without guessing.');
  }
  function currentView(query) {
    const rows = Gmail.pageRows();
    const route = Gmail.searchHash(query);
    const hash = decodeURI(location.hash || '');
    // Newer Gmail versions append a page segment when moving through search pages.
    const expected = decodeURI(route);
    if (!(hash === expected || hash.startsWith(expected + '/'))) return false;
    const search = document.querySelector('input[name="q"], form[role="search"] input');
    if (!search || search.value.trim() !== query.trim()) return false;
    if (rows.length) return {rows, signature:Gmail.pageSignature(rows)};
    if (Gmail.isEmpty()) return {rows:[], empty:true, signature:'empty'};
    return false;
  }
  async function goSearch(query) {
    abortIfStopped();
    const destination = Gmail.searchHash(query);
    // Force navigating back to the FIRST page even after the prior query was paged.
    if (location.hash === destination) {
      location.hash = '#all';
      await wait(240);
    }
    location.hash = destination;
    await wait(550);
    return until(() => currentView(query), 'Search results unavailable for ' + query);
  }
  async function nextPage(query, signature) {
    abortIfStopped();
    let button = Gmail.olderButton();
    if (!button) {
      const status = Gmail.paginationStatus();
      if (status?.total != null && status.end >= status.total) return false;
      if (!Gmail.pageRows().length && Gmail.isEmpty()) return false;
      // Unknown last page state -> don't silently omit protected conversations.
      throw new Error('Cannot verify Gmail reached the final results page. Check English-language Gmail UI and retry.');
    }
    button.click();
    await wait(320);
    await until(() => {
      const view = currentView(query);
      return view && view.signature !== signature ? view : false;
    }, 'Next Gmail page was not confirmed');
    return true;
  }
  async function scanPages(query, onPage, label) {
    const start = await goSearch(query);
    let pages = 0;
    let view = start;
    while (true) {
      abortIfStopped();
      if (++pages > MAX_PAGES) throw Error('Safety cap reached (20,000 Gmail pages). No mail deleted.');
      if (view.empty) {
        log(`${label}: no results`);
        return;
      }
      if (!view.rows.length) throw Error('Gmail has no readable conversation rows. Stopped for safety.');
      await onPage(view.rows, pages);
      if (pages % 5 === 0) log(`${label}: scanned ${pages} pages`);
      const before = view.signature;
      if (!await nextPage(query, before)) {
        log(`${label}: finished (${pages} page${pages===1?'':'s'})`);
        return;
      }
      view = await until(() => currentView(query), 'Page failed to render');
    }
  }
  function run(taskName, fn) {
    if (running) return;
    running = true;
    stopRequested = false;
    logEntries = [];
    accountKey = Gmail.accountKey();
    buttonState();
    log(taskName);
    return Promise.resolve().then(fn).catch(error => {
      log(error.message || String(error), 'error');
      status(error.message || String(error));
    }).finally(async () => {
      if (extensionSelectionActive && Gmail.accountKey() === accountKey) {
        try {
          for (const {node} of Gmail.pageRows()) {
            const cb = Gmail.rowCheckbox(node);
            if (Gmail.checked(cb)) cb.click();
          }
        } catch (_) {}
      }
      extensionSelectionActive = false;
      running = false; stopRequested = false; buttonState();
    });
  }
  function sourceQuery(scope) { return scope === 'inbox' ? 'in:inbox' : 'in:all'; }
  function readRules() {
    const raw = screen.querySelector('#gk-rules').value;
    return {raw, rules:parseRules(raw), scope:screen.querySelector('#gk-scope').value};
  }
  async function executePreview() {
    await run('Beginning read-only protection scan.', async () => {
      setPreview(null);
      const {raw, rules, scope} = readRules();
      await saveSettings();
      const protect = new Set();
      const description = new Map();
      const add = (rows) => {
        for (const row of rows) {
          protect.add(row.id);
          if (protect.size > 100000) throw Error('More than 100,000 protected threads. Safety limit reached.');
          if (description.size < 5 && !description.has(row.id)) description.set(row.id, `${row.sender}: ${row.subject}`);
        }
      };
      // Always protect any conversations containing a draft.
      status('Protecting draft conversations…');
      await scanPages('in:drafts', rows=>add(rows), 'Draft protection');
      for (let i = 0; i < rules.length; i++) {
        status(`Finding protected conversations ${i+1}/${rules.length}: ${rules[i].description}`);
        await scanPages(rules[i].query, rows=>add(rows), `Keep rule ${i+1}`);
      }
      log(`${short(protect.size)} unique protected thread IDs collected`);
      const totalIds = new Set();
      let protectedCount = 0;
      let deletableCount = 0;
      const samples = [];
      status('Previewing all target conversations…');
      await scanPages(sourceQuery(scope), rows => {
        for (const row of rows) {
          if (totalIds.has(row.id)) continue;
          totalIds.add(row.id);
          if (protect.has(row.id)) protectedCount++;
          else {
            deletableCount++;
            if (samples.length < 7) samples.push({sender:row.sender,subject:row.subject});
          }
        }
        status(`Preview: ${short(totalIds.size)} checked · ${short(deletableCount)} would move to Trash`);
      }, 'Preview');
      abortIfStopped();
      const data = {
        path:accountKey, createdAt:Date.now(), rules:raw, scope,
        protectedIds:[...protect], protectedCount, deletableCount,
        totalCount:totalIds.size, samples
      };
      setPreview(data);
      status(`Preview complete. ${short(deletableCount)} unprotected conversations would move to Trash.`);
      log('Read-only preview complete. No Gmail messages changed.', 'success');
      // Snapshot remains in memory, but not in persistent storage to reduce exposure.
    });
  }
  async function afterDelete(oldIds) {
    const ids = new Set(oldIds);
    return until(() => {
      const rows = Gmail.pageRows();
      const remaining = rows.filter(x => ids.has(x.id));
      if (remaining.length) return false;
      if (!rows.length && !Gmail.isEmpty()) return false;
      return {signature:Gmail.pageSignature(rows) || 'empty', rows};
    }, 'Gmail did not confirm those threads disappeared from All Mail', 40000);
  }
  async function executeDeletion() {
    if (!screen.querySelector('#gk-ack').checked || screen.querySelector('#gk-confirm').value.trim() !== 'MOVE TO TRASH') {
      status('Check confirmation and type MOVE TO TRASH exactly.');
      return;
    }
    await run('Starting guarded Gmail Trash operation.', async () => {
      const {raw, scope} = readRules();
      const snapshot = preview;
      if (!snapshot || snapshot.path !== accountKey || snapshot.rules !== raw || snapshot.scope !== scope || Date.now() - snapshot.createdAt > TTL) {
        throw Error('Protection preview expired or Gmail account/rules changed. Scan again before deletion.');
      }
      const protectedIds = new Set(snapshot.protectedIds);
      status(`Protecting ${short(protectedIds.size)} known conversations. Starting Trash batches…`);
      log(`${short(protectedIds.size)} protected IDs loaded from the completed preview.`);
      const query = sourceQuery(scope);
      let view = await goSearch(query);
      let totalMoved = 0;
      let pagesPassed = 0;
      let batches = 0;
      let fingerprint = '';
      while (true) {
        abortIfStopped();
        ensureNotModal();
        if (batches > 30000 || pagesPassed > MAX_PAGES) throw Error('Safety limit reached. Stop and rescan.');
        if (view.empty) break;
        if (!view.rows.length) throw Error('Conversation list disappeared unexpectedly. Stopped.');
        const currentIds = view.rows.map(r=>r.id);
        const candidates = currentIds.filter(id => !protectedIds.has(id));
        const signature = view.signature;
        if (candidates.length) {
          // Fail closed if another row was already selected in Gmail.
          extensionSelectionActive = true;
          await Gmail.selectExactly(candidates, view.rows, wait);
          abortIfStopped();
          const btn = Gmail.deleteButton();
          if (!btn || Gmail.isDisabled(btn)) throw Error('Gmail Delete toolbar button not found. Nothing further deleted.');
          if (Gmail.pageSignature() !== signature) throw Error('Gmail conversation list changed before deletion; aborting.');
          // Exactly the chosen rows, no protected threads, are selected.
          btn.click();
          extensionSelectionActive = false;
          batches++;
          await afterDelete(candidates);
          totalMoved += candidates.length;
          status(`Moved ${short(totalMoved)} conversations to Trash in ${batches} batches…`);
          log(`Batch ${batches}: moved ${candidates.length} conversations to Trash`, 'success');
          await wait(650);
          view = await goSearch(query);
          pagesPassed = 0;
          fingerprint = '';
          continue;
        }
        // Protected-only page. Advance; never select even one protected row.
        Gmail.verifyCanSelect(view.rows);
        const marker = signature;
        if (fingerprint === marker) throw Error('Pagination made no progress. Stopped safely.');
        fingerprint = marker;
        pagesPassed++;
        status(`Skipping ${short(protectedIds.size)} protected threads · ${short(totalMoved)} moved; checking page ${pagesPassed}…`);
        if (!await nextPage(query, marker)) break;
        view = await until(() => currentView(query), 'Gmail page did not render');
      }
      log(`Done. ${short(totalMoved)} conversations moved to Trash.`, 'success');
      status(`Finished: ${short(totalMoved)} conversations moved to Trash; protected threads skipped.`);
    });
    // A new full scan is required after a destructive run, even if paused.
    setPreview(null);
    screen.querySelector('#gk-ack').checked = false;
    screen.querySelector('#gk-confirm').value = '';
  }
  // Pause when user directly interacts with Gmail during a run (programmatic
  // clicks from the content script have isTrusted === false).
  document.addEventListener('click', event => {
    if (running && event.isTrusted && screen && !screen.contains(event.target) && !document.querySelector('#gk-launcher').contains(event.target)) {
      stopRequested = true;
      log('A manual Gmail click was detected; stopping automation.', 'error');
    }
  }, true);
  chrome.runtime.onMessage.addListener((message) => {
    if (message?.type === 'GK_TOGGLE') toggle();
  });
  mount();
})();
