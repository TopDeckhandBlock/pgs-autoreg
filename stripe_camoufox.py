# Camoufox-based Stripe fill — bypasses Anomaly detection (payment-hitter skill §15)
# Usage: PYTHONPATH="" python311 stripe_camoufox.py <checkout_url>
import json,time,re,sys,os
BASE=os.path.dirname(os.path.abspath(__file__))
CARD=json.load(open(os.path.join(BASE,'secrets.json')))['card']
YK=open(os.path.join(BASE,'yescaptcha_key.txt')).read().strip()
import urllib.request

def yc(fn,payload):
    req=urllib.request.Request(f'https://api.yescaptcha.com/{fn}',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
    return json.loads(urllib.request.urlopen(req,timeout=30).read())

def solve_hcaptcha(sitekey,pageurl):
    r=yc('createTask',{'clientKey':YK,'task':{'type':'HCaptchaTaskProxyless','websiteURL':pageurl,'websiteKey':sitekey,'isInvisible':True}})
    tid=r.get('taskId')
    if not tid: return None
    print('hc task',tid,flush=True)
    for _ in range(45):
        time.sleep(4)
        res=yc('getTaskResult',{'clientKey':YK,'taskId':tid})
        if res.get('status')=='ready': return res['solution']['gRecaptchaResponse']
        if res.get('errorId'): print('hc err',res,flush=True); return None
    return None

def main(url):
    from camoufox.sync_api import Camoufox
    proxy=None
    bd=os.environ.get('BD_PROXY')
    if bd:
        m=__import__('re').match(r'http://([^:]+):([^@]+)@([^:]+):(\d+)',bd)
        if m:
            u,p_,h,po=m.groups()
            proxy={'server':f'http://{h}:{po}','username':u,'password':p_}
            print('BD proxy:',h,flush=True)
    else:
        pf=os.path.join(BASE,'us_proxies.txt')
        if os.path.exists(pf):
            pl=[l.strip() for l in open(pf) if l.strip()]
            if pl:
                proxy={'server':pl[0]}
                print('proxy:',pl[0],flush=True)
    with Camoufox(proxy=proxy,os=['windows'],headless=False,geoip=True if proxy else False) as browser:
        pg=browser.new_page()
        pg.goto(url,timeout=90000,wait_until='domcontentloaded')
        print('TITLE:',pg.title(),flush=True)
        fr=None
        for _ in range(12):
            pg.wait_for_timeout(4000)
            for f in pg.frames:
                try:
                    if f.locator('input[name="cardNumber"]').count()>0: fr=f;break
                except: pass
            if fr: break
        if not fr:
            pg.screenshot(path=os.path.join(BASE,'cam_noframe.png'))
            print('NOFORM',flush=True); return False
        print('form ok',flush=True)
        # fill
        for n,v in [('cardNumber',CARD['num']),('cardExpiry',CARD['exp']),('cardCvc',CARD['cvc']),('billingName',CARD['name'])]:
            el=fr.locator(f'input[name="{n}"]').first
            el.click(timeout=8000); el.type(v,delay=90); pg.wait_for_timeout(400)
        print('card ok',flush=True)
        try:
            fr.locator('select[name="billingCountry"]').first.select_option(label='United States',timeout=8000)
            pg.wait_for_timeout(2000)
        except Exception as e: print('country',str(e)[:50])
        for n,v in [('billingAddressLine1',CARD['addr']),('billingLocality',CARD['city']),('billingPostalCode',CARD['zip'])]:
            try:
                el=fr.locator(f'input[name="{n}"]').first
                if el.count()>0: el.fill(v,timeout=6000); pg.wait_for_timeout(300)
            except Exception as e: print('addr',n,str(e)[:40])
        try:
            fr.locator('select[name="billingAdministrativeArea"]').first.select_option(label='Texas',timeout=6000)
        except: pass
        pg.screenshot(path=os.path.join(BASE,'cam_filled.png'),full_page=True)
        # submit
        btn=fr.locator('button[type=submit]').first
        btn.click(force=True,timeout=10000)
        print('submitted',flush=True)
        solved=False
        for i in range(30):
            pg.wait_for_timeout(5000)
            u=pg.url
            print(f't+{(i+1)*5}s {u[:80]}',flush=True)
            if 'success' in u or 'checkout=succ' in u or 'pgsgrove' in u:
                pg.screenshot(path=os.path.join(BASE,'cam_success.png'),full_page=True)
                print('SUCCESS',flush=True); return True
            # captcha modal?
            try:
                need=pg.evaluate("""()=>{const w=document.querySelector('iframe[src*="hcaptcha.com/captcha"]');return !!(w&&w.offsetWidth>50);}""")
            except: need=False
            if need and not solved:
                sitekey=None
                for f in pg.frames:
                    m=re.search(r'sitekey=([a-fA-F0-9-]+)',f.url)
                    if m and 'hcaptcha' in f.url: sitekey=m.group(1);break
                print('sitekey',sitekey,flush=True)
                if sitekey:
                    tok=solve_hcaptcha(sitekey,url.split('#')[0])
                    if tok:
                        for f in pg.frames:
                            try:
                                f.evaluate("""(t)=>{['h-captcha-response','g-recaptcha-response'].forEach(n=>{let e=document.querySelector('[name="'+n+'"]');if(!e){e=document.createElement('textarea');e.name=n;e.style.display='none';document.body.appendChild(e);}e.value=t;});if(window.hcaptcha){try{window.hcaptcha.setResponse(t);}catch(e){}}}""",tok)
                            except: pass
                        # click checkbox in hcaptcha frame
                        for f in pg.frames:
                            if 'hcaptcha' in f.url:
                                try:
                                    f.locator('#checkbox').first.click(timeout=4000)
                                except: pass
                        pg.wait_for_timeout(3000)
                        try: btn.click(force=True,timeout=8000)
                        except: pass
                        solved=True
                        print('captcha handled',flush=True)
            # connection issues -> resubmit
            try:
                bt=pg.evaluate("()=>document.body.innerText||''")
                if 'connection issues' in bt and i%2==0:
                    print('conn banner -> resubmit',flush=True)
                    btn.click(force=True,timeout=8000)
            except: pass
        pg.screenshot(path=os.path.join(BASE,'cam_after.png'),full_page=True)
        print('TIMEOUT',flush=True)
        return False

if __name__=='__main__':
    url=sys.argv[1] if len(sys.argv)>1 else open(os.path.join(BASE,'pgs_checkout_url.txt')).read().strip()
    ok=main(url)
    print('RESULT:','OK' if ok else 'FAIL')
