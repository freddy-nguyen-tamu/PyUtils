"""Optional end-to-end smoke test with Playwright and a fake Gmail DOM.
Run: python tests/mock_browser_test.py (requires playwright and Chromium).
No Google account is accessed.
"""
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).resolve().parents[1]
HTML = '''<!doctype html><html><head><title>Mock Gmail</title></head><body>
<form role="search"><input name="q" /></form>
<main role="main">
 <div gh="mtb"><button data-tooltip="Delete" type="button">Delete</button></div>
 <div id="pg"></div><button data-tooltip="Older" type="button">Older</button>
 <table><tbody id="rows"></tbody></table>
 <div id="empty"></div>
</main>
<script>
window.__store = {};window.chrome = {storage:{local:{async get(k){return {[k]:window.__store[k]}},async set(x){Object.assign(window.__store,x)}}},runtime:{onMessage:{addListener(){}}}};
window.mail = [
 {id:'abcdef0001',email:'important@example.com',subject:'Keep this important conversation',draft:false,deleted:false},
 {id:'abcdef0002',email:'someone@example.com',subject:'Draft protected',draft:true,deleted:false},
 {id:'abcdef0003',email:'finance@company.com',subject:'Company message',draft:false,deleted:false},
 {id:'abcdef0004',email:'advertise@example.com',subject:'Discard one',draft:false,deleted:false},
 {id:'abcdef0005',email:'bulk@example.com',subject:'Discard two',draft:false,deleted:false},
 {id:'abcdef0006',email:'foo@example.com',subject:'Discard three',draft:false,deleted:false}
];
let offset=0;
function getQuery(){return decodeURIComponent((location.hash.split('/')[1]||'').split('?')[0]);}
function matches(x,q){if (q==='in:all')return true;if(q==='in:drafts')return x.draft;if(q.includes('important@example.com'))return x.id==='abcdef0001';if(q.includes('@company.com'))return x.id==='abcdef0003';return false;}
function render(){
 const q=getQuery(); document.querySelector('input[name=q]').value=q;
 const data=window.mail.filter(x=>!x.deleted&&matches(x,q));
 const page=data.slice(offset,offset+2);
 document.querySelector('#pg').textContent=data.length?`${offset+1}–${offset+page.length} of ${data.length}`:'';
 document.querySelector('[data-tooltip=Older]').disabled=offset+2>=data.length;
 const tbody=document.querySelector('#rows'); tbody.textContent='';
 for(const x of page){const tr=document.createElement('tr');tr.className='zA';tr.setAttribute('data-legacy-thread-id',x.id);
   const check=document.createElement('td'); check.className='oZ-x3'; const cb=document.createElement('div');cb.className='oZ-jc T-Jo';cb.setAttribute('role','checkbox');cb.setAttribute('aria-checked','false');cb.style.cssText='width:24px;height:24px;border:1px solid black;';cb.onclick=()=>cb.setAttribute('aria-checked',cb.getAttribute('aria-checked')==='true'?'false':'true');check.append(cb);tr.append(check);
   const from=document.createElement('td');from.className='yW';const e=document.createElement('span');e.setAttribute('email',x.email);e.textContent=x.email;from.append(e);tr.append(from);
   const subject=document.createElement('td');subject.className='bog';subject.textContent=x.subject;tr.append(subject);tbody.append(tr);
 }
 document.querySelector('#empty').textContent=data.length?'':'No conversations found.';
}
document.querySelector('[data-tooltip=Older]').addEventListener('click',()=>{offset+=2;render()});
document.querySelector('[data-tooltip=Delete]').addEventListener('click',()=>{
 const ids=[...document.querySelectorAll('tr.zA')].filter(x=>x.querySelector('[role=checkbox]').getAttribute('aria-checked')==='true').map(x=>x.getAttribute('data-legacy-thread-id'));
 for(const x of window.mail)if(ids.includes(x.id))x.deleted=true;
 render();
});
window.addEventListener('hashchange',()=>{offset=0;render()});render();
</script>
</body></html>'''

def main():
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
        context=browser.new_context(viewport={'width':1500,'height':900})
        page=context.new_page()
        errors=[]
        page.on('pageerror',lambda exc:errors.append(str(exc)))
        page.set_content(HTML)
        page.evaluate("location.hash = '#all'")
        for file in ['rules.js','gmail-dom.js']:
            page.add_script_tag(path=str(ROOT/file))
        page.evaluate("GKGmail.accountKey = () => 'mock-account'")
        page.add_script_tag(path=str(ROOT/'content.js'))
        page.add_style_tag(path=str(ROOT/'styles.css'))
        page.locator('#gk-launcher').click()
        page.locator('#gk-rules').fill('important@example.com\n@company.com')
        page.locator('#gk-scan').click()
        page.wait_for_timeout(6000)
        print('DEBUG',page.locator('#gk-status').inner_text(),page.locator('#gk-log').inner_text(),page.evaluate('location.href'), flush=True)
        page.wait_for_function("document.querySelector('#gk-preview') && !document.querySelector('#gk-preview').hidden", timeout=12000)
        counts=page.locator('#gk-protected').inner_text(),page.locator('#gk-deletable').inner_text()
        print('Preview:',counts)
        assert counts == ('3','3'),f'Unexpected preview counts {counts}'
        page.locator('#gk-ack').check()
        page.locator('#gk-confirm').fill('MOVE TO TRASH')
        page.locator('#gk-trash').click()
        page.wait_for_timeout(7000)
        print('DELETE DEBUG',page.locator('#gk-status').inner_text(),page.locator('#gk-log').inner_text(),flush=True)
        page.wait_for_function("document.querySelector('#gk-status').textContent.includes('Finished:')", timeout=12000)
        remaining=page.evaluate('mail.map(x=>({id:x.id,deleted:x.deleted}))')
        print('Result:',remaining)
        assert all(not x['deleted'] for x in remaining[:3]),'Protected thread deleted!'
        assert all(x['deleted'] for x in remaining[3:]),'Expected threads not moved to Trash'
        assert not errors,errors
        print('PASS — protected threads + draft remain; unprotected threads trashed')
        browser.close()

if __name__ == '__main__': main()
