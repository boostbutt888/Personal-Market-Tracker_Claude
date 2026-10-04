/* Market Tracker — browser app. Plain JS, no dependencies. */
"use strict";

// ------------------------------------------------------------------ constants
const KEY = "stock-dashboard-";
const SECTIONS = [
  { id: "tickers", name: "Watchlist" },
  { id: "alerts", name: "Alert rules" },
  { id: "actions", name: "Action prompts" },
  { id: "benchmark", name: "Watchlist vs benchmarks" },
  { id: "options", name: "Options ideas" },
  { id: "news", name: "News" },
  { id: "market", name: "Market map" },
];
const BENCHMARKS = {
  US: [
    { symbol: "SPY", label: "S&P 500", on: true, color: "#4c78a8" },
    { symbol: "QQQ", label: "Nasdaq", on: true, color: "#9467bd" },
    { symbol: "DIA", label: "Dow", on: true, color: "#2a9d8f" },
    { symbol: "^VIX", label: "VIX", on: false, color: "#e45756" },
  ],
  SG: [
    { symbol: "^STI", label: "STI Index", on: true, color: "#4c78a8" },
    { symbol: "ES3.SI", label: "SPDR STI ETF", on: true, color: "#9467bd" },
    { symbol: "G3B.SI", label: "Nikko STI ETF", on: true, color: "#2a9d8f" },
    { symbol: "EWS", label: "MSCI Singapore", on: false, color: "#e45756" },
  ],
};
const RANGE_LABEL = { "1mo": "1M", "3mo": "3M", "6mo": "6M", "1y": "1Y", "2y": "2Y", "5y": "5Y", max: "Max" };
const THEME_VARIANTS = [
  { name: "sage", dark: false }, { name: "harbour", dark: false }, { name: "blush", dark: false },
  { name: "midnight", dark: true }, { name: "ember", dark: true }, { name: "moss", dark: true },
];

// ------------------------------------------------------------------ storage
const store = {
  get(k, d) { try { const v = localStorage.getItem(KEY + k); return v === null ? d : v; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(KEY + k, v); } catch { /* ignore */ } if (k !== PREFS_OWNER) schedulePrefsPush(); },
  json(k, d) { try { return JSON.parse(store.get(k, "")) ?? d; } catch { return d; } },
};

// Display preferences are kept in the browser for speed and mirrored into the signed-in
// user's encrypted store, so each user gets their own settings on any device.
const PREFS_OWNER = "prefs-owner";
function localPrefs() {
  const out = {};
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k.startsWith(KEY) && k !== KEY + PREFS_OWNER) out[k.slice(KEY.length)] = localStorage.getItem(k);
    }
  } catch { /* ignore */ }
  return out;
}
let prefsTimer = null;
function schedulePrefsPush() {
  if (!state?.csrf) return;
  clearTimeout(prefsTimer);
  prefsTimer = setTimeout(() => {
    api("/api/prefs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ prefs: localPrefs() }) }).catch(() => {});
  }, 800);
}
/** Returns true if the page is reloading to apply this user's saved preferences. */
async function syncPrefs(username) {
  let server = {};
  try { server = (await api("/api/prefs")).prefs || {}; } catch { return false; }
  const local = localPrefs();
  const owner = store.get(PREFS_OWNER, "");
  const same = JSON.stringify(Object.entries(server).sort()) === JSON.stringify(Object.entries(local).sort());
  try { localStorage.setItem(KEY + PREFS_OWNER, username); } catch { /* ignore */ }
  if (same) return false;
  const hasServer = Object.keys(server).length > 0;
  if (!hasServer && (owner === username || owner === "")) { schedulePrefsPush(); return false; }
  // Another user's settings are in this browser, or this user changed settings elsewhere.
  try {
    Object.keys(local).forEach((k) => localStorage.removeItem(KEY + k));
    Object.entries(server).forEach(([k, v]) => localStorage.setItem(KEY + k, v));
  } catch { return false; }
  if (sessionStorage.getItem("prefs-reloaded") === "1") return false;
  sessionStorage.setItem("prefs-reloaded", "1");
  location.reload();
  return true;
}

// ------------------------------------------------------------------ state
const state = {
  watchlist: [],
  usdToSgdRate: 1,
  fx: null,
  market: store.get("active-watchlist-market", "US"),
  range: store.get("range", "3mo"),
  currency: store.get("currency", "USD"),
  changeMode: store.get("change-mode", "percent"),
  refreshMode: store.get("refresh-mode", "manual"),
  refreshInterval: Number(store.get("refresh-interval", "5")),
  marketCount: store.get("market-stock-count", "12"),
  sectionOrder: store.json("section-order", SECTIONS.map((s) => s.id)),
  benchEnabled: store.json("benchmarks", {}),
  optionsFocus: store.get("options-focus", "all"),
  selectedOptionsSymbol: null,
  selectedMarketSector: null,
  activePositionTarget: null,
  latestChartResults: {}, // symbol -> {range, data} | {range, error}
  latestNewsGroups: [],
  marketSegments: null,
  newsOpen: new Set(),
  autoRefreshTimer: null,
  watchlistSearchTimer: null,
  lastWatchlistSearchQuery: "",
  autoLabel: null,
  refreshing: false,
  csrf: null,
  account: null,
};

// ------------------------------------------------------------------ utilities
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const avg = (arr) => arr.reduce((a, b) => a + b, 0) / arr.length;
const keyOf = (e) => `${e.symbol}|${e.market}`;
const cls = (n) => (n > 0.00001 ? "up" : n < -0.00001 ? "down" : "flat");
const MINUS = "−";

function fmt(n, dp = 2) {
  if (n == null || !isFinite(n)) return "—";
  return Number(n).toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp });
}
function priceDp(p, ccy) {
  const a = Math.abs(p);
  if (a < 1) return 4;
  if (a < 20 && ccy === "SGD") return 3;
  return 2;
}
function ccySymbol(ccy) { return { USD: "$", SGD: "S$" }[ccy] ?? (ccy ? ccy + " " : ""); }
function money(n, ccy, dp) {
  if (n == null || !isFinite(n)) return "—";
  const d = dp ?? priceDp(n, ccy);
  const sign = n < 0 ? MINUS : "";
  return `${sign}${ccySymbol(ccy)}${fmt(Math.abs(n), d)}`;
}
function signedMoney(n, ccy, dp) {
  if (n == null || !isFinite(n)) return "—";
  return (n > 0 ? "+" : "") + money(n, ccy, dp);
}
function signedPct(n, dp = 2) {
  if (n == null || !isFinite(n)) return "—";
  return `${n > 0 ? "+" : n < 0 ? MINUS : ""}${fmt(Math.abs(n), dp)}%`;
}
function compactMoney(n, ccy) {
  if (n == null || !isFinite(n)) return "—";
  const a = Math.abs(n);
  if (a >= 1e6) return `${n < 0 ? MINUS : ""}${ccySymbol(ccy)}${fmt(a / 1e6, 2)}M`;
  return money(n, ccy, 2);
}
/** Convert an amount from its native currency into the display currency. */
function toDisplay(value, fromCcy) {
  const to = state.currency;
  if (value == null || !fromCcy || fromCcy === to) return value;
  const r = state.usdToSgdRate || 1;
  if (fromCcy === "USD" && to === "SGD") return value * r;
  if (fromCcy === "SGD" && to === "USD") return value / r;
  return value; // other currencies are shown unconverted
}
function relTime(iso) {
  const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const h = Math.round(mins / 60);
  return h < 48 ? `${h}h ago` : `${Math.round(h / 24)}d ago`;
}
function dur(mins) {
  const h = Math.floor(mins / 60), m = mins % 60, d = Math.floor(h / 24);
  if (d >= 1) return `${d}d ${h % 24}h`;
  return h ? `${h}h ${m}m` : `${m}m`;
}

// Security: every state-changing request carries the session's CSRF token. Requests made while
// the user hasn't touched the page for a minute are flagged as background so they don't keep an
// idle session alive.
const activity = { last: Date.now() };
["pointerdown", "keydown", "wheel", "touchstart"].forEach((ev) =>
  window.addEventListener(ev, () => { activity.last = Date.now(); }, { passive: true, capture: true }));

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  const method = (options.method || "GET").toUpperCase();
  if (method !== "GET" && method !== "HEAD") headers["X-CSRF-Token"] = state.csrf || "";
  if (Date.now() - activity.last > 60000) headers["X-Background"] = "1";
  const res = await fetch(path, { credentials: "same-origin", ...options, headers });
  if (res.status === 401) { location.href = "/login"; throw new Error("Signed out"); }
  let body = null;
  try { body = await res.json(); } catch { /* ignore */ }
  if (!res.ok) throw new Error(body?.error || `HTTP ${res.status}`);
  return body;
}

let toastTimer = null;
function toast(message, actionLabel, action) {
  const el = $("toast");
  el.innerHTML = esc(message) + (actionLabel ? ` <button class="btn small" style="margin-left:10px">${esc(actionLabel)}</button>` : "");
  el.hidden = false;
  if (actionLabel) el.querySelector("button").onclick = () => { el.hidden = true; action(); };
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, actionLabel ? 6000 : 2600);
}

async function mapLimit(items, limit, fn) {
  const queue = [...items];
  const workers = Array.from({ length: Math.min(limit, queue.length) }, async () => {
    while (queue.length) { const item = queue.shift(); await fn(item); }
  });
  await Promise.all(workers);
}

// ------------------------------------------------------------------ watchlist helpers
function visibleEntries() {
  const list = state.watchlist;
  if (state.market === "ALL") return [...list.filter((e) => e.market === "US"), ...list.filter((e) => e.market === "SG")];
  return list.filter((e) => e.market === state.market);
}
function chartFor(symbol) {
  const r = state.latestChartResults[symbol];
  return r && r.range === state.range ? r : null;
}
async function saveWatchlist() {
  const saved = await api("/api/watchlist", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ symbols: state.watchlist }),
  });
  state.watchlist = saved.symbols;
}

// ------------------------------------------------------------------ metrics shared by cards / actions / options
function metrics(entry, data) {
  const closes = data.points.map((p) => p.close);
  const price = data.price;
  const sma = (n) => (closes.length >= n ? avg(closes.slice(-n)) : null);
  const hi = Math.max(...closes), lo = Math.min(...closes);
  const rets = [];
  const tail = closes.slice(-61);
  for (let i = 1; i < tail.length; i++) rets.push(Math.log(tail[i] / tail[i - 1]));
  const mean = rets.length ? avg(rets) : 0;
  const vol = rets.length > 5 ? Math.sqrt(rets.reduce((a, r) => a + (r - mean) ** 2, 0) / (rets.length - 1)) * Math.sqrt(252) : null;
  const hasPos = entry.position > 0;
  const gainPct = hasPos && entry.averagePrice ? (price / entry.averagePrice - 1) * 100 : null;
  return {
    price, hi, lo, vol, hasPos, gainPct,
    sma5: sma(5), sma20: sma(20),
    fromHigh: (price / hi - 1) * 100,
    fromLow: (price / lo - 1) * 100,
    rangePct: closes.length ? (price / closes[0] - 1) * 100 : 0,
    dayPct: data.dayChangePct,
    ccy: data.currency,
  };
}

// ------------------------------------------------------------------ cards
const cardEls = new Map();

function renderCards() {
  const grid = $("cards");
  const entries = visibleEntries();
  grid.innerHTML = "";
  cardEls.clear();
  $("cards-empty").hidden = entries.length > 0;
  let lastMarket = null;
  for (const entry of entries) {
    if (state.market === "ALL" && entry.market !== lastMarket) {
      const t = document.createElement("div");
      t.className = "group-title";
      t.textContent = entry.market === "US" ? "United States" : "Singapore";
      grid.appendChild(t);
      lastMarket = entry.market;
    }
    const el = buildCard(entry);
    cardEls.set(keyOf(entry), el);
    grid.appendChild(el);
  }
}

function updateCard(entry) {
  const old = cardEls.get(keyOf(entry));
  if (!old) return;
  const el = buildCard(entry);
  old.replaceWith(el);
  cardEls.set(keyOf(entry), el);
}

function buildCard(entry) {
  const node = $("stock-card-template").content.firstElementChild.cloneNode(true);
  const q = (s) => node.querySelector(s);
  const result = chartFor(entry.symbol);
  const data = result?.data;
  q(".sym").textContent = entry.symbol;
  q(".mkt-chip").textContent = entry.market;
  q(".card-label").textContent = entry.label !== entry.symbol ? entry.label : data?.name || "";
  q(".card-label").title = q(".card-label").textContent;

  q(".pos-btn").onclick = () => openPositionModal(entry);
  const ruleCount = typeof rulesForSymbol === "function" ? rulesForSymbol(entry) : 0;
  q(".alert-btn").textContent = ruleCount ? `Alerts ${ruleCount}` : "Alert";
  q(".alert-btn").classList.toggle("has-rules", ruleCount > 0);
  q(".alert-btn").onclick = () => openRuleModal({ symbol: entry.symbol, market: entry.market });
  q(".remove-btn").onclick = () => removeEntry(entry);
  q(".opt-btn").onclick = () => {
    state.selectedOptionsSymbol = entry.symbol;
    renderOptions();
    const panel = document.querySelector('[data-collapsible="options"]');
    setCollapsed(panel, false);
    panel.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  if (!result) {
    node.classList.add("is-loading");
    q(".card-price").innerHTML = '<span class="skeleton" style="display:inline-block;width:90px;height:22px"></span>';
    q(".spark").outerHTML = '<div class="skeleton" style="height:84px"></div>';
    q(".source").textContent = "Loading…";
    return node;
  }
  if (result.error) {
    q(".card-price").textContent = "—";
    q(".spark").outerHTML = `<div class="card-error">Data unavailable. ${esc(result.error).slice(0, 140)}</div>`;
    q(".source").textContent = "No data";
    return node;
  }

  const ccy = data.currency;
  q(".card-price").textContent = money(data.price, ccy);
  const ch = q(".card-change");
  ch.className = `card-change num ${cls(data.dayChangePct)}`;
  ch.textContent = state.changeMode === "value"
    ? `${signedMoney(toDisplay(data.dayChange, ccy), state.currency, Math.abs(data.dayChange) < 0.1 ? 3 : 2)} today`
    : `${signedPct(data.dayChangePct)} today`;
  if (data.extendedPrice) {
    const ext = q(".card-ext");
    ext.hidden = false;
    ext.innerHTML = `${esc(data.extendedLabel)} <b>${money(data.extendedPrice, ccy)}</b> <span class="${cls(data.extendedChangePct)}">${signedPct(data.extendedChangePct)}</span>`;
  }

  q(".spark").innerHTML = sparkSvg(data.points, entry.averagePrice, entry.symbol);
  const m = metrics(entry, data);
  q(".card-range").innerHTML =
    `<span>L ${fmt(m.lo, priceDp(m.lo, ccy))}</span><span class="${cls(m.rangePct)}">${RANGE_LABEL[state.range]} ${signedPct(m.rangePct, 1)}</span><span>H ${fmt(m.hi, priceDp(m.hi, ccy))}</span>`;

  if (entry.position > 0) {
    const value = toDisplay(data.price * entry.position, ccy);
    const dayPl = toDisplay(data.dayChange * entry.position, ccy);
    let rows = `<span class="pk">${fmt(entry.position, entry.position % 1 ? 3 : 0)} sh · value</span><span class="pv">${money(value, state.currency, 2)}</span>`;
    rows += `<span class="pk">Day P&amp;L</span><span class="pv ${cls(dayPl)}">${signedMoney(dayPl, state.currency, 2)}</span>`;
    if (entry.averagePrice) {
      const unreal = toDisplay((data.price - entry.averagePrice) * entry.position, ccy);
      const pct = (data.price / entry.averagePrice - 1) * 100;
      rows += `<span class="pk">Avg ${money(entry.averagePrice, ccy)}</span><span class="pv ${cls(unreal)}">${signedMoney(unreal, state.currency, 2)} (${signedPct(pct, 1)})</span>`;
    }
    q(".card-position").innerHTML = rows;
  }
  const src = q(".source");
  src.textContent = data.dataSource + (data.marketState && data.marketState !== "CLOSED" && data.isLive ? ` · ${data.marketState.toLowerCase()}` : "");
  if (data.isLive) src.classList.add("live");
  return node;
}

function sparkSvg(points, avgPrice, symbol) {
  if (!points || points.length < 2) return "";
  const W = 300, H = 84, pad = 4;
  const closes = points.map((p) => p.close);
  let lo = Math.min(...closes), hi = Math.max(...closes);
  const showAvg = avgPrice && avgPrice >= lo * 0.9 && avgPrice <= hi * 1.1;
  if (showAvg) { lo = Math.min(lo, avgPrice); hi = Math.max(hi, avgPrice); }
  const span = hi - lo || 1;
  const x = (i) => (i / (closes.length - 1)) * W;
  const y = (v) => pad + (1 - (v - lo) / span) * (H - pad * 2);
  const line = closes.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const color = closes[closes.length - 1] >= closes[0] ? "var(--up)" : "var(--down)";
  const id = "g" + symbol.replace(/[^A-Za-z0-9]/g, "_");
  return `<defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" style="stop-color:${color};stop-opacity:.22"/><stop offset="1" style="stop-color:${color};stop-opacity:0"/></linearGradient></defs>
    <path d="${line}L${W},${H}L0,${H}Z" fill="url(#${id})"/>
    ${showAvg ? `<line class="avg" x1="0" x2="${W}" y1="${y(avgPrice)}" y2="${y(avgPrice)}"/>` : ""}
    <path class="line" d="${line}" style="stroke:${color}"/>
    <circle cx="${W}" cy="${y(closes[closes.length - 1])}" r="3" style="fill:${color}"/>`;
}

// ------------------------------------------------------------------ add / remove / search
async function addEntry(evt) {
  evt.preventDefault();
  let symbol = $("symbol-input").value.trim().toUpperCase();
  const label = $("label-input").value.trim();
  const market = $("add-market").value;
  if (!symbol) return;
  if (market === "SG" && !symbol.includes(".") && !symbol.startsWith("^") && /^[A-Z0-9]{2,5}$/.test(symbol)) symbol += ".SI";
  clearTimeout(state.watchlistSearchTimer);
  state.lastWatchlistSearchQuery = "";
  hideSuggestions();
  if (state.watchlist.some((e) => e.symbol === symbol && e.market === market)) {
    toast(`${symbol} is already on the ${market} list`);
    $("symbol-input").value = ""; $("label-input").value = ""; state.autoLabel = null;
    return;
  }
  const entry = { symbol, label: label || symbol, market };
  state.watchlist.push(entry);
  hideSuggestions();
  $("symbol-input").value = ""; $("label-input").value = ""; state.autoLabel = null;
  try { await saveWatchlist(); } catch (e) { toast(`Could not save: ${e.message}`); }
  if (state.market !== "ALL" && state.market !== market) setMarket(market, false);
  renderCards();
  renderOptionsSymbols();
  toast(`Added ${symbol}`);
  await loadCharts({ onlyMissing: true });
  loadNews();
}

async function removeEntry(entry) {
  const idx = state.watchlist.findIndex((e) => keyOf(e) === keyOf(entry));
  if (idx < 0) return;
  const [removed] = state.watchlist.splice(idx, 1);
  renderCards(); renderAfterCharts(); renderNews(state.latestNewsGroups.filter((g) => !(g.symbol === entry.symbol && g.market === entry.market)));
  try { await saveWatchlist(); } catch (e) { toast(`Could not save: ${e.message}`); return; }
  toast(`Removed ${removed.symbol}`, "Undo", async () => {
    state.watchlist.splice(idx, 0, removed);
    await saveWatchlist();
    renderCards(); renderAfterCharts(); loadNews();
  });
}

function queueSearch(evt) {
  const q = evt.target.value.trim();
  // typing a new ticker drops a label that was auto-filled from an earlier suggestion
  if (evt.target.id === "symbol-input" && state.autoLabel && $("label-input").value === state.autoLabel) { $("label-input").value = ""; state.autoLabel = null; }
  clearTimeout(state.watchlistSearchTimer);
  if (q.length < 2) { hideSuggestions(); return; }
  state.watchlistSearchTimer = setTimeout(async () => {
    const market = $("add-market").value;
    const tag = `${q}|${market}`;
    state.lastWatchlistSearchQuery = tag;
    try {
      const res = await api(`/api/search?q=${encodeURIComponent(q)}&market=${market}`);
      if (state.lastWatchlistSearchQuery === tag) showSuggestions(res.items || []);
    } catch { hideSuggestions(); }
  }, 250);
}

function showSuggestions(items) {
  const list = $("suggestions");
  if (!items.length) { list.innerHTML = '<li class="s-none"><span></span><span class="s-ex">No matches</span></li>'; list.hidden = false; return; }
  list.innerHTML = items.map((it, i) =>
    `<li data-i="${i}"><span class="s-sym">${esc(it.symbol)}</span><span class="s-name">${esc(it.label)}</span><span class="s-ex">${esc(it.exchange)}</span></li>`).join("");
  list.hidden = false;
  list.querySelectorAll("li[data-i]").forEach((li) => {
    li.onmousedown = (e) => { e.preventDefault(); pickSuggestion(items[Number(li.dataset.i)]); };
  });
  list._items = items;
}
function hideSuggestions() { $("suggestions").hidden = true; }
function pickSuggestion(item) {
  $("symbol-input").value = item.symbol;
  $("label-input").value = item.label;
  state.autoLabel = item.label;
  $("add-market").value = item.market;
  hideSuggestions();
  $("symbol-input").focus();
}
function suggestionKeys(evt) {
  const list = $("suggestions");
  if (list.hidden) return;
  const lis = [...list.querySelectorAll("li[data-i]")];
  let i = lis.findIndex((li) => li.classList.contains("active"));
  if (evt.key === "ArrowDown" || evt.key === "ArrowUp") {
    evt.preventDefault();
    i = evt.key === "ArrowDown" ? Math.min(lis.length - 1, i + 1) : Math.max(0, i - 1);
    lis.forEach((li, j) => li.classList.toggle("active", j === i));
  } else if (evt.key === "Enter" && i >= 0) {
    evt.preventDefault();
    pickSuggestion(list._items[i]);
  } else if (evt.key === "Escape") hideSuggestions();
}

// ------------------------------------------------------------------ position modal
function openPositionModal(entry) {
  state.activePositionTarget = entry;
  $("position-title").textContent = `Position · ${entry.symbol}`;
  const data = chartFor(entry.symbol)?.data;
  $("position-sub").textContent = data?.price ? `Last ${money(data.price, data.currency)} · ${entry.label}` : entry.label;
  $("position-qty").value = entry.position ?? "";
  $("position-avg").value = entry.averagePrice ?? "";
  openModal("position-modal");
  $("position-qty").focus();
}
async function savePosition(evt, clear = false) {
  evt?.preventDefault();
  const entry = state.activePositionTarget;
  if (!entry) return;
  const target = state.watchlist.find((e) => keyOf(e) === keyOf(entry));
  const qty = parseFloat($("position-qty").value);
  const avgP = parseFloat($("position-avg").value);
  if (clear || !(qty > 0)) { delete target.position; delete target.averagePrice; }
  else {
    target.position = qty;
    if (avgP >= 0 && isFinite(avgP)) target.averagePrice = avgP; else delete target.averagePrice;
  }
  closeModal("position-modal");
  try { await saveWatchlist(); toast(clear ? "Position cleared" : "Position saved"); } catch (e) { toast(`Could not save: ${e.message}`); }
  const fresh = state.watchlist.find((e) => keyOf(e) === keyOf(entry));
  if (fresh) updateCard(fresh);
  renderAfterCharts();
}

// ------------------------------------------------------------------ modals
function openModal(id) {
  $(id).hidden = false;
  const focusable = $(id).querySelector("input:not([type=hidden]):not([hidden]), select, button:not([data-close])");
  if (focusable && id !== "position-modal") setTimeout(() => focusable.focus(), 30);
}
function closeModal(id) { $(id).hidden = true; if (id === "position-modal") state.activePositionTarget = null; }

// ------------------------------------------------------------------ chart loading
async function loadCharts({ onlyMissing = false } = {}) {
  const entries = visibleEntries();
  const bench = state.market === "ALL" ? [] : BENCHMARKS[state.market].filter(isBenchOn).map((b) => b.symbol);
  const symbols = [...new Set([...entries.map((e) => e.symbol), ...bench])].filter((s) => !onlyMissing || !chartFor(s));
  await mapLimit(symbols, 6, async (symbol) => {
    const range = state.range;
    try {
      const data = await api(`/api/chart?symbol=${encodeURIComponent(symbol)}&range=${range}`);
      state.latestChartResults[symbol] = { range, data };
    } catch (e) {
      state.latestChartResults[symbol] = { range, error: e.message };
    }
    if (range !== state.range) return;
    for (const entry of visibleEntries().filter((e) => e.symbol === symbol)) updateCard(entry);
  });
  renderAfterCharts();
}

function renderAfterCharts() {
  renderSummary();
  if (typeof renderRules === "function") renderRules();
  renderActions();
  renderOptionsSymbols();
  renderOptions();
  renderBenchmark();
}

// ------------------------------------------------------------------ summary strip
function renderSummary() {
  const entries = visibleEntries();
  const rows = entries.map((e) => ({ e, d: chartFor(e.symbol)?.data })).filter((r) => r.d);
  let value = 0, day = 0, unreal = 0, cost = 0, positions = 0;
  for (const { e, d } of rows) {
    if (!(e.position > 0)) continue;
    positions++;
    value += toDisplay(d.price * e.position, d.currency);
    day += toDisplay(d.dayChange * e.position, d.currency);
    if (e.averagePrice) {
      unreal += toDisplay((d.price - e.averagePrice) * e.position, d.currency);
      cost += toDisplay(e.averagePrice * e.position, d.currency);
    }
  }
  const ups = rows.filter((r) => r.d.dayChangePct > 0).length;
  const downs = rows.filter((r) => r.d.dayChangePct < 0).length;
  const sorted = [...rows].sort((a, b) => b.d.dayChangePct - a.d.dayChangePct);
  const best = sorted[0], worst = sorted[sorted.length - 1];
  const avgDay = rows.length ? avg(rows.map((r) => r.d.dayChangePct)) : null;
  const region = { US: "US", SG: "Singapore", ALL: "all regions" }[state.market];
  const ccy = state.currency;
  const stat = (k, v, s, c = "") => `<div class="stat"><div class="k">${k}</div><div class="v ${c}">${v}</div><div class="s">${s}</div></div>`;
  let html = "";
  if (positions) {
    html += stat("Holdings value", compactMoney(value, ccy), `${positions} position${positions > 1 ? "s" : ""} · ${region}`);
    html += stat("Day P&amp;L", signedMoney(day, ccy, 2), value ? signedPct((day / (value - day)) * 100) : "—", cls(day));
    html += stat("Unrealized P&amp;L", cost ? signedMoney(unreal, ccy, 2) : "—", cost ? `${signedPct((unreal / cost) * 100)} on cost` : "Add average prices", cls(unreal));
  } else {
    html += stat("Holdings value", "—", `Add a position on any card (${region})`);
  }
  html += stat("Breadth today", `<span class="up">${ups}▲</span> <span class="down">${downs}▼</span>`,
    avgDay == null ? "Loading…" : `Average move ${signedPct(avgDay)}`);
  if (best) {
    html += stat("Leader / laggard", `<span class="${cls(best.d.dayChangePct)}">${esc(best.e.symbol)} ${signedPct(best.d.dayChangePct, 1)}</span>`,
      worst && worst !== best ? `${esc(worst.e.symbol)} ${signedPct(worst.d.dayChangePct, 1)}` : "");
  }
  $("summary").innerHTML = html;
}

// ------------------------------------------------------------------ actions
function actionFor(entry, m, newsCount) {
  const p = (v) => fmt(v, priceDp(v, m.ccy));
  const rl = RANGE_LABEL[state.range];
  let a;
  if (m.sma20 == null) a = { tone: "neutral", tag: "Wait", text: "Not enough history in this range to judge the trend." };
  else {
    const trendUp = m.sma5 > m.sma20 && m.price > m.sma20;
    if (m.fromHigh >= -2 && trendUp)
      a = { tone: "positive", tag: "Breakout watch", text: `Within ${fmt(Math.abs(m.fromHigh), 1)}% of the ${rl} high (${p(m.hi)}) with the 5-day average above the 20-day. A close above ${p(m.hi)} would confirm.` };
    else if (m.hasPos && m.gainPct != null && m.gainPct >= 25 && m.price < m.sma5)
      a = { tone: "warning", tag: "Manage winner", text: `Up ${fmt(m.gainPct, 1)}% on cost but slipped below the 5-day average (${p(m.sma5)}). Consider a trailing stop near the 20-day average (${p(m.sma20)}).` };
    else if (m.dayPct <= -4)
      a = { tone: "warning", tag: "Risk check", text: `Down ${fmt(Math.abs(m.dayPct), 1)}% today. Check the news before acting; the 20-day average is ${p(m.sma20)}.` };
    else if (m.price < m.sma20 && m.fromHigh <= -15)
      a = { tone: "warning", tag: "Risk check", text: `${fmt(Math.abs(m.fromHigh), 1)}% below the ${rl} high and under the 20-day average (${p(m.sma20)}). Trend is weak — size carefully.` };
    else if (m.price < m.sma20 && m.price >= m.sma20 * 0.97 && m.rangePct > 0)
      a = { tone: "positive", tag: "Pullback watch", text: `Pulling back to the 20-day average (${p(m.sma20)}) inside a ${signedPct(m.rangePct, 1)} ${rl} uptrend. Watch whether it holds.` };
    else if (m.hasPos && m.gainPct != null && m.gainPct > 0)
      a = { tone: "neutral", tag: "Hold / wait", text: `Position is ${signedPct(m.gainPct, 1)} on cost with no fresh signal. Review if it loses the 20-day average (${p(m.sma20)}).` };
    else if (trendUp)
      a = { tone: "positive", tag: "Trend intact", text: `Above both the 5-day (${p(m.sma5)}) and 20-day (${p(m.sma20)}) averages. No action needed; let the trend work.` };
    else
      a = { tone: "neutral", tag: "Wait", text: `No clear setup. A move above ${p(Math.max(m.sma5, m.sma20))} or below ${p(Math.min(m.sma5, m.sma20, m.price * 0.97))} would say more.` };
  }
  if (newsCount >= 3) a.text += ` Heavy news flow (${newsCount} headlines in 3 days) — expect volatility.`;
  return a;
}

function renderActions() {
  const box = $("actions");
  const entries = visibleEntries();
  if (!entries.length) { box.innerHTML = '<p class="empty">Add tickers to get prompts.</p>'; return; }
  const newsCount = (e) => state.latestNewsGroups.find((g) => g.symbol === e.symbol && g.market === e.market)?.items.length ?? 0;
  const order = { warning: 0, positive: 1, neutral: 2 };
  const items = entries.map((e) => {
    const d = chartFor(e.symbol)?.data;
    if (!d || d.points.length < 2) return null;
    const m = metrics(e, d);
    return { e, m, a: actionFor(e, m, newsCount(e)) };
  }).filter(Boolean).sort((x, y) => order[x.a.tone] - order[y.a.tone]);
  if (!items.length) { box.innerHTML = '<p class="state">Waiting for chart data…</p>'; return; }
  box.innerHTML = items.map(({ e, m, a }) => {
    const p = (v) => (v == null ? "—" : fmt(v, priceDp(v, m.ccy)));
    return `<div class="action ${a.tone}"><div class="bar"></div><div>
      <div class="action-head"><b>${esc(e.symbol)}</b><span class="action-tag">${esc(a.tag)}</span></div>
      <p>${esc(a.text)}</p>
      <div class="metrics"><span>px ${p(m.price)}</span><span>sma5 ${p(m.sma5)}</span><span>sma20 ${p(m.sma20)}</span>
      <span>vs high ${signedPct(m.fromHigh, 1)}</span><span>day ${signedPct(m.dayPct, 1)}</span>${m.gainPct != null ? `<span>on cost ${signedPct(m.gainPct, 1)}</span>` : ""}</div>
    </div></div>`;
  }).join("");
}

// ------------------------------------------------------------------ benchmarks
function isBenchOn(b) { return state.benchEnabled[b.symbol] ?? b.on; }

function renderBenchmarkControls() {
  const box = $("benchmark-controls");
  if (state.market === "ALL") { box.innerHTML = ""; return; }
  box.innerHTML = BENCHMARKS[state.market].map((b) =>
    `<button type="button" class="chip ${isBenchOn(b) ? "on" : ""}" data-sym="${esc(b.symbol)}"><i class="sw" style="background:${b.color}"></i>${esc(b.label)}</button>`).join("");
  box.querySelectorAll(".chip").forEach((chip) => {
    chip.onclick = async () => {
      const b = BENCHMARKS[state.market].find((x) => x.symbol === chip.dataset.sym);
      state.benchEnabled[b.symbol] = !isBenchOn(b);
      store.set("benchmarks", JSON.stringify(state.benchEnabled));
      renderBenchmarkControls();
      renderBenchmark();
      if (isBenchOn(b) && !chartFor(b.symbol)) await loadCharts({ onlyMissing: true });
    };
  });
}

function normalizedSeries(dates, points) {
  const map = new Map(points.map((p) => [p.date, p.close]));
  let base = null, last = null;
  return dates.map((d) => {
    if (map.has(d)) { last = map.get(d); if (base == null) base = last; }
    return base == null || last == null ? null : (last / base - 1) * 100;
  });
}

function renderBenchmark() {
  const box = $("benchmark");
  if (state.market === "ALL") { box.innerHTML = '<p class="state">Pick <b>US</b> or <b>SG</b> to compare your watchlist against that market’s benchmarks.</p>'; return; }
  const entries = visibleEntries().filter((e) => chartFor(e.symbol)?.data?.points?.length > 1);
  const benches = BENCHMARKS[state.market].filter(isBenchOn).filter((b) => chartFor(b.symbol)?.data?.points?.length > 1);
  if (!entries.length && !benches.length) { box.innerHTML = '<p class="state">Waiting for chart data…</p>'; return; }

  const dateSet = new Set();
  [...entries.map((e) => e.symbol), ...benches.map((b) => b.symbol)].forEach((s) => chartFor(s).data.points.forEach((p) => dateSet.add(p.date)));
  const dates = [...dateSet].sort();
  const series = [];
  if (entries.length) {
    const each = entries.map((e) => normalizedSeries(dates, chartFor(e.symbol).data.points));
    const values = dates.map((_, i) => { const v = each.map((s) => s[i]).filter((x) => x != null); return v.length ? avg(v) : null; });
    series.push({ label: `Your ${state.market} watchlist (${entries.length})`, color: "var(--accent)", main: true, values });
  }
  for (const b of benches) series.push({ label: b.label, color: b.color, values: normalizedSeries(dates, chartFor(b.symbol).data.points) });

  const all = series.flatMap((s) => s.values).filter((v) => v != null);
  let lo = Math.min(0, ...all), hi = Math.max(0, ...all);
  const padY = (hi - lo) * 0.08 || 1; lo -= padY; hi += padY;
  const W = Math.max(320, box.clientWidth || 800), H = window.innerWidth < 760 ? 240 : 320;
  const L = 50, R = 12, T = 12, B = 26, pw = W - L - R, ph = H - T - B;
  const x = (i) => L + (dates.length > 1 ? (i / (dates.length - 1)) * pw : 0);
  const y = (v) => T + (1 - (v - lo) / (hi - lo)) * ph;

  const step = niceStep((hi - lo) / 5);
  let grid = "";
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
    grid += `<line class="grid" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text x="${L - 8}" y="${y(v) + 4}" text-anchor="end">${v > 0 ? "+" : ""}${fmt(v, step < 1 ? 1 : 0)}%</text>`;
  }
  grid += `<line class="zero" x1="${L}" x2="${W - R}" y1="${y(0)}" y2="${y(0)}"/>`;
  const ticks = Math.min(6, dates.length);
  for (let t = 0; t < ticks; t++) {
    const i = Math.round((t / Math.max(1, ticks - 1)) * (dates.length - 1));
    grid += `<text x="${x(i)}" y="${H - 6}" text-anchor="${t === 0 ? "start" : t === ticks - 1 ? "end" : "middle"}">${shortDate(dates[i])}</text>`;
  }
  const paths = [...series].reverse().map((s) => {
    let d = "", pen = false;
    s.values.forEach((v, i) => { if (v == null) { pen = false; return; } d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`; pen = true; });
    return `<path class="series ${s.main ? "main" : ""}" d="${d}" style="stroke:${s.color}"/>`;
  }).join("");

  box.innerHTML = `<svg class="bench-svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">${grid}${paths}
      <line class="cursor" id="bench-cursor" x1="0" x2="0" y1="${T}" y2="${T + ph}" visibility="hidden"/>
      <rect x="${L}" y="${T}" width="${pw}" height="${ph}" fill="transparent" id="bench-hit"/></svg>
    <div class="bench-legend">${series.map((s) => {
      const last = [...s.values].reverse().find((v) => v != null);
      return `<span><i style="background:${s.color}"></i>${esc(s.label)} <b class="num ${cls(last)}">${signedPct(last)}</b></span>`;
    }).join("")}</div>`;

  const hit = $("bench-hit"), cursor = $("bench-cursor"), tip = $("tooltip");
  hit.onmousemove = (evt) => {
    const rect = hit.getBoundingClientRect();
    const i = Math.round(((evt.clientX - rect.left) / rect.width) * (dates.length - 1));
    if (i < 0 || i >= dates.length) return;
    cursor.setAttribute("x1", x(i)); cursor.setAttribute("x2", x(i)); cursor.setAttribute("visibility", "visible");
    tip.innerHTML = `<div class="tt-date">${esc(dates[i])}</div>` + series.map((s) =>
      `<div class="tt-row"><span style="color:${s.main ? "inherit" : s.color}">${esc(s.main ? "Watchlist" : s.label)}</span><span>${signedPct(s.values[i])}</span></div>`).join("");
    tip.hidden = false;
    const tx = Math.min(evt.clientX + 14, window.innerWidth - tip.offsetWidth - 8);
    tip.style.left = `${tx}px`; tip.style.top = `${evt.clientY + 14}px`;
  };
  hit.onmouseleave = () => { tip.hidden = true; cursor.setAttribute("visibility", "hidden"); };
}
function niceStep(raw) {
  const p = 10 ** Math.floor(Math.log10(raw || 1));
  const n = raw / p;
  return (n < 1.5 ? 1 : n < 3 ? 2 : n < 7 ? 5 : 10) * p;
}
function shortDate(iso) {
  const d = new Date(iso + "T00:00:00");
  const long = ["1y", "2y", "5y", "max"].includes(state.range);
  return d.toLocaleDateString("en-SG", long ? { month: "short", year: "2-digit" } : { day: "numeric", month: "short" });
}

// ------------------------------------------------------------------ options ideas
function strikeStep(p) { return p < 5 ? 0.5 : p < 25 ? 0.5 : p < 100 ? 1 : p < 250 ? 2.5 : p < 1000 ? 5 : 10; }
function roundStrike(v, p) { const s = strikeStep(p); return Math.max(s, Math.round(v / s) * s); }

const OPTION_IDEAS = [
  { name: "Long call", tags: ["bullish"], legs: (k) => `Buy 1 × ${k(0.25)} call · 30–60 DTE`,
    when: "You expect a move up and want capped risk with leverage.", risk: "Premium paid. Time decay works against you." },
  { name: "Bull call spread", tags: ["bullish"], legs: (k) => `Buy ${k(0)} call / sell ${k(1)} call · 30–45 DTE`,
    when: "Moderately bullish; cheaper than a lone call.", risk: "Net debit. Gains capped at the short strike." },
  { name: "Cash-secured put", tags: ["bullish", "income"], legs: (k) => `Sell 1 × ${k(-1)} put · 30–45 DTE, hold cash`,
    when: "Happy to buy lower; get paid to wait.", risk: "Assigned at the strike if price falls through it." },
  { name: "Covered call", tags: ["income", "neutral"], legs: (k) => `Own 100 sh · sell 1 × ${k(1)} call · 30 DTE`,
    when: "Flat to mildly bullish; harvest premium on shares you hold.", risk: "Upside capped at the strike; shares still carry downside." },
  { name: "Long put", tags: ["bearish", "hedge"], legs: (k) => `Buy 1 × ${k(-0.25)} put · 30–60 DTE`,
    when: "You expect a drop, or want simple portfolio insurance.", risk: "Premium paid. Needs a timely move down." },
  { name: "Bear put spread", tags: ["bearish"], legs: (k) => `Buy ${k(0)} put / sell ${k(-1)} put · 30–45 DTE`,
    when: "Moderately bearish at lower cost than a lone put.", risk: "Net debit. Gains capped at the short strike." },
  { name: "Protective put", tags: ["hedge"], legs: (k) => `Own shares · buy ${k(-1)} put · 60–90 DTE`,
    when: "Lock in a floor under a winning position through an event.", risk: "Premium drag if nothing happens." },
  { name: "Collar", tags: ["hedge", "neutral"], legs: (k) => `Own shares · buy ${k(-1)} put / sell ${k(1)} call`,
    when: "Low-cost hedge: the call premium funds the put.", risk: "Caps upside at the call strike." },
  { name: "Iron condor", tags: ["neutral", "income"], legs: (k) => `Sell ${k(-1)}P / ${k(1)}C · buy ${k(-1.6)}P / ${k(1.6)}C`,
    when: "Expect a range-bound month.", risk: "Losses to wing width if price breaks out." },
  { name: "Long straddle", tags: ["neutral"], legs: (k) => `Buy ${k(0)} call + ${k(0)} put · around a catalyst`,
    when: "Big move expected, direction unknown (earnings, rulings).", risk: "Both premiums lost if the stock sits still." },
];

function renderOptionsSymbols() {
  const sel = $("options-symbol");
  const entries = visibleEntries();
  if (!entries.some((e) => e.symbol === state.selectedOptionsSymbol)) state.selectedOptionsSymbol = entries[0]?.symbol ?? null;
  const html = entries.map((e) => `<option value="${esc(e.symbol)}">${esc(e.symbol)} · ${esc(e.label)}</option>`).join("");
  if (sel.innerHTML !== html) sel.innerHTML = html;
  if (state.selectedOptionsSymbol) sel.value = state.selectedOptionsSymbol;
}

function renderOptions() {
  document.querySelectorAll("#options-focus button").forEach((b) => b.classList.toggle("active", b.dataset.focus === state.optionsFocus));
  const ctx = $("options-context"), grid = $("options");
  const entry = visibleEntries().find((e) => e.symbol === state.selectedOptionsSymbol);
  const data = entry && chartFor(entry.symbol)?.data;
  if (!entry || !data || data.points.length < 20) {
    ctx.innerHTML = entry ? "Waiting for enough chart data…" : "Add a ticker to see ideas.";
    grid.innerHTML = "";
    return;
  }
  const m = metrics(entry, data);
  const trend = m.sma5 > m.sma20 && m.price > m.sma20 ? "bullish" : m.sma5 < m.sma20 && m.price < m.sma20 ? "bearish" : "neutral";
  const vol = m.vol ?? 0.3;
  const sigma = m.price * vol * Math.sqrt(30 / 365);
  const k = (n) => fmt(roundStrike(m.price + n * sigma, m.price), strikeStep(m.price) < 1 ? 1 : 0).replace(/\.0$/, "");
  const shares = entry.position || 0;
  ctx.innerHTML = `<span>Price <b>${money(m.price, m.ccy)}</b></span><span>Trend <b>${trend}</b></span>
    <span>Realised vol <b>${m.vol ? fmt(vol * 100, 0) + "%" : "n/a"}</b></span>
    <span>30-day 1σ range <b>${fmt(m.price - sigma, 2)} – ${fmt(m.price + sigma, 2)}</b></span>
    ${shares ? `<span>You hold <b>${fmt(shares, 0)}</b> sh</span>` : ""}
    ${entry.market === "SG" ? `<span class="hint">Most SGX stocks have no listed options — treat these as learning only.</span>` : ""}`;

  const fits = (idea) => idea.tags.includes(trend) || (idea.tags.includes("hedge") && shares > 0 && trend !== "bullish")
    || (idea.name === "Covered call" && shares >= 100);
  const ideas = OPTION_IDEAS.filter((i) => state.optionsFocus === "all" || i.tags.includes(state.optionsFocus))
    .map((i) => ({ ...i, fit: fits(i) })).sort((a, b) => b.fit - a.fit);
  grid.innerHTML = ideas.map((i) => `<div class="idea ${i.fit ? "fits" : ""}">
      <h4>${esc(i.name)} ${i.fit ? '<span class="fit">Fits current trend</span>' : ""}</h4>
      <div class="tags">${i.tags.map((t) => `<span class="tag">${t}</span>`).join("")}</div>
      <div class="legs">${esc(i.legs(k))}</div>
      <p><b>When:</b> ${esc(i.when)}</p><p><b>Risk:</b> ${esc(i.risk)}</p></div>`).join("") || '<p class="empty">No ideas for this focus.</p>';
}

// ------------------------------------------------------------------ news
async function loadNews() {
  const box = $("news");
  if (!state.latestNewsGroups.length) box.innerHTML = '<div class="skeleton" style="height:120px"></div>';
  try {
    const res = await api(`/api/news?market=${state.market}`);
    state.latestNewsGroups = res.groups || [];
    renderNews(state.latestNewsGroups);
    renderActions();
  } catch (e) {
    box.innerHTML = `<p class="state error">News unavailable: ${esc(e.message)}</p>`;
  }
}

function renderNews(groups) {
  const box = $("news");
  if (!groups.length) { box.innerHTML = '<p class="empty">No tickers in this region.</p>'; return; }
  box.innerHTML = groups.map((g) => {
    const k = `${g.symbol}|${g.market}`;
    const preview = g.error ? "Could not load headlines" : g.items[0]?.title || "No headlines in the last 3 days";
    return `<div class="news-group ${state.newsOpen.has(k) ? "open" : ""}" data-k="${esc(k)}">
      <button type="button" class="news-head"><span class="chev"></span><span class="sym">${esc(g.symbol)}</span>
        <span class="preview">${esc(preview)}</span><span class="count">${g.items.length}</span></button>
      <ul class="news-items">${g.items.map((it) => `<li><a href="${esc(it.link)}" target="_blank" rel="noopener noreferrer">${esc(it.title)}</a>
        <div class="news-meta">${esc(it.source)} · ${relTime(it.publishedAt)}</div></li>`).join("") || '<li class="news-meta">Nothing recent.</li>'}</ul></div>`;
  }).join("");
  box.querySelectorAll(".news-group").forEach((el) => {
    el.querySelector(".news-head").onclick = () => {
      el.classList.toggle("open");
      el.classList.contains("open") ? state.newsOpen.add(el.dataset.k) : state.newsOpen.delete(el.dataset.k);
    };
  });
}

// ------------------------------------------------------------------ market map
function tileStyle(pct) {
  const p = pct ?? 0;
  const intensity = Math.min(Math.abs(p) / 3, 1);
  const mix = Math.round(10 + intensity * 75);
  const color = p >= 0 ? "var(--up)" : "var(--down)";
  return { bg: `color-mix(in srgb, ${color} ${mix}%, var(--tile-base))`, light: mix > 50 };
}

async function loadMarketOverview() {
  const box = $("market-overview");
  if (!state.marketSegments) box.innerHTML = '<div class="skeleton" style="height:160px"></div>';
  try {
    const res = await api("/api/market-overview");
    state.marketSegments = res.segments || [];
    renderMarketOverview();
    if (!state.marketSegments.length) box.innerHTML = `<p class="state error">Market data unavailable. ${esc((res.errors || [])[0] || "")}</p>`;
    if (state.selectedMarketSector) loadDrilldown(state.selectedMarketSector);
  } catch (e) {
    box.innerHTML = `<p class="state error">Market data unavailable: ${esc(e.message)}</p>`;
  }
}

function tileHtml(s, extraClass = "") {
  const t = tileStyle(s.dayChangePct);
  return `<button type="button" class="market-tile ${extraClass} ${t.light ? "light-text" : ""}" style="background:${t.bg}" data-sym="${esc(s.symbol)}" title="${esc(s.label || s.symbol)} · ${money(s.price, s.currency)}">
    <div><div class="t-sym">${esc(s.symbol.replace(/\.SI$/, ""))}</div>${s.label && s.label !== s.symbol ? `<div class="t-label">${esc(s.label)}</div>` : ""}</div>
    <div class="t-pct">${signedPct(s.dayChangePct)}</div></button>`;
}

function renderMarketOverview() {
  const groups = ["Broad Market", "Sectors", "Singapore"];
  const box = $("market-overview");
  box.innerHTML = groups.map((g) => {
    const segs = state.marketSegments.filter((s) => s.group === g).sort((a, b) => b.dayChangePct - a.dayChangePct);
    if (!segs.length) return "";
    return `<div class="market-group"><h5>${g}</h5><div class="market-grid">${segs.map((s) =>
      tileHtml(s, `${s.hasDrilldown ? "" : "no-drill"} ${s.symbol === state.selectedMarketSector ? "selected" : ""}`)).join("")}</div></div>`;
  }).join("");
  box.querySelectorAll(".market-tile").forEach((tile) => {
    tile.onclick = () => {
      const seg = state.marketSegments.find((s) => s.symbol === tile.dataset.sym);
      if (!seg?.hasDrilldown) return;
      if (state.selectedMarketSector === seg.symbol) { state.selectedMarketSector = null; $("market-drilldown").hidden = true; renderMarketOverview(); return; }
      state.selectedMarketSector = seg.symbol;
      renderMarketOverview();
      loadDrilldown(seg.symbol);
    };
  });
}

async function loadDrilldown(symbol) {
  const box = $("market-drilldown");
  box.hidden = false;
  box.innerHTML = '<div class="skeleton" style="height:140px"></div>';
  try {
    const res = await api(`/api/market-sector?symbol=${encodeURIComponent(symbol)}&count=${state.marketCount}`);
    if (state.selectedMarketSector !== symbol) return;
    const stocks = res.stocks || [];
    const big = new Set([...stocks].sort((a, b) => Math.abs(b.dayChangePct) - Math.abs(a.dayChangePct)).slice(0, 3).map((s) => s.symbol));
    const upN = stocks.filter((s) => s.dayChangePct > 0).length;
    box.innerHTML = `<div class="drill-head"><h4>${esc(res.sectorLabel)} <span class="hint">· ${esc(res.sectorSymbol)}</span></h4>
        <span class="hint">${stocks.length} of ${res.available} members · <span class="up">${upN} up</span> / <span class="down">${stocks.length - upN} down</span> · strongest first</span></div>
      <div class="sector-stock-grid">${stocks.map((s) => tileHtml(s, big.has(s.symbol) ? "big" : "")).join("")}</div>
      ${res.errors?.length ? `<p class="fineprint">${res.errors.length} symbols unavailable.</p>` : ""}`;
  } catch (e) {
    box.innerHTML = `<p class="state error">Drilldown unavailable: ${esc(e.message)}</p>`;
  }
}

// ------------------------------------------------------------------ exchange clocks
const EXCHANGES = [
  { name: "NYSE", tz: "America/New_York", sessions: [["pre", 240, 570], ["open", 570, 960], ["post", 960, 1200]] },
  { name: "SGX", tz: "Asia/Singapore", sessions: [["pre", 510, 540], ["open", 540, 720], ["lunch", 720, 780], ["open", 780, 1020]] },
];
function zonedMinuteOfWeek(tz, date = new Date()) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", { timeZone: tz, weekday: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
    .formatToParts(date).map((p) => [p.type, p.value]));
  const dow = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].indexOf(parts.weekday);
  return dow * 1440 + Number(parts.hour) * 60 + Number(parts.minute);
}
function sessionAt(ex, mow) {
  const dow = Math.floor(mow / 1440) % 7, mod = mow % 1440;
  if (dow >= 5) return "closed";
  return ex.sessions.find(([, s, e]) => mod >= s && mod < e)?.[0] ?? "closed";
}
function renderClocks() {
  const now = new Date();
  $("today-label").textContent = now.toLocaleDateString("en-SG", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  $("market-clocks").innerHTML = EXCHANGES.map((ex) => {
    const mow = zonedMinuteOfWeek(ex.tz, now);
    const cur = sessionAt(ex, mow);
    let n = 1;
    if (cur === "open") { while (n < 10080 && sessionAt(ex, (mow + n) % 10080) === "open") n++; }
    else { while (n < 10080 && sessionAt(ex, (mow + n) % 10080) !== "open") n++; }
    const label = { open: "Open", pre: "Pre-market", post: "After hours", lunch: "Lunch break", closed: "Closed" }[cur];
    const when = cur === "open" ? `closes in ${dur(n)}` : `opens in ${dur(n)}`;
    const klass = cur === "open" ? "open" : cur === "closed" ? "" : "ext";
    return `<div class="clock ${klass}" title="Regular-hours schedule; exchange holidays are not accounted for"><span class="dot"></span><b>${ex.name}</b> ${label} <span class="when">· ${when}</span></div>`;
  }).join("");
}

// ------------------------------------------------------------------ theme
function applyTheme() {
  const mode = $("theme-select").value;
  store.set("theme", mode);
  const body = document.body;
  delete body.dataset.themeVariant;
  if (mode === "random") {
    const v = THEME_VARIANTS[Math.floor(Math.random() * THEME_VARIANTS.length)];
    body.dataset.theme = v.dark ? "dark" : "light";
    body.dataset.themeVariant = v.name;
  } else if (mode === "auto") {
    const h = new Date().getHours();
    body.dataset.theme = h >= 19 || h < 7 ? "dark" : "light";
  } else body.dataset.theme = mode;
}

// ------------------------------------------------------------------ region + range
function setMarket(market, reload = true) {
  state.market = market;
  store.set("active-watchlist-market", market);
  document.querySelectorAll("#market-select button").forEach((b) => b.classList.toggle("active", b.dataset.market === market));
  if (market !== "ALL") $("add-market").value = market;
  if (!reload) return;
  renderCards();
  renderBenchmarkControls();
  renderAfterCharts();
  loadCharts({ onlyMissing: true });
  loadNews();
}
function setRange(range) {
  state.range = range;
  store.set("range", range);
  syncRangeControls();
  renderCards();
  loadCharts();
}
function syncRangeControls() {
  $("range-select").value = state.range;
  document.querySelectorAll("#range-quick button").forEach((b) => b.classList.toggle("active", b.dataset.range === state.range));
}

// ------------------------------------------------------------------ refresh
async function refreshDashboard() {
  if (state.refreshing) return;
  state.refreshing = true;
  $("refresh-button").classList.add("spinning");
  try {
    await Promise.allSettled([loadNews(), loadMarketOverview(), loadFx(), loadCharts()]);
    $("last-updated").textContent = `Updated ${new Date().toLocaleTimeString("en-SG", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`;
  } finally {
    state.refreshing = false;
    $("refresh-button").classList.remove("spinning");
  }
}
function setupAutoRefresh() {
  clearInterval(state.autoRefreshTimer);
  state.autoRefreshTimer = null;
  $("refresh-interval").disabled = state.refreshMode !== "auto";
  if (state.refreshMode === "auto") state.autoRefreshTimer = setInterval(refreshDashboard, state.refreshInterval * 60000);
}

// ------------------------------------------------------------------ fx + version
async function loadFx() {
  try {
    state.fx = await api("/api/fx");
    state.usdToSgdRate = state.fx.rate || 1;
    $("fx-label").textContent = `USD/SGD ${fmt(state.usdToSgdRate, 4)} · ${state.fx.source}`;
  } catch {
    state.usdToSgdRate = 1;
    $("fx-label").textContent = "USD/SGD unavailable — conversions paused";
  }
}
async function loadVersion() {
  try {
    const v = await api("/api/version");
    state.version = v;
    $("version").textContent = `Market Tracker ${v.display}`;
    $("demo-banner").hidden = !v.demo;
    const chip = $("version-chip");
    const isNew = store.get("seen-release", "") !== v.release;
    chip.innerHTML = `${esc(v.display)}${isNew ? ' <b>New</b>' : ""}`;
    chip.hidden = false;
    chip.onclick = $("version").onclick = openReleaseNotes;
  } catch { $("version").textContent = "Market Tracker"; }
}

function mdInline(text) {
  return esc(text).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>");
}
async function openReleaseNotes() {
  openModal("release-modal");
  const body = $("release-body");
  body.innerHTML = '<div class="skeleton" style="height:160px"></div>';
  try {
    const res = await api("/api/release-notes");
    const cur = res.current;
    $("release-current").textContent = `You are running ${cur.display}${cur.releaseDate ? `, released ${cur.releaseDate}` : ""}.`;
    body.innerHTML = res.releases.map((r) => `<section class="release ${r.version === cur.release ? "current" : ""}">
        <h4><span class="mono">v${esc(r.version)}</span>${r.date ? ` <span class="hint">· ${esc(r.date)}</span>` : ""}${r.version === cur.release ? ' <span class="pill">Installed</span>' : ""}</h4>
        ${r.summary ? `<p>${mdInline(r.summary)}</p>` : ""}
        ${r.sections.map((s) => `<h5>${esc(s.title)}</h5><ul>${s.items.map((i) => `<li>${mdInline(i)}</li>`).join("")}</ul>`).join("")}
      </section>`).join("") || '<p class="empty">No release notes found.</p>';
    store.set("seen-release", cur.release);
    $("version-chip").textContent = cur.display;
  } catch (e) { body.innerHTML = `<p class="form-error">${esc(e.message)}</p>`; }
}

// ------------------------------------------------------------------ layout: collapse + order
function setCollapsed(panel, collapsed) {
  panel.classList.toggle("is-collapsed", collapsed);
  panel.querySelector(".panel-toggle").setAttribute("aria-expanded", String(!collapsed));
  store.set(`collapse-${panel.dataset.collapsible}`, collapsed ? "1" : "0");
  if (!collapsed && panel.dataset.collapsible === "benchmark") renderBenchmark();
}
function initCollapsibles() {
  document.querySelectorAll("[data-collapsible]").forEach((panel) => {
    panel.classList.toggle("is-collapsed", store.get(`collapse-${panel.dataset.collapsible}`, "0") === "1");
    panel.querySelector(".panel-toggle").onclick = () => setCollapsed(panel, !panel.classList.contains("is-collapsed"));
  });
}
function applySectionOrder() {
  const ids = SECTIONS.map((s) => s.id);
  const order = [...state.sectionOrder.filter((id) => ids.includes(id)), ...ids.filter((id) => !state.sectionOrder.includes(id))];
  state.sectionOrder = order;
  const main = $("sections");
  order.forEach((id) => main.appendChild(document.querySelector(`[data-collapsible="${id}"]`)));
  renderOrderList();
}
function renderOrderList() {
  const list = $("section-order");
  list.innerHTML = state.sectionOrder.map((id, i) => `<li><span>${esc(SECTIONS.find((s) => s.id === id).name)}</span>
    <button type="button" data-i="${i}" data-d="-1" ${i === 0 ? "disabled" : ""} aria-label="Move up">↑</button>
    <button type="button" data-i="${i}" data-d="1" ${i === state.sectionOrder.length - 1 ? "disabled" : ""} aria-label="Move down">↓</button></li>`).join("");
  list.querySelectorAll("button").forEach((b) => {
    b.onclick = () => {
      const i = Number(b.dataset.i), j = i + Number(b.dataset.d);
      [state.sectionOrder[i], state.sectionOrder[j]] = [state.sectionOrder[j], state.sectionOrder[i]];
      store.set("section-order", JSON.stringify(state.sectionOrder));
      applySectionOrder();
    };
  });
}

// ------------------------------------------------------------------ bindings
function bindControls() {
  $("refresh-button").onclick = refreshDashboard;
  $("controls-button").onclick = () => openModal("controls-modal");
  document.querySelectorAll("[data-close]").forEach((el) => { el.onclick = () => closeModal(el.closest(".modal-shell").id); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      document.querySelectorAll(".modal-shell:not([hidden])").forEach((m) => { if (m.id !== "secret-modal") closeModal(m.id); });
      if (typeof closeBell === "function") closeBell();
    }
    const typing = /INPUT|SELECT|TEXTAREA/.test(document.activeElement?.tagName);
    if (!typing && (e.key === "r" || e.key === "R") && !e.ctrlKey && !e.metaKey) refreshDashboard();
  });

  document.querySelectorAll("#market-select button").forEach((b) => { b.onclick = () => setMarket(b.dataset.market); });
  document.querySelectorAll("#range-quick button").forEach((b) => { b.onclick = () => setRange(b.dataset.range); });
  $("range-select").onchange = (e) => setRange(e.target.value);

  $("add-form").onsubmit = addEntry;
  ["symbol-input", "label-input"].forEach((id) => { $(id).oninput = queueSearch; $(id).onkeydown = suggestionKeys; $(id).onblur = () => setTimeout(hideSuggestions, 150); });

  $("position-form").onsubmit = (e) => savePosition(e);
  $("position-clear").onclick = () => savePosition(null, true);

  $("theme-select").onchange = applyTheme;
  $("currency-select").onchange = (e) => { state.currency = e.target.value; store.set("currency", state.currency); renderCards(); renderAfterCharts(); };
  $("change-select").onchange = (e) => { state.changeMode = e.target.value; store.set("change-mode", state.changeMode); renderCards(); };
  $("refresh-mode").onchange = (e) => { state.refreshMode = e.target.value; store.set("refresh-mode", state.refreshMode); setupAutoRefresh(); };
  $("refresh-interval").onchange = (e) => { state.refreshInterval = Number(e.target.value); store.set("refresh-interval", e.target.value); setupAutoRefresh(); };
  $("market-count").onchange = (e) => { state.marketCount = e.target.value; store.set("market-stock-count", e.target.value); if (state.selectedMarketSector) loadDrilldown(state.selectedMarketSector); };
  $("order-reset").onclick = () => { state.sectionOrder = SECTIONS.map((s) => s.id); store.set("section-order", JSON.stringify(state.sectionOrder)); applySectionOrder(); };

  $("options-symbol").onchange = (e) => { state.selectedOptionsSymbol = e.target.value; renderOptions(); };
  document.querySelectorAll("#options-focus button").forEach((b) => { b.onclick = () => { state.optionsFocus = b.dataset.focus; store.set("options-focus", b.dataset.focus); renderOptions(); }; });
  $("news-expand").onclick = () => {
    const groups = [...document.querySelectorAll(".news-group")];
    const open = !groups.every((g) => g.classList.contains("open"));
    groups.forEach((g) => { g.classList.toggle("open", open); open ? state.newsOpen.add(g.dataset.k) : state.newsOpen.delete(g.dataset.k); });
    $("news-expand").textContent = open ? "Collapse all" : "Expand all";
  };

  let resizeTimer;
  window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(renderBenchmark, 150); });
}

// ------------------------------------------------------------------ init
async function init() {
  closeModal("position-modal");
  try {
    state.account = await api("/api/account");
    state.csrf = state.account.csrf;
  } catch (e) {
    return; // api() already redirected to /login
  }
  if (await syncPrefs(state.account.username)) return;
  sessionStorage.removeItem("prefs-reloaded");
  bindControls();
  initAccount();
  initAlerts();
  loadVersion();
  syncRangeControls();
  setMarket(["US", "SG", "ALL"].includes(state.market) ? state.market : "US", false);
  $("theme-select").value = store.get("theme", "auto");
  applyTheme();
  $("currency-select").value = state.currency;
  $("change-select").value = state.changeMode;
  $("refresh-mode").value = state.refreshMode;
  $("refresh-interval").value = String(state.refreshInterval);
  $("market-count").value = state.marketCount;
  setupAutoRefresh();
  applySectionOrder();
  initCollapsibles();
  renderBenchmarkControls();
  renderClocks();
  setInterval(renderClocks, 30000);
  setInterval(() => { if ($("theme-select").value === "auto") applyTheme(); }, 600000);
  await loadFx();
  try {
    state.watchlist = (await api("/api/watchlist")).symbols || [];
  } catch (e) {
    toast(`Could not load watchlist: ${e.message}`);
  }
  renderCards();
  renderSummary();
  await refreshDashboard();
}

document.addEventListener("DOMContentLoaded", init);
