import json,os,time,re
BASE=os.path.dirname(os.path.abspath(__file__))
CARD=json.load(open(os.path.join(BASE,'secrets.json')))['card']
url=open(os.path.join(BASE,'pgs_checkout_url.txt')).read().strip()
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b=p.chromium.launch(headless=False,channel='chrome',args=['--disable-blink-features=AutomationControlled'])
    ctx=b.new_context(viewport={'width':1280,'height':950},locale='en-US',timezone_id='America/New_York',
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36')
    ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});")
    pg=ctx.new_page()
    pg.goto(url,timeout=90000,wait_until='commit')
    pg.wait_for_timeout(15000)
    print('TITLE:',pg.title(),flush=True)
    fr=None
    for f in pg.frames:
        try:
            if f.locator('input[name="cardNumber"]').count()>0: fr=f;break
        except: pass
    if not fr:
        print('NOFORM title=',pg.title(),flush=True); pg.screenshot(path=BASE+'/diag_noform.png'); b.close(); raise SystemExit
    for n,v in [('cardNumber',CARD['num']),('cardExpiry',CARD['exp']),('cardCvc',CARD['cvc']),('billingName',CARD['name'])]:
        el=fr.locator(f'input[name="{n}"]').first; el.click(timeout=8000); el.type(v,delay=90); pg.wait_for_timeout(400)
    try: fr.locator('select[name="billingCountry"]').first.select_option(label='United States',timeout=8000); pg.wait_for_timeout(2000)
    except Exception as e: print('country',e)
    for n,v in [('billingAddressLine1',CARD['addr']),('billingLocality',CARD['city']),('billingPostalCode',CARD['zip'])]:
        try:
            el=fr.locator(f'input[name="{n}"]').first
            if el.count()>0: el.fill(v,timeout=6000); pg.wait_for_timeout(300)
        except Exception as e: print('addr',n,str(e)[:40])
    pg.screenshot(path=BASE+'/diag_filled.png',full_page=True)
    fr.locator('button[type=submit]').first.click(timeout=10000)
    print('SUBMITTED',flush=True)
    for i in range(14):
        pg.wait_for_timeout(5000)
        try:
            t=pg.evaluate("()=>document.body.innerText||''").replace('\n',' ')[:300]
        except: t='?'
        iframes=[f.url[:45] for f in pg.frames if 'hcaptcha' in f.url or '3ds' in f.url.lower() or 'challenge' in f.url.lower() or 'acs' in f.url.lower()]
        print(f't+{(i+1)*5}s url={pg.url[:55]} ifr={iframes}',flush=True)
        print('   TXT:',t,flush=True)
        pg.screenshot(path=BASE+f'/diag_t{(i+1)}.png',full_page=True)
    b.close()
print('DIAG_DONE')
