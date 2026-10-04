# Market Tracker — User Manual

## Signing in
- **First run**: the sign-in page asks you to create an account (username + a password of at least 10 characters). You then get a **recovery key** — save it in a password manager or print it. It's the only way to reset a forgotten password without losing your data.
- **Sign in**: username and password. Tick *Keep me signed in on this device for 30 days* on your own computer; leave it off on shared devices (you're then signed out after 12 hours, or 60 minutes without activity).
- **Two-factor**: if it's on, enter the 6-digit code from your authenticator app. No phone? Choose *Use a backup code instead*.
- **Forgot password?** Enter your username, recovery key and a new password. You get a new recovery key; the old one stops working.
- After 5 wrong attempts, sign-in pauses for 15 minutes.
- Restarting the server signs everyone out.

## Account & security (shield button, top right)
- **Overview**: a security checklist, a new recovery key, export of your watchlist and rules, and *Lock vault & sign out everywhere*. The owner can also allow other people on the network to create their own accounts.
- **Password**: enter your current password and the new one twice. Other devices are signed out.
- **Two-factor**: *Set up two-factor* → scan the QR code with Google Authenticator, Microsoft Authenticator, 1Password, Bitwarden or Authy → enter the code → save the 10 backup codes. Here you can also make new backup codes or turn 2FA off (needs your password and a code).
- **Sessions**: every signed-in device, with *Sign out* per device or for all others.
- **Activity**: sign-ins, failed attempts, password and 2FA changes, with the IP address.
- The sign-out button (arrow, far right) ends this session. Background alerts keep running until the server stops or you lock the vault.

Lost your phone *and* your backup codes? On the computer running the app, stop the server and run `python app.py --disable-2fa yourname`.

## Top bar
- **Exchange clocks**: NYSE and SGX status (open / pre-market / after hours / lunch break / closed) and time until the next open or close. Public holidays are not taken into account.
- **Refresh** (or press `R`): reloads prices, news and the market map.
- **Settings** (slider icon): refresh mode, theme, currency, change display, chart range, section order.
- **Version** (small tag beside the date, also in the footer): click it for the release notes.
- **Bell**: triggered alerts. A red badge counts unread ones; click one to mark it read.

## Summary strip
For the current region: total holdings value, today's P&L, unrealized P&L against your average prices, how many tickers are up or down, and the day's leader and laggard. Values are shown in your display currency. US and SG holdings are converted at the live USD/SGD rate shown in the footer.

## Watchlist
- **Region**: US, SG or All. All shows both lists (US first). Every ticker is saved as either US or SG.
- **Add**: type a ticker or company name. Suggestions appear after 2 characters; use arrow keys + Enter or click. For SG you can type the bare SGX code (e.g. `D05`) and `.SI` is added for you.
- **Range**: 1M / 3M / 6M / 1Y / 5Y (more in Settings) changes every card and the benchmark chart.
- **Card**: price in the stock's own currency, today's change, pre-market or after-hours price when relevant, sparkline (dashed line = your average cost), range low/high/return.
- **Position**: enter shares and average price. Leave shares empty or press Clear to remove the position.
- **Alert**: creates an alert rule for that ticker. The button shows how many active rules it has.
- **Options**: jumps to Options Ideas for that ticker.
- **✕**: removes the ticker. Use Undo in the popup to restore it.

## Alert rules
Rules watch the market for you and notify you when something happens. They are checked in the background (every 5 minutes by default) for as long as the server is running, even with the browser closed.

| Rule | Fires when |
|---|---|
| Price rises above / falls below | The price crosses your level |
| Rises / falls by at least % in a day | Today's move reaches your threshold |
| Drops % below its 3-month high | A pullback reaches your threshold |
| Crosses above / below its moving average | Price moves through the 20, 50, 100 or 200-day average |
| Take profit / stop loss | Your gain or loss against your average price reaches your threshold (needs a position) |
| Portfolio up / down % today | Your total holdings (US, SG or all, converted) move that much in a day |
| One holding exceeds % of portfolio | Any single position grows beyond your limit |

- **New rule**: *+ New rule*, or *Alert* on a card. The sentence at the bottom confirms what the rule does and where the stock is now.
- **Quick add**: one click adds a −8% stop-loss or +20% take-profit to every position, a "portfolio down 3%" rule or a 25% concentration limit.
- A rule fires when its condition *becomes* true, then waits for its quiet period (15 min to 1 week) before it can fire again. Choose *Once, then switch off* for one-time targets.
- Status tags: **Condition met**, **Watching**, **Waiting** (no data yet, or a position is missing), **Paused** (switched off).
- **Check now** evaluates every rule immediately.

## Notification channels (Alert rules › Channels & timing)
Every alert appears in the bell. You can also switch on:
- **Desktop pop-ups** while the dashboard is open. Click *Allow in this browser*. They need HTTPS or opening the app at 127.0.0.1.
- **Telegram**: create a bot with @BotFather (/newbot), send it any message, get your chat ID from @userinfobot.
- **ntfy**: free phone push. Install the ntfy app, subscribe to a long random topic name and enter the same name here.
- **Email**: any SMTP server. For Gmail use smtp.gmail.com, port 465, SSL, and a Google app password.
- **Discord / Slack**: paste an incoming-webhook URL.

*Send test* saves your settings and sends a test message. Tokens and passwords are stored encrypted and never shown again (leave a field blank to keep the saved value). **Quiet hours** mute phone, email and chat channels overnight; alerts still go to the bell.

## Action prompts
Rules-based signals using the 5- and 20-day averages, the range high/low, today's move, your cost basis and news volume. Warnings sort first. The labels are Breakout watch, Pullback watch, Trend intact, Risk check, Manage winner, Hold / wait and Wait. They are prompts for your own judgement, not recommendations.

## Watchlist vs benchmarks
The thick accent line is the equal-weighted average of your region's watchlist, rebased to 0% at the start of the range. Toggle benchmarks with the chips (US: S&P 500, Nasdaq, Dow, VIX; SG: STI, SPDR STI ETF, Nikko STI ETF, MSCI Singapore). Hover for values on any date. The chart is paused in All mode.

## Options ideas
Pick a ticker and a focus (Bullish, Bearish, Neutral, Income, Hedge). Strikes are estimated from the stock's realised volatility (a 30-day one-standard-deviation move) and rounded. Ideas that match the current trend or your holdings are highlighted. These are not live option-chain quotes, and most SGX stocks have no listed options.

## News
Headlines from the last 3 days, up to 5 per ticker. Click a row to expand it; headlines open in a new tab.

## Market map
Tiles for the broad US market, the 11 SPDR sectors and the STI, coloured by today's move (deeper = bigger). Click a tile to see its member stocks, strongest first. The Drilldown selector sets how many members to show (12, 20 or All). Click the tile again to close.

## Settings
- **Refresh**: Manual, or Auto every 1–15 minutes.
- **Theme**: Auto (dark from 7pm to 7am), Light, Dark, or Random palette.
- **Display currency**: USD or SGD for values and P&L.
- **Change shown as**: percent or value.
- **Section order**: move panels up or down; Reset layout restores the default.

Everything is saved per user. Each user has their own folder, `data/users/<username>/`, holding their encrypted tickers, positions, alert rules, notification settings and display settings, so two users never see each other's data. Display settings follow you to any browser you sign in from. Back up the `data` folder.

## Troubleshooting
- *"Too many failed attempts"*: wait 15 minutes, or restart the server.
- *2FA code rejected*: your phone's clock must be set automatically; codes change every 30 seconds.
- *No desktop pop-ups*: check *Allow in this browser* in Channels, and that you opened the app at 127.0.0.1 or over HTTPS.
- *Alerts stopped after a restart*: sign in once — the server needs your password to unlock your rules.
- *"Data unavailable" on a card*: Yahoo and Stooq both failed for that symbol. Check the ticker (SG tickers end in `.SI`) or try again later.
- *Port busy*: the server picks a free port and prints the new URL.
- *No internet*: run `run-demo.bat` to use the interface with synthetic data.
