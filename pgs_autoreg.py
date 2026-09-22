# PGSGrove (Phoenix Grove) autoreg — полный пайплайн без браузера до Stripe
# signup (Supabase) -> email verify (IMAP) -> stripe-checkout edge fn ->
# Stripe form fill + hCaptcha (YesCaptcha) -> api_mint_plan_key RPC -> pgsk_plan_* ключ
#
# Run: PYTHONPATH="" C:/Users/User/AppData/Local/Programs/Python/Python311/python.exe pgs_autoreg.py N
# Deps: playwright (Python 3.11), YesCaptcha key в yescaptcha_key.txt или env YESCAPTCHA_KEY
import sys, os, json, time, re, random, string, hashlib, urllib.request, urllib.error
import imaplib, email as emaillib
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).parent
SUPABASE = "https://btncuytmqzuwazgidche.supabase.co"
ANON_KEY = (BASE_DIR / "anonkey.txt").read_text().strip()
SECRETS = json.loads((BASE_DIR / "secrets.json").read_text()) if (BASE_DIR / "secrets.json").exists() else {}
GMAIL_USER = os.environ.get("GMAIL_USER", SECRETS.get("gmail_user", ""))
GMAIL_PASS = os.environ.get("GMAIL_APP_PASS", SECRETS.get("gmail_pass", ""))
PASSWORD = os.environ.get("PGS_PASSWORD", (BASE_DIR / "pw.txt").read_text().strip() if (BASE_DIR / "pw.txt").exists() else "")
TOS_VERSION = "cp-tos-draft-1"
ACCOUNTS = BASE_DIR / "pgs_accounts.jsonl"
YC_KEY = (BASE_DIR / "yescaptcha_key.txt").read_text().strip() if (BASE_DIR / "yescaptcha_key.txt").exists() else os.environ.get("YESCAPTCHA_KEY", "")

CARD = SECRETS.get("card", {})
BD = SECRETS.get("brightdata", {})          # {customer, zone, password}
TS_SITEKEY = SECRETS.get("turnstile_sitekey", "0x4AAAAAAE_-aKARYR1q2ImK")

def bd_proxy(session=None):
    """Bright Data ISP proxy with optional sticky session. Returns urllib ProxyHandler-compatible dict or None."""
    if not BD.get("customer"): return None
    sess = f"-session-{session}" if session else ""
    u = f"brd-customer-{BD['customer']}-zone-{BD.get('zone','isp_proxy1')}{sess}"
    url = f"http://{u}:{BD.get('password','')}@brd.superproxy.io:33335"
    return {"http": url, "https": url}

def bd_pw_proxy(session=None):
    """Same proxy split for playwright/camoufox dict format."""
    if not BD.get("customer"): return None
    sess = f"-session-{session}" if session else ""
    return {"server": "http://brd.superproxy.io:33335",
            "username": f"brd-customer-{BD['customer']}-zone-{BD.get('zone','isp_proxy1')}{sess}",
            "password": BD.get("password", "")}

def openers(proxy=None):
    """urllib opener that optionally routes through proxy."""
    if proxy:
        return urllib.request.build_opener(urllib.request.ProxyHandler(proxy))
    return urllib.request.build_opener()

OPENER = openers()  # replaced per-account in main()

def log(m): print(f"[{datetime.now().strftime('%H:%M:%S')}] {m}", flush=True)

def http(url, data, headers, timeout=30):
    req = urllib.request.Request(url, data=data, headers=headers)
    with OPENER.open(req, timeout=timeout) as r:
        return r.status, json.loads(r.read() or b"{}")

def http_raw(url, data, headers, timeout=30):
    """Like http() but tolerates non-JSON responses."""
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        try:
            return r.status, json.loads(raw or b"{}")
        except Exception:
            return r.status, {"_raw": raw[:300].decode("utf-8", "ignore")}

def gen_email(n):
    tag = "pgs" + "".join(random.choices(string.ascii_lowercase + string.digits, k=9))
    return f"{GMAIL_USER.split('@')[0]}+{tag}@gmail.com"

# ── 0. Turnstile (Supabase signup captcha) ────────────────────────
def solve_turnstile(page_url="https://api.pgsgrove.com/login", tries=3):
    for k in range(tries):
        r = yc_api("createTask", {"clientKey": YC_KEY, "task": {
            "type": "TurnstileTaskProxyless", "websiteURL": page_url, "websiteKey": TS_SITEKEY}})
        tid = r.get("taskId")
        if not tid:
            continue
        for _ in range(40):
            time.sleep(3)
            res = yc_api("getTaskResult", {"clientKey": YC_KEY, "taskId": tid})
            if res.get("status") == "ready":
                return res["solution"]["token"]
            if res.get("errorId"):
                log(f"turnstile attempt {k}: {res.get('errorCode')}")
                break
    return None

def yc_api(fn, payload):
    req = urllib.request.Request(f"https://api.yescaptcha.com/{fn}",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

# ── 1. Signup ─────────────────────────────────────────────────────
def signup(email):
    ctok = solve_turnstile()
    if not ctok:
        return {"error": "turnstile_failed"}
    body = json.dumps({"email": email, "password": PASSWORD,
                       "data": {"signup_source": "api"},
                       "gotrue_meta_security": {"captcha_token": ctok}}).encode()
    st, d = http(f"{SUPABASE}/auth/v1/signup", body,
                 {"Content-Type": "application/json", "apikey": ANON_KEY})
    return d

# ── 2. IMAP verify link ───────────────────────────────────────────
def wait_verify_link(email_tag, timeout=180):
    """Poll Gmail for Supabase/Phoenix Grove confirm link."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            m = imaplib.IMAP4_SSL("imap.gmail.com", timeout=25)
            m.login(GMAIL_USER, GMAIL_PASS)
            m.select("INBOX")
            _, data = m.search(None, "(UNSEEN)")
            ids = data[0].split()[-15:]
            for i in reversed(ids):
                _, md = m.fetch(i, "(RFC822)")
                msg = emaillib.message_from_bytes(md[0][1])
                subj = ((msg.get("Subject") or "") + (msg.get("From") or "")).lower()
                if any(k in subj for k in ("phoenix", "grove", "supabase", "pgsgrove")):
                    body = ""
                    for part in msg.walk():
                        if part.get_content_type() in ("text/plain", "text/html"):
                            body += part.get_payload(decode=True).decode("utf-8", "ignore")
                    links = re.findall(r"https://[^\s\"'<>]+", body)
                    cand = [l.replace("&amp;", "&") for l in links
                            if "verify" in l or "token_hash" in l or "confirmation" in l]
                    m.logout()
                    if cand: return cand[0]
            m.logout()
        except Exception as e:
            log(f"imap err: {e}")
        time.sleep(6)
    return None

def verify_email(link, proxy=None):
    """Follow verify link, grab access_token from 303 Location fragment."""
    class NoRedir(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k): return None
    handlers = [NoRedir]
    if proxy:
        handlers.append(urllib.request.ProxyHandler(proxy))
    op = urllib.request.build_opener(*handlers)
    try:
        r = op.open(link, timeout=25)
        loc = r.url
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location", "")
    m = re.search(r"access_token=([^&#]+)", loc)
    return m.group(1) if m else None

# ── 3. Stripe checkout session ────────────────────────────────────
def create_checkout(token):
    body = json.dumps({"tier": "taster",
                       "success_url": "https://api.pgsgrove.com/plan?checkout=success",
                       "cancel_url": "https://api.pgsgrove.com/plan"}).encode()
    st, d = http(f"{SUPABASE}/functions/v1/stripe-checkout", body,
                 {"Content-Type": "application/json", "apikey": ANON_KEY,
                  "Authorization": f"Bearer {token}"})
    return d.get("url")

# ── 4. Stripe fill + hCaptcha ─────────────────────────────────────
def yc(fn, payload):
    # always DIRECT (never via BD proxy — yesCaptcha is not geo-sensitive)
    req = urllib.request.Request(f"https://api.yescaptcha.com/{fn}",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def solve_hcaptcha(sitekey, pageurl, rqdata=None):
    task = {"type": "HCaptchaTaskProxyless", "websiteURL": pageurl,
            "websiteKey": sitekey, "isInvisible": True, "enterprise": True}
    if rqdata:
        task["rqdata"] = rqdata
    r = yc("createTask", {"clientKey": YC_KEY, "task": task})
    tid = r.get("taskId")
    if not tid:
        log(f"hc task err: {r}")
        return None
    log(f"hc task {tid} rqdata={bool(rqdata)}")
    for _ in range(45):
        time.sleep(4)
        res = yc("getTaskResult", {"clientKey": YC_KEY, "taskId": tid})
        if res.get("status") == "ready":
            return res["solution"]["gRecaptchaResponse"]
        if res.get("errorId"):
            log(f"hc err: {res}")
            return None
    return None

def stripe_fill(checkout_url, pw_proxy=None):
    use_camoufox = True
    try:
        from camoufox.sync_api import Camoufox  # noqa
    except Exception:
        use_camoufox = False
    if use_camoufox:
        try:
            return _stripe_fill_camoufox(checkout_url, pw_proxy)
        except Exception as e:
            log(f"camoufox path failed ({str(e)[:80]}) — falling back to chrome")
    return _stripe_fill_chrome(checkout_url, pw_proxy)

def _stripe_fill_camoufox(checkout_url, pw_proxy=None):
    from camoufox.sync_api import Camoufox
    with Camoufox(proxy=pw_proxy, os=["windows"], headless=False,
                  geoip=bool(pw_proxy)) as browser:
        pg = browser.new_page()
        pg.goto(checkout_url, timeout=90000, wait_until="domcontentloaded")
        pg.wait_for_timeout(8000)
        log(f"camoufox title: {pg.title()}")
        return _fill_and_submit(pg, checkout_url, out_prefix="cam")

def _stripe_fill_chrome(checkout_url, pw_proxy=None):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=False, channel="chrome",
                              args=["--disable-blink-features=AutomationControlled"])
        ctx = b.new_context(viewport={"width": 1280, "height": 950}, locale="en-US",
                            timezone_id="America/Chicago", proxy=pw_proxy,
                            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
        pg = ctx.new_page()
        pg.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        pg.goto(checkout_url, timeout=90000, wait_until="commit")
        ok, info = _fill_and_submit(pg, checkout_url, out_prefix="chr")
        b.close()
        return ok, info

def _fill_and_submit(pg, checkout_url, out_prefix="chr"):
    # wait for card form frame (up to 90s)
    fr = None
    for _ in range(18):
        pg.wait_for_timeout(5000)
        for f in pg.frames:
            try:
                if f.locator('input[name="cardNumber"]').count() > 0:
                    fr = f; break
            except Exception:
                pass
        if fr: break
    if not fr:
        pg.screenshot(path=str(BASE_DIR / "fail_noform.png"))
        try:
            (BASE_DIR / "fail_noform.html").write_text(pg.content(), encoding="utf-8")
        except Exception:
            pass
        return False, "no card form"
    log("stripe form found")
    try:
        fr.locator("text=USD").first.click(timeout=5000)
        pg.wait_for_timeout(1200)
    except Exception:
        pass
    for n, v in [("cardNumber", CARD["num"]), ("cardExpiry", CARD["exp"]),
                 ("cardCvc", CARD["cvc"]), ("billingName", CARD["name"])]:
        el = fr.locator(f'input[name="{n}"]').first
        el.click(timeout=8000); el.type(v, delay=80)
        pg.wait_for_timeout(500)
    log("card typed")
    try:
        fr.locator('select[name="billingCountry"]').first.select_option(
            label="United States", timeout=8000)
        pg.wait_for_timeout(2500)
    except Exception as e:
        log(f"country err {e}")
    for n, v in [("billingAddressLine1", CARD["addr"]),
                 ("billingLocality", CARD["city"]),
                 ("billingPostalCode", CARD["zip"])]:
        for a in range(4):
            try:
                el = fr.locator(f'input[name="{n}"]').first
                el.wait_for(state="visible", timeout=6000)
                el.fill(v, timeout=6000)
                pg.wait_for_timeout(400)
                break
            except Exception:
                pg.wait_for_timeout(1500)
    try:
        fr.locator('select[name="billingAdministrativeArea"]').first.select_option(
            label=CARD["state"], timeout=8000)
        pg.wait_for_timeout(1000)
    except Exception as e:
        log(f"state err {e}")
    pg.screenshot(path=str(BASE_DIR / "stripe_filled.png"), full_page=True)
    # submit FIRST — hCaptcha checkbox modal appears after submit
    fr.locator("button[type=submit]").first.click(timeout=10000)
    log("submitted")
    for i in range(30):
        pg.wait_for_timeout(5000)
        u = pg.url
        log(f"t+{(i+1)*5}s {u[:90]}")
        if "success" in u or "checkout=succ" in u or "pgsgrove" in u:
            pg.screenshot(path=str(BASE_DIR / "stripe_success.png"), full_page=True)
            return True, u
        # detect hCaptcha checkbox modal — scan ALL frames, not just main page
        modal = False
        hc_frame = None
        for f in pg.frames:
            if "hcaptcha" in f.url and "invisible" not in f.url:
                try:
                    if f.locator("#checkbox").count() > 0:
                        modal = True; hc_frame = f; break
                except Exception:
                    pass
            if "hcaptcha.com/captcha" in f.url:
                modal = True; hc_frame = f
        if not modal:
            try:
                t = pg.evaluate("()=>document.body.innerText||''")
                modal = bool(re.search(r"I am human|One more step", t))
            except Exception:
                modal = False
        if modal:
            log("hcaptcha modal visible")
            # STEP 1: real click on the checkbox — camoufox may pass passive evaluation
            clicked = False
            if hc_frame is not None:
                try:
                    cb = hc_frame.locator("#checkbox").first
                    cb.click(timeout=6000, force=True)
                    clicked = True
                    log("checkbox clicked, waiting for passive pass...")
                except Exception as e:
                    log(f"checkbox click err {str(e)[:60]}")
            if clicked:
                passed = False
                for _w in range(6):
                    pg.wait_for_timeout(3000)
                    still = False
                    for f in pg.frames:
                        if "hcaptcha" in f.url and "invisible" not in f.url:
                            try:
                                if f.locator("#checkbox").count() > 0: still = True
                            except Exception: pass
                    if not still:
                        passed = True; break
                if passed:
                    log("captcha passed via real click!")
                    try:
                        fr.locator("button[type=submit]").first.click(timeout=8000)
                    except Exception: pass
                    continue
                log("passive pass failed — falling back to solver")
            # STEP 2: solve via YesCaptcha and inject (enterprise: needs rqdata)
            sitekey = None; rqdata = None
            for f in pg.frames:
                m = re.search(r"sitekey=([a-fA-F0-9-]+)", f.url)
                if m and "hcaptcha" in f.url:
                    sitekey = m.group(1)
                    r = re.search(r"rqdata=([^&#]+)", f.url)
                    if r: rqdata = r.group(1)
                    break
            if not sitekey:
                try:
                    sitekey = pg.evaluate("()=>{for(const f of document.querySelectorAll('iframe[src*=hcaptcha]')){const m=f.src.match(/sitekey=([a-fA-F0-9-]+)/);if(m)return m[1];}return null;}")
                except Exception:
                    pass
            if not rqdata:
                for f in pg.frames:
                    try:
                        srcs = f.evaluate("()=>Array.from(document.querySelectorAll('iframe')).map(i=>i.src).join('\\n')")
                        r = re.search(r"rqdata=([^&#\"]+)", srcs or "")
                        if r: rqdata = r.group(1); break
                    except Exception:
                        pass
            log(f"sitekey: {sitekey} rqdata: {bool(rqdata)}")
            if sitekey:
                tok = solve_hcaptcha(sitekey, checkout_url.split("#")[0], rqdata)
                if tok:
                    for f in pg.frames:
                        try:
                            f.evaluate("""(token)=>{
                                document.querySelectorAll('textarea[name="h-captcha-response"],textarea[name="g-recaptcha-response"]').forEach(ta=>{ta.value=token;});
                                document.querySelectorAll('input[name="h-captcha-response"]').forEach(i=>{i.value=token;});
                                if(window.hcaptcha){try{Object.keys(window.hcaptcha._psts||{}).forEach(w=>window.hcaptcha.setResponse(token,w));}catch(e){}}
                                if(window.onHCaptchaSuccess){try{window.onHCaptchaSuccess(token);}catch(e){}}
                                const cel=document.querySelector('[data-callback]');
                                if(cel){const cb=cel.getAttribute('data-callback');if(window[cb]){try{window[cb](token);}catch(e){}}}
                            }""", tok)
                        except Exception:
                            pass
                    # postMessage challenge-passed from inside hcaptcha frames
                    for f in pg.frames:
                        if "hcaptcha" in f.url:
                            try:
                                f.evaluate("""(token)=>{window.parent.postMessage(JSON.stringify({source:'hcaptcha',label:'challenge-closed',contents:{event:'challenge-passed',response:token,expiration:120}}),'*');}""", tok)
                            except Exception:
                                pass
                    log("token injected via setResponse — waiting for callback")
                    # setResponse triggers the captcha callback which closes the modal;
                    # do NOT click #checkbox (that opens a visual puzzle challenge)
                    pg.wait_for_timeout(6000)
                    try:
                        fr.locator("button[type=submit]").first.click(timeout=8000)
                        log("resubmitted after captcha")
                    except Exception as e:
                        log(f"resubmit err {str(e)[:80]}")
        try:
            al = [a for a in fr.locator('[role=alert]').all_inner_texts() if a.strip()]
            if al: log(f"ALERT: {al[:2]}")
            # stripe "connection issues" -> re-click submit (up to 3 times)
            body_txt = pg.evaluate("()=>document.body.innerText||''")
            if "connection issues" in body_txt and i % 3 == 0:
                log("connection issues banner — resubmitting")
                fr.locator("button[type=submit]").first.click(timeout=8000)
        except Exception:
            pass
    pg.screenshot(path=str(BASE_DIR / "stripe_after.png"), full_page=True)
    final = pg.url
    return False, final

# ── 5. Mint plan key ──────────────────────────────────────────────
def mint_key(token):
    raw = "pgsk_plan_" + os.urandom(32).hex()
    h = hashlib.sha256(raw.encode()).hexdigest()
    display = raw[:15] + "\u2026" + raw[-4:]
    body = json.dumps({"p_key_hash": h, "p_display_prefix": display,
                       "p_name": None, "p_tos_version": TOS_VERSION}).encode()
    st, d = http(f"{SUPABASE}/rest/v1/rpc/api_mint_plan_key", body,
                 {"Content-Type": "application/json", "apikey": ANON_KEY,
                  "Authorization": f"Bearer {token}"})
    return raw if d.get("ok") else None, d

def test_key(key):
    body = json.dumps({"model": "glm-5.3-flash",
                       "messages": [{"role": "user", "content": "say OK"}],
                       "max_tokens": 5}).encode()
    try:
        st, d = http("https://api.pgsgrove.com/v1/chat/completions", body,
                     {"Content-Type": "application/json",
                      "Authorization": f"Bearer {key}"}, timeout=40)
        return d["choices"][0]["message"]["content"].strip()
    except urllib.error.HTTPError as e:
        return f"HTTP{e.code}:{e.read()[:120]}"

# ── Main ──────────────────────────────────────────────────────────
def register_one(idx):
    global OPENER
    rec = {"idx": idx, "ts": datetime.now().isoformat(), "status": "start"}
    em = gen_email(idx)
    rec["email"] = em
    # per-account sticky Bright Data session (US exit) — signup/checkout/verify all via proxy
    session = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    rec["bd_session"] = session
    prox = bd_proxy(session)
    OPENER = openers(prox)
    log(f"[{idx}] {em} (bd session {session})")
    try:
        d = signup(em)
        if not d.get("id"):
            rec.update(status="signup_fail", err=str(d)[:200]); return rec
        rec["user_id"] = d["id"]
        rec["status"] = "signed_up"
    except urllib.error.HTTPError as e:
        rec.update(status="signup_fail", err=f"{e.code} {e.read()[:150]}"); return rec
    link = wait_verify_link(em)
    if not link:
        rec["status"] = "no_verify_mail"; return rec
    rec["status"] = "mail_found"
    token = verify_email(link, prox)
    if not token:
        rec["status"] = "verify_fail"; return rec
    rec["status"] = "verified"
    checkout = create_checkout(token)
    if not checkout:
        rec["status"] = "checkout_fail"; return rec
    rec["status"] = "checkout_created"
    ok, info = False, "not attempted"
    for attempt in range(3):
        try:
            # NB: Bright Data zone blocks stripe.com (NS_ERROR_PROXY_FORBIDDEN),
            # so the Stripe step runs WITHOUT proxy — camoufox alone passes Radar.
            ok, info = stripe_fill(checkout)
        except Exception as e:
            ok, info = False, f"exc:{str(e)[:120]}"
        if ok:
            break
        log(f"stripe attempt {attempt+1} failed: {str(info)[:80]} — regenerating checkout")
        time.sleep(5)
        checkout = create_checkout(token)
        if not checkout:
            break
    rec["stripe"] = "ok" if ok else f"fail:{info}"
    if not ok:
        rec["status"] = "stripe_fail"; return rec
    # subscription webhook may lag — retry mint a few times
    raw = None
    for attempt in range(6):
        time.sleep(10)
        raw, d = mint_key(token)
        if raw: break
        log(f"mint retry {attempt}: {d}")
    if not raw:
        rec.update(status="mint_fail", err=str(d)[:200]); return rec
    rec["api_key"] = raw
    rec["status"] = "done"
    resp = test_key(raw)
    rec["key_test"] = resp
    log(f"[{idx}] DONE key works: {resp}")
    return rec

def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    for i in range(n):
        rec = register_one(i)
        with open(ACCOUNTS, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        log(f"[{i}] status={rec['status']}")
        if i < n - 1:
            time.sleep(random.randint(30, 90))

if __name__ == "__main__":
    main()
