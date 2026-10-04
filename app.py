#!/usr/bin/env python3
"""Personal Market Tracker - a local, password-protected portfolio dashboard.

Standard library only. Run:  python app.py          (live data)
                             python app.py --demo   (synthetic data, works offline)

Accounts, 2FA and the encrypted vault live in security.py; alert rules and
notification channels in alerts.py.
"""
from __future__ import annotations

import csv
import errno
import hashlib
import html
import io
import json
import math
import os
import random
import re
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import alerts
import security
from security import AuthError

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
WATCHLIST_FILE = ROOT / "watchlist.json"
CHANGELOG_FILE = ROOT / "CHANGELOG.md"

HOST = os.environ.get("STOCK_DASHBOARD_HOST", "127.0.0.1")
PORT = int(os.environ.get("STOCK_DASHBOARD_PORT", "8000"))
DEMO_MODE = "--demo" in sys.argv or os.environ.get("STOCK_DASHBOARD_DEMO") == "1"
DATA_DIR = Path(os.environ.get("STOCK_DASHBOARD_DATA_DIR") or (ROOT / ("data-demo" if DEMO_MODE else "data")))
CERT_DIR = ROOT / "certs"
TLS_CERT = os.environ.get("STOCK_DASHBOARD_CERT") or (str(CERT_DIR / "cert.pem") if (CERT_DIR / "cert.pem").exists() else "")
TLS_KEY = os.environ.get("STOCK_DASHBOARD_KEY") or (str(CERT_DIR / "key.pem") if (CERT_DIR / "key.pem").exists() else "")
USE_TLS = bool(TLS_CERT and TLS_KEY)
SESSION_COOKIE = "mt_session"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)
HTTP_TIMEOUT = 10
CHART_RANGES = {"1mo", "3mo", "6mo", "1y", "2y", "5y", "max"}
RANGE_DAYS = {"1mo": 31, "3mo": 92, "6mo": 183, "1y": 366, "2y": 731, "5y": 1827, "max": 36500}
POOL = ThreadPoolExecutor(max_workers=10)

DEFAULT_WATCHLIST = {
    "symbols": [
        {"symbol": "AAPL", "label": "Apple", "market": "US"},
        {"symbol": "MSFT", "label": "Microsoft", "market": "US"},
        {"symbol": "NVDA", "label": "Nvidia", "market": "US"},
    ]
}

# --------------------------------------------------------------------------- #
# Market segments and drilldown members
# --------------------------------------------------------------------------- #
MARKET_SEGMENTS = [
    {"symbol": "SPY", "label": "S&P 500", "group": "Broad Market"},
    {"symbol": "QQQ", "label": "Nasdaq 100", "group": "Broad Market"},
    {"symbol": "DIA", "label": "Dow", "group": "Broad Market"},
    {"symbol": "IWM", "label": "Russell 2000", "group": "Broad Market"},
    {"symbol": "XLK", "label": "Technology", "group": "Sectors"},
    {"symbol": "XLF", "label": "Financials", "group": "Sectors"},
    {"symbol": "XLV", "label": "Health Care", "group": "Sectors"},
    {"symbol": "XLY", "label": "Consumer Discretionary", "group": "Sectors"},
    {"symbol": "XLP", "label": "Consumer Staples", "group": "Sectors"},
    {"symbol": "XLI", "label": "Industrials", "group": "Sectors"},
    {"symbol": "XLE", "label": "Energy", "group": "Sectors"},
    {"symbol": "XLB", "label": "Materials", "group": "Sectors"},
    {"symbol": "XLU", "label": "Utilities", "group": "Sectors"},
    {"symbol": "XLRE", "label": "Real Estate", "group": "Sectors"},
    {"symbol": "XLC", "label": "Communication Services", "group": "Sectors"},
    {"symbol": "^STI", "label": "Straits Times Index", "group": "Singapore"},
]

MARKET_BROAD_STOCKS = {
    "SPY": "AAPL MSFT NVDA AMZN META GOOGL BRK-B AVGO TSLA JPM LLY V UNH XOM MA COST HD PG JNJ WMT NFLX ABBV BAC CRM ORCL KO",
    "QQQ": "AAPL MSFT NVDA AMZN META AVGO GOOGL TSLA COST NFLX AMD PEP ADBE CSCO TMUS QCOM INTU AMAT TXN ISRG BKNG PLTR MU PANW",
    "DIA": "UNH GS MSFT HD CAT SHW V MCD AMGN CRM AXP TRV JPM IBM AAPL HON AMZN BA PG JNJ CVX MRK NVDA WMT KO DIS CSCO MMM VZ NKE",
    "IWM": "IONQ RKLB HIMS SFM FN INSM CRDO AIT MLI ENSG CVLT UFPI SPXC TXRH KTOS JOBY OKLO ASTS",
}
MARKET_SECTOR_STOCKS = {
    "XLK": "NVDA AAPL MSFT AVGO ORCL CRM CSCO AMD IBM ADBE NOW ACN QCOM INTU TXN AMAT MU PLTR LRCX KLAC ANET PANW",
    "XLF": "BRK-B JPM V MA BAC WFC GS MS AXP SPGI BLK C SCHW PGR CB MMC ICE CME PYPL COF",
    "XLV": "LLY UNH JNJ ABBV MRK TMO ABT ISRG AMGN DHR BSX PFE SYK GILD VRTX MDT BMY CVS ELV CI",
    "XLY": "AMZN TSLA HD MCD BKNG TJX LOW SBUX NKE ORLY CMG ABNB MAR GM HLT AZO ROST F DHI LULU",
    "XLP": "COST WMT PG KO PM PEP MDLZ MO CL TGT KMB KDP GIS STZ SYY KR HSY KHC ADM EL",
    "XLI": "GE CAT RTX UBER HON UNP ETN BA DE LMT ADP GD NOC WM TT PH CSX UPS FDX EMR",
    "XLE": "XOM CVX COP EOG WMB SLB PSX KMI MPC OKE OXY VLO BKR FANG TRGP DVN HAL CTRA EQT",
    "XLB": "LIN SHW APD ECL FCX NEM CTVA DD NUE DOW VMC MLM PPG IFF LYB STLD BALL AVY PKG IP",
    "XLU": "NEE SO DUK CEG SRE AEP D VST PCG EXC XEL PEG ED EIX WEC ETR AWK DTE PPL AEE",
    "XLRE": "PLD AMT EQIX WELL SPG PSA O CCI DLR VICI EXR CBRE AVB IRM EQR VTR SBAC WY INVH ARE",
    "XLC": "META GOOGL GOOG NFLX TMUS DIS CMCSA VZ T CHTR EA TTWO LYV OMC WBD IPG MTCH FOXA NWSA",
    "^STI": "D05.SI O39.SI U11.SI Z74.SI C38U.SI A17U.SI S68.SI BN4.SI C6L.SI G13.SI F34.SI S63.SI "
            "Y92.SI 9CI.SI N2IU.SI ME8U.SI M44U.SI U96.SI V03.SI H78.SI BS6.SI 5E2.SI J36.SI C07.SI "
            "D01.SI S58.SI U14.SI AJBU.SI",
}
SG_LABELS = {
    "D05.SI": "DBS", "O39.SI": "OCBC", "U11.SI": "UOB", "Z74.SI": "Singtel", "C38U.SI": "CapitaLand ICT",
    "A17U.SI": "CapitaLand Ascendas", "S68.SI": "SGX", "BN4.SI": "Keppel", "C6L.SI": "SIA",
    "G13.SI": "Genting SG", "F34.SI": "Wilmar", "S63.SI": "ST Engineering", "Y92.SI": "Thai Bev",
    "9CI.SI": "CapitaLand Inv", "N2IU.SI": "Mapletree PCT", "ME8U.SI": "Mapletree Ind", "M44U.SI": "Mapletree Log",
    "U96.SI": "Sembcorp", "V03.SI": "Venture", "H78.SI": "HongKong Land", "BS6.SI": "YZJ Shipbldg",
    "5E2.SI": "Seatrium", "J36.SI": "Jardine M&C", "C07.SI": "Jardine C&C", "D01.SI": "DFI Retail",
    "S58.SI": "SATS", "U14.SI": "UOL", "AJBU.SI": "Keppel DC REIT", "ES3.SI": "SPDR STI ETF",
    "G3B.SI": "Nikko AM STI ETF", "^STI": "Straits Times Index",
}
MARKET_DRILLDOWN_STOCKS = {k: v.split() for k, v in {**MARKET_BROAD_STOCKS, **MARKET_SECTOR_STOCKS}.items()}
SEGMENT_LABELS = {s["symbol"]: s["label"] for s in MARKET_SEGMENTS}

# --------------------------------------------------------------------------- #
# Small TTL cache (keeps us polite to public endpoints)
# --------------------------------------------------------------------------- #
_cache: dict[str, tuple[float, object]] = {}
_cache_lock = threading.Lock()


def cached(key: str, ttl: float, producer):
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = producer()
    with _cache_lock:
        _cache[key] = (time.time(), value)
    return value


# --------------------------------------------------------------------------- #
# Watchlist persistence
# --------------------------------------------------------------------------- #
_watchlist_lock = threading.Lock()


def _to_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def normalize_market(value, symbol: str = "") -> str:
    text = str(value or "").strip().upper()
    if text in ("SG", "SGX", "SINGAPORE"):
        return "SG"
    if text in ("US", "USA"):
        return "US"
    return "SG" if symbol.upper().endswith(".SI") else "US"


def normalize_watchlist(payload) -> dict:
    items = payload.get("symbols", []) if isinstance(payload, dict) else []
    result, seen = [], set()
    for raw in items if isinstance(items, list) else []:
        if isinstance(raw, str):
            raw = {"symbol": raw}
        if not isinstance(raw, dict):
            continue
        symbol = str(raw.get("symbol", "")).strip().upper()
        if not symbol:
            continue
        market = normalize_market(raw.get("market"), symbol)
        if (symbol, market) in seen:
            continue
        seen.add((symbol, market))
        entry = {"symbol": symbol, "label": str(raw.get("label") or "").strip() or symbol, "market": market}
        position = _to_float(raw.get("position"))
        average = _to_float(raw.get("averagePrice"))
        if position is not None:
            entry["position"] = position
        if average is not None and average >= 0:
            entry["averagePrice"] = average
        result.append(entry)
    return {"symbols": result}


def load_watchlist(user_id: str) -> dict:
    """The user's watchlist from their encrypted vault."""
    vault = ACCOUNTS.read_vault(user_id)
    return normalize_watchlist(vault.get("watchlist") or DEFAULT_WATCHLIST)


def save_watchlist(user_id: str, payload) -> dict:
    data = normalize_watchlist(payload)

    def mutate(vault):
        vault["watchlist"] = data
    ACCOUNTS.update_vault(user_id, mutate)
    return data


def initialise_vault(user_id: str, first_account: bool) -> str | None:
    """Seed a new account's vault. The first account adopts the old plaintext watchlist.json,
    which is then deleted so no unencrypted copy is left behind."""
    note = None
    watchlist = DEFAULT_WATCHLIST
    if first_account and WATCHLIST_FILE.exists():
        try:
            watchlist = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
            note = "imported"
        except (OSError, json.JSONDecodeError):
            watchlist = DEFAULT_WATCHLIST
    vault = {"watchlist": normalize_watchlist(watchlist)}
    alerts.alerts_section(vault)
    ACCOUNTS.write_vault(user_id, vault)
    if note == "imported" and ACCOUNTS.read_vault(user_id).get("watchlist"):
        try:
            WATCHLIST_FILE.unlink()
        except OSError:
            note = "imported-but-not-deleted"
    return note


# --------------------------------------------------------------------------- #
# Version metadata
# --------------------------------------------------------------------------- #
def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None


def version_payload() -> dict:
    release = "0.0.0"
    try:
        match = re.search(r"^##\s*\[([^\]]+)\]", CHANGELOG_FILE.read_text(encoding="utf-8"), re.M)
        if match:
            release = match.group(1).strip()
    except OSError:
        pass
    build = _git("rev-list", "--count", "HEAD") or "0"
    commit = _git("rev-parse", "--short", "HEAD") or "unknown"
    number = f"{release}+{build}.{commit}"
    # Without git there is no build/commit, so show the plain release number.
    display = f"v{number}" if commit != "unknown" else f"v{release}"
    return {"release": release, "build": build, "commit": commit, "releaseDate": release_date(release),
            "versionNumber": number, "display": display, "demo": DEMO_MODE}


def release_notes() -> list[dict]:
    """CHANGELOG.md parsed into [{version, date, summary, sections:[{title, items}]}], newest first."""
    try:
        text = CHANGELOG_FILE.read_text(encoding="utf-8")
    except OSError:
        return []
    releases, current, section = [], None, None
    for raw in text.splitlines():
        line = raw.rstrip()
        head = re.match(r"^##\s*\[([^\]]+)\](?:\s*-\s*(.+))?$", line)
        if head:
            current = {"version": head.group(1).strip(), "date": (head.group(2) or "").strip(),
                       "summary": "", "sections": []}
            releases.append(current)
            section = None
        elif current is None:
            continue
        elif line.startswith("### "):
            section = {"title": line[4:].strip(), "items": []}
            current["sections"].append(section)
        elif line.startswith("- "):
            if section is None:
                section = {"title": "Changes", "items": []}
                current["sections"].append(section)
            section["items"].append(line[2:].strip())
        elif line.strip() and section is None:
            current["summary"] = (current["summary"] + " " + line.strip()).strip()
    return releases


def release_date(version: str) -> str:
    return next((r["date"] for r in release_notes() if r["version"] == version), "")


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #
def fetch_text(url: str, timeout: float = HTTP_TIMEOUT) -> str:
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json,text/csv,application/rss+xml,text/xml,*/*",
        "Accept-Language": "en-US,en;q=0.9",
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def fetch_json(url: str) -> dict:
    return json.loads(fetch_text(url))


# --------------------------------------------------------------------------- #
# Yahoo chart
# --------------------------------------------------------------------------- #
def yahoo_chart_url(symbol: str, range_: str, interval: str = "1d", prepost: bool = False) -> str:
    q = urllib.parse.quote(symbol, safe="")
    extra = "&includePrePost=true" if prepost else ""
    return f"https://query1.finance.yahoo.com/v8/finance/chart/{q}?range={range_}&interval={interval}{extra}"


def _market_state(meta: dict) -> str:
    period = meta.get("currentTradingPeriod") or {}
    now = time.time()
    for key, label in (("pre", "PRE"), ("regular", "REGULAR"), ("post", "POST")):
        window = period.get(key) or {}
        if window.get("start") and window.get("end") and window["start"] <= now < window["end"]:
            return label
    return "CLOSED"


def parse_yahoo_chart(payload: dict, symbol: str) -> dict:
    chart = payload.get("chart") or {}
    if chart.get("error"):
        raise ValueError(chart["error"].get("description") or "Yahoo chart error")
    result = (chart.get("result") or [None])[0]
    if not result:
        raise ValueError("Empty Yahoo chart result")
    meta = result.get("meta") or {}
    offset = int(meta.get("gmtoffset") or 0)
    stamps = result.get("timestamp") or []
    closes = (((result.get("indicators") or {}).get("quote") or [{}])[0].get("close")) or []

    def local_date(ts: int) -> str:
        return datetime.fromtimestamp(ts + offset, tz=timezone.utc).strftime("%Y-%m-%d")

    points = []
    for ts, close in zip(stamps, closes):
        if close is None:
            continue
        date = local_date(ts)
        point = {"date": date, "timestamp": ts * 1000, "close": round(float(close), 4)}
        if points and points[-1]["date"] == date:
            points[-1] = point
        else:
            points.append(point)

    price = _to_float(meta.get("regularMarketPrice"))
    if price is None and points:
        price = points[-1]["close"]
    if price is None:
        raise ValueError("No price in Yahoo chart")

    previous = None
    market_time = meta.get("regularMarketTime")
    if market_time:
        today = local_date(int(market_time))
        earlier = [p for p in points if p["date"] < today]
        if earlier:
            previous = earlier[-1]["close"]
        if points and points[-1]["date"] == today:
            points[-1]["close"] = round(price, 4)
        elif points and points[-1]["date"] < today:
            points.append({"date": today, "timestamp": int(market_time) * 1000, "close": round(price, 4)})
    if previous is None:
        previous = _to_float(meta.get("chartPreviousClose")) if len(points) < 2 else points[-2]["close"]

    change = price - previous if previous else 0.0
    return {
        "symbol": symbol,
        "name": meta.get("longName") or meta.get("shortName") or "",
        "price": round(price, 4),
        "previousClose": previous,
        "dayChange": round(change, 4),
        "dayChangePct": round(change / previous * 100, 4) if previous else 0.0,
        "currency": meta.get("currency") or ("SGD" if symbol.endswith(".SI") else "USD"),
        "exchange": meta.get("exchangeName") or meta.get("fullExchangeName") or "",
        "marketState": _market_state(meta),
        "isLive": True,
        "dataSource": "Yahoo Finance",
        "points": points,
    }


def extended_hours_quote(symbol: str, state: str, regular_price: float) -> dict:
    """Pre-market / after-hours last price from a 1-day 5m chart with pre/post data."""
    try:
        payload = fetch_json(yahoo_chart_url(symbol, "1d", "5m", prepost=True))
        result = payload["chart"]["result"][0]
        closes = [c for c in result["indicators"]["quote"][0].get("close") or [] if c is not None]
        if not closes:
            return {}
        last = float(closes[-1])
        change = last - regular_price
        return {
            "extendedLabel": "Pre-market" if state == "PRE" else "After hours",
            "extendedPrice": round(last, 4),
            "extendedChange": round(change, 4),
            "extendedChangePct": round(change / regular_price * 100, 4) if regular_price else 0.0,
        }
    except Exception:  # noqa: BLE001 - optional enrichment only
        return {}


# --------------------------------------------------------------------------- #
# Stooq fallback
# --------------------------------------------------------------------------- #
def stooq_symbol(symbol: str) -> str:
    s = symbol.strip().lower().replace(":", ".")
    if s.endswith(".si"):
        return s[:-3] + ".sg"
    return s if "." in s or s.startswith("^") else f"{s}.us"


def fetch_stooq_chart(symbol: str, range_: str) -> dict:
    text = fetch_text(f"https://stooq.com/q/d/l/?s={urllib.parse.quote(stooq_symbol(symbol))}&i=d")
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows or "Close" not in rows[0]:
        raise ValueError("Stooq returned no data")
    cutoff = (datetime.now() - timedelta(days=RANGE_DAYS.get(range_, 92))).strftime("%Y-%m-%d")
    points = []
    for row in rows:
        close = _to_float(row.get("Close"))
        date = row.get("Date") or ""
        if close is None or date < cutoff:
            continue
        stamp = int(datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
        points.append({"date": date, "timestamp": stamp, "close": round(close, 4)})
    if not points:
        raise ValueError("Stooq returned no points in range")
    price = points[-1]["close"]
    previous = points[-2]["close"] if len(points) > 1 else price
    change = price - previous
    return {
        "symbol": symbol, "name": "", "price": price, "previousClose": previous,
        "dayChange": round(change, 4), "dayChangePct": round(change / previous * 100, 4) if previous else 0.0,
        "currency": "SGD" if symbol.endswith(".SI") else "USD", "exchange": "", "marketState": "CLOSED",
        "isLive": False, "dataSource": "Stooq (end of day)", "points": points,
    }


def get_chart(symbol: str, range_: str, extended: bool = True) -> dict:
    if DEMO_MODE:
        return demo_chart(symbol, range_)

    def produce():
        errors = []
        try:
            data = parse_yahoo_chart(fetch_json(yahoo_chart_url(symbol, range_)), symbol)
            if extended and data["marketState"] in ("PRE", "POST") and not symbol.startswith("^"):
                data.update(extended_hours_quote(symbol, data["marketState"], data["price"]))
            return data
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Yahoo: {exc}")
        try:
            return fetch_stooq_chart(symbol, range_)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Stooq: {exc}")
        raise RuntimeError("; ".join(errors))

    return cached(f"chart:{symbol}:{range_}:{int(extended)}", 60, produce)


def get_snapshot(symbol: str) -> dict:
    """Lightweight quote: price + day change, built from a 5-day chart."""
    data = get_chart(symbol, "1mo" if DEMO_MODE else "5d", extended=False)
    return {k: data[k] for k in ("symbol", "price", "dayChange", "dayChangePct", "currency",
                                 "marketState", "isLive", "dataSource")}


def snapshots(symbols: list[str]) -> tuple[list[dict], list[str]]:
    results, errors = [], []
    futures = {symbol: POOL.submit(get_snapshot, symbol) for symbol in symbols}
    for symbol, future in futures.items():
        try:
            results.append(future.result(timeout=25))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{symbol}: {exc}")
    return results, errors


# --------------------------------------------------------------------------- #
# Market overview
# --------------------------------------------------------------------------- #
def market_overview() -> dict:
    snaps, errors = snapshots([s["symbol"] for s in MARKET_SEGMENTS])
    by_symbol = {s["symbol"]: s for s in snaps}
    segments = []
    for seg in MARKET_SEGMENTS:
        snap = by_symbol.get(seg["symbol"])
        if snap:
            segments.append({**seg, **snap, "hasDrilldown": seg["symbol"] in MARKET_DRILLDOWN_STOCKS})
    return {"segments": segments, "errors": errors}


def market_sector(symbol: str, count: int) -> dict:
    members = MARKET_DRILLDOWN_STOCKS.get(symbol)
    if not members:
        raise KeyError(symbol)
    count = max(4, min(count, len(members)))
    snaps, errors = snapshots(members[:count])
    for snap in snaps:
        snap["label"] = SG_LABELS.get(snap["symbol"], snap["symbol"])
    snaps.sort(key=lambda s: s.get("dayChangePct") or 0, reverse=True)
    return {"sectorSymbol": symbol, "sectorLabel": SEGMENT_LABELS.get(symbol, symbol),
            "requestedCount": count, "available": len(members), "stocks": snaps, "errors": errors}


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #
def infer_market(symbol: str, exchange: str) -> str:
    return "SG" if symbol.upper().endswith(".SI") or "singapore" in (exchange or "").lower() else "US"


def search_symbols(query: str, market: str) -> dict:
    if DEMO_MODE:
        return demo_search(query, market)

    def produce():
        url = ("https://query2.finance.yahoo.com/v1/finance/search?"
               + urllib.parse.urlencode({"q": query, "quotesCount": 20, "newsCount": 0, "listsCount": 0}))
        return fetch_json(url).get("quotes") or []

    quotes = cached(f"search:{query.lower()}", 3600, produce)
    items, seen = [], set()
    for q in quotes:
        symbol = str(q.get("symbol") or "").upper()
        if not symbol:
            continue
        exchange = q.get("exchDisp") or q.get("exchange") or ""
        item_market = infer_market(symbol, exchange)
        if market in ("US", "SG") and item_market != market:
            continue
        if (symbol, item_market) in seen:
            continue
        seen.add((symbol, item_market))
        items.append({"symbol": symbol, "label": q.get("shortname") or q.get("longname") or symbol,
                      "exchange": exchange, "type": q.get("quoteType") or "", "market": item_market})
        if len(items) >= 8:
            break
    return {"market": market, "items": items}


# --------------------------------------------------------------------------- #
# News (Google News RSS)
# --------------------------------------------------------------------------- #
def news_query(entry: dict) -> str:
    symbol, label = entry["symbol"], entry["label"]
    if entry["market"] == "SG":
        name = label if label != symbol else symbol.replace(".SI", "")
        return f'"{name}" Singapore stock when:3d'
    if label and label.upper() != symbol:
        return f'"{symbol}" stock OR "{label}" stock when:3d'
    return f'"{symbol}" stock when:3d'


def fetch_news_for(entry: dict) -> list[dict]:
    locale = "hl=en-SG&gl=SG&ceid=SG:en" if entry["market"] == "SG" else "hl=en-US&gl=US&ceid=US:en"
    url = f"https://news.google.com/rss/search?q={urllib.parse.quote(news_query(entry))}&{locale}"
    root = ET.fromstring(fetch_text(url))
    cutoff = datetime.now(timezone.utc) - timedelta(days=3)
    items = []
    for node in root.iter("item"):
        title = html.unescape(node.findtext("title") or "").strip()
        source = (node.findtext("source") or "").strip()
        if " - " in title:
            head, tail = title.rsplit(" - ", 1)
            title, source = head.strip(), source or tail.strip()
        try:
            published = parsedate_to_datetime(node.findtext("pubDate") or "")
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        if published < cutoff or not title:
            continue
        items.append({"title": title, "link": node.findtext("link") or "", "source": source,
                      "publishedAt": published.isoformat()})
    items.sort(key=lambda i: i["publishedAt"], reverse=True)
    return items[:5]


def news_payload(market: str, user_id: str) -> dict:
    entries = [e for e in load_watchlist(user_id)["symbols"] if market == "ALL" or e["market"] == market]
    groups = []
    futures = []
    for entry in entries:
        if DEMO_MODE:
            futures.append((entry, None))
        else:
            futures.append((entry, POOL.submit(cached, f"news:{entry['symbol']}:{entry['market']}",
                                               600, lambda e=entry: fetch_news_for(e))))
    for entry, future in futures:
        group = {"symbol": entry["symbol"], "label": entry["label"], "market": entry["market"], "items": []}
        try:
            group["items"] = demo_news(entry) if future is None else future.result(timeout=25)
        except Exception as exc:  # noqa: BLE001
            group["error"] = str(exc)
        groups.append(group)
    return {"market": market, "groups": groups}


# --------------------------------------------------------------------------- #
# FX
# --------------------------------------------------------------------------- #
def fx_payload() -> dict:
    if DEMO_MODE:
        return {"base": "USD", "quote": "SGD", "rate": 1.2875, "source": "Demo", "isLive": False}

    def produce():
        try:
            data = fetch_json("https://api.frankfurter.dev/v1/latest?base=USD&symbols=SGD")
            return {"base": "USD", "quote": "SGD", "rate": float(data["rates"]["SGD"]),
                    "source": "Frankfurter (ECB)", "asOf": data.get("date"), "isLive": True}
        except Exception:  # noqa: BLE001
            data = parse_yahoo_chart(fetch_json(yahoo_chart_url("SGD=X", "5d")), "SGD=X")
            return {"base": "USD", "quote": "SGD", "rate": data["price"], "source": "Yahoo Finance",
                    "isLive": True}

    return cached("fx:USDSGD", 1800, produce)


# --------------------------------------------------------------------------- #
# Demo data (deterministic synthetic prices so the UI works offline)
# --------------------------------------------------------------------------- #
def _seed(*parts) -> int:
    return int(hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()[:12], 16)


def demo_chart(symbol: str, range_: str) -> dict:
    days = min(RANGE_DAYS.get(range_, 92), 1827)
    rng = random.Random(_seed(symbol))
    if symbol in ("^VIX", "^STI"):
        base = {"^VIX": 16.0, "^STI": 3200.0}[symbol]
    elif symbol.endswith(".SI"):
        base = rng.uniform(1.2, 30)
    else:
        base = rng.uniform(20, 480)
    drift, vol = rng.uniform(-0.0003, 0.0008), rng.uniform(0.007, 0.022)
    today = datetime.now().date()
    dates = [today - timedelta(days=i) for i in range(1827, -1, -1)]
    dates = [d for d in dates if d.weekday() < 5]
    walk = random.Random(_seed(symbol, "walk"))
    price, series = base, []
    for d in dates:
        price *= math.exp(drift + vol * walk.gauss(0, 1))
        series.append((d, price))
    # today's wiggle changes a little each minute so refresh looks alive
    tick = random.Random(_seed(symbol, datetime.now().strftime("%Y%m%d%H%M")))
    series[-1] = (series[-1][0], series[-1][1] * (1 + tick.gauss(0, vol / 6)))
    scale = base / series[-1][1]  # anchor today's price near the base so levels look plausible
    series = [(d, p * scale) for d, p in series]
    cutoff = today - timedelta(days=days)
    points = [{"date": d.isoformat(), "timestamp": int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000),
               "close": round(p, 4)} for d, p in series if d >= cutoff]
    price, previous = points[-1]["close"], points[-2]["close"]
    change = price - previous
    return {
        "symbol": symbol, "name": SG_LABELS.get(symbol, ""), "price": price, "previousClose": previous,
        "dayChange": round(change, 4), "dayChangePct": round(change / previous * 100, 4),
        "currency": "SGD" if symbol.endswith(".SI") or symbol == "^STI" else "USD",
        "exchange": "SES" if symbol.endswith(".SI") else "DEMO", "marketState": "CLOSED",
        "isLive": False, "dataSource": "Demo data", "points": points,
    }


DEMO_UNIVERSE = [
    ("AAPL", "Apple Inc.", "NasdaqGS"), ("AMZN", "Amazon.com, Inc.", "NasdaqGS"),
    ("ASTS", "AST SpaceMobile, Inc.", "NasdaqGS"), ("GOOGL", "Alphabet Inc.", "NasdaqGS"),
    ("META", "Meta Platforms, Inc.", "NasdaqGS"), ("MSFT", "Microsoft Corporation", "NasdaqGS"),
    ("NVDA", "NVIDIA Corporation", "NasdaqGS"), ("PLTR", "Palantir Technologies Inc.", "NasdaqGS"),
    ("RKLB", "Rocket Lab Corporation", "NasdaqCM"), ("TSLA", "Tesla, Inc.", "NasdaqGS"),
    ("D05.SI", "DBS Group Holdings Ltd", "Singapore"), ("O39.SI", "Oversea-Chinese Banking Corp", "Singapore"),
    ("U11.SI", "United Overseas Bank Ltd", "Singapore"), ("Z74.SI", "Singapore Telecommunications", "Singapore"),
    ("C6L.SI", "Singapore Airlines Ltd", "Singapore"), ("S68.SI", "Singapore Exchange Ltd", "Singapore"),
]


def demo_search(query: str, market: str) -> dict:
    q = query.lower()
    items = [{"symbol": s, "label": n, "exchange": e, "type": "EQUITY", "market": infer_market(s, e)}
             for s, n, e in DEMO_UNIVERSE if q in s.lower() or q in n.lower()]
    items = [i for i in items if market not in ("US", "SG") or i["market"] == market]
    return {"market": market, "items": items[:8]}


def demo_news(entry: dict) -> list[dict]:
    rng = random.Random(_seed(entry["symbol"], datetime.now().strftime("%Y%m%d")))
    templates = ["{l} shares move as traders weigh latest guidance", "Analysts revisit {l} price targets",
                 "What the options market is pricing in for {l}", "{l}: three things to watch this week",
                 "Funds adjust {l} stakes in latest filings", "{l} volume spikes ahead of sector data"]
    now = datetime.now(timezone.utc)
    count = rng.randint(0, 5)
    return [{"title": f"[Demo] {rng.choice(templates).format(l=entry['label'])}", "link": "https://news.google.com/",
             "source": rng.choice(["Demo Wire", "Sample Times", "Placeholder Daily"]),
             "publishedAt": (now - timedelta(hours=rng.randint(1, 70))).isoformat()} for _ in range(count)]




# --------------------------------------------------------------------------- #
# Accounts + alert engine
# --------------------------------------------------------------------------- #
ACCOUNTS = security.AccountService(DATA_DIR)


def _fx_rate() -> float:
    return fx_payload()["rate"]


ALERTS = alerts.AlertEngine(ACCOUNTS, lambda symbol, range_: get_chart(symbol, range_, extended=False), _fx_rate,
                            log=lambda msg: print(msg, flush=True))


def alerts_payload(user_id: str) -> dict:
    vault = ACCOUNTS.read_vault(user_id)
    sec = alerts.alerts_section(vault)
    return {
        "rules": ALERTS.rule_views(vault),
        "settings": sec["settings"],
        "channels": alerts.public_channels(sec["channels"]),
        "types": alerts.RULE_TYPES,
        "maPeriods": alerts.MA_PERIODS,
        "quietNow": alerts.in_quiet_hours(sec["settings"]),
        "backgroundActive": ACCOUNTS.is_unlocked(user_id),
    }


def notifications_payload(user_id: str, limit: int = 50) -> dict:
    vault = ACCOUNTS.read_vault(user_id)
    items = vault.get("notifications") or []
    return {"items": items[:limit], "unread": sum(1 for n in items if not n.get("read")), "total": len(items)}


def export_payload(user_id: str) -> dict:
    vault = ACCOUNTS.read_vault(user_id)
    sec = alerts.alerts_section(vault)
    user = ACCOUNTS.user(user_id)
    return {"exportedAt": security.now_iso(), "username": user["username"],
            "watchlist": normalize_watchlist(vault.get("watchlist") or {}),
            "alertRules": [{k: r.get(k) for k in ("type", "symbol", "market", "value", "period", "repeat",
                                                    "cooldownMinutes", "note", "enabled")} for r in sec["rules"]],
            "alertSettings": sec["settings"],
            "displayPreferences": vault.get("prefs") or {},
            "note": "Channel secrets (bot tokens, passwords) are not included."}


# --------------------------------------------------------------------------- #
# Request handler
# --------------------------------------------------------------------------- #
STATIC_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".ico": "image/x-icon",
                ".png": "image/png"}
PUBLIC_STATIC = {"login.js", "styles.css"}
SECURITY_HEADERS = {
    "Content-Security-Policy": ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                                "img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; "
                                "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}
if USE_TLS:
    SECURITY_HEADERS["Strict-Transport-Security"] = "max-age=31536000"


class StockDashboardHandler(BaseHTTPRequestHandler):
    server_version = "MarketTracker/0.2"
    sys_version = ""

    # ---- helpers ---------------------------------------------------------- #
    def log_message(self, fmt, *args):  # quieter console: only log problems
        if len(args) > 1 and str(args[1]).startswith(("4", "5")) and str(args[1]) not in ("401", "403", "429"):
            sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    @property
    def client_ip(self) -> str:
        return self.client_address[0] if self.client_address else ""

    @property
    def agent(self) -> str:
        return (self.headers.get("User-Agent") or "")[:300]

    def _send(self, status: int, body: bytes, content_type: str, headers: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in SECURITY_HEADERS.items():
            self.send_header(key, value)
        for key, value in (headers or {}).items():
            if isinstance(value, list):
                for v in value:
                    self.send_header(key, v)
            else:
                self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(self, payload, status: int = 200, headers: dict | None = None):
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8", headers)

    def redirect(self, location: str, headers: dict | None = None):
        self._send(303, b"", "text/plain", {"Location": location, **(headers or {})})

    def session_cookie(self, token: str, remember: bool) -> str:
        parts = [f"{SESSION_COOKIE}={token}", "Path=/", "HttpOnly", "SameSite=Strict"]
        if remember:
            parts.append(f"Max-Age={security.REMEMBER_DAYS * 86400}")
        if USE_TLS:
            parts.append("Secure")
        return "; ".join(parts)

    def clear_cookie(self) -> str:
        return f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict" + ("; Secure" if USE_TLS else "")

    def token(self) -> str | None:
        jar = cookies.SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie") or "")
        except cookies.CookieError:
            return None
        morsel = jar.get(SESSION_COOKIE)
        return morsel.value if morsel else None

    def current_session(self):
        return ACCOUNTS.session(self.token(), touch=self.headers.get("X-Background") != "1")

    def same_origin(self) -> bool:
        """Reject cross-site POSTs. Browsers send Origin on POST; if present it must match Host."""
        origin = self.headers.get("Origin")
        if not origin or origin == "null":
            return origin is None
        host = (self.headers.get("Host") or "").lower()
        return urllib.parse.urlparse(origin).netloc.lower() == host

    def serve_static(self, name: str):
        path = (STATIC_DIR / name).resolve()
        if STATIC_DIR.resolve() not in path.parents or not path.is_file():
            self.send_json({"error": "Not found"}, 404)
            return
        self._send(200, path.read_bytes(), STATIC_TYPES.get(path.suffix, "application/octet-stream"))

    def read_json(self) -> dict:
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            raise AuthError("Expected JSON.", 415)
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1_000_000:
            raise AuthError("Request too large.", 413)
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            data = json.loads(raw or b"{}")
        except json.JSONDecodeError as exc:
            raise AuthError("Invalid JSON.", 400) from exc
        if not isinstance(data, dict):
            raise AuthError("Invalid JSON.", 400)
        return data

    def send_error_json(self, exc: AuthError):
        headers = {}
        if exc.extra.get("retryAfter"):
            headers["Retry-After"] = str(exc.extra["retryAfter"])
        self.send_json({"error": str(exc), **exc.extra}, exc.status, headers)

    # ---- GET -------------------------------------------------------------- #
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path, query = parsed.path, urllib.parse.parse_qs(parsed.query)
        arg = lambda key, default="": (query.get(key) or [default])[0].strip()  # noqa: E731
        try:
            session = self.current_session()
            if path == "/login":
                if session:
                    return self.redirect("/")
                return self.serve_static("login.html")
            if path == "/logout":  # old bookmark: just go to the login page
                return self.redirect("/login")
            if path.startswith("/static/") and path[len("/static/"):] in PUBLIC_STATIC:
                return self.serve_static(path[len("/static/"):])
            if path == "/api/auth/state":
                return self.send_json({"setupRequired": ACCOUNTS.setup_required(), "signupOpen": ACCOUNTS.signup_open(),
                                       "signedIn": bool(session), "demo": DEMO_MODE, "https": USE_TLS,
                                       "legacyWatchlist": WATCHLIST_FILE.exists(),
                                       "version": version_payload()["display"]})
            if not session:
                if path.startswith("/api/"):
                    return self.send_json({"error": "Authentication required"}, 401)
                if path.startswith("/static/"):
                    return self._send(401, b"Authentication required", "text/plain")
                return self.redirect("/login")
            return self.route_get(path, arg, session)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except AuthError as exc:
            self.send_error_json(exc)
        except Exception as exc:  # noqa: BLE001
            self.send_json({"error": str(exc)}, 500)

    def route_get(self, path: str, arg, session):
        uid = session.user_id
        if path in ("/", "/index.html"):
            return self.serve_static("index.html")
        if path.startswith("/static/"):
            return self.serve_static(path[len("/static/"):])
        if path == "/api/account":
            return self.send_json(ACCOUNTS.profile(session))
        if path == "/api/account/sessions":
            return self.send_json({"sessions": ACCOUNTS.list_sessions(uid, session)})
        if path == "/api/account/activity":
            return self.send_json({"events": ACCOUNTS.activity(uid)})
        if path == "/api/account/export":
            body = json.dumps(export_payload(uid), indent=2).encode("utf-8")
            name = f"market-tracker-{ACCOUNTS.user(uid)['username']}-{datetime.now():%Y%m%d}.json"
            return self._send(200, body, "application/json; charset=utf-8",
                              {"Content-Disposition": f'attachment; filename="{name}"'})
        if path == "/api/alerts":
            return self.send_json(alerts_payload(uid))
        if path == "/api/notifications":
            try:
                limit = max(1, min(int(arg("limit", "50")), alerts.NOTIFICATIONS_KEEP))
            except ValueError:
                limit = 50
            return self.send_json(notifications_payload(uid, limit))
        if path == "/api/watchlist":
            return self.send_json(load_watchlist(uid))
        if path == "/api/prefs":
            return self.send_json({"prefs": ACCOUNTS.read_vault(uid).get("prefs") or {}})
        if path == "/api/version":
            return self.send_json(version_payload())
        if path == "/api/release-notes":
            return self.send_json({"current": version_payload(), "releases": release_notes()})
        if path == "/api/fx":
            return self.send_json(fx_payload())
        if path == "/api/news":
            market = arg("market", "US").upper()
            return self.send_json(news_payload(market if market in ("US", "SG", "ALL") else "US", uid))
        if path == "/api/search":
            q, market = arg("q"), arg("market", "US").upper()
            if len(q) < 2:
                return self.send_json({"market": market, "items": []})
            return self.send_json(search_symbols(q, market))
        if path == "/api/market-overview":
            return self.send_json(market_overview())
        if path == "/api/market-sector":
            symbol = arg("symbol").upper()
            try:
                count = int(arg("count", "12"))
            except ValueError:
                count = 12
            if symbol not in MARKET_DRILLDOWN_STOCKS:
                return self.send_json({"error": f"No drilldown for {symbol}"}, 404)
            return self.send_json(market_sector(symbol, count))
        if path == "/api/chart":
            symbol, range_ = arg("symbol").upper(), arg("range", "3mo")
            if not symbol:
                return self.send_json({"error": "symbol is required"}, 400)
            if range_ not in CHART_RANGES:
                range_ = "3mo"
            try:
                return self.send_json(get_chart(symbol, range_))
            except Exception as exc:  # noqa: BLE001
                return self.send_json({"symbol": symbol, "error": f"Data unavailable ({exc})"}, 502)
        return self.send_json({"error": "Not found"}, 404)

    # ---- POST ------------------------------------------------------------- #
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            if not self.same_origin():
                raise AuthError("Cross-site request blocked.", 403)
            if path.startswith("/api/auth/"):
                return self.route_auth(path, self.read_json())
            session = self.current_session()
            if not session:
                raise AuthError("Authentication required", 401)
            if not hmac_equal(self.headers.get("X-CSRF-Token") or "", session.csrf):
                raise AuthError("Security token missing or expired. Reload the page.", 403)
            return self.route_post(path, self.read_json(), session)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except AuthError as exc:
            self.send_error_json(exc)
        except alerts.RuleError as exc:
            self.send_json({"error": str(exc)}, 400)
        except Exception as exc:  # noqa: BLE001
            self.send_json({"error": str(exc)}, 500)

    def route_auth(self, path: str, body: dict):
        ip, agent = self.client_ip, self.agent
        if path in ("/api/auth/setup", "/api/auth/signup"):
            first = ACCOUNTS.setup_required()
            user, recovery_key, dek = ACCOUNTS.register(str(body.get("username") or ""), str(body.get("password") or ""),
                                                        ip, agent)
            imported = initialise_vault(user["id"], first)
            token = ACCOUNTS.sign_in(user["username"], str(body.get("password")), False, ip, agent)["token"]
            return self.send_json({"ok": True, "recoveryKey": recovery_key, "imported": imported},
                                  201, {"Set-Cookie": self.session_cookie(token, False)})
        if path == "/api/auth/login":
            remember = bool(body.get("remember"))
            result = ACCOUNTS.sign_in(str(body.get("username") or ""), str(body.get("password") or ""),
                                      remember, ip, agent)
            if result.get("twoFactor"):
                return self.send_json({"twoFactor": True, "challenge": result["challenge"]})
            return self.send_json({"ok": True}, 200, {"Set-Cookie": self.session_cookie(result["token"], remember)})
        if path == "/api/auth/2fa":
            challenge = str(body.get("challenge") or "")
            remember = bool(body.get("remember"))
            token = ACCOUNTS.verify_challenge(challenge, str(body.get("code") or ""), ip, agent)
            return self.send_json({"ok": True}, 200, {"Set-Cookie": self.session_cookie(token, remember)})
        if path == "/api/auth/recover":
            fresh = ACCOUNTS.reset_with_recovery_key(str(body.get("username") or ""), str(body.get("recoveryKey") or ""),
                                                     str(body.get("newPassword") or ""), ip)
            return self.send_json({"ok": True, "recoveryKey": fresh})
        if path == "/api/auth/logout":
            ACCOUNTS.sign_out(self.token() or "")
            return self.send_json({"ok": True}, 200, {"Set-Cookie": self.clear_cookie()})
        raise AuthError("Not found", 404)

    def route_post(self, path: str, body: dict, session):
        uid, ip = session.user_id, self.client_ip
        # --- account & security
        if path == "/api/account/password":
            ended = ACCOUNTS.change_password(session, str(body.get("current") or ""), str(body.get("new") or ""), ip)
            return self.send_json({"ok": True, "signedOutOthers": ended})
        if path == "/api/account/2fa/begin":
            return self.send_json(ACCOUNTS.totp_begin(session))
        if path == "/api/account/2fa/enable":
            return self.send_json({"ok": True, "backupCodes": ACCOUNTS.totp_enable(session, str(body.get("code") or ""), ip)})
        if path == "/api/account/2fa/disable":
            ACCOUNTS.totp_disable(session, str(body.get("password") or ""), str(body.get("code") or ""), ip)
            return self.send_json({"ok": True})
        if path == "/api/account/2fa/backup-codes":
            return self.send_json({"backupCodes": ACCOUNTS.regenerate_backup_codes(session, str(body.get("password") or ""), ip)})
        if path == "/api/account/recovery-key":
            return self.send_json({"recoveryKey": ACCOUNTS.new_recovery_key_for(session, str(body.get("password") or ""), ip)})
        if path == "/api/account/sessions/revoke":
            if body.get("others"):
                return self.send_json({"ok": True, "ended": ACCOUNTS.revoke_other_sessions(uid, session)})
            return self.send_json({"ok": ACCOUNTS.revoke_session(uid, str(body.get("id") or ""))})
        if path == "/api/account/lock":
            ACCOUNTS.lock_vault(uid, ip)
            return self.send_json({"ok": True}, 200, {"Set-Cookie": self.clear_cookie()})
        if path == "/api/account/settings":
            if ACCOUNTS.user(uid).get("role") != "owner":
                raise AuthError("Only the owner account can change this.", 403)
            ACCOUNTS.set_signup(bool(body.get("allowSignup")))
            return self.send_json({"ok": True, "allowSignup": bool(body.get("allowSignup"))})
        # --- watchlist
        if path == "/api/watchlist":
            return self.send_json(save_watchlist(uid, body), 201)
        if path == "/api/prefs":
            raw = body.get("prefs") if isinstance(body.get("prefs"), dict) else {}
            prefs = {str(k)[:60]: str(v)[:2000] for k, v in list(raw.items())[:60]}
            ACCOUNTS.update_vault(uid, lambda vault: vault.__setitem__("prefs", prefs))
            return self.send_json({"ok": True, "prefs": prefs})
        # --- alerts
        if path == "/api/alerts/rule":
            rule_id = str(body.get("id") or "")

            def mutate(vault):
                sec = alerts.alerts_section(vault)
                if rule_id:
                    idx = next((i for i, r in enumerate(sec["rules"]) if r["id"] == rule_id), None)
                    if idx is None:
                        raise alerts.RuleError("That rule no longer exists.")
                    sec["rules"][idx] = alerts.normalize_rule(body, sec["rules"][idx])
                    return sec["rules"][idx]
                if len(sec["rules"]) >= alerts.MAX_RULES:
                    raise alerts.RuleError(f"You can have up to {alerts.MAX_RULES} rules.")
                rule = alerts.normalize_rule(body)
                sec["rules"].append(rule)
                return rule
            rule = ACCOUNTS.update_vault(uid, mutate)
            return self.send_json({"ok": True, "rule": rule, **alerts_payload(uid)}, 201)
        if path == "/api/alerts/rule/delete":
            rule_id = str(body.get("id") or "")

            def mutate(vault):
                sec = alerts.alerts_section(vault)
                sec["rules"] = [r for r in sec["rules"] if r["id"] != rule_id]
            ACCOUNTS.update_vault(uid, mutate)
            return self.send_json({"ok": True, **alerts_payload(uid)})
        if path == "/api/alerts/rule/toggle":
            rule_id, enabled = str(body.get("id") or ""), bool(body.get("enabled"))

            def mutate(vault):
                for r in alerts.alerts_section(vault)["rules"]:
                    if r["id"] == rule_id:
                        r["enabled"] = enabled
                        if enabled:
                            r["lastMet"] = None
            ACCOUNTS.update_vault(uid, mutate)
            return self.send_json({"ok": True, **alerts_payload(uid)})
        if path == "/api/alerts/settings":
            def mutate(vault):
                sec = alerts.alerts_section(vault)
                if isinstance(body.get("channels"), dict):
                    sec["channels"] = alerts.merge_channels(sec["channels"], body["channels"])
                if isinstance(body.get("settings"), dict):
                    sec["settings"] = alerts.merge_settings(sec["settings"], body["settings"])
            ACCOUNTS.update_vault(uid, mutate)
            return self.send_json({"ok": True, **alerts_payload(uid)})
        if path == "/api/alerts/test":
            channel = str(body.get("channel") or "") or None
            return self.send_json({"results": ALERTS.test_channels(uid, channel)})
        if path == "/api/alerts/check":
            summary = ALERTS.check_user(uid, force=True)
            return self.send_json({**summary, **alerts_payload(uid)})
        # --- notifications
        if path == "/api/notifications/read":
            ids = set(body.get("ids") or [])

            def mutate(vault):
                for n in vault.get("notifications") or []:
                    if body.get("all") or n.get("id") in ids:
                        n["read"] = True
            ACCOUNTS.update_vault(uid, mutate)
            return self.send_json(notifications_payload(uid))
        if path == "/api/notifications/clear":
            ACCOUNTS.update_vault(uid, lambda vault: vault.__setitem__("notifications", []))
            return self.send_json(notifications_payload(uid))
        raise AuthError("Not found", 404)


def hmac_equal(a: str, b: str) -> bool:
    import hmac as _hmac
    return bool(a) and _hmac.compare_digest(a.encode(), b.encode())


# --------------------------------------------------------------------------- #
# Entrypoint
# --------------------------------------------------------------------------- #
def lan_address() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return None


def admin_command() -> bool:
    """Owner tools that need access to this computer (the trust boundary):
    python app.py --list-users
    python app.py --disable-2fa USERNAME   (lost phone and backup codes)"""
    args = sys.argv[1:]
    if "--list-users" in args:
        for uid in ACCOUNTS.all_user_ids():
            u = ACCOUNTS.user(uid)
            print(f"{u['username']:<24} {u.get('role', 'member'):<7} 2FA {'on ' if u['totp'].get('enabled') else 'off'}  created {u.get('createdAt')}")
        return True
    if "--disable-2fa" in args:
        idx = args.index("--disable-2fa")
        name = args[idx + 1] if idx + 1 < len(args) else ""
        user = ACCOUNTS.find_user(name)
        if not user:
            print(f"No account called {name!r}.")
            return True
        user["totp"] = {"enabled": False, "secret": None, "lastStep": 0}
        user["backupCodes"] = {"salt": "", "hashes": []}
        ACCOUNTS.audit(user, "two_factor_disabled", "console", "reset from the server console")
        print(f"Two-factor sign-in turned off for {user['username']}. (Stop the server before running this.)")
        return True
    return False


def main():
    if admin_command():
        return
    try:
        server = ThreadingHTTPServer((HOST, PORT), StockDashboardHandler)
    except OSError as exc:
        if exc.errno not in (errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", -1), 10048):
            raise
        print(f"Port {PORT} is busy - picking a free port instead.")
        server = ThreadingHTTPServer((HOST, 0), StockDashboardHandler)
    server.daemon_threads = True
    scheme = "http"
    if USE_TLS:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(TLS_CERT, TLS_KEY)
        server.socket = context.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
        scheme = "https"
    port = server.server_address[1]
    ALERTS.start()
    print("Market Tracker", version_payload()["display"], "(DEMO DATA)" if DEMO_MODE else "")
    print(f"  Local:   {scheme}://127.0.0.1:{port}/")
    if HOST in ("0.0.0.0", ""):
        ip = lan_address()
        if ip:
            print(f"  Network: {scheme}://{ip}:{port}/")
        if not USE_TLS:
            print("  Note: LAN mode without HTTPS - passwords cross your Wi-Fi unencrypted. See README > HTTPS.")
    if ACCOUNTS.setup_required():
        print("  First run: open the link above to create your account.")
    print(f"  Data folder: {DATA_DIR}")
    print("  Alert rules run while this window is open and you have signed in once. Ctrl+C to stop.")
    if "--no-browser" not in sys.argv and os.environ.get("STOCK_DASHBOARD_NO_BROWSER") != "1":
        url = f"{scheme}://127.0.0.1:{port}/"
        opener = threading.Timer(0.8, lambda: webbrowser.open(url))
        opener.daemon = True
        opener.start()
        print("  Opening the app in your browser... (add --no-browser to skip)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        ALERTS.stop()
        server.server_close()


if __name__ == "__main__":
    main()
