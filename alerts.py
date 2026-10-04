"""Portfolio alert rules, the background checker, and notification delivery.

Rules and channel settings live in each user's encrypted vault. A background thread
checks the rules of every unlocked account on the user's chosen interval and sends a
notification when a rule's condition *becomes* true (edge-triggered), so a rule doesn't
repeat every few minutes while a stock sits above a level.

Channels (all optional, configured per user):
  * In-app notification centre + browser/desktop pop-ups while the dashboard is open
  * Telegram bot
  * ntfy (free phone push - https://ntfy.sh)
  * Email via SMTP (e.g. Gmail with an app password)
  * Discord / Slack incoming webhook
"""
from __future__ import annotations

import json
import secrets
import smtplib
import ssl
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage

# --------------------------------------------------------------------------- #
# Rule catalogue
# --------------------------------------------------------------------------- #
# scope: "symbol" rules watch one ticker; "position" also needs shares + average price;
#        "portfolio" rules look at all holdings of the chosen region.
RULE_TYPES = {
    "price_above":    {"scope": "symbol",    "unit": "price", "label": "Price rises above"},
    "price_below":    {"scope": "symbol",    "unit": "price", "label": "Price falls below"},
    "day_up":         {"scope": "symbol",    "unit": "pct",   "label": "Rises by at least % in a day"},
    "day_down":       {"scope": "symbol",    "unit": "pct",   "label": "Falls by at least % in a day"},
    "off_high":       {"scope": "symbol",    "unit": "pct",   "label": "Drops % below its 3-month high"},
    "cross_above_ma": {"scope": "symbol",    "unit": "ma",    "label": "Crosses above its moving average"},
    "cross_below_ma": {"scope": "symbol",    "unit": "ma",    "label": "Crosses below its moving average"},
    "take_profit":    {"scope": "position",  "unit": "pct",   "label": "Gain on cost reaches % (take profit)"},
    "stop_loss":      {"scope": "position",  "unit": "pct",   "label": "Loss on cost reaches % (stop loss)"},
    "portfolio_up":   {"scope": "portfolio", "unit": "pct",   "label": "Portfolio up % today"},
    "portfolio_down": {"scope": "portfolio", "unit": "pct",   "label": "Portfolio down % today"},
    "concentration":  {"scope": "portfolio", "unit": "pct",   "label": "One holding exceeds % of portfolio"},
}
CROSS_TYPES = {"cross_above_ma", "cross_below_ma"}
MA_PERIODS = (20, 50, 100, 200)
MAX_RULES = 100
NOTIFICATIONS_KEEP = 200
DEFAULT_SETTINGS = {"intervalMinutes": 5, "quietHours": {"enabled": False, "start": "23:00", "end": "07:00"}}
DEFAULT_CHANNELS = {
    "browser":  {"enabled": True},
    "telegram": {"enabled": False, "botToken": "", "chatId": ""},
    "ntfy":     {"enabled": False, "server": "https://ntfy.sh", "topic": "", "token": ""},
    "email":    {"enabled": False, "host": "smtp.gmail.com", "port": 465, "security": "ssl",
                 "username": "", "password": "", "from": "", "to": ""},
    "webhook":  {"enabled": False, "url": ""},
}
SECRET_FIELDS = {"telegram": ["botToken"], "ntfy": ["token"], "email": ["password"], "webhook": ["url"]}
EXTERNAL_CHANNELS = ("telegram", "ntfy", "email", "webhook")
CHANNEL_NAMES = {"telegram": "Telegram", "ntfy": "ntfy push", "email": "Email", "webhook": "Webhook"}


class RuleError(ValueError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _num(value) -> float | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if n == n and abs(n) != float("inf") else None


def ccy_symbol(ccy: str) -> str:
    return {"USD": "$", "SGD": "S$"}.get(ccy or "", f"{ccy} " if ccy else "")


def fmt_money(value: float, ccy: str) -> str:
    dp = 4 if abs(value) < 1 else 3 if abs(value) < 20 and ccy == "SGD" else 2
    return f"{'-' if value < 0 else ''}{ccy_symbol(ccy)}{abs(value):,.{dp}f}"


def fmt_pct(value: float, dp: int = 1) -> str:
    return f"{'+' if value > 0 else ''}{value:.{dp}f}%"


# --------------------------------------------------------------------------- #
# Vault section helpers
# --------------------------------------------------------------------------- #
def alerts_section(vault: dict) -> dict:
    sec = vault.setdefault("alerts", {})
    sec.setdefault("rules", [])
    settings = sec.setdefault("settings", {})
    for k, v in DEFAULT_SETTINGS.items():
        settings.setdefault(k, json.loads(json.dumps(v)))
    channels = sec.setdefault("channels", {})
    for name, defaults in DEFAULT_CHANNELS.items():
        ch = channels.setdefault(name, {})
        for k, v in defaults.items():
            ch.setdefault(k, v)
    vault.setdefault("notifications", [])
    return sec


def normalize_rule(raw: dict, existing: dict | None = None) -> dict:
    if not isinstance(raw, dict):
        raise RuleError("Invalid rule.")
    rtype = str(raw.get("type") or "")
    spec = RULE_TYPES.get(rtype)
    if not spec:
        raise RuleError("Choose a rule type.")
    rule = dict(existing or {})
    rule.update({"type": rtype, "enabled": bool(raw.get("enabled", True))})
    if spec["scope"] in ("symbol", "position"):
        symbol = str(raw.get("symbol") or "").strip().upper()
        if not symbol or len(symbol) > 20:
            raise RuleError("Choose a ticker.")
        market = str(raw.get("market") or "").upper()
        rule["symbol"] = symbol
        rule["market"] = market if market in ("US", "SG") else ("SG" if symbol.endswith(".SI") else "US")
    else:
        market = str(raw.get("market") or "ALL").upper()
        rule["symbol"] = ""
        rule["market"] = market if market in ("US", "SG", "ALL") else "ALL"
    if spec["unit"] == "ma":
        period = int(_num(raw.get("period")) or 50)
        if period not in MA_PERIODS:
            raise RuleError(f"Moving average must be one of {', '.join(map(str, MA_PERIODS))} days.")
        rule["period"] = period
        rule["value"] = None
    else:
        value = _num(raw.get("value"))
        if value is None or value <= 0:
            raise RuleError("Enter a positive number." if spec["unit"] == "pct" else "Enter a price above zero.")
        if spec["unit"] == "pct" and value > 1000:
            raise RuleError("That percentage is too large.")
        rule["value"] = value
        rule.pop("period", None)
    repeat = str(raw.get("repeat") or "rearm")
    rule["repeat"] = repeat if repeat in ("rearm", "once") else "rearm"
    cooldown = _num(raw.get("cooldownMinutes"))
    rule["cooldownMinutes"] = int(min(max(cooldown if cooldown is not None else 240, 0), 10080))
    rule["note"] = str(raw.get("note") or "").strip()[:200]
    if not existing:
        rule.update({"id": secrets.token_hex(6), "createdAt": now_iso(), "lastMet": None,
                     "lastTriggeredAt": None, "triggerCount": 0})
    elif any(existing.get(k) != rule.get(k) for k in ("type", "symbol", "market", "value", "period")):
        rule["lastMet"] = None   # condition changed: take a fresh baseline
    return rule


def public_channels(channels: dict) -> dict:
    """Channel settings for the browser, with secrets masked."""
    out = {}
    for name, cfg in channels.items():
        view = dict(cfg)
        for field in SECRET_FIELDS.get(name, []):
            value = str(cfg.get(field) or "")
            view[field] = ""
            view[f"{field}Set"] = bool(value)
            view[f"{field}Hint"] = ("…" + value[-4:]) if len(value) > 8 else ("set" if value else "")
        out[name] = view
    return out


def merge_channels(current: dict, incoming: dict) -> dict:
    """Apply edits from the browser. An empty secret keeps the stored one; `clear<Field>` removes it."""
    merged = json.loads(json.dumps(current))
    for name, defaults in DEFAULT_CHANNELS.items():
        patch = incoming.get(name)
        if not isinstance(patch, dict):
            continue
        target = merged.setdefault(name, dict(defaults))
        for key, default in defaults.items():
            if key not in patch:
                continue
            value = patch[key]
            if key in SECRET_FIELDS.get(name, []):
                if patch.get(f"clear{key[0].upper()}{key[1:]}"):
                    target[key] = ""
                elif isinstance(value, str) and value.strip():
                    target[key] = value.strip()
                continue
            if isinstance(default, bool):
                target[key] = bool(value)
            elif isinstance(default, int):
                target[key] = int(_num(value) or default)
            else:
                target[key] = str(value or "").strip()[:300]
    for name in ("ntfy", "webhook"):
        url = merged[name].get("server" if name == "ntfy" else "url") or ""
        if url and not url.lower().startswith(("https://", "http://")):
            raise RuleError(f"{CHANNEL_NAMES[name]} address must start with https://")
    if merged["email"]["security"] not in ("ssl", "starttls", "none"):
        merged["email"]["security"] = "ssl"
    return merged


def merge_settings(current: dict, incoming: dict) -> dict:
    merged = json.loads(json.dumps(current))
    if "intervalMinutes" in incoming:
        merged["intervalMinutes"] = int(min(max(_num(incoming["intervalMinutes"]) or 5, 1), 240))
    quiet = incoming.get("quietHours")
    if isinstance(quiet, dict):
        q = merged.setdefault("quietHours", {})
        q["enabled"] = bool(quiet.get("enabled"))
        for key in ("start", "end"):
            text = str(quiet.get(key) or q.get(key) or "")
            try:
                datetime.strptime(text, "%H:%M")
            except ValueError as exc:
                raise RuleError("Quiet hours use HH:MM, e.g. 23:00.") from exc
            q[key] = text
    return merged


def in_quiet_hours(settings: dict, when: datetime | None = None) -> bool:
    quiet = settings.get("quietHours") or {}
    if not quiet.get("enabled"):
        return False
    when = when or datetime.now()
    minutes = when.hour * 60 + when.minute
    start = sum(int(x) * m for x, m in zip(quiet["start"].split(":"), (60, 1)))
    end = sum(int(x) * m for x, m in zip(quiet["end"].split(":"), (60, 1)))
    if start == end:
        return False
    return start <= minutes < end if start < end else minutes >= start or minutes < end


# --------------------------------------------------------------------------- #
# Channel senders
# --------------------------------------------------------------------------- #
def _post(url: str, body: bytes, headers: dict, timeout: float = 12):
    request = urllib.request.Request(url, data=body, headers={"User-Agent": "MarketTracker/0.2", **headers},
                                     method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read(2000)


def send_telegram(cfg: dict, title: str, body: str):
    if not cfg.get("botToken") or not cfg.get("chatId"):
        raise RuntimeError("Add the bot token and chat ID first")
    url = f"https://api.telegram.org/bot{cfg['botToken']}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": cfg["chatId"], "text": f"{title}\n{body}",
                                   "disable_web_page_preview": "true"}).encode()
    _post(url, data, {"Content-Type": "application/x-www-form-urlencoded"})


def send_ntfy(cfg: dict, title: str, body: str, tone: str = "neutral"):
    if not cfg.get("topic"):
        raise RuntimeError("Add a topic name first")
    server = (cfg.get("server") or "https://ntfy.sh").rstrip("/")
    headers = {"Title": title.encode("ascii", "replace").decode(), "Priority": "high" if tone == "warning" else "default",
               "Tags": "chart_with_downwards_trend" if tone == "warning" else "chart_with_upwards_trend",
               "Content-Type": "text/plain; charset=utf-8"}
    if cfg.get("token"):
        headers["Authorization"] = f"Bearer {cfg['token']}"
    _post(f"{server}/{urllib.parse.quote(cfg['topic'].strip(), safe='')}", body.encode("utf-8"), headers)


def send_email(cfg: dict, title: str, body: str):
    for field in ("host", "to"):
        if not cfg.get(field):
            raise RuntimeError("Add the SMTP server and a recipient first")
    msg = EmailMessage()
    msg["Subject"] = f"[Market Tracker] {title}"
    msg["From"] = cfg.get("from") or cfg.get("username") or "market-tracker@localhost"
    msg["To"] = cfg["to"]
    msg.set_content(f"{body}\n\n-- Sent by your Market Tracker alert rules. Not investment advice.")
    port = int(cfg.get("port") or (465 if cfg.get("security") == "ssl" else 587))
    context = ssl.create_default_context()
    if cfg.get("security") == "ssl":
        server = smtplib.SMTP_SSL(cfg["host"], port, timeout=15, context=context)
    else:
        server = smtplib.SMTP(cfg["host"], port, timeout=15)
        if cfg.get("security") == "starttls":
            server.starttls(context=context)
    with server:
        if cfg.get("username"):
            server.login(cfg["username"], cfg.get("password") or "")
        server.send_message(msg)


def send_webhook(cfg: dict, title: str, body: str):
    url = cfg.get("url") or ""
    if not url:
        raise RuntimeError("Add the webhook URL first")
    if "discord.com/api/webhooks" in url or "discordapp.com/api/webhooks" in url:
        payload = {"content": f"**{title}**\n{body}"}
    else:
        payload = {"text": f"*{title}*\n{body}"}
    _post(url, json.dumps(payload).encode(), {"Content-Type": "application/json"})


SENDERS = {"telegram": send_telegram, "ntfy": send_ntfy, "email": send_email, "webhook": send_webhook}


def deliver(channels: dict, settings: dict, title: str, body: str, tone: str, force: bool = False) -> dict:
    """Send to every enabled external channel. Returns {channel: 'sent' | 'muted' | 'error: ...'}."""
    results = {}
    quiet = in_quiet_hours(settings) and not force
    for name in EXTERNAL_CHANNELS:
        cfg = channels.get(name) or {}
        if not cfg.get("enabled"):
            continue
        if quiet:
            results[name] = "muted (quiet hours)"
            continue
        try:
            if name == "ntfy":
                send_ntfy(cfg, title, body, tone)
            else:
                SENDERS[name](cfg, title, body)
            results[name] = "sent"
        except Exception as exc:  # noqa: BLE001 - report, never crash the checker
            results[name] = f"error: {str(exc)[:160]}"
    return results


# --------------------------------------------------------------------------- #
# Rule evaluation
# --------------------------------------------------------------------------- #
class Evaluation:
    __slots__ = ("met", "current", "status", "title", "body", "tone")

    def __init__(self, met, current=None, status="", title="", body="", tone="neutral"):
        self.met, self.current, self.status = met, current, status
        self.title, self.body, self.tone = title, body, tone


def rule_summary(rule: dict) -> str:
    t, v = rule["type"], rule.get("value")
    sym = rule.get("symbol") or ""
    ccy = "SGD" if rule.get("market") == "SG" else "USD"
    region = {"US": "US", "SG": "SG", "ALL": "total"}.get(rule.get("market"), "total")
    return {
        "price_above": lambda: f"{sym} rises above {fmt_money(v, ccy)}",
        "price_below": lambda: f"{sym} falls below {fmt_money(v, ccy)}",
        "day_up": lambda: f"{sym} up {v:g}% or more in a day",
        "day_down": lambda: f"{sym} down {v:g}% or more in a day",
        "off_high": lambda: f"{sym} {v:g}% or more below its 3-month high",
        "cross_above_ma": lambda: f"{sym} crosses above its {rule.get('period')}-day average",
        "cross_below_ma": lambda: f"{sym} crosses below its {rule.get('period')}-day average",
        "take_profit": lambda: f"{sym} position up {v:g}% on cost",
        "stop_loss": lambda: f"{sym} position down {v:g}% on cost",
        "portfolio_up": lambda: f"{region[0].upper() + region[1:]} portfolio up {v:g}% or more today",
        "portfolio_down": lambda: f"{region[0].upper() + region[1:]} portfolio down {v:g}% or more today",
        "concentration": lambda: f"Any holding above {v:g}% of the {region} portfolio",
    }[t]()


class AlertEngine:
    def __init__(self, accounts, get_chart, get_fx, log=print):
        self.accounts = accounts
        self.get_chart = get_chart      # (symbol, range) -> chart dict (cached upstream)
        self.get_fx = get_fx            # () -> USD->SGD rate
        self.log = log
        self._last_run: dict[str, float] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="alert-data")

    # ---- data -------------------------------------------------------------- #
    def _chart(self, symbol: str) -> dict | None:
        try:
            return self.get_chart(symbol, "1y")
        except Exception:  # noqa: BLE001
            return None

    def prefetch(self, rules: list[dict], watchlist: list[dict]):
        """Warm the chart cache in parallel so evaluating many rules stays quick."""
        symbols = {r["symbol"] for r in rules if r.get("symbol")}
        if any(RULE_TYPES[r["type"]]["scope"] == "portfolio" for r in rules):
            symbols |= {e["symbol"] for e in watchlist if (e.get("position") or 0) > 0}
        futures = [self._pool.submit(self._chart, s) for s in symbols]
        for f in futures:
            try:
                f.result(timeout=30)
            except Exception:  # noqa: BLE001
                pass

    def _holdings(self, watchlist: list[dict], market: str):
        rows = [e for e in watchlist if (e.get("position") or 0) > 0 and (market == "ALL" or e["market"] == market)]
        return [(e, self._chart(e["symbol"])) for e in rows]

    def evaluate(self, rule: dict, watchlist: list[dict]) -> Evaluation:
        t, v = rule["type"], rule.get("value")
        spec = RULE_TYPES[t]
        if spec["scope"] == "portfolio":
            return self._evaluate_portfolio(rule, watchlist)
        sym = rule["symbol"]
        data = self._chart(sym)
        if not data or not data.get("points"):
            return Evaluation(None, status="No price data right now")
        price, ccy, day = data["price"], data.get("currency") or "USD", data.get("dayChangePct") or 0.0
        px = fmt_money(price, ccy)
        where = f"{sym} is at {px} ({fmt_pct(day)} today)."
        if t == "price_above":
            return Evaluation(price > v, price, f"Now {px}", f"{sym} above {fmt_money(v, ccy)}", where, "positive")
        if t == "price_below":
            return Evaluation(price < v, price, f"Now {px}", f"{sym} below {fmt_money(v, ccy)}", where, "warning")
        if t == "day_up":
            return Evaluation(day >= v, day, f"Today {fmt_pct(day)}", f"{sym} up {day:.1f}% today", where, "positive")
        if t == "day_down":
            return Evaluation(day <= -v, day, f"Today {fmt_pct(day)}", f"{sym} down {abs(day):.1f}% today", where, "warning")
        closes = [p["close"] for p in data["points"]]
        if t == "off_high":
            recent = closes[-63:]
            high = max(recent)
            off = (price / high - 1) * 100
            return Evaluation(off <= -v, off, f"{fmt_pct(off)} vs high {fmt_money(high, ccy)}",
                              f"{sym} {abs(off):.1f}% below its 3-month high",
                              f"{where} The 3-month high is {fmt_money(high, ccy)}.", "warning")
        if t in CROSS_TYPES:
            n = rule.get("period") or 50
            if len(closes) < n:
                return Evaluation(None, status=f"Needs {n} days of history")
            ma = sum(closes[-n:]) / n
            above = price > ma
            met = above if t == "cross_above_ma" else not above
            word = "above" if t == "cross_above_ma" else "below"
            return Evaluation(met, price, f"Now {px} · {n}d avg {fmt_money(ma, ccy)}",
                              f"{sym} crossed {word} its {n}-day average",
                              f"{where} The {n}-day average is {fmt_money(ma, ccy)}.",
                              "positive" if word == "above" else "warning")
        # position rules
        entry = next((e for e in watchlist if e["symbol"] == sym and e["market"] == rule["market"]), None)
        if not entry or not (entry.get("position") or 0) > 0 or not entry.get("averagePrice"):
            return Evaluation(None, status="Add shares and an average price on the card")
        gain = (price / entry["averagePrice"] - 1) * 100
        pnl = (price - entry["averagePrice"]) * entry["position"]
        detail = f"{where} Average cost {fmt_money(entry['averagePrice'], ccy)}, P&L {fmt_money(pnl, ccy)} ({fmt_pct(gain)})."
        if t == "take_profit":
            return Evaluation(gain >= v, gain, f"On cost {fmt_pct(gain)}", f"{sym} take-profit: up {gain:.1f}% on cost",
                              detail, "positive")
        return Evaluation(gain <= -v, gain, f"On cost {fmt_pct(gain)}", f"{sym} stop-loss: down {abs(gain):.1f}% on cost",
                          detail, "warning")

    def _evaluate_portfolio(self, rule: dict, watchlist: list[dict]) -> Evaluation:
        t, v, market = rule["type"], rule["value"], rule.get("market") or "ALL"
        holdings = [(e, d) for e, d in self._holdings(watchlist, market) if d]
        if not holdings:
            return Evaluation(None, status="No positions with price data")
        try:
            rate = float(self.get_fx() or 1)
        except Exception:  # noqa: BLE001
            rate = 1.0
        base = "SGD" if market == "SG" else "USD"

        def conv(amount, ccy):
            if ccy == base:
                return amount
            if ccy == "SGD" and base == "USD":
                return amount / rate
            if ccy == "USD" and base == "SGD":
                return amount * rate
            return amount

        values = {e["symbol"]: conv(d["price"] * e["position"], d.get("currency")) for e, d in holdings}
        total = sum(values.values())
        label = {"US": "US", "SG": "SG", "ALL": "Total"}[market]
        if t in ("portfolio_up", "portfolio_down"):
            day = sum(conv(d["dayChange"] * e["position"], d.get("currency")) for e, d in holdings)
            prev = total - day
            pct = day / prev * 100 if prev else 0.0
            body = f"{label} portfolio {fmt_money(total, base)}, today {fmt_money(day, base)} ({fmt_pct(pct)})."
            if t == "portfolio_up":
                return Evaluation(pct >= v, pct, f"Today {fmt_pct(pct)}", f"{label} portfolio up {pct:.1f}% today",
                                  body, "positive")
            return Evaluation(pct <= -v, pct, f"Today {fmt_pct(pct)}", f"{label} portfolio down {abs(pct):.1f}% today",
                              body, "warning")
        top_symbol, top_value = max(values.items(), key=lambda kv: kv[1])
        share = top_value / total * 100 if total else 0
        return Evaluation(share > v, share, f"Largest {top_symbol} {share:.1f}%",
                          f"{top_symbol} is {share:.1f}% of your {'' if market == 'ALL' else label + ' '}portfolio",
                          f"{top_symbol} is worth {fmt_money(top_value, base)} of {fmt_money(total, base)} "
                          f"({share:.1f}%). Your limit is {v:g}%.", "warning")

    # ---- views ------------------------------------------------------------- #
    def rule_views(self, vault: dict) -> list[dict]:
        sec = alerts_section(vault)
        watchlist = (vault.get("watchlist") or {}).get("symbols") or []
        self.prefetch(sec["rules"], watchlist)
        views = []
        for rule in sec["rules"]:
            ev = self.evaluate(rule, watchlist)
            views.append({**rule, "summary": rule_summary(rule), "status": ev.status, "current": ev.current,
                          "met": ev.met, "scope": RULE_TYPES[rule["type"]]["scope"]})
        return views

    # ---- checking ---------------------------------------------------------- #
    def check_user(self, user_id: str, force: bool = False) -> dict:
        """Evaluate every enabled rule; notify on false->true edges. Returns a summary."""
        accounts = self.accounts
        fired: list[dict] = []
        snapshot = accounts.read_vault(user_id)
        self.prefetch([r for r in alerts_section(snapshot)["rules"] if r.get("enabled")],
                      (snapshot.get("watchlist") or {}).get("symbols") or [])
        with accounts.vault_lock(user_id):
            vault = accounts.read_vault(user_id)
            sec = alerts_section(vault)
            watchlist = (vault.get("watchlist") or {}).get("symbols") or []
            changed = False
            checked = 0
            now = time.time()
            for rule in sec["rules"]:
                if not rule.get("enabled"):
                    continue
                checked += 1
                ev = self.evaluate(rule, watchlist)
                rule["lastCheckedAt"] = now_iso()
                changed = True
                if ev.met is None:
                    continue
                previous = rule.get("lastMet")
                rule["lastMet"] = bool(ev.met)
                if not ev.met:
                    continue
                edge = previous is False or (previous is None and rule["type"] not in CROSS_TYPES)
                last = rule.get("lastTriggeredAt")
                cooled = True
                if last:
                    try:
                        elapsed = now - datetime.fromisoformat(last).timestamp()
                        cooled = elapsed >= rule.get("cooldownMinutes", 0) * 60
                    except ValueError:
                        cooled = True
                if not edge or not cooled:
                    continue
                rule["lastTriggeredAt"] = now_iso()
                rule["triggerCount"] = int(rule.get("triggerCount") or 0) + 1
                if rule.get("repeat") == "once":
                    rule["enabled"] = False
                body = ev.body + (f"\nAlert: {rule_summary(rule)}." ) + (f"\nNote: {rule['note']}" if rule.get("note") else "")
                fired.append({"rule": rule, "title": ev.title, "body": body, "tone": ev.tone})
            if changed:
                accounts.write_vault(user_id, vault)
            channels, settings = json.loads(json.dumps(sec["channels"])), dict(sec["settings"])
        self._last_run[user_id] = time.time()
        # Deliver outside the vault lock (network calls can be slow), then record.
        records = []
        for item in fired:
            results = deliver(channels, settings, item["title"], item["body"], item["tone"])
            records.append({"id": secrets.token_hex(6), "at": now_iso(), "ruleId": item["rule"]["id"],
                            "symbol": item["rule"].get("symbol") or "", "title": item["title"], "body": item["body"],
                            "tone": item["tone"], "read": False, "delivery": results})
        if records:
            self.add_notifications(user_id, records)
        return {"checked": checked, "fired": len(records), "notifications": records,
                "checkedAt": now_iso()}

    def add_notifications(self, user_id: str, records: list[dict]):
        def mutate(vault):
            alerts_section(vault)
            vault["notifications"] = (records[::-1] + vault["notifications"])[:NOTIFICATIONS_KEEP]
        self.accounts.update_vault(user_id, mutate)

    def test_channels(self, user_id: str, only: str | None = None) -> dict:
        vault = self.accounts.read_vault(user_id)
        sec = alerts_section(vault)
        channels = json.loads(json.dumps(sec["channels"]))
        if only:
            channels = {k: ({**v, "enabled": k == only}) for k, v in channels.items()}
        results = deliver(channels, sec["settings"], "Test notification",
                          "Market Tracker can reach you here. Alert rules will arrive like this.", "neutral", force=True)
        if not results:
            results = {"none": "No external channels are switched on"}
        return results

    # ---- background loop --------------------------------------------------- #
    def start(self):
        if self._thread:
            return
        self._thread = threading.Thread(target=self._loop, name="alert-checker", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.wait(20):
            for user_id in self.accounts.unlocked_user_ids():
                try:
                    vault = self.accounts.read_vault(user_id)
                    sec = alerts_section(vault)
                    if not any(r.get("enabled") for r in sec["rules"]):
                        continue
                    interval = int(sec["settings"].get("intervalMinutes") or 5) * 60
                    if time.time() - self._last_run.get(user_id, 0) < interval:
                        continue
                    summary = self.check_user(user_id)
                    if summary["fired"]:
                        self.log(f"[alerts] {summary['fired']} alert(s) sent for user {user_id[:6]}")
                except Exception as exc:  # noqa: BLE001
                    self._last_run[user_id] = time.time()
                    self.log(f"[alerts] check failed for user {user_id[:6]}: {exc}")
