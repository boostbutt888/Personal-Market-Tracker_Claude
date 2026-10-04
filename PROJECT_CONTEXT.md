# Project Context: Codex P1 Markets

This file is the rebuild dossier for the local stock-market dashboard in this repository. It is written so the project can be recreated from a blank folder without needing to infer behavior from memory.

## Identity

- Project name: `Codex-P1-Markets`
- App name shown in the UI: `Codex Markets dashboard`
- Product concept: a local, passcode-protected stock dashboard for daily watchlist review.
- Primary use case: run a small Python server on a personal computer, open a browser dashboard, track US and Singapore watchlists, compare against benchmarks, scan news, and review market-sector heatmaps.
- Runtime style: no build step, no package manager, no framework. The app is plain Python plus static HTML, CSS, and JavaScript.

## Repository Layout

```text
.
├── app.py
├── static/
│   ├── index.html
│   ├── app.js
│   └── styles.css
├── watchlist.json
├── README.md
├── MANUAL.md
├── CHANGELOG.md
├── run.bat
├── run.sh
├── run-lan.bat
└── run-lan.sh
```

File responsibilities:

- `security.py` (v0.2): accounts, 2FA, sessions, encrypted vaults. `alerts.py` (v0.2): alert rules, checker, channels.
- `app.py`: Python HTTP server, login gate, local API routes, watchlist persistence, Yahoo/Stooq/Google News data fetchers, version metadata, and market-sector definitions.
- `static/index.html`: page structure, dashboard panels, modals, stock-card template, and links to CSS/JS assets.
- `static/app.js`: all browser behavior, local state, API calls, charts, heatmaps, options ideas, action prompts, benchmark comparison, search suggestions, modals, and persistence in `localStorage`.
- `static/styles.css`: all visual styling, theme variables, responsive layout, card styling, modal styling, charts, heatmaps, and theme variants.
- `watchlist.json`: local user data store for symbols, labels, markets, positions, and average prices.
- `README.md`: setup and Git workflow notes.
- `MANUAL.md`: end-user operating manual.
- `CHANGELOG.md`: release history; `app.py` reads this for the base release version.
- `run.*`: local launch scripts.
- `run-lan.*`: LAN launch scripts that bind the server to all network interfaces.

## Runtime And Dependencies

Required:

- Python 3.10+ recommended. The code uses modern type-hint syntax such as `str | None`.
- A browser with modern JavaScript and SVG support.
- Internet access at runtime for external market data, news, and USD/SGD exchange rates.

Not required:

- No Flask, Django, FastAPI, Node, npm, Vite, React, or external Python packages.
- No database.
- No build command.

The server uses only Python standard-library modules:

- `http.server`, `urllib`, `json`, `csv`, `io`, `xml.etree.ElementTree`, `datetime`, `email.utils`, `cookies`, `socket`, `subprocess`, `pathlib`, `re`, `os`.

## Launching

Default local mode:

```bash
python app.py
```

or:

```bash
python3 app.py
```

Windows helper:

```bat
run.bat
```

Mac/Linux helper:

```bash
./run.sh
```

LAN mode:

```bash
./run-lan.sh
```

or:

```bat
run-lan.bat
```

Default environment:

- `STOCK_DASHBOARD_HOST`: defaults to `127.0.0.1`
- `STOCK_DASHBOARD_PORT`: defaults to `8000`

LAN helpers set:

```text
STOCK_DASHBOARD_HOST=0.0.0.0
```

If port `8000` is busy, `app.py` catches `EADDRINUSE`, binds to port `0`, and prints the fallback URL.

## Authentication (v0.2.0 — replaces the 1010 passcode)

Implemented in `security.py`; see README › Security model for the user-facing summary.

- Storage (v0.3.0): one folder per user. `data/users/<username>/account.json` holds username, role (`owner` for the first account, `member` after), PBKDF2-SHA256 salt/iterations (600,000), `auth` verifier, data key wrapped by the password KEK and by the recovery key, TOTP state, hashed backup codes, last 60 audit events. `data/users/<username>/vault.dat` holds `{watchlist, alerts:{rules, settings, channels}, notifications, prefs}` sealed with that user's 256-bit data key (HMAC-SHA256 CTR keystream + HMAC-SHA256 tag, context-bound). `data/server.json` holds server-wide settings. The v0.2 layout (`data/users.json`, `data/vaults/<id>.vault`) is migrated on start. `data-demo/` is used in `--demo` mode; `STOCK_DASHBOARD_DATA_DIR` overrides.
- Display preferences (the `stock-dashboard-*` localStorage keys) are mirrored to `prefs` in the vault via `GET/POST /api/prefs` and applied on sign-in.
- First account imports `watchlist.json` into its vault and deletes the plaintext file. `watchlist.json` is no longer read after that.
- Sessions are in memory only (server restart signs everyone out and locks vaults). Cookie `mt_session`: `Path=/; HttpOnly; SameSite=Strict` (+ `Secure` over HTTPS); 12 h / 60 min idle, or 30 days with "remember". Requests with `X-Background: 1` don't reset the idle timer.
- Every non-auth POST needs `X-CSRF-Token` (from `GET /api/account`); all POSTs must be JSON and pass an Origin check.
- Throttling: 5 failures per username or 20 per IP → 15-minute lock.
- 2FA: TOTP (SHA1, 6 digits, 30 s, ±1 step, replay-protected), 10 backup codes; recovery key resets the password without data loss.
- Unauthenticated: `/login` (static `login.html` + `login.js`), `/static/login.js`, `/static/styles.css`, `/api/auth/*`. Everything else redirects to `/login` or returns 401.
- Security headers on every response: CSP (no inline script), X-Frame-Options DENY, nosniff, no-referrer, Permissions-Policy, COOP; HSTS when HTTPS.
- HTTPS: `certs/cert.pem` + `certs/key.pem` (or `STOCK_DASHBOARD_CERT`/`_KEY`).
- Console: `python app.py --list-users`, `python app.py --disable-2fa NAME`.

## Alerts (v0.2.0)

Implemented in `alerts.py`; UI in `static/alerts.js`.

- 12 rule types: `price_above`, `price_below`, `day_up`, `day_down`, `off_high` (3-month), `cross_above_ma`, `cross_below_ma` (20/50/100/200), `take_profit`, `stop_loss` (vs average price), `portfolio_up`, `portfolio_down`, `concentration` (region `US`/`SG`/`ALL`, FX-converted).
- Edge-triggered (`lastMet` false → true; cross rules take a baseline first), per-rule quiet period (`cooldownMinutes`), `repeat: once|rearm`.
- Background thread checks unlocked accounts every `intervalMinutes` (1–240, default 5). Uses 1-year daily charts via the existing cached `get_chart`.
- Channels: bell + browser `Notification` (client polls `/api/notifications` every 60 s), Telegram, ntfy, SMTP email, Discord/Slack webhook; quiet hours mute external channels. Secrets are masked in API responses; a blank secret on save keeps the stored value.

## Server Architecture

`app.py` defines constants first, then data helpers, external fetchers, API builders, and finally `StockDashboardHandler`.

Important constants:

- `ROOT`: project root.
- `STATIC_DIR`: `ROOT / "static"`.
- `WATCHLIST_FILE`: `ROOT / "watchlist.json"`.
- `CHANGELOG_FILE`: `ROOT / "CHANGELOG.md"`.
- `HOST`: from `STOCK_DASHBOARD_HOST`, default `127.0.0.1`.
- `PORT`: from `STOCK_DASHBOARD_PORT`, default `8000`.
- `ACCESS_CODE`: `1010`.
- `MARKET_SEGMENTS`: broad market and sector ETF definitions.
- `MARKET_BROAD_STOCKS`: drilldown members for `SPY`, `QQQ`, `DIA`, and `IWM`.
- `MARKET_SECTOR_STOCKS`: drilldown members for sector ETFs such as `XLK`, `XLF`, `XLV`, etc.
- `MARKET_DRILLDOWN_STOCKS`: merged broad and sector drilldown map.

Main server lifecycle:

1. `main()` reads host/port.
2. It creates `ThreadingHTTPServer`.
3. If the configured port is busy, it binds to an available port.
4. It prints reachable URLs.
5. It serves forever until interrupted.

## API Routes

All API routes require the auth cookie except `/login`.

### `GET /`

Serves `static/index.html`.

### `GET /login`

Serves the generated login page.

### `POST /login`

Accepts form body:

```text
code=1010
```

On success, sets the auth cookie and redirects to `/`.

### `GET /api/watchlist`

Returns the normalized local watchlist.

Response shape:

```json
{
  "symbols": [
    {
      "symbol": "AAPL",
      "label": "Apple",
      "market": "US",
      "position": 10,
      "averagePrice": 150
    }
  ]
}
```

`position` and `averagePrice` are optional.

### `POST /api/watchlist`

Accepts the same watchlist shape and writes it to `watchlist.json` after normalization.

Normalization rules:

- `symbol`: trimmed and uppercased.
- `label`: trimmed; defaults to `symbol`.
- `market`: normalized to `US` or `SG`.
- `position`: optional float.
- `averagePrice`: optional float.
- Empty symbols are removed.

Returns the saved watchlist with HTTP `201`.

### `GET /api/version`

Returns version metadata assembled from `CHANGELOG.md` and Git.

Response shape:

```json
{
  "release": "0.9.0",
  "build": "123",
  "commit": "abc1234",
  "versionNumber": "0.9.0+123.abc1234",
  "display": "v0.9.0+123.abc1234"
}
```

If Git is unavailable, build is `0` and commit is `unknown`.

### `GET /api/news?market=US`

Returns recent Google News RSS results grouped by watchlist entry.

Accepted market values:

- `US`
- `SG`
- `ALL`

Response shape:

```json
{
  "market": "US",
  "groups": [
    {
      "symbol": "AAPL",
      "label": "Apple",
      "items": [
        {
          "title": "Headline",
          "link": "https://...",
          "source": "Publisher",
          "publishedAt": "2026-04-28T12:00:00+00:00"
        }
      ]
    }
  ]
}
```

Implementation details:

- Query is built as `"SYMBOL" stock OR "LABEL" stock when:3d`.
- Only items from the last 3 days are kept.
- Each ticker is limited to 5 headlines.
- Titles are HTML-unescaped and publisher suffixes after the final `" - "` are removed.

### `GET /api/search?q=apple&market=US`

Uses Yahoo Finance search to suggest tickers while adding a watchlist entry.

Response shape:

```json
{
  "market": "US",
  "items": [
    {
      "symbol": "AAPL",
      "label": "Apple Inc.",
      "exchange": "NasdaqGS",
      "type": "EQUITY",
      "market": "US"
    }
  ]
}
```

Market inference:

- Symbols ending in `.SI` or exchanges containing `Singapore` are `SG`.
- Everything else is `US`.
- Results are filtered to the requested market and deduped by symbol plus market.
- Up to 8 results are returned.

### `GET /api/market-overview`

Returns quote snapshots for all broad and sector market segments.

Response shape:

```json
{
  "segments": [
    {
      "symbol": "SPY",
      "label": "S&P 500",
      "group": "Broad Market",
      "price": 500,
      "dayChange": 1.23,
      "dayChangePct": 0.25,
      "isLive": true,
      "dataSource": "Yahoo Finance"
    }
  ],
  "errors": []
}
```

Primary source is Yahoo quote. If it fails, the server falls back to chart fetches per segment.

### `GET /api/market-sector?symbol=XLK&count=12`

Returns quote snapshots for drilldown stocks within a market segment.

Rules:

- `symbol` must exist in `MARKET_DRILLDOWN_STOCKS`.
- `count` is clamped between `4` and the number of available entries.
- Passing `999` from the UI effectively means all available entries.

Response shape:

```json
{
  "sectorSymbol": "XLK",
  "sectorLabel": "Technology",
  "requestedCount": 12,
  "stocks": [],
  "errors": []
}
```

### `GET /api/chart?symbol=AAPL&range=3mo`

Returns chart and quote data for one symbol.

Supported ranges:

- `1mo`
- `3mo`
- `6mo`
- `1y`
- `2y`
- `5y`
- `max`

Response shape:

```json
{
  "symbol": "AAPL",
  "price": 190.12,
  "dayChange": 1.23,
  "dayChangePct": 0.65,
  "currency": "USD",
  "exchange": "NMS",
  "marketState": "REGULAR",
  "preMarketPrice": 191,
  "preMarketChange": 0.88,
  "preMarketChangePct": 0.46,
  "isLive": true,
  "dataSource": "Yahoo Finance",
  "points": [
    {
      "date": "2026-04-28",
      "timestamp": 1777334400000,
      "close": 190.12
    }
  ]
}
```

Fetcher order:

1. Yahoo Finance chart endpoint.
2. Stooq CSV endpoint.

Yahoo endpoint:

```text
https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={range}&interval=1d
```

Stooq endpoint:

```text
https://stooq.com/q/d/l/?s={normalized_symbol}&i=d
```

Stooq normalization:

- Plain symbols become lowercase and get `.us`, e.g. `AAPL` -> `aapl.us`.
- Symbols with `.` or `:` keep market-like suffixes, with `:` converted to `.`.

## External Data Sources

Runtime services used by the app:

- Yahoo Finance chart API for quote/chart data.
- Yahoo Finance quote API for market overview snapshots.
- Yahoo Finance search API for watchlist suggestions.
- Stooq daily CSV as a chart fallback.
- Google News RSS for recent headlines.
- Frankfurter API for USD to SGD conversion in the browser.

Because these are public endpoints without project-owned service contracts, failures should be handled gracefully. The existing app usually surfaces a friendly unavailable/error state instead of crashing.

## Watchlist Data Model

`watchlist.json` is the only persistent server-side data store.

Canonical shape:

```json
{
  "symbols": [
    {
      "symbol": "AAPL",
      "label": "Apple",
      "market": "US",
      "position": 10,
      "averagePrice": 150
    }
  ]
}
```

Fields:

- `symbol`: required ticker string.
- `label`: display label.
- `market`: `US` or `SG`.
- `position`: optional number of shares or units.
- `averagePrice`: optional average cost basis.

Backward compatibility:

- Entries without `market` default to `US`.
- Invalid numeric fields are ignored.

If `watchlist.json` is missing, the server creates a default US watchlist with `AAPL`, `MSFT`, and `NVDA`.

## Front-End Structure

The dashboard has these main sections, each represented as a collapsible panel with `data-collapsible`:

- `tickers`: watchlist region selector, add form, search suggestions, stock cards.
- `actions`: rules-based action prompts per watchlist stock.
- `options`: educational options strategy ideas per selected stock.
- `benchmark`: normalized watchlist average vs selected benchmarks.
- `news`: recent headlines grouped by ticker.
- `market`: broad market and sector heatmap with drilldowns.

Modals:

- `position-modal`: edit or clear position and average price.
- `controls-modal`: refresh, theme, currency, change display, range, and section arrangement.

Template:

- `stock-card-template`: cloned for each stock card.

Charts:

- Individual stock cards use SVG paths generated in JavaScript.
- Benchmark view uses a single SVG with normalized line series and hover tooltip.
- Market heatmaps use CSS grid tiles with computed red/green color intensity.

## Front-End State

Global JavaScript state:

- `watchlist`: current normalized watchlist.
- `usdToSgdRate`: browser-loaded currency conversion rate, default `1`.
- `autoRefreshTimer`: interval handle.
- `selectedMarketSector`: selected market heatmap tile.
- `activePositionTarget`: stock currently being edited in the position modal.
- `watchlistSearchTimer`: debounce timer for suggestions.
- `lastWatchlistSearchQuery`: last suggestion query.
- `latestChartResults`: last loaded chart payloads.
- `latestNewsGroups`: last loaded news groups.
- `selectedOptionsSymbol`: selected ticker in Options Ideas.

`localStorage` keys:

- `stock-dashboard-theme`: selected theme mode.
- `stock-dashboard-collapse-{section}`: expanded/collapsed state per panel.
- `stock-dashboard-refresh-mode`: `manual` or `auto`.
- `stock-dashboard-refresh-interval`: auto-refresh minutes.
- `stock-dashboard-market-stock-count`: market drilldown count.
- `stock-dashboard-active-watchlist-market`: `US`, `SG`, or `ALL`.
- `stock-dashboard-section-order`: user-arranged panel order.

## Front-End Features

### Themes

Theme selector values:

- `auto`
- `light`
- `dark`
- `random`

`auto` chooses dark between 7 PM and 7 AM local time. `random` chooses from a set of light and dark palette variants. Theme variants are implemented with `body[data-theme-variant="..."]` CSS custom properties.

### Market Selection

Watchlist region selector supports:

- `US`
- `SG`
- `ALL`

Behavior:

- Stock cards filter by selected region.
- `ALL` shows both US and SG watchlist items, with US first and SG second.
- Benchmark comparison follows the selected region.
- In `ALL` mode, benchmark comparison pauses and asks the user to choose a region.
- The add-watchlist market selector follows the current region unless current region is `ALL`.

### Benchmarks

US benchmark set:

- `SPY`: S&P 500
- `QQQ`: Nasdaq
- `DIA`: Dow
- `^VIX`: VIX, disabled by default

Singapore benchmark set:

- `^STI`: STI Index
- `ES3.SI`: SPDR STI ETF
- `G3B.SI`: Singapore STI ETF
- `EWS`: MSCI Singapore, disabled by default

Benchmark lines are normalized to percentage movement from the first available point.

### Currency

Display currency selector supports:

- `USD`
- `SGD`

The browser fetches:

```text
https://api.frankfurter.dev/v1/latest?base=USD&symbols=SGD
```

If the request fails, `usdToSgdRate` remains `1`.

### Change Display

Change selector supports:

- percent mode
- value mode

Value mode converts the displayed change into the selected display currency.

### Positions

Each card has a `Position` button. The modal lets the user save:

- number of shares/units
- average price

Position display includes:

- position size
- current value
- average price, if present
- unrealized gain/loss, if average price and current price are both available

Saved position data goes back into `watchlist.json`.

### Watchlist Search

Typing in either the ticker input or label input triggers debounced `/api/search` calls after the query has at least 2 characters. Selecting a suggestion fills:

- symbol
- label
- market

### Watchlist Actions

The Actions panel uses recent chart data and news counts to generate rules-based prompts. Inputs include:

- latest price
- 5-period and 20-period simple averages
- selected-period high and low
- distance from high/low
- day change percent
- whether the stock has a saved position
- number of recent headlines

Possible tones:

- positive
- warning
- neutral

Example categories:

- Breakout watch
- Risk check
- Pullback watch
- Wait
- Manage winner
- Hold / wait

### Options Ideas

The Options panel generates educational strategy ideas from the selected stock's trend and the selected strategy focus.

Focus filters:

- all
- bullish
- bearish
- neutral
- income
- hedge

The ideas are generated client-side. They are not options-chain quotes and do not fetch live contracts.

### News

News groups are collapsed/expanded per ticker in the UI. Headlines open in a new browser tab.

### Market View

Market overview tiles are grouped by:

- Broad Market
- Sectors

Segments:

- `SPY`: S&P 500
- `QQQ`: Nasdaq 100
- `DIA`: Dow
- `IWM`: Russell 2000
- `XLK`: Technology
- `XLF`: Financials
- `XLV`: Health Care
- `XLY`: Consumer Discretionary
- `XLP`: Consumer Staples
- `XLI`: Industrials
- `XLE`: Energy
- `XLB`: Materials
- `XLU`: Utilities
- `XLRE`: Real Estate
- `XLC`: Communication Services

Clicking a supported tile loads a dense heatmap of member stocks. Tiles are sorted from strongest daily gain to weakest daily performance.

## Styling Notes

Design character:

- Dashboard-like but personal and warm.
- Uses monospace font: `"Courier New", Courier, monospace`.
- Large hero at top.
- Rounded panels and cards.
- CSS custom properties drive all themes.
- Cards, panels, controls, and modals use translucent surfaces and subtle borders.
- Responsive layout collapses to single-column controls and cards below `760px`.

Important CSS concepts:

- `:root` defines the default light theme.
- `body[data-theme="dark"]` defines the dark theme.
- `body[data-theme-variant="..."]` defines random theme variants.
- `.collapsible-panel.is-collapsed .panel-body` hides panel content.
- `.market-tile` background color is set inline by JavaScript.
- `.sector-stock-grid` uses dense CSS grid layout.
- `.modal-shell[hidden]` and similar selectors force hidden UI to disappear.

## Versioning

The displayed app version is generated at runtime by:

1. Reading the first `## [...]` heading in `CHANGELOG.md`.
2. Running Git commands for commit count and short SHA.
3. Combining them into:

```text
v{release}+{build}.{commit}
```

Example:

```text
v0.9.0+123.abc1234
```

If Git cannot be found, the app still runs and reports fallback metadata.

## Rebuild Steps From Scratch

1. Create the project folder.
2. Create `app.py` with a standard-library `ThreadingHTTPServer`.
3. Add constants for root paths, host, port, passcode, market segments, and drilldown stock maps.
4. Implement watchlist load/save normalization around `watchlist.json`.
5. Implement version payload parsing from `CHANGELOG.md` plus optional Git metadata.
6. Implement external fetch helpers:
   - generic `fetch_text`
   - Yahoo chart URL and parser
   - Stooq CSV URL and parser
   - Yahoo quote URL and market snapshot builder
   - Yahoo search URL and search result normalizer
   - Google News RSS URL and parser
7. Implement API routes listed above.
8. Implement the login page and cookie check.
9. Serve `static/index.html`, `/static/app.js`, and `/static/styles.css`.
10. Create `static/index.html` with the dashboard sections and modal IDs used by `app.js`.
11. Create `static/app.js` with:
    - DOM bindings
    - localStorage state
    - watchlist filtering
    - chart loading and rendering
    - benchmark rendering
    - news rendering
    - market heatmap rendering
    - search suggestions
    - position modal behavior
    - controls modal behavior
    - initialization flow
12. Create `static/styles.css` with theme variables, responsive dashboard layout, card styling, SVG chart styling, modal styling, and heatmap styling.
13. Create `watchlist.json` with an empty or starter `symbols` array.
14. Create launch scripts for local and LAN modes.
15. Create `README.md`, `MANUAL.md`, and `CHANGELOG.md`.
16. Run `python app.py`.
17. Open the printed URL and enter passcode `1010`.
18. Add symbols and verify:
    - watchlist save/load
    - chart cards
    - benchmark lines
    - news groups
    - market heatmap
    - position save/clear
    - theme and refresh settings

## Initialization Flow In Browser

`init()` in `static/app.js` performs this sequence:

1. Close the position modal.
2. Load version metadata.
3. Sync range controls.
4. Initialize saved market selection.
5. Initialize theme.
6. Initialize refresh controls.
7. Initialize market controls.
8. Initialize section order.
9. Initialize collapsible panels.
10. Render benchmark controls.
11. Fetch USD/SGD exchange rate.
12. Load watchlist.
13. Refresh the dashboard.

`refreshDashboard()` loads:

1. News.
2. Market overview.
3. Charts and benchmarks.

## Important Rebuild Gotchas

- Keep HTML IDs exactly aligned with `static/app.js`; the JavaScript queries many elements on load.
- Keep `stock-card-template` structure compatible with `renderCard`.
- The server auth gate affects static assets too. The login cookie must be set before `/static/app.js` and `/static/styles.css` will be served.
- `CHANGELOG.md` is part of runtime behavior because version parsing depends on it.
- Yahoo and Stooq payloads have different shapes; the chart response should be normalized before reaching the front end.
- `ALL` market mode is a watchlist display mode, not a persisted symbol market.
- Watchlist entries should persist as `US` or `SG`, never `ALL`.
- Position and average price are optional and should not be saved if invalid.
- Browser preferences are intentionally stored in `localStorage`, not in `watchlist.json`.
- The app should continue to render partial dashboard sections if one external data source fails.
- The LAN scripts expose the app to the local network; the passcode is still simple and should not be treated as internet-safe security.

## Verification Checklist

Use this checklist after a rebuild or major edit:

- `python -m py_compile app.py` passes.
- `python app.py` starts and prints at least one URL.
- Login page appears before authentication.
- Passcode `1010` redirects to `/`.
- `/api/watchlist` returns JSON after login.
- Adding a ticker updates `watchlist.json`.
- Removing a ticker updates `watchlist.json`.
- Position modal saves and clears both `position` and `averagePrice`.
- US, SG, and All watchlist views work.
- Benchmark controls render for US and SG.
- `ALL` mode does not try to compare mixed-region benchmarks.
- Chart range selector changes card charts and benchmark charts.
- Watchlist search suggestions appear for two-character queries.
- News groups render even when some tickers have no items.
- Market View renders broad and sector tiles.
- Clicking a market tile loads sector drilldown.
- Theme selector changes the page.
- Auto refresh starts and stops without duplicating timers.
- The app remains usable if an external endpoint returns an error.

## Current Release History Snapshot

The current changelog top release is `0.9.0` dated `2026-04-28`.

Recent major capabilities:

- Separate US and Singapore watchlists.
- All-markets watchlist view.
- Market-aware benchmarks.
- Ticker/company search suggestions.
- Random theme variants.
- Market heatmap drilldowns.
- Position modal and average price tracking.
- LAN launch support.
- Login gate.
- Watchlist news.
- Options ideas and action prompts.

