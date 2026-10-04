/* Market Tracker — account & security: password, two-factor, sessions, activity, sign-out, idle lock. */
"use strict";

const EVENT_LABELS = {
  account_created: "Account created", signed_in: "Signed in", signed_out: "Signed out",
  sign_in_failed: "Failed sign-in attempt", account_locked: "Sign-in paused after repeated failures",
  password_changed: "Password changed", password_reset_with_recovery_key: "Password reset with recovery key",
  two_factor_enabled: "Two-factor turned on", two_factor_disabled: "Two-factor turned off",
  backup_code_used: "Backup code used", backup_codes_regenerated: "New backup codes created",
  recovery_key_replaced: "New recovery key created", session_revoked: "Device signed out", vault_locked: "Vault locked",
};
const WARN_EVENTS = new Set(["sign_in_failed", "account_locked", "two_factor_disabled", "backup_code_used", "password_reset_with_recovery_key"]);

function postJSON(path, body = {}) {
  return api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}
function when(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("en-SG", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

// ------------------------------------------------------------------ password strength (mirrors login.js)
function pwStrength(pw, user = "") {
  if (!pw) return { score: 0, label: "" };
  if (pw.length < 10) return { score: 0, label: `${10 - pw.length} more character${pw.length === 9 ? "" : "s"} needed` };
  let score = 1 + (pw.length >= 14) + (pw.length >= 18);
  if ([/[a-z]/, /[A-Z]/, /[0-9]/, /[^A-Za-z0-9]/].filter((r) => r.test(pw)).length >= 3) score++;
  if (/(.)\1{2,}/.test(pw)) score--;
  if (user && pw.toLowerCase().includes(user.toLowerCase())) return { score: 0, label: "Don't include your username" };
  score = Math.max(1, Math.min(4, score));
  return { score, label: ["", "Weak", "Fair", "Good", "Strong"][score] };
}

// ------------------------------------------------------------------ init
function initAccount() {
  const acct = state.account;
  $("account-name").textContent = acct.username;
  $("account-button").onclick = () => openAccount("overview");
  $("signout-button").onclick = signOut;
  document.querySelectorAll("#account-tabs button").forEach((b) => { b.onclick = () => showAccountTab(b.dataset.tab); });

  $("pw-username").value = acct.username;
  $("pw-new").addEventListener("input", () => {
    const s = pwStrength($("pw-new").value, acct.username);
    $("pw-strength").dataset.score = s.score;
    $("pw-strength").querySelector("span").textContent = s.label;
  });
  $("password-form").onsubmit = changePassword;
  $("secret-saved").onchange = (e) => { $("secret-done").disabled = !e.target.checked; };
  $("secret-done").onclick = () => { $("secret-modal").hidden = true; };

  renderSecurityBanner();
  startIdleWatch();
  const params = new URLSearchParams(location.search);
  if (params.has("welcome")) {
    history.replaceState(null, "", "/");
    toast(`Welcome, ${acct.username}. Your data is encrypted.`);
  }
}

async function refreshAccount() {
  state.account = await api("/api/account");
  state.csrf = state.account.csrf;
  renderSecurityBanner();
  return state.account;
}

function renderSecurityBanner() {
  const b = $("security-banner");
  const key = `2fa-nudge-${state.account.username}`;
  if (state.account.twoFactor || store.get(key, "") === "dismissed") { b.hidden = true; return; }
  b.innerHTML = `<span><b>Protect your portfolio:</b> turn on two-factor sign-in so a stolen password isn't enough.</span>
    <button type="button" class="btn small" id="banner-2fa">Set up</button>
    <button type="button" class="btn small ghost" id="banner-dismiss">Not now</button>`;
  b.hidden = false;
  $("banner-2fa").onclick = () => openAccount("twofactor");
  $("banner-dismiss").onclick = () => { store.set(key, "dismissed"); b.hidden = true; };
}

async function signOut() {
  try { await postJSON("/api/auth/logout"); } catch { /* ignore */ }
  location.replace("/login");
}

function startIdleWatch() {
  const minutes = state.account.idleMinutes;
  if (!minutes) return;
  setInterval(async () => {
    if (Date.now() - activity.last > minutes * 60000) {
      try { await postJSON("/api/auth/logout"); } catch { /* ignore */ }
      location.replace("/login");
    }
  }, 30000);
}

// ------------------------------------------------------------------ modal + tabs
async function openAccount(tab) {
  openModal("account-modal");
  await refreshAccount().catch(() => {});
  showAccountTab(tab);
}

function showAccountTab(tab) {
  document.querySelectorAll("#account-tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  document.querySelectorAll("#account-modal .tab-body").forEach((p) => { p.hidden = p.dataset.pane !== tab; });
  if (tab === "overview") renderOverview();
  if (tab === "twofactor") renderTwoFactor();
  if (tab === "sessions") renderSessions();
  if (tab === "activity") renderActivity();
  if (tab === "password") {
    $("password-form").reset(); $("pw-error").hidden = true;
    $("pw-strength").dataset.score = 0; $("pw-strength").querySelector("span").textContent = "";
    setTimeout(() => $("pw-current").focus(), 30);
  }
}

function pane(name) { return document.querySelector(`#account-modal [data-pane="${name}"]`); }

function check(ok, title, detail, action = "") {
  return `<li class="${ok ? "ok" : "todo"}"><span class="tick" aria-hidden="true">${ok ? "✓" : "!"}</span>
    <div><b>${title}</b><div class="hint">${detail}</div></div>${action}</li>`;
}

function renderOverview() {
  const a = state.account;
  const p = pane("overview");
  p.innerHTML = `
    <dl class="facts">
      <div><dt>Username</dt><dd>${esc(a.username)} ${a.role === "owner" ? '<span class="pill">Owner</span>' : ""}</dd></div>
      <div><dt>Account created</dt><dd>${when(a.createdAt)}</dd></div>
      <div><dt>This session</dt><dd>${a.remember ? "Remembered on this device" : `Signs out after ${a.idleMinutes} min idle`} · until ${when(a.sessionExpiresAt)}</dd></div>
    </dl>
    <h4>Security checklist</h4>
    <ul class="checklist">
      ${check(true, "Password protected", `Last changed ${when(a.passwordChangedAt)}.`, '<button type="button" class="btn small" data-jump="password">Change</button>')}
      ${check(a.twoFactor, a.twoFactor ? "Two-factor sign-in is on" : "Two-factor sign-in is off",
        a.twoFactor ? `${a.backupCodesLeft} backup code${a.backupCodesLeft === 1 ? "" : "s"} left.` : "Add a 6-digit code from your phone at every sign-in.",
        `<button type="button" class="btn small ${a.twoFactor ? "" : "primary"}" data-jump="twofactor">${a.twoFactor ? "Manage" : "Turn on"}</button>`)}
      ${check(true, "Data encrypted at rest", "Watchlist, positions, alert rules and channel secrets are sealed in your vault file with a key only your password (or recovery key) can unlock.")}
      ${check(true, "Recovery key", "Lets you reset a forgotten password without losing data. Lost it? Make a new one.",
        '<button type="button" class="btn small" id="new-recovery">New key</button>')}
    </ul>
    <div class="inline-prompt" id="recovery-prompt" hidden></div>
    ${a.role === "owner" ? `<h4>Server</h4>
      <label class="check"><input type="checkbox" id="allow-signup" ${a.allowSignup ? "checked" : ""}> Allow other people on this network to create their own accounts</label>` : ""}
    <h4>Your data</h4>
    <div class="row-actions">
      <a class="btn small" href="/api/account/export" download>Export watchlist &amp; rules (.json)</a>
      <button type="button" class="btn small danger" id="lock-vault">Lock vault &amp; sign out everywhere</button>
    </div>
    <p class="hint">Locking forgets the decryption key on the server, which also pauses background alerts until you sign in again.</p>`;
  p.querySelectorAll("[data-jump]").forEach((b) => { b.onclick = () => showAccountTab(b.dataset.jump); });
  $("new-recovery").onclick = () => passwordPrompt($("recovery-prompt"), "Confirm your password to create a new recovery key. The old key stops working.",
    async (password) => {
      const res = await postJSON("/api/account/recovery-key", { password });
      showSecret("Your new recovery key", "The previous key no longer works. Store this one safely.", res.recoveryKey,
        `market-tracker-recovery-key-${state.account.username}.txt`);
    });
  if ($("allow-signup")) $("allow-signup").onchange = async (e) => {
    try { await postJSON("/api/account/settings", { allowSignup: e.target.checked }); toast(e.target.checked ? "Sign-ups allowed" : "Sign-ups turned off"); }
    catch (err) { toast(err.message); e.target.checked = !e.target.checked; }
  };
  $("lock-vault").onclick = async () => {
    const btn = $("lock-vault");
    if (btn.dataset.armed !== "1") { btn.dataset.armed = "1"; btn.textContent = "Click again to lock"; setTimeout(() => { btn.dataset.armed = ""; btn.textContent = "Lock vault & sign out everywhere"; }, 4000); return; }
    try { await postJSON("/api/account/lock"); } catch { /* ignore */ }
    location.replace("/login");
  };
}

/** Render a small inline "confirm with password (and code)" form. */
function passwordPrompt(host, message, onSubmit, { withCode = false, submitLabel = "Confirm" } = {}) {
  host.hidden = false;
  host.innerHTML = `<form class="stack">
      <p class="hint">${esc(message)}</p>
      <div class="${withCode ? "two-col" : ""}">
        <label>Password<input type="password" name="password" autocomplete="current-password" required></label>
        ${withCode ? '<label>Authenticator or backup code<input name="code" autocomplete="one-time-code" required></label>' : ""}
      </div>
      <p class="form-error" hidden></p>
      <div class="row-actions"><button type="submit" class="btn small primary">${esc(submitLabel)}</button><button type="button" class="btn small ghost" data-cancel>Cancel</button></div>
    </form>`;
  const form = host.querySelector("form");
  form.querySelector("[data-cancel]").onclick = () => { host.hidden = true; host.innerHTML = ""; };
  form.onsubmit = async (e) => {
    e.preventDefault();
    const err = form.querySelector(".form-error");
    err.hidden = true;
    form.querySelector("button[type=submit]").disabled = true;
    try {
      await onSubmit(form.password.value, form.code?.value || "");
      host.hidden = true; host.innerHTML = "";
    } catch (ex) {
      err.textContent = ex.message; err.hidden = false;
      form.querySelector("button[type=submit]").disabled = false;
    }
  };
  setTimeout(() => form.password.focus(), 30);
}

// ------------------------------------------------------------------ password
async function changePassword(evt) {
  evt.preventDefault();
  const err = $("pw-error");
  err.hidden = true;
  const next = $("pw-new").value;
  if (next !== $("pw-confirm").value) { err.textContent = "The new passwords don't match."; err.hidden = false; return; }
  if (pwStrength(next, state.account.username).score < 1) { err.textContent = "Use at least 10 characters and don't include your username."; err.hidden = false; return; }
  const btn = evt.target.querySelector("button[type=submit]");
  btn.disabled = true;
  try {
    const res = await postJSON("/api/account/password", { current: $("pw-current").value, new: next });
    $("password-form").reset();
    await refreshAccount();
    toast(res.signedOutOthers ? `Password changed. ${res.signedOutOthers} other device(s) signed out.` : "Password changed.");
    showAccountTab("overview");
  } catch (e) {
    err.textContent = e.message; err.hidden = false;
  } finally { btn.disabled = false; }
}

// ------------------------------------------------------------------ two-factor
function renderTwoFactor() {
  const a = state.account;
  const p = pane("twofactor");
  if (!a.twoFactor) {
    p.innerHTML = `<div class="stack">
      <p>Two-factor sign-in asks for a 6-digit code from an authenticator app on your phone after your password.
      Even if someone learns your password, they can't get in without your phone.</p>
      <p class="hint">Works with Google Authenticator, Microsoft Authenticator, 1Password, Bitwarden, Authy and others.</p>
      <div class="row-actions"><button type="button" class="btn primary" id="tf-begin">Set up two-factor</button></div>
      <div id="tf-setup"></div></div>`;
    $("tf-begin").onclick = beginTwoFactor;
    return;
  }
  p.innerHTML = `<div class="stack">
    <div class="status-line ok"><span class="tick">✓</span> Two-factor sign-in is <b>on</b> since ${when(a.twoFactorSince)}.</div>
    <p class="hint">Backup codes left: <b>${a.backupCodesLeft}</b> of 10. Each works once if you don't have your phone.</p>
    <div class="row-actions">
      <button type="button" class="btn small" id="tf-codes">New backup codes</button>
      <button type="button" class="btn small danger" id="tf-off">Turn off two-factor</button>
    </div>
    <div class="inline-prompt" id="tf-prompt" hidden></div></div>`;
  $("tf-codes").onclick = () => passwordPrompt($("tf-prompt"), "Confirm your password. Your old backup codes will stop working.", async (password) => {
    const res = await postJSON("/api/account/2fa/backup-codes", { password });
    await refreshAccount();
    renderTwoFactor();
    showBackupCodes(res.backupCodes);
  }, { submitLabel: "Create new codes" });
  $("tf-off").onclick = () => passwordPrompt($("tf-prompt"), "To turn off two-factor, confirm your password and a current code.", async (password, code) => {
    await postJSON("/api/account/2fa/disable", { password, code });
    await refreshAccount();
    renderTwoFactor();
    toast("Two-factor sign-in turned off");
  }, { withCode: true, submitLabel: "Turn off" });
}

async function beginTwoFactor() {
  const host = $("tf-setup");
  $("tf-begin").hidden = true;
  try {
    const res = await postJSON("/api/account/2fa/begin");
    let qr = "";
    try {
      const q = qrcode(0, "M");
      q.addData(res.uri);
      q.make();
      qr = q.createSvgTag({ cellSize: 4, margin: 2, scalable: true, alt: "Two-factor set-up QR code" });
    } catch { qr = '<p class="hint">QR code unavailable — enter the key manually.</p>'; }
    host.innerHTML = `<ol class="steps">
      <li>Open your authenticator app and add an account by scanning this code:
        <div class="qr-row"><div class="qr">${qr}</div>
          <div><div class="hint">Can't scan? Enter this key manually (time-based):</div>
          <div class="secret-inline mono">${esc(res.secret)}</div></div></div></li>
      <li>Enter the 6-digit code the app shows:
        <form class="row-actions" id="tf-confirm">
          <input id="tf-first-code" class="code-input small" inputmode="numeric" autocomplete="one-time-code" maxlength="7" required>
          <button type="submit" class="btn primary">Turn on</button>
        </form>
        <p class="form-error" id="tf-error" hidden></p></li></ol>`;
    $("tf-first-code").focus();
    $("tf-confirm").onsubmit = async (e) => {
      e.preventDefault();
      $("tf-error").hidden = true;
      try {
        const out = await postJSON("/api/account/2fa/enable", { code: $("tf-first-code").value });
        await refreshAccount();
        renderTwoFactor();
        showBackupCodes(out.backupCodes);
      } catch (ex) { $("tf-error").textContent = ex.message; $("tf-error").hidden = false; }
    };
  } catch (e) {
    host.innerHTML = `<p class="form-error">${esc(e.message)}</p>`;
    $("tf-begin").hidden = false;
  }
}

function showBackupCodes(codes) {
  showSecret("Save your backup codes",
    "If you lose your phone, each of these lets you sign in once. They're shown only now.",
    codes.join("\n"), `market-tracker-backup-codes-${state.account.username}.txt`, true);
}

function showSecret(title, sub, text, filename, grid = false) {
  $("secret-title").textContent = title;
  $("secret-sub").textContent = sub;
  const box = $("secret-value");
  box.classList.toggle("codes", grid);
  box.innerHTML = grid ? text.split("\n").map((c) => `<span>${esc(c)}</span>`).join("") : esc(text);
  $("secret-saved").checked = false;
  $("secret-done").disabled = true;
  $("secret-copy").textContent = "Copy";
  $("secret-copy").onclick = async () => {
    try { await navigator.clipboard.writeText(text); $("secret-copy").textContent = "Copied"; } catch { toast("Copy blocked — select the text instead"); }
  };
  $("secret-download").onclick = () => {
    const body = `Market Tracker — ${title}\nAccount: ${state.account.username}\nCreated: ${new Date().toString()}\n\n${text}\n`;
    const url = URL.createObjectURL(new Blob([body], { type: "text/plain" }));
    const a = document.createElement("a");
    a.href = url; a.download = filename; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  $("secret-modal").hidden = false;
}

// ------------------------------------------------------------------ sessions + activity
async function renderSessions() {
  const p = pane("sessions");
  p.innerHTML = '<div class="skeleton" style="height:120px"></div>';
  try {
    const { sessions } = await api("/api/account/sessions");
    p.innerHTML = `<p class="hint">Devices signed in to your account. Sessions end when the server restarts.</p>
      <ul class="session-list">${sessions.map((s) => `<li>
        <div><b>${esc(s.device)}</b> ${s.current ? '<span class="pill">This device</span>' : ""}
        <div class="hint">${esc(s.ip)} · signed in ${when(s.signedInAt)} · active ${relTime(s.lastSeenAt)}${s.remember ? " · remembered" : ""}</div></div>
        ${s.current ? "" : `<button type="button" class="btn small" data-revoke="${esc(s.id)}">Sign out</button>`}</li>`).join("")}</ul>
      ${sessions.length > 1 ? '<div class="row-actions"><button type="button" class="btn small danger" id="revoke-others">Sign out all other devices</button></div>' : ""}`;
    p.querySelectorAll("[data-revoke]").forEach((b) => {
      b.onclick = async () => { await postJSON("/api/account/sessions/revoke", { id: b.dataset.revoke }); renderSessions(); };
    });
    if ($("revoke-others")) $("revoke-others").onclick = async () => {
      const res = await postJSON("/api/account/sessions/revoke", { others: true });
      toast(`${res.ended} device(s) signed out`);
      renderSessions();
    };
  } catch (e) { p.innerHTML = `<p class="form-error">${esc(e.message)}</p>`; }
}

async function renderActivity() {
  const p = pane("activity");
  p.innerHTML = '<div class="skeleton" style="height:120px"></div>';
  try {
    const { events } = await api("/api/account/activity");
    p.innerHTML = events.length ? `<ul class="activity-list">${events.map((e) => `<li class="${WARN_EVENTS.has(e.event) ? "warn" : ""}">
        <span class="when">${when(e.at)}</span><span>${esc(EVENT_LABELS[e.event] || e.event)}${e.detail ? ` <span class="hint">· ${esc(e.detail)}</span>` : ""}</span>
        <span class="hint mono">${esc(e.ip || "")}</span></li>`).join("")}</ul>`
      : '<p class="empty">No activity yet.</p>';
  } catch (e) { p.innerHTML = `<p class="form-error">${esc(e.message)}</p>`; }
}
