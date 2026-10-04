# Market Tracker

A personal portfolio dashboard that runs on your own computer. Tracks US and Singapore watchlists and positions, compares them with benchmarks, surfaces news, shows a sector heatmap — and now watches your portfolio for you with alert rules that reach your phone.

Plain Python (standard library only) + static HTML/CSS/JS. No installs, no build step, no database.

## Quick start (Windows)

1. Install Python 3.10+ from python.org (tick "Add Python to PATH").
2. Double-click `run.bat`.
3. Your browser opens the app automatically (http://127.0.0.1:8000/). **Create your account** (first run only). Save the recovery key it shows you.
4. Optional but recommended: **Account & security › Two-factor** to add a code from your phone at sign-in.

Upgrading from 0.1? Your `watchlist.json` is imported into the first account's encrypted vault and the plaintext file is deleted.

No internet, or just want a look? Double-click `run-demo.bat` for synthetic data (it uses a separate `data-demo/` folder).

Mac/Linux: `./run.sh` (or `python3 app.py`). Demo: `python3 app.py --demo`.

## Launch options

| Script / flag | What it does |
|---|---|
| `run.bat` / `run.sh` | Local only (127.0.0.1:8000) |
| `run-lan.bat` / `run-lan.sh` | Reachable from other devices on your Wi-Fi; prints the network URL |
| `run-demo.bat` / `--demo` | Synthetic prices and headlines, no network needed |
| `--no-browser` | Don't open the browser automatically on start |
| `python app.py --list-users` | List accounts and whether 2FA is on |
| `python app.py --disable-2fa NAME` | Turn off 2FA for someone who lost their phone *and* backup codes (stop the server first) |

Environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `STOCK_DASHBOARD_HOST` | `127.0.0.1` | Bind address (`0.0.0.0` for LAN) |
| `STOCK_DASHBOARD_PORT` | `8000` | Port; falls back to a free port if busy |
| `STOCK_DASHBOARD_DATA_DIR` | `data/` | Where accounts and vaults are stored |
| `STOCK_DASHBOARD_CERT` / `_KEY` | `certs/cert.pem`, `certs/key.pem` if present | Turns on HTTPS |
| `STOCK_DASHBOARD_DEMO` | unset | `1` = demo mode |

## Security model

| Layer | How it works |
|---|---|
| Passwords | PBKDF2-HMAC-SHA256, 600,000 iterations, per-user salt. Min. 10 characters; common passwords and ones containing the username are refused. |
| Two-factor | Optional TOTP (RFC 6238, 30 s, 6 digits). QR set-up, 10 hashed one-time backup codes, used codes can't be replayed. |
| Encryption at rest | Each account has a random 256-bit data key. It seals the user's vault file (`data/users/<name>/vault.dat`): watchlist, positions, alert rules, notification history and channel secrets such as bot tokens and SMTP passwords. The data key is stored only in wrapped form — once under your password and once under your recovery key. Cipher: HMAC-SHA256 counter-mode keystream + HMAC-SHA256 tag (encrypt-then-MAC), standard library only. |
| Recovery | The recovery key (shown once at sign-up) resets a forgotten password without losing data. Without it or the password the vault cannot be decrypted — by design. |
| Sessions | 256-bit random tokens, `HttpOnly; SameSite=Strict` cookie (`Secure` over HTTPS). 12 h and 60 min idle, or 30 days with "keep me signed in". All sessions end when the server stops. |
| Brute force | 5 wrong passwords or codes lock the account for 15 min; 20 failures lock the IP address. |
| Web hardening | CSRF token on every change + Origin check, strict Content-Security-Policy (no inline scripts), no framing, no-sniff, no-referrer. |
| Audit | Account & security › Activity lists sign-ins, failures, password and 2FA changes. |

**Background alerts and the data key.** After you sign in, the server keeps your data key in memory so alert rules keep running after you close the browser. Stopping the server (or Account & security › *Lock vault*) forgets it; alerts resume after your next sign-in.

**What this does not protect against:** malware running as you on the same computer while the server is running. Keep Windows updated and don't run the app on a shared account.

### HTTPS (recommended for LAN mode)

Without HTTPS, passwords and data cross your Wi-Fi unencrypted. To turn it on, create a certificate and key named `cert.pem` and `key.pem` in a `certs` folder next to `app.py` — the server picks them up automatically.

With OpenSSL (included with Git for Windows: `"C:\Program Files\Git\usr\bin\openssl.exe"`), replacing the IP with your computer's LAN address:

```bash
mkdir certs
openssl req -x509 -newkey rsa:2048 -nodes -days 825 -keyout certs/key.pem -out certs/cert.pem \
  -subj "/CN=market-tracker" -addext "subjectAltName=DNS:localhost,IP:127.0.0.1,IP:192.168.1.20"
```

Browsers will warn about a self-signed certificate the first time; accept it once per device. (`mkcert` gives you a certificate your devices trust without warnings.) Do not expose the app to the internet.

## Alert rules and notifications

Rules are checked every 1–60 minutes (default 5) and fire when a condition *becomes* true, then wait for their quiet period before firing again.

| Channel | Set-up |
|---|---|
| Bell + desktop pop-ups | Built in. Pop-ups need the dashboard open, and HTTPS or `127.0.0.1`. |
| Telegram | Create a bot with @BotFather, message it once, get your chat ID from @userinfobot. |
| ntfy | Install the ntfy app, subscribe to a long random topic name, enter the same name. |
| Email | Any SMTP server. Gmail: `smtp.gmail.com`, 465, SSL, with an app password. |
| Discord / Slack | Paste an incoming-webhook URL. |

## Files

```
app.py              server, routes, market data fetchers, demo data
security.py         accounts, passwords, 2FA, sessions, encrypted vaults
alerts.py           alert rules, background checker, notification channels
static/login.html   sign-in, sign-up, 2FA and recovery screens (+ login.js)
static/index.html   dashboard structure, card template, modals
static/app.js       dashboard behaviour
static/account.js   Account & security modal
static/alerts.js    alert rules, channels, notification bell
static/vendor/qrcode.js   QR code generator (MIT, Kazuhiko Arase)
static/styles.css   themes and layout
data/server.json                   server-wide settings (allow sign-ups)
data/users/<name>/account.json     that user's sign-in record: salted verifier, wrapped keys, 2FA, activity log
data/users/<name>/vault.dat        that user's encrypted data: watchlist, positions, rules, channels, settings
CHANGELOG.md        release notes; top version is shown in the footer
```

**Back up the `data/` folder** (it is encrypted, so the backup is safe to store in the cloud). It is git-ignored.

## API

`/api/auth/*` works without a session; everything else needs the session cookie, and every POST needs the `X-CSRF-Token` header returned by `GET /api/account`.

| Route | Purpose |
|---|---|
| `GET /api/auth/state` | Set-up required? sign-ups open? signed in? |
| `POST /api/auth/setup` · `signup` · `login` · `2fa` · `recover` · `logout` | Account creation, sign-in (with 2FA challenge), password reset with recovery key |
| `GET /api/account` · `sessions` · `activity` · `export` | Profile + CSRF token, devices, audit log, JSON export |
| `POST /api/account/password` · `2fa/begin` · `2fa/enable` · `2fa/disable` · `2fa/backup-codes` · `recovery-key` · `sessions/revoke` · `lock` · `settings` | Security actions |
| `GET/POST /api/watchlist` | Watchlist `{symbols:[{symbol,label,market,position?,averagePrice?}]}` |
| `GET /api/alerts` | Rules with live status, settings, masked channels |
| `POST /api/alerts/rule` · `rule/delete` · `rule/toggle` · `settings` · `test` · `check` | Manage rules and channels, send a test, check now |
| `GET /api/notifications` · `POST /api/notifications/read` · `clear` | Notification centre |
| `GET /api/chart` · `market-overview` · `market-sector` · `news` · `search` · `fx` · `version` | Market data (unchanged) |

Data sources: Yahoo Finance (chart, search), Stooq (chart fallback), Google News RSS, Frankfurter (FX). Responses are cached on the server for 60 s (prices), 10 min (news), and 30 min (FX).

## Git

```bash
git init && git add . && git commit -m "Market Tracker 0.2.0"
```
`data/`, `certs/` and `watchlist.json` are ignored so personal data and keys never reach a repository.
