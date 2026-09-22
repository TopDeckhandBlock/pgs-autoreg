# pgs-autoreg

Autoreg + free-trial farming for PGSGrove / Phoenix Grove AI API (api.pgsgrove.com).

Full pipeline, mostly pure HTTP (Supabase backend), Playwright only for the Stripe card form:

1. **Signup** — Supabase anon key extracted from the SPA bundle (`/assets/index-*.js`), `POST {supabase}/auth/v1/signup` with gmail `+alias`
2. **Email verify** — poll Gmail IMAP for the confirm link → follow `/auth/v1/verify?token=...` → 303 with `access_token` in the URL fragment
3. **Checkout session** — edge function `POST {supabase}/functions/v1/stripe-checkout` `{tier:"taster"}` → live `checkout.stripe.com` URL (first month free, $3.99 after)
4. **Stripe fill** — Playwright headless: card fields (`cardNumber`/`cardExpiry`/`cardCvc`/`billing*`), country=US, currency=USD, then invisible **hCaptcha solved via YesCaptcha** (`HCaptchaTaskProxyless`) + token injection into all frames
5. **Key mint** — RPC `api_mint_plan_key` with SHA-256 of a generated `pgsk_plan_<64hex>` raw key → raw key returned locally
6. **Verify** — `POST https://api.pgsgrove.com/v1/chat/completions` with the new key (glm-5.3-flash "say OK")

Accounts append to `pgs_accounts.jsonl` (email, user_id, api_key, status, key_test).

## Run

```bash
PYTHONPATH="" C:/Users/User/AppData/Local/Programs/Python/Python311/python.exe pgs_autoreg.py N
```

Requires: Python 3.11 + playwright, `anonkey.txt` (Supabase anon key), `yescaptcha_key.txt` or `YESCAPTCHA_KEY` env.

## Notes

- Taster plan: DeepSeek V4 Flash 0731 + GLM 5.3 Flash, 18M+ tokens/mo, first month free
- Stripe checkout sessions are single-use — regenerate via the edge function if expired
- The plan key only mints AFTER Stripe confirms the subscription (webhook lag) — script retries mint 6x with 10s backoff
- `api_mint_plan_key` errors: `tier` (no active plan), `tos` (wrong tos_version), `key_limit`, `disabled`
- tos_version constant: `cp-tos-draft-1`
