/* Market Tracker — alert rules, notification channels and the notification bell. */
"use strict";

const RULE_DEFAULTS = { day_up: 5, day_down: 5, off_high: 10, take_profit: 20, stop_loss: 8, portfolio_up: 3, portfolio_down: 3, concentration: 25 };
const CHANNEL_LABELS = { telegram: "Telegram", ntfy: "ntfy", email: "Email", webhook: "Webhook" };
const alertState = { data: null, editing: null, notifications: [], seen: null, unread: 0, pollTimer: null };

// ------------------------------------------------------------------ init + load
function initAlerts() {
  $("alerts-new").onclick = () => openRuleModal({});
  $("alerts-check").onclick = checkNow;
  $("alerts-channels").onclick = openChannels;
  $("rule-form").onsubmit = saveRule;
  $("rule-delete").onclick = deleteRule;
  ["rule-type", "rule-symbol", "rule-region", "rule-value", "rule-period"].forEach((id) => {
    $(id).addEventListener("input", syncRuleForm);
    $(id).addEventListener("change", syncRuleForm);
  });
  $("channels-form").onsubmit = saveChannels;
  $("channels-form").querySelectorAll("[data-test]").forEach((b) => { b.onclick = () => testChannel(b.dataset.test, b); });
  $("browser-allow").onclick = async () => {
    if (!("Notification" in window)) return;
    await Notification.requestPermission();
    renderBrowserPermission();
  };
  $("bell-button").onclick = toggleBell;
  $("notif-read-all").onclick = async () => { applyNotifications(await postJSON("/api/notifications/read", { all: true })); };
  $("notif-clear").onclick = async () => { applyNotifications(await postJSON("/api/notifications/clear")); };
  document.addEventListener("click", (e) => {
    if (!$("bell-popover").hidden && !e.target.closest(".bell-wrap")) closeBell();
  });
  loadAlerts();
  pollNotifications();
  alertState.pollTimer = setInterval(pollNotifications, 60000);
}

async function loadAlerts() {
  try {
    alertState.data = await api("/api/alerts");
  } catch (e) {
    $("rule-list").innerHTML = `<p class="state error">Alert rules unavailable: ${esc(e.message)}</p>`;
    return;
  }
  renderRules();
  visibleEntries().forEach(updateCard);
}

function applyAlerts(payload) {
  if (payload && payload.rules) alertState.data = { ...alertState.data, ...payload };
  renderRules();
  visibleEntries().forEach(updateCard);
}

function rulesForSymbol(entry) {
  const rules = alertState.data?.rules || [];
  return rules.filter((r) => r.symbol === entry.symbol && r.market === entry.market && r.enabled).length;
}

// ------------------------------------------------------------------ rule list
function renderRules() {
  const d = alertState.data;
  if (!d) return;
  const rules = d.rules || [];
  const on = Object.entries(d.channels || {}).filter(([k, v]) => k !== "browser" && v.enabled).map(([k]) => CHANNEL_LABELS[k]);
  const checked = rules.map((r) => r.lastCheckedAt).filter(Boolean).sort().pop();
  const q = d.settings.quietHours;
  $("alert-status").innerHTML = [
    `<span><i class="dot ${d.backgroundActive ? "on" : ""}"></i>Checked in the background every <b>${d.settings.intervalMinutes} min</b> while the server runs</span>`,
    `<span>Delivers to: <b>${["Bell", d.channels.browser?.enabled ? "desktop pop-ups" : null, ...on].filter(Boolean).join(", ")}</b></span>`,
    q.enabled ? `<span>Quiet hours ${esc(q.start)}–${esc(q.end)}${d.quietNow ? " <b>(now)</b>" : ""}</span>` : "",
    checked ? `<span>Last check ${relTime(checked)}</span>` : "",
  ].filter(Boolean).join("");

  const box = $("rule-list");
  if (!rules.length) {
    box.innerHTML = `<p class="empty">No rules yet. A rule watches a price, a trend or your whole portfolio and pings you when it triggers — so you don't have to stare at the screen.</p>`;
  } else {
    const order = (r) => (!r.enabled ? 3 : r.met ? 0 : r.met === null ? 2 : 1);
    box.innerHTML = [...rules].sort((a, b) => order(a) - order(b)).map((r) => {
      const status = !r.enabled ? ["paused", "Paused"] : r.met === true ? ["met", "Condition met"] : r.met === false ? ["watching", "Watching"] : ["nodata", "Waiting"];
      const meta = [r.status, r.lastTriggeredAt ? `last fired ${relTime(r.lastTriggeredAt)}` : "not fired yet",
        r.repeat === "once" ? "fires once" : null, r.note ? `“${r.note}”` : null].filter(Boolean).map(esc).join(" · ");
      return `<div class="rule ${status[0]} ${r.enabled ? "" : "off"}">
        <label class="switch" title="${r.enabled ? "Pause" : "Resume"}"><input type="checkbox" data-toggle="${esc(r.id)}" ${r.enabled ? "checked" : ""}><span></span></label>
        <div class="rule-main"><div class="rule-summary">${esc(r.summary)}</div><div class="hint">${meta}</div></div>
        <span class="rule-state ${status[0]}">${status[1]}</span>
        <button type="button" class="btn small ghost" data-edit="${esc(r.id)}">Edit</button></div>`;
    }).join("");
    box.querySelectorAll("[data-toggle]").forEach((cb) => {
      cb.onchange = async () => {
        try { applyAlerts(await postJSON("/api/alerts/rule/toggle", { id: cb.dataset.toggle, enabled: cb.checked })); }
        catch (e) { toast(e.message); cb.checked = !cb.checked; }
      };
    });
    box.querySelectorAll("[data-edit]").forEach((b) => { b.onclick = () => openRuleModal(rules.find((r) => r.id === b.dataset.edit)); });
  }
  renderPresets();
}

function renderPresets() {
  const positions = state.watchlist.filter((e) => e.position > 0 && e.averagePrice);
  const rules = alertState.data?.rules || [];
  const has = (type, e) => rules.some((r) => r.type === type && (!e || (r.symbol === e.symbol && r.market === e.market)));
  const presets = [];
  const needStop = positions.filter((e) => !has("stop_loss", e));
  const needTake = positions.filter((e) => !has("take_profit", e));
  if (needStop.length) presets.push({ label: `Stop-loss at −8% on ${needStop.length} position${needStop.length > 1 ? "s" : ""}`, rules: needStop.map((e) => ({ type: "stop_loss", symbol: e.symbol, market: e.market, value: 8, cooldownMinutes: 1440 })) });
  if (needTake.length) presets.push({ label: `Take-profit at +20% on ${needTake.length} position${needTake.length > 1 ? "s" : ""}`, rules: needTake.map((e) => ({ type: "take_profit", symbol: e.symbol, market: e.market, value: 20, cooldownMinutes: 1440 })) });
  if (positions.length && !has("portfolio_down")) presets.push({ label: "Portfolio down 3% in a day", rules: [{ type: "portfolio_down", market: "ALL", value: 3, cooldownMinutes: 1440 }] });
  if (positions.length > 1 && !has("concentration")) presets.push({ label: "Any holding over 25% of portfolio", rules: [{ type: "concentration", market: "ALL", value: 25, cooldownMinutes: 10080 }] });
  const box = $("alert-presets");
  if (!presets.length) {
    box.innerHTML = positions.length ? "" : '<p class="hint">Tip: add shares and an average price on a card to unlock stop-loss, take-profit and portfolio rules.</p>';
    return;
  }
  box.innerHTML = `<span class="hint">Quick add:</span>${presets.map((p, i) => `<button type="button" class="chip" data-preset="${i}">+ ${esc(p.label)}</button>`).join("")}`;
  box.querySelectorAll("[data-preset]").forEach((b) => {
    b.onclick = async () => {
      b.disabled = true;
      let last = null;
      try {
        for (const rule of presets[Number(b.dataset.preset)].rules) last = await postJSON("/api/alerts/rule", rule);
        applyAlerts(last);
        toast("Rules added");
      } catch (e) { toast(e.message); b.disabled = false; }
    };
  });
}

async function checkNow() {
  const btn = $("alerts-check");
  btn.disabled = true; btn.textContent = "Checking…";
  try {
    const res = await postJSON("/api/alerts/check");
    applyAlerts(res);
    toast(res.fired ? `${res.fired} alert${res.fired > 1 ? "s" : ""} triggered` : `${res.checked} rule${res.checked === 1 ? "" : "s"} checked — nothing new`);
    pollNotifications();
  } catch (e) { toast(e.message); } finally { btn.disabled = false; btn.textContent = "Check now"; }
}

// ------------------------------------------------------------------ rule editor
function openRuleModal(ruleOrPrefill) {
  const rule = ruleOrPrefill?.id ? ruleOrPrefill : null;
  alertState.editing = rule;
  const sel = $("rule-symbol");
  const entries = [...state.watchlist].sort((a, b) => a.market.localeCompare(b.market) || a.symbol.localeCompare(b.symbol));
  const want = rule || ruleOrPrefill || {};
  if (want.symbol && !entries.some((e) => e.symbol === want.symbol && e.market === want.market))
    entries.push({ symbol: want.symbol, label: want.symbol, market: want.market || "US" });
  sel.innerHTML = entries.map((e) => `<option value="${esc(e.symbol)}|${e.market}">${esc(e.symbol)} · ${esc(e.label)} (${e.market})</option>`).join("");
  $("rule-title").textContent = rule ? "Edit alert rule" : want.symbol ? `New alert · ${want.symbol}` : "New alert rule";
  $("rule-type").value = rule?.type || "price_above";
  if (want.symbol) sel.value = `${want.symbol}|${want.market}`;
  $("rule-region").value = rule?.market && ["US", "SG", "ALL"].includes(rule.market) && rule.symbol === "" ? rule.market : "ALL";
  $("rule-period").value = String(rule?.period || 50);
  $("rule-repeat").value = rule?.repeat || "rearm";
  $("rule-cooldown").value = String(rule?.cooldownMinutes ?? 240);
  if (![...$("rule-cooldown").options].some((o) => o.value === $("rule-cooldown").value)) $("rule-cooldown").value = "240";
  $("rule-note").value = rule?.note || "";
  $("rule-value").value = rule?.value ?? "";
  $("rule-value").dataset.touched = rule ? "1" : "";
  $("rule-delete").hidden = !rule;
  $("rule-error").hidden = true;
  syncRuleForm();
  openModal("rule-modal");
}

function selectedEntry() {
  const [symbol, market] = ($("rule-symbol").value || "").split("|");
  return state.watchlist.find((e) => e.symbol === symbol && e.market === market) || (symbol ? { symbol, market, label: symbol } : null);
}

function syncRuleForm(evt) {
  if (evt?.target?.id === "rule-value") $("rule-value").dataset.touched = "1";
  const type = $("rule-type").value;
  const spec = alertState.data?.types?.[type] || { scope: "symbol", unit: "price" };
  const portfolio = spec.scope === "portfolio";
  $("rule-symbol-wrap").hidden = portfolio;
  $("rule-region-wrap").hidden = !portfolio;
  $("rule-period-wrap").hidden = spec.unit !== "ma";
  $("rule-value-wrap").hidden = spec.unit === "ma";
  const entry = portfolio ? null : selectedEntry();
  const data = entry ? chartFor(entry.symbol)?.data : null;
  const ccy = data?.currency || (entry?.market === "SG" ? "SGD" : "USD");
  $("rule-value-label").textContent = spec.unit === "price" ? `Price (${ccy})` : "Percent";
  $("rule-value-unit").textContent = spec.unit === "price" ? "" : "%";
  $("rule-value").step = spec.unit === "price" ? "any" : "0.5";

  // sensible default when the user hasn't typed a value yet
  if (!$("rule-value").dataset.touched && evt?.target?.id !== "rule-value") {
    if (spec.unit === "price") {
      const px = data?.price;
      $("rule-value").value = px ? Number((px * (type === "price_above" ? 1.05 : 0.95)).toPrecision(4)) : "";
    } else if (spec.unit === "pct") $("rule-value").value = RULE_DEFAULTS[type] ?? "";
  }

  const v = parseFloat($("rule-value").value);
  const sym = entry?.symbol || "";
  const valueText = spec.unit === "price" ? money(v, ccy) : `${fmt(v, v % 1 ? 1 : 0)}%`;
  const region = { ALL: "total", US: "US", SG: "Singapore" }[$("rule-region").value];
  const period = $("rule-period").value;
  const sentence = {
    price_above: `${sym} trades above ${valueText}`, price_below: `${sym} trades below ${valueText}`,
    day_up: `${sym} is up ${valueText} or more on the day`, day_down: `${sym} is down ${valueText} or more on the day`,
    off_high: `${sym} is ${valueText} or more below its 3-month high`,
    cross_above_ma: `${sym} crosses above its ${period}-day average`, cross_below_ma: `${sym} crosses below its ${period}-day average`,
    take_profit: `your ${sym} position is up ${valueText} on cost`, stop_loss: `your ${sym} position is down ${valueText} on cost`,
    portfolio_up: `your ${region} portfolio is up ${valueText} on the day`, portfolio_down: `your ${region} portfolio is down ${valueText} on the day`,
    concentration: `any single holding is more than ${valueText} of your ${region} portfolio`,
  }[type];
  let now = "";
  if (data && !portfolio) {
    if (spec.unit === "price" || spec.unit === "ma") now = ` Now ${money(data.price, ccy)}.`;
    else if (type === "day_up" || type === "day_down") now = ` Today ${signedPct(data.dayChangePct)}.`;
    else if (spec.scope === "position" && entry?.averagePrice) now = ` Now ${signedPct((data.price / entry.averagePrice - 1) * 100)} on cost.`;
  }
  let warn = "";
  if (spec.scope === "position" && !(entry?.position > 0 && entry?.averagePrice)) warn = " ⚠ Add shares and an average price on this card first.";
  if (portfolio && !state.watchlist.some((e) => e.position > 0)) warn = " ⚠ Add positions to your cards first.";
  $("rule-preview").textContent = sentence && (spec.unit === "ma" || isFinite(v)) ? `Notify me when ${sentence}.${now}${warn}` : warn.trim();
}

async function saveRule(evt) {
  evt.preventDefault();
  const type = $("rule-type").value;
  const spec = alertState.data.types[type];
  const entry = selectedEntry();
  const body = {
    id: alertState.editing?.id, type,
    symbol: spec.scope === "portfolio" ? "" : entry?.symbol,
    market: spec.scope === "portfolio" ? $("rule-region").value : entry?.market,
    value: $("rule-value").value, period: $("rule-period").value,
    repeat: $("rule-repeat").value, cooldownMinutes: $("rule-cooldown").value, note: $("rule-note").value,
    enabled: alertState.editing ? alertState.editing.enabled : true,
  };
  try {
    applyAlerts(await postJSON("/api/alerts/rule", body));
    closeModal("rule-modal");
    toast(alertState.editing ? "Rule updated" : "Rule added");
  } catch (e) { $("rule-error").textContent = e.message; $("rule-error").hidden = false; }
}

async function deleteRule() {
  const rule = alertState.editing;
  if (!rule) return;
  const btn = $("rule-delete");
  if (btn.dataset.armed !== "1") { btn.dataset.armed = "1"; btn.textContent = "Click again to delete"; setTimeout(() => { btn.dataset.armed = ""; btn.textContent = "Delete"; }, 3500); return; }
  btn.dataset.armed = ""; btn.textContent = "Delete";
  try {
    applyAlerts(await postJSON("/api/alerts/rule/delete", { id: rule.id }));
    closeModal("rule-modal");
    toast("Rule deleted");
  } catch (e) { toast(e.message); }
}

// ------------------------------------------------------------------ channels
function channelField(channel, name) { return $("channels-form").querySelector(`[data-channel="${channel}"] [name="${name}"]`); }

function openChannels() {
  const d = alertState.data;
  if (!d) return;
  for (const [name, cfg] of Object.entries(d.channels)) {
    for (const [key, value] of Object.entries(cfg)) {
      const input = channelField(name, key);
      if (!input) continue;
      if (input.type === "checkbox") input.checked = !!value;
      else if (cfg[`${key}Set`] !== undefined) {
        input.value = "";
        input.placeholder = cfg[`${key}Set`] ? `Saved ${cfg[`${key}Hint`] || ""} — leave blank to keep` : "";
      } else input.value = value ?? "";
    }
  }
  $("channels-form").querySelectorAll(".test-result").forEach((s) => { s.textContent = ""; s.className = "test-result"; });
  $("alert-interval").value = String(d.settings.intervalMinutes);
  if (![...$("alert-interval").options].some((o) => o.value === $("alert-interval").value)) $("alert-interval").value = "5";
  $("quiet-enabled").checked = !!d.settings.quietHours.enabled;
  $("quiet-start").value = d.settings.quietHours.start;
  $("quiet-end").value = d.settings.quietHours.end;
  $("channels-error").hidden = true;
  renderBrowserPermission();
  openModal("channels-modal");
}

function renderBrowserPermission() {
  const el = $("browser-perm");
  const btn = $("browser-allow");
  if (!("Notification" in window)) { el.textContent = "This browser doesn't support desktop notifications."; btn.hidden = true; return; }
  if (!window.isSecureContext) {
    el.textContent = "Pop-ups need HTTPS (or opening the app on this computer at 127.0.0.1). Use the bell, Telegram or ntfy on this device.";
    btn.hidden = true; return;
  }
  const p = Notification.permission;
  el.textContent = p === "granted" ? "Allowed in this browser. Pop-ups appear while the dashboard is open (even in a background tab)."
    : p === "denied" ? "Blocked in this browser — allow notifications for this site in the browser's site settings."
      : "Shows a desktop pop-up when a rule fires while the dashboard is open.";
  btn.hidden = p !== "default";
}

function collectChannels() {
  const out = {};
  $("channels-form").querySelectorAll("[data-channel]").forEach((fs) => {
    const name = fs.dataset.channel;
    out[name] = {};
    fs.querySelectorAll("[name]").forEach((input) => { out[name][input.name] = input.type === "checkbox" ? input.checked : input.value; });
  });
  return out;
}

async function persistChannels() {
  const payload = {
    channels: collectChannels(),
    settings: { intervalMinutes: $("alert-interval").value, quietHours: { enabled: $("quiet-enabled").checked, start: $("quiet-start").value || "23:00", end: $("quiet-end").value || "07:00" } },
  };
  const res = await postJSON("/api/alerts/settings", payload);
  applyAlerts(res);
  return res;
}

async function saveChannels(evt) {
  evt.preventDefault();
  $("channels-error").hidden = true;
  try {
    await persistChannels();
    closeModal("channels-modal");
    toast("Notification settings saved");
  } catch (e) { $("channels-error").textContent = e.message; $("channels-error").hidden = false; }
}

async function testChannel(channel, btn) {
  const out = btn.parentElement.querySelector(".test-result");
  out.className = "test-result"; out.textContent = "Saving and sending…";
  btn.disabled = true;
  try {
    const cb = channelField(channel, "enabled");
    if (!cb.checked) cb.checked = true;
    await persistChannels();
    openChannelsKeepValues();
    const { results } = await postJSON("/api/alerts/test", { channel });
    const r = results[channel] || Object.values(results)[0] || "";
    out.textContent = r === "sent" ? "Sent ✓ — check your device" : r;
    out.classList.add(r === "sent" ? "ok" : "bad");
  } catch (e) { out.textContent = e.message; out.classList.add("bad"); } finally { btn.disabled = false; }
}

/** After saving from inside the modal, refresh secret placeholders without closing it. */
function openChannelsKeepValues() {
  const d = alertState.data;
  for (const [name, cfg] of Object.entries(d.channels)) {
    for (const key of Object.keys(cfg)) {
      if (cfg[`${key}Set`] === undefined) continue;
      const input = channelField(name, key);
      if (input) { input.value = ""; input.placeholder = cfg[`${key}Set`] ? `Saved ${cfg[`${key}Hint`] || ""} — leave blank to keep` : ""; }
    }
  }
}

// ------------------------------------------------------------------ bell + notifications
async function pollNotifications() {
  try { applyNotifications(await api("/api/notifications?limit=30")); } catch { /* signed out or offline */ }
}

function applyNotifications(res) {
  const items = res.items || [];
  const fresh = alertState.seen ? items.filter((n) => !n.read && !alertState.seen.has(n.id)) : [];
  alertState.seen = new Set(items.map((n) => n.id));
  alertState.notifications = items;
  alertState.unread = res.unread || 0;
  const badge = $("bell-badge");
  badge.hidden = !alertState.unread;
  badge.textContent = alertState.unread > 9 ? "9+" : String(alertState.unread);
  $("bell-button").classList.toggle("ringing", alertState.unread > 0);
  if (!$("bell-popover").hidden) renderBell();
  if (fresh.length) {
    toast(fresh.length === 1 ? `🔔 ${fresh[0].title}` : `🔔 ${fresh.length} new alerts`);
    const allowed = alertState.data?.channels?.browser?.enabled && "Notification" in window && Notification.permission === "granted";
    if (allowed) fresh.slice(0, 3).forEach((n) => { try { new Notification(n.title, { body: n.body, tag: n.id }); } catch { /* ignore */ } });
    loadAlerts();
  }
}

function toggleBell() {
  const pop = $("bell-popover");
  if (pop.hidden) { pop.hidden = false; $("bell-button").setAttribute("aria-expanded", "true"); renderBell(); }
  else closeBell();
}
function closeBell() { $("bell-popover").hidden = true; $("bell-button").setAttribute("aria-expanded", "false"); }

function renderBell() {
  const list = $("notif-list");
  const items = alertState.notifications;
  if (!items.length) { list.innerHTML = '<li class="notif-empty">No notifications yet. Triggered alert rules appear here.</li>'; return; }
  list.innerHTML = items.map((n) => {
    const delivery = Object.entries(n.delivery || {}).map(([k, v]) => `${CHANNEL_LABELS[k] || k}: ${v}`).join(" · ");
    return `<li class="notif ${n.read ? "" : "unread"} ${esc(n.tone)}" data-id="${esc(n.id)}">
      <div class="notif-title">${esc(n.title)}</div>
      <div class="notif-body">${esc(n.body).replace(/\n/g, "<br>")}</div>
      <div class="notif-meta">${relTime(n.at)}${delivery ? ` · ${esc(delivery)}` : ""}</div></li>`;
  }).join("");
  list.querySelectorAll(".notif.unread").forEach((li) => {
    li.onclick = async () => { applyNotifications(await postJSON("/api/notifications/read", { ids: [li.dataset.id] })); };
  });
}
