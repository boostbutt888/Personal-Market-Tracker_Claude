# Changelog

All notable changes to Market Tracker. The first `## [x.y.z]` heading is read at runtime as the release version.

## [0.3.0] - 2026-10-05

Each user now has a separate data store, and display settings are saved with the account.

### Changed
- **One folder per user**: `data/users/<username>/` holds that user's `account.json` (sign-in record) and `vault.dat` (encrypted watchlist, positions, alert rules, channels, notifications, settings). Server-wide settings moved to `data/server.json`.
- Data from 0.2 (`data/users.json`, `data/vaults/`) is moved into the new layout automatically on first start. The old index is kept as `users.json.migrated`.

### Added
- **Display settings are saved per user**: theme, currency, chart range, region, refresh mode, section order, collapsed panels and benchmark choices are stored in the user's encrypted store and follow them to any browser. Two people sharing one browser no longer share settings.
- Display settings are included in the JSON export.
- Usernames that Windows cannot use as folder names (e.g. `con`, `nul`) are refused.

## [0.2.1] - 2026-10-05

Opens in your browser on start, and the version is easier to see.

### Added
- The app opens in your default browser when the server starts. Add `--no-browser` (or set `STOCK_DASHBOARD_NO_BROWSER=1`) to skip.
- Version number next to the title, in the footer and on the sign-in page.
- **Release notes** window: click the version to read these notes in the app. A "New" tag shows until you have opened the notes for the installed version.

### Changed
- Without git the version shows as `v0.2.1` instead of `v0.2.1+0.unknown`.

## [0.2.0] - 2026-09-27

Security, encrypted storage and portfolio alerts.

### Added
- **Accounts** replace the shared `1010` passcode: username + password (min. 10 characters, common passwords refused), PBKDF2-SHA256 with 600,000 iterations.
- **Change password** from Account & security; other signed-in devices are signed out.
- **Optional two-factor sign-in** (TOTP, works with any authenticator app) with QR set-up, 10 one-time backup codes, and replay protection.
- **Recovery key** shown once at sign-up; resets a forgotten password without losing data. Can be regenerated.
- **Encrypted vault per account**: watchlist, positions, alert rules, notification history and channel secrets are sealed with a random 256-bit data key (encrypt-then-MAC, HMAC-SHA256). The key is protected by the password and the recovery key and only exists in server memory after sign-in.
- Existing `watchlist.json` is imported into the first account's vault, then the plaintext file is deleted.
- **Sessions**: random server-side tokens in `HttpOnly; SameSite=Strict` cookies, 12-hour sessions with 60-minute idle sign-out, or 30 days with "keep me signed in". Sessions list with remote sign-out; "Lock vault & sign out everywhere".
- **Brute-force protection**: 5 failed attempts lock the account for 15 minutes (20 per IP address).
- **Activity log** of sign-ins, failures, password and 2FA changes.
- **Hardening**: CSRF tokens + Origin checks on every change, Content-Security-Policy, no-frames, no-sniff, no-referrer headers; optional **HTTPS** (drop `cert.pem`/`key.pem` into `certs/`).
- Optional multi-user: the owner can let others on the network create their own accounts, each with a separate encrypted vault.
- **Alert rules** (12 types): price above/below, daily move up/down, % off 3-month high, crossing the 20/50/100/200-day average, take-profit / stop-loss on cost, portfolio up/down on the day, and concentration (one holding > X%). Edge-triggered with a quiet period, "fire once" option, notes and one-click presets.
- **Background checker** runs every 1–60 minutes while the server runs, even with the browser closed.
- **Notifications**: bell with unread badge, desktop pop-ups, Telegram, ntfy phone push, email (SMTP), Discord/Slack webhooks, quiet hours, per-channel test button.
- Export watchlist and rules as JSON. Console tools: `--list-users`, `--disable-2fa USER`.

### Changed
- `STOCK_DASHBOARD_CODE` is no longer used. Data now lives in `data/` (`data-demo/` in demo mode).

## [0.1.0] - 2026-09-27

First prototype, rebuilt from the Codex P1 Markets dossier.

### Added
- Standard-library Python server with passcode gate (`1010`), local and LAN launch scripts, automatic port fallback.
- Watchlist stored in `watchlist.json` with separate US and Singapore lists plus an All view.
- Stock cards with sparkline, range high/low, day change (percent or value), pre-market / after-hours line, and position P&L.
- Portfolio summary strip: holdings value, day P&L, unrealized P&L, breadth, leader/laggard — converted USD<->SGD both ways.
- NYSE and SGX open/closed clocks with time to next open/close.
- Ticker search suggestions (Yahoo Finance search).
- Rules-based action prompts (breakout, pullback, risk check, manage winner, hold, wait).
- Educational options ideas with volatility-based strike estimates and focus filters.
- Watchlist-vs-benchmark chart (SPY/QQQ/DIA/VIX or STI/ES3/G3B/EWS) with hover tooltip.
- Google News headlines per ticker (last 3 days, max 5).
- Market map heatmap for broad market, 11 SPDR sectors and the STI, with member drilldowns.
- Settings: manual/auto refresh, theme (auto/light/dark/random palette), display currency, change mode, range, section order.
- `--demo` mode with deterministic synthetic data for offline use.

### Changed vs. original spec
- Market snapshots use parallel Yahoo chart calls (the v7 quote API now requires a crumb token) with a 60-second server cache.
- USD/SGD rate is fetched server-side (`/api/fx`) with a Yahoo `SGD=X` fallback.
- Auth cookie holds a derived token rather than the raw passcode; cookie lasts 30 days; `/logout` added.
