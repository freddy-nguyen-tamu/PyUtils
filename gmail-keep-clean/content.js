/* No Gmail API, background scraping, artificial searches, date partitioning, or URL pagination. */
(() => {
  'use strict';
  if (window.top !== window || window.__GK_V5__) return;
  window.__GK_V5__ = true;
  const Gmail = globalThis.GKGmailV5;
  const Rules = globalThis.GKRulesV5;
  const LEGACY_STORAGE = 'gmail_keepclean_v2:';
  const STORAGE = 'gmail_keepclean_walk_v5:';
  const delay = ms => new Promise(resolve => setTimeout(resolve,ms));
  const defaultRules = [
    'leasing@zoneapts.com',
    '@organizations.oci.oraclecloud.com',
    '@google.com',
    '@groq.co',
    '@conservicemail.com',
    '@horizonra-resident.com',
    '@confirminsurance.com',
    '@mynotifications.cvs.com',
    '@amazon.com',
    '@messages.rakuten.com',
    '@polosrus.com',
    'ebookviecom@gmail.com'
  ].join('\n');
  let panel, launcher, running = false, stopping = false, mailbox = null, mode = null;
  let seen = new Set();
  let stats = {checked:0, kept:0, trashed:0, skipped:0, pages:0};
  let logLines = [];
  function $id(id) {return panel.querySelector('#'+id);}
  function add(tag, parent, text='', attrs={}) {
    const node = document.createElement(tag);
    if (text) node.textContent=text;
    for (const [key,value] of Object.entries(attrs)) {
      if (key === 'class') node.className = value;
      else if (key === 'id') node.id = value;
      else if (key === 'type') node.type = value;
      else node.setAttribute(key,value);
    }
    if (parent) parent.append(node);
    return node;
  }
  function log(text, type='info') {
    logLines.push({time:new Date().toLocaleTimeString(),text,type});
    if(logLines.length>150)logLines.shift();
    if (!panel) return;
    const area=$id('gk-log'); area.replaceChildren();
    for (const item of logLines.slice(-20)) add('div',area,`${item.time}  ${item.text}`,{class:item.type});
    area.scrollTop=area.scrollHeight;
    renderStats();
  }
  function setStatus(value) {if(panel)$id('gk-status').textContent=value;}
  function renderStats() {
    if (!panel) return;
    for (const [name,value] of Object.entries(stats)) {
      const element=panel.querySelector('#gk-stat-'+name);
      if (element) element.textContent=Number(value).toLocaleString();
    }
  }
  function controls() {
    if(!panel)return;
    $id('gk-review').disabled=running;
    $id('gk-clean').disabled=running;
    $id('gk-pause').disabled=!running;
    $id('gk-rules').disabled=running;
    $id('gk-confirm').disabled=running;
  }
  function stopIfNeeded() {
    if(stopping)throw new Error('Paused. Press Start again to continue from All Mail.');
    if(Gmail.identifyAccount()!==mailbox)throw new Error('Gmail account changed. Stopping.');
    if(Gmail.dialogPresent())throw new Error('Gmail opened a dialog. Stopped; review the dialog manually.');
  }
  async function waitFor(condition,timeout=12000,reason='Gmail did not finish navigating') {
    const start=Date.now();
    while(Date.now()-start<timeout) {
      stopIfNeeded();
      try{if(condition())return true;}catch(_){/* Gmail is updating DOM. */}
      await delay(130);
    }
    throw new Error(`${reason}. Use Gmail controls manually, return to All Mail, then restart.`);
  }
  function handleMissingId(row) {
    // Missing identifiers mean we cannot safely remember the conversation across pages.
    if (!row.id) throw new Error('Gmail did not expose a stable ID for a row. Automation stopped safely.');
  }
  async function backToList() {
    stopIfNeeded();
    if (Gmail.allMailRoute() && Gmail.rows().length) return;
    const back=Gmail.backControl();
    if(back && !Gmail.disabled(back)) {
      back.click();
      try {
        await waitFor(()=>Gmail.allMailRoute(),5500,'Back control did not navigate');
        await delay(180);
        return;
      }catch(error) {
        if(stopping)throw error;
        log('Gmail Back did not respond. Trying the native All Mail sidebar.', 'warning');
      }
    }
    // The sidebar link is also a normal Gmail navigation control. It is an
    // independently discovered fallback, never an invented hash or URL.
    const all=Gmail.allMailControl();
    if(!all || Gmail.disabled(all)) {
      throw new Error('Neither Gmail Back nor All Mail sidebar could be used. Paused without guessing.');
    }
    all.click();
    await waitFor(()=>Gmail.allMailRoute(),14000,'All Mail sidebar did not return to the list');
    await delay(180);
  }
  async function returnToFirstPage() {
    stopIfNeeded();
    // Reset to All Mail by using the actual sidebar link, not guessing a page URL.
    if(Gmail.allMailRoute() && !/\/p\d+\/?$/i.test(location.hash)) return;
    const link=Gmail.allMailControl();
    if(!link)throw new Error('Could not locate the All Mail sidebar link. Click All Mail manually, then restart.');
    link.click();
    await waitFor(()=>Gmail.allMailRoute() && !/\/p\d+\/?$/i.test(location.hash),15000,'All Mail navigation did not return to the first page');
  }
  async function advanceToNext() {
    stopIfNeeded();
    const next=Gmail.nextControl();
    const range=Gmail.counter();
    if (!next || Gmail.disabled(next)) {
      if(range?.total !== null && range?.total !== undefined && range.last>=range.total) return false;
      if(!Gmail.rows().length && /no conversations|no mail|no messages/i.test(document.querySelector('[role="main"]')?.textContent||''))return false;
      throw new Error('Cannot prove this is the last All Mail page. Use Gmail Next results manually and restart.');
    }
    const before=Gmail.fingerprint();
    next.click();
    await waitFor(()=>Gmail.allMailRoute() && Gmail.rows().length>0 && Gmail.fingerprint()!==before,11000,
      'Gmail Next results did not change the visible page');
    await delay(220);
    stats.pages++; renderStats();
    return true;
  }
  async function inspectRow(row,rules) {
    stopIfNeeded();
    if(row.draft) return {status:'keep', match:{rule:{label:'Draft conversation'},address:''}};
    const oldHash=location.hash;
    Gmail.openRow(row);
    await waitFor(()=>Gmail.isConversationOpen() && location.hash!==oldHash,14000,'Opening the conversation did not change the Gmail view');
    await waitFor(()=>Gmail.openedThreadId()===row.id,6500,
      'Gmail did not confirm the opened conversation ID; no deletion is permitted');
    await delay(250);
    stopIfNeeded();
    const result=await Gmail.inspectConversation(rules,delay);
    return result;
  }
  async function deleteOpenConversation(row) {
    stopIfNeeded();
    if(!Gmail.isConversationOpen())throw new Error('Gmail is no longer displaying the inspected conversation. Deletion canceled.');
    const del=Gmail.trashControl();
    if(!del || Gmail.disabled(del))throw new Error('Gmail Delete control not found. No click performed.');
    // Only this line moves mail; it is unreachable in review-only mode.
    del.click();
    await waitFor(()=>Gmail.allMailRoute() && !Gmail.rows().some(r=>r.id===row.id),15000,
      'Deletion could not be verified in All Mail; check the conversation manually before continuing');
    stats.trashed++;
    log(`Moved one verified unprotected conversation to Trash (total ${stats.trashed}).`,'success');
    // After a delete the mailbox changes, shifting page boundaries. Always
    // restart from All Mail rather than skipping rows displaced by deletion.
    await returnToFirstPage();
  }
  async function saveRules() {
    const rulesText=$id('gk-rules').value;
    await chrome.storage.local.set({[STORAGE+mailbox]:{rulesText}});
  }
  async function loadRules() {
    const saved=await chrome.storage.local.get([STORAGE+mailbox,LEGACY_STORAGE+`https://mail.google.com/mail/u/${mailbox.split(':')[0]}`]);
    const legacy=saved[LEGACY_STORAGE+`https://mail.google.com/mail/u/${mailbox.split(':')[0]}`];
    const current=saved[STORAGE+mailbox];
    $id('gk-rules').value=current?.rulesText || legacy?.rules || defaultRules;
  }
  async function execute(action) {
    if (running) return;
    try {
      mailbox=Gmail.identifyAccount();
      if(!Gmail.allMailRoute())throw new Error('Open Gmail → All Mail before starting. Do not start from Search results or Inbox.');
      const rules=Rules.parse($id('gk-rules').value);
      if(action==='clean' && $id('gk-confirm').value.trim()!=='MOVE TO TRASH') throw new Error('Type MOVE TO TRASH before starting automatic deletion.');
      if(!Gmail.rows().length)throw new Error('No visible All Mail rows were found. Wait for Gmail to load.');
      await saveRules();
      running=true;stopping=false;mode=action;
      seen=new Set(); stats={checked:0,kept:0,trashed:0,skipped:0,pages:1};
      logLines=[]; controls();renderStats();
      log(action==='clean'?'Started one-conversation-at-a-time cleanup.':'Started non-deleting review of up to 10 conversations.');
      setStatus('Working inside All Mail. Do not interact with Gmail while running.');
      const reviewLimit=10;
      let visits=0;
      let safetyMax=100000;
      while(--safetyMax>0){
        stopIfNeeded();
        if(!Gmail.allMailRoute())throw new Error('Gmail left All Mail unexpectedly; stopped.');
        const row=Gmail.rows().find(r=>!r.id || !seen.has(r.id));
        if(!row){
          const advanced=await advanceToNext();
          if(!advanced)break;
          continue;
        }
        handleMissingId(row);
        // Mark as visited before navigation: never retry ambiguous actions automatically.
        seen.add(row.id); visits++;stats.checked++;
        setStatus(`Checking conversation ${stats.checked} · ${stats.kept} kept · ${stats.trashed} trashed · ${stats.skipped} skipped`);
        const result=await inspectRow(row,rules);
        if(result.status==='keep'){
          stats.kept++;
          log(`KEEP: ${result.match?.rule?.label||'matched a rule'}${result.match?.address?' via '+result.match.address:''}.`,'success');
          await backToList();
        } else if(result.status==='trash') {
          if(action==='clean') {
            await deleteOpenConversation(row);
          } else {
            log('REVIEW ONLY: unprotected conversation found; no deletion.');
            await backToList();
          }
        } else {
          stats.skipped++;
          log(`SKIP: ${result.why||'participants could not be verified'}. No deletion.`,'warning');
          await backToList();
        }
        renderStats();
        if(action==='review' && visits>=reviewLimit){
          log('Review limit reached. Nothing was moved to Trash.','success');
          break;
        }
      }
      if(safetyMax===0)throw new Error('Safety iteration limit reached; stopped.');
      const unfinished=stats.skipped>0;
      setStatus(action==='review'?'Review finished. No emails deleted.':`Finished: ${stats.trashed} trashed, ${stats.kept} kept, ${stats.skipped} requiring manual review.`);
      log(unfinished?`Finished with ${stats.skipped} skipped conversations that require manual review.`:'Finished without any unverified deletions.', unfinished?'warning':'success');
    }catch(e){
      log(e?.message||String(e),'error');setStatus(e?.message||String(e));
    }finally{
      running=false;stopping=false;controls();renderStats();
    }
  }
  function makeUI(){
    if(document.getElementById('gk-v5-launcher'))return;
    launcher=add('button',document.documentElement,'Keep & Clean',{id:'gk-v5-launcher',type:'button'});
    panel=add('aside',document.documentElement,'',{id:'gk-v5-panel','aria-label':'Gmail Keep & Clean',hidden:''});
    const head=add('header',panel,'',{class:'gk-head'});
    add('div',head,'Keep & Clean',{class:'gk-brand'});
    const close=add('button',head,'×',{class:'gk-close',type:'button','aria-label':'Close panel'});
    const main=add('div',panel,'',{class:'gk-main'});
    add('p',main,'Works only in Gmail → All Mail. It opens each conversation, checks sender and recipient metadata, and keeps any thread matching an address or domain. No Gmail search, page URLs, or OAuth.',{class:'gk-note'});
    add('label',main,'Conversations to preserve',{for:'gk-rules',class:'gk-label'});
    add('textarea',main,'',{id:'gk-rules',spellcheck:'false',placeholder:'person@example.com\n@company.com'});
    add('div',main,'One address or @domain per line. Entire matching threads are kept. Subdomains are included. The old search/subject rules are not supported in this simpler mode.',{class:'gk-help'});
    add('p',main,'Opening messages may mark them read. Missing or hidden participant details cause a SKIP, never automatic deletion. Trash is recoverable until Gmail empties it.',{class:'gk-warning'});
    add('div',main,'',{class:'gk-stat-grid'});
    const metrics=main.querySelector('.gk-stat-grid');
    for(const [name,title] of [['checked','Checked'],['kept','Kept'],['trashed','Trashed'],['skipped','Skipped'],['pages','Pages']]){
      const card=add('div',metrics,'',{class:'gk-stat'});
      add('strong',card,'0',{id:'gk-stat-'+name});add('span',card,title);
    }
    add('label',main,'Type MOVE TO TRASH to enable cleanup',{for:'gk-confirm',class:'gk-label'});
    add('input',main,'',{id:'gk-confirm',type:'text',autocomplete:'off',placeholder:'MOVE TO TRASH'});
    const buttons=add('div',main,'',{class:'gk-actions'});
    add('button',buttons,'Review 10',{id:'gk-review',type:'button',class:'gk-secondary'});
    add('button',buttons,'Start cleaning',{id:'gk-clean',type:'button',class:'gk-danger'});
    add('button',buttons,'Pause',{id:'gk-pause',type:'button',class:'gk-secondary'});
    add('div',main,'Ready. Open All Mail to begin.',{id:'gk-status',class:'gk-status'});
    add('div',main,'Activity',{class:'gk-label'});
    add('div',main,'',{id:'gk-log',class:'gk-log','aria-live':'polite'});
    add('p',main,'Scans real expanded message cards; uses Gmail Back with an All Mail sidebar fallback. If Next results does not advance or a message cannot be fully verified, it stops or skips rather than guessing. No external services.',{class:'gk-help'});
    launcher.addEventListener('click',()=>{panel.hidden=!panel.hidden; launcher.hidden=!panel.hidden;});
    close.addEventListener('click',()=>{panel.hidden=true;launcher.hidden=false;});
    $id('gk-review').addEventListener('click',()=>execute('review'));
    $id('gk-clean').addEventListener('click',()=>execute('clean'));
    $id('gk-pause').addEventListener('click',()=>{stopping=true;setStatus('Stopping safely after current navigation.');});
    try {mailbox=Gmail.identifyAccount();loadRules().catch(e=>log(String(e),'error'));} catch(e){log(e.message,'error');}
    controls();renderStats();
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',makeUI,{once:true});
  else makeUI();
  chrome.runtime.onMessage.addListener((msg,_sender,respond)=>{
    if(msg?.type==='GK_TOGGLE'){
      panel.hidden=!panel.hidden;
      launcher.hidden=!panel.hidden;
      respond({ok:true});
    }
  });
})();
