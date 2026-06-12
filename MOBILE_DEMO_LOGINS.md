# Hypershop — Default Mobile App Logins

Ready-to-use demo accounts for every Hypershop mobile app. Seed them with:

```bash
python -m scripts.seed_mobile_logins      # idempotent; run inside the api container / on the host
```

(All three login methods below are verified end-to-end against the API.)

| App(s) | Login method | Credentials |
|---|---|---|
| **customer-android / customer-hms / customer-ios** | email + password | **customer@hypershop.dev** / **Customer@Local12** |
| **rider-android / rider-hms** | phone + OTP | phone **+8801700000002**, OTP = **any 6 digits** (e.g. `000000`) when `OTP_DEV_BYPASS=true` |

The rider account also has a password (**rider@hypershop.dev** / **Rider@Local12**) so the
email+password screen works too, if the app build exposes one.

## Making these work on the deployed backend (Render)

The signed apps call `https://api.hypershop.com.bd`, so the demo logins only work
after the backend is deployed (see `DEPLOY_RENDER.md`). Then:

1. **Seed the accounts** — open the Render `hypershop-api` service → **Shell**:
   ```bash
   python -m scripts.seed_mobile_logins
   ```
2. **Enable rider OTP without SMS** (demo/staging only) — Env Group
   `hypershop-shared` → set **`OTP_DEV_BYPASS=true`**. Now any 6-digit code logs the
   rider in. For **real production**, leave it unset (false) and configure a real SMS
   provider instead — otherwise anyone could log in as any phone.

> ⚠️ These are DEMO credentials. Don't ship `OTP_DEV_BYPASS=true` or these accounts
> to a real public production environment.
