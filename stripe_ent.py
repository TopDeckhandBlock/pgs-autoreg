import json,os,time,re,sys
BASE=os.path.dirname(os.path.abspath(__file__))
CARD=json.load(open(os.path.join(BASE,'secrets.json')))['card']
YK=open(os.path.join(BASE,'yescaptcha_key.txt')).read().strip()
url=open(os.path.join(BASE,'pgs_checkout_url.txt')).read().strip()
import urllib.request

def yc(fn,payload):
    req=urllib.request.Request(f'https://api.yescaptcha.com/{fn}',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
    return json.loads(urllib.request.urlopen(req,timeout=30).read())

def solve(sitekey,pageurl,rqdata=None):
    task={'type':'HCaptchaTaskProxyless','websiteURL':pageurl,'websiteKey':sitekey,'isInvisible':True,'enterprise':True}
    if rqdata: task['rqdata']=rqdata
    r=yc('createTask',{'clientKey':YK,'task':task})
    tid=r.get('taskId')
    if not tid: print('taskerr',r,flush=True); return None
    print('task',tid,'rqdata' if rqdata else 'norq',flush=True)
    for _ in range(45):
        time.sleep(4)
        res=yc('getTaskResult',{'clientKey':YK,'taskId':tid})
        if res.get('status')=='ready': return res['solution']['gRecaptchaResponse']
        if res.get('errorId'): print('err',res.get('errorCode'),flush=True); return None
    return None

from camoufox.sync_api import Camoufox
with Camoufox(os=['windows'],headless=False,geoip=False) as b:
    pg=b.new_page()
    pg.goto(url,timeout=90000,wait_until='domcontentloaded')
    pg.wait_for_timeout(8000)
    fr=None
    for _ in range(15):
        for f in pg.frames:
            try:
                if f.locator('input[name="cardNumber"]').count()>0: fr=f;break
            except: pass
        if fr: break
        pg.wait_for_timeout(4000)
    if not fr: print('NOFORM'); sys.exit(1)
    print('form ok',flush=True)
    for n,v in [('cardNumber',CARD['num']),('cardExpiry',CARD['exp']),('cardCvc',CARD['cvc']),('billingName',CARD['name'])]:
        el=fr.locator(f'input[name="{n}"]').first; el.click(timeout=8000); el.type(v,delay=90); pg.wait_for_timeout(400)
    try:
        fr.locator('select[name="billingCountry"]').first.select_option(label='United States',timeout=8000); pg.wait_for_timeout(2000)
    except: pass
    try: fr.locator('input[name="billingPostalCode"]').first.fill(CARD['zip'],timeout=6000)
    except: pass
    fr.locator('button[type=submit]').first.click(timeout=10000)
    print('submitted',flush=True)
    pg.wait_for_timeout(8000)
    # find hcaptcha frame, extract sitekey + rqdata
    sitekey=None; rqdata=None
    for f in pg.frames:
        if 'hcaptcha' in f.url:
            m=re.search(r'sitekey=([a-fA-F0-9-]+)',f.url)
            if m: sitekey=m.group(1)
            r=re.search(r'rqdata=([^&#]+)',f.url)
            if r: rqdata=r.group(1)
            try:
                cfg=f.evaluate("()=>{try{return JSON.stringify(window.hcaptcha||null)}catch(e){return null}}")
            except: cfg=None
    # also try to pull rqdata from iframe src attributes in the challenge frame's parent
    if not rqdata:
        for f in pg.frames:
            try:
                srcs=f.evaluate("()=>Array.from(document.querySelectorAll('iframe')).map(i=>i.src).join('\\n')")
                m=re.search(r'rqdata=([^&#\\"]+)',srcs or '')
                if m: rqdata=m.group(1); break
            except: pass
    print('sitekey',sitekey,'rqdata',bool(rqdata),flush=True)
    if sitekey:
        tok=solve(sitekey,url.split('#')[0],rqdata)
        if tok:
            print('token len',len(tok),flush=True)
            for f in pg.frames:
                try:
                    f.evaluate("""(t)=>{
                        document.querySelectorAll('textarea[name="h-captcha-response"],textarea[name="g-recaptcha-response"]').forEach(ta=>{ta.value=t;});
                        document.querySelectorAll('input[name="h-captcha-response"]').forEach(i=>{i.value=t;});
                        if(window.hcaptcha){try{Object.keys(window.hcaptcha._psts||{}).forEach(w=>window.hcaptcha.setResponse(t,w));}catch(e){}}
                        if(window.onHCaptchaSuccess){try{window.onHCaptchaSuccess(t);}catch(e){}}
                        const cel=document.querySelector('[data-callback]');
                        if(cel){const cb=cel.getAttribute('data-callback');if(window[cb]){try{window[cb](t);}catch(e){}}}
                    }""",tok)
                except: pass
            for f in pg.frames:
                if 'hcaptcha' in f.url:
                    try:
                        f.evaluate("""(t)=>{window.parent.postMessage(JSON.stringify({source:'hcaptcha',label:'challenge-closed',contents:{event:'challenge-passed',response:t,expiration:120}}),'*');}""",tok)
                    except: pass
            print('injected (5-method + postMessage), waiting',flush=True)
            pg.wait_for_timeout(8000)
            # modal gone?
            gone=True
            for f in pg.frames:
                if 'hcaptcha' in f.url and 'invisible' not in f.url:
                    try:
                        if f.locator('#checkbox').count()>0: gone=False
                    except: pass
            print('modal gone:',gone,flush=True)
            for i in range(24):
                pg.wait_for_timeout(5000)
                u=pg.url
                print(f't+{(i+1)*5}s {u[:80]}',flush=True)
                if 'success' in u or 'checkout=succ' in u or 'pgsgrove' in u:
                    pg.screenshot(path=BASE+'/ent_success.png',full_page=True)
                    print('SUCCESS',flush=True); break
            pg.screenshot(path=BASE+'/ent_after.png',full_page=True)
            open(BASE+'/ent_final_url.txt','w').write(pg.url)
        else:
            print('NO TOKEN',flush=True)
print('ENT_DONE',flush=True)
