/* Gmail's visible UI only: no API, guessed search routes or date partitions. */
(function (root) {
  'use strict';
  const Rules = root.GKRulesV5;
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  function visible(el) {
    if (!el || !el.isConnected) return false;
    const r = el.getBoundingClientRect();
    return !!(r.width && r.height) && getComputedStyle(el).visibility !== 'hidden';
  }
  function disabled(el) {
    return !el || el.hasAttribute('disabled') || el.getAttribute('aria-disabled') === 'true' ||
      el.classList.contains('T-I-JW') || el.classList.contains('T-I-JE');
  }
  function find(selectors, scope = document) {
    for (const selector of selectors) {
      for (const el of scope.querySelectorAll(selector)) {
        if (visible(el) && !disabled(el)) return el;
      }
    }
    return null;
  }
  function textProp(el) {
    return ['aria-label', 'data-tooltip', 'title'].map(k => el.getAttribute(k) || '').join(' ').toLowerCase();
  }
  function namedControl(re, scope = document, includeText = false) {
    return [...scope.querySelectorAll('button, [role="button"], [title], [data-tooltip]')]
      .find(el => visible(el) && !disabled(el) &&
        (re.test(textProp(el)) || (includeText && re.test((el.textContent || '').trim().toLowerCase().slice(0, 65))))) || null;
  }
  function identifyAccount() {
    const index = location.pathname.match(/\/mail\/u\/([^/]+)\//)?.[1];
    if (index === undefined || location.hostname !== 'mail.google.com') throw new Error('Open a Gmail tab first.');
    return `${index}:${(document.querySelector('meta[name="og-profile-acct"]')?.content || '').toLowerCase()}`;
  }
  function allMailRoute() { return /^#all(?:\/p\d+)?\/?$/i.test(location.hash); }
  function isConversationOpen() { return /^#all\/(?!p\d+\/?$).+/i.test(location.hash); }
  function rows() {
    return [...document.querySelectorAll('[role="main"] tr.zA')].filter(visible).map(el => {
      const ids = [...new Set([...el.querySelectorAll('[data-legacy-thread-id]'), el]
        .map(n => n.getAttribute('data-legacy-thread-id')).filter(Boolean))];
      const id = ids.length === 1 && /^[0-9a-f]{16}$/i.test(ids[0]) ? ids[0].toLowerCase() : null;
      const subject = find(['.bog', '.y6', '[data-thread-id]'], el);
      const draft = /\bdraft\b/i.test(el.querySelector('.aKz, .bq3, .y6')?.textContent || '') ||
        !!el.querySelector('[aria-label*="Draft" i]');
      return {element: el, id, subject, draft};
    });
  }
  function counter() {
    const el = find(['[role="main"] .Dj', '[role="main"] [role="status"]']);
    const m = el?.textContent?.replace(/,/g, '').match(/(\d+)\s*[–—-]\s*(\d+)\s+of\s+(\d+|many)/i);
    return m ? {first:Number(m[1]),last:Number(m[2]),total:m[3].toLowerCase()==='many'?null:Number(m[3])} : null;
  }
  function fingerprint() {return JSON.stringify({hash:location.hash,range:counter(),ids:rows().map(r=>r.id)});}
  function nextControl() {
    return namedControl(/\b(next results|older|next page)\b/i,document);
  }
  function backControl() {
    // The live Gmail capture puts the ACTIVE message toolbar OUTSIDE role=main:
    // <div gh="mtb">...<div title="Back to All Mail" act="19">.
    const active=document.querySelector('[gh="mtb"]');
    if(active) {
      const preferred=find(['[title="Back to All Mail"]','[aria-label="Back to All Mail"]',
        '[data-tooltip="Back to All Mail"]','[act="19"]'], active);
      if(preferred)return preferred;
      const semantic=namedControl(/\bback to (?:all mail|search results|inbox)\b/i,active);
      if(semantic)return semantic;
    }
    return find(['[title="Back to All Mail"]','[aria-label="Back to All Mail"]',
      '[data-tooltip="Back to All Mail"]','[role="main"] [act="19"]']) ||
      namedControl(/\bback to (?:all mail|search results|inbox)\b/i,document);
  }
  function allMailControl() {
    return find(['[role="navigation"] a[href$="#all"]','a[href$="#all"]',
      '[role="navigation"] [aria-label="All Mail"]', '[role="navigation"] [title="All Mail"]',
      '[role="navigation"] [data-tooltip="All Mail"]', '[aria-label="All Mail"]',
      '[title="All Mail"]','[data-tooltip="All Mail"]']);
  }
  function trashControl() {
    // The live capture's active conversation Delete button is act="10" inside
    // gh="mtb"; other pre-rendered, hidden Gmail toolbars also have act="10".
    const active=document.querySelector('[gh="mtb"]');
    if(active) {
      const button=find(['[role="button"][act="10"]','[role="button"][data-tooltip="Delete"]',
        '[role="button"][aria-label="Delete"]',
        'button[data-tooltip="Delete"]','button[aria-label="Delete"]'],active);
      if(button)return button;
    }
    const candidates=[...document.querySelectorAll('[role="button"][act="10"],'+
      ' [role="button"][data-tooltip="Delete"], [role="button"][aria-label="Delete"]')];
    // Never click an item in a hidden menu or an individual message's actions.
    return candidates.find(el=>visible(el) && !disabled(el) &&
      el.closest('.G-tF, [gh="mtb"], [role="toolbar"]') &&
      !el.closest('.adn, .bqY, [role="menu"]')) || null;
  }
  function openedThreadId() {
    // Captured live Gmail: <h2 class="hP" data-legacy-thread-id="...">
    // We must verify the actual open thread, not just infer it from a changed URL.
    const main=document.querySelector('[role="main"]');
    if(!main) return null;
    const candidates=[...main.querySelectorAll('h2[data-legacy-thread-id], [data-legacy-thread-id]')]
      .filter(visible).map(el=>(el.getAttribute('data-legacy-thread-id')||'').toLowerCase())
      .filter(id=>/^[0-9a-f]{16}$/.test(id));
    const unique=[...new Set(candidates)];
    return unique.length===1?unique[0]:null;
  }
  function openRow(row) {
    if(!row.id || !row.subject || !visible(row.subject))throw new Error('No verified conversation target is available.');
    row.subject.click();
  }
  function collectAddresses(node) {
    const addresses=new Set();
    if(!node) return addresses;
    for(const el of node.querySelectorAll('[email], [data-hovercard-id], [data-email]')) {
      for(const attr of ['email','data-hovercard-id','data-email']) {
        const value=(el.getAttribute(attr)||'').trim().toLowerCase();
        if(Rules.VALID_EMAIL.test(value))addresses.add(value);
      }
    }
    return addresses;
  }
  function messageCards() {
    const main=document.querySelector('[role="main"]');
    if(!main)return [];
    const cards=[...main.querySelectorAll('.aHU [role="listitem"], .aHU .h7')];
    const unique=[...new Set(cards)].filter(el=>visible(el));
    if(unique.length)return unique;
    return [...main.querySelectorAll('.adn')].filter(visible);
  }
  function cardBody(card) {return card.matches('.adn') ? card : card.querySelector('.adn');}
  function hiddenCards() {
    return messageCards().filter(el=>el.getAttribute('aria-expanded')==='false'||!cardBody(el));
  }
  function showMoreControl() {
    const main=document.querySelector('[role="main"]') || document;
    return namedControl(/\bshow more messages\b/i,main);
  }
  function expandAllControl() {
    const main=document.querySelector('[role="main"]') || document;
    return namedControl(/\bexpand all\b/i,main);
  }
  async function expandMessages() {
    // The Gmail capture has .h7[aria-expanded="true"] *outside* .adn; this is NOT collapsed.
    for(let pass=0;pass<5;pass++) {
      const more=showMoreControl();
      if(more) { more.click(); await sleep(350); continue; }
      const collapsed=hiddenCards();
      if(!collapsed.length)break;
      const all=expandAllControl();
      if(all) {
        const before=collapsed.length;
        all.click();
        await sleep(350);
        if(hiddenCards().length<before)continue;
        // Gmail ignored Expand all; try the visible collapsed cards directly.
      }
      let changed=false;
      for(const card of collapsed.slice(0,25)) {
        if(card.getAttribute('aria-expanded')==='false') {
          card.click(); changed=true; await sleep(100);
        }
      }
      if(!changed)break;
    }
    return hiddenCards().length;
  }
  function detailsPopup() {
    // Only visible details belonging to the current conversation, never hidden
    // pre-rendered Gmail UI elsewhere on the page.
    const main=document.querySelector('[role="main"]');
    return find(['.ajE','[role="dialog"]','.ajC'],main || document) ||
      find(['.ajE','[role="dialog"]','.ajC'],document);
  }
  async function inspectConversation(rules, delay=sleep) {
    const main=document.querySelector('[role="main"]');
    if(!main)return {status:'unknown',why:'Gmail conversation view is not loaded'};
    await expandMessages();
    const cards=messageCards();
    if(!cards.length)return {status:'unknown',why:'Gmail has no verifiable message cards'};
    const discovered=new Set();
    let incomplete=0, recipientsMissing=0, checked=0;
    for(const card of cards) {
      const message=cardBody(card);
      if(!message || card.getAttribute('aria-expanded')==='false') {incomplete++;continue;}
      const sender=message.querySelector('.gD[email], .gD[data-hovercard-id], [email].gD');
      if(!sender) {incomplete++;continue;}
      checked++;
      const detail= namedControl(/^show details$/i,message) ||
        find(['.ajy'],message);
      let popup=null;
      if(detail) {
        detail.click();
        await delay(180);
        popup=detailsPopup();
      }
      const addresses=collectAddresses(message);
      if(popup)for(const a of collectAddresses(popup))addresses.add(a);
      for(const a of addresses) discovered.add(a);
      const match=[...addresses].map(address=>({address,rule:Rules.matching(address,rules)})).find(x=>x.rule);
      if(match)return {status:'keep',match,count:checked,addresses:discovered.size};
      // Recipient attributes are visible on real Gmail's .g2[email]. If Gmail
      // hides them and Show details fails, do not make a destructive inference.
      const recipients=message.querySelector('.g2[email], .ajB [email], .hb [email]');
      if(!recipients && !popup)recipientsMissing++;
      if(popup) {
        // Close Show details with the same native trigger to avoid interfering
        // with navigation to the next thread. If still open, recovery uses All Mail.
        if(visible(detail))detail.click();
        await delay(70);
      }
    }
    if(incomplete || recipientsMissing || !checked || checked!==cards.length) {
      return {status:'unknown',why:`Verified ${checked}/${cards.length} messages; ${incomplete} collapsed/missing headers, ${recipientsMissing} without recipients`};
    }
    return {status:'trash',count:checked,addresses:discovered.size};
  }
  function dialogPresent() { return !!find(['[role="alertdialog"]','dialog[open]']); }
  const api={visible,find,disabled,identifyAccount,allMailRoute,isConversationOpen,rows,counter,fingerprint,
    nextControl,backControl,allMailControl,trashControl,openedThreadId,openRow,collectAddresses,messageCards,
    hiddenCards,showMoreControl,expandMessages,inspectConversation,dialogPresent};
  root.GKGmailV5=api;
  if(typeof module==='object'&&module.exports)module.exports=api;
})(typeof globalThis!=='undefined'?globalThis:this);
