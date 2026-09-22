# pgs-autoreg

Autoreg + free-trial farming for PGSGrove / Phoenix Grove AI API (api.pgsgrove.com).

Taster plan: first month **free**, then $3.99/mo. Full pipeline:

1. **Turnstile** — Supabase signup is captcha-protected (Cloudflare Turnstile, sitekey `0x4AAAAAAE_-aKARYR1q2ImK`). Solved via YesCaptcha `TurnstileTaskProxyless`.
2. **Signup** — Supabase anon key extracted from the SPA bundle (`/assets/index-*.js`), `POST {supabase}/auth/v1/signup` + `gotrue_meta_security.captcha_token`
3. **Proxy** — per-account sticky **Bright Data** ISP session (`brd-customer-…-zone-…-session-…@brd.superproxy.io:33335`). Without proxy Supabase returns 403 "Signups are not accepted from this address" (IP ban after a few signups).
4. **Email verify** — poll Gmail IMAP for the confirm link → follow `/auth/v1/verify?token=...` → 303 with `access_token` in the Location fragment (through the same proxy)
5. **Checkout session** — edge function `POST {supabase}/functions/v1/stripe-checkout` `{tier:"taster"}` → live `checkout.stripe.com` URL. Sessions are single-use; regenerate on retry.
6. **Stripe fill** — **Camoufox** (Firefox antidetect) primary: playwright/chromium gets stuck on a skeleton or silently rejected by invisible hCaptcha. Card fields → country US → ZIP. hCaptcha checkbox modal after submit → solved via YesCaptcha `HCaptchaTaskProxyless` + token injection into all frames + checkbox click + resubmit. Fallback: system Chrome (`channel="chrome"`, headful).
7. **Key mint** — RPC `api_mint_plan_key` with SHA-256 of a generated `pgsk_plan_<64hex>` raw key. Only mints AFTER Stripe confirms the subscription (webhook lag → 6x retry/10s). Errors: `tier` (no active plan), `tos`, `key_limit`, `disabled`. tos_version: `cp-tos-draft-1`.
8. **Verify** — `POST https://api.pgsgrove.com/v1/chat/completions` with the new key (glm-5.3-flash "say OK")

Accounts append to `pgs_accounts.jsonl` (email, user_id, bd_session, api_key, status, key_test).

## Run

```bash
PYTHONPATH="" C:/Users/User/AppData/Local/Programs/Python/Python311/python.exe pgs_autoreg.py N
```

Requires: Python 3.11 + playwright + camoufox (`pip install camoufox` and browser fetched to
`%LOCALAPPDATA%\camoufox\camoufox\Cache\browsers\official\<ver>\` with a `version.json` containing the
correct sha256 of the release zip, plus a `.0.5_FLAG` file in `Cache/` — otherwise camoufox wipes and
re-downloads on every launch).

Secret files (gitignored): `anonkey.txt` (Supabase anon key), `yescaptcha_key.txt`, `pw.txt`,
`secrets.json`:
```json
{"gmail_user":"…","gmail_pass":"…","card":{"num":"…","exp":"…","cvc":"…","name":"…","addr":"…","city":"…","state":"…","zip":"…"},
 "brightdata":{"customer":"…","zone":"isp_proxy1","password":"***"}}
```

## Notes / pitfalls

- **403 "not accepted from this address"** = IP ban, not email ban. Always use fresh BD sticky session per account.
- **400 captcha_failed** = signup without Turnstile token (captcha was added to the site mid-project).
- **Stripe skeleton page (no card form)** = headless/automation detected. Use headful + real Chrome or Camoufox.
- Stripe shows currency from the session-creation IP (UAH from UA, USD from US) — irrelevant for a $0 trial.
- "connection issues" banner after submit = silent reject; resubmit after captcha solve.
- Camoufox first launch downloads addons (~1 min). If `installed_verstr()` raises `CamoufoxNotInstalled`,
  check `Cache/config.json` → `active_version` matches an existing `browsers/…` dir with `version.json`.
