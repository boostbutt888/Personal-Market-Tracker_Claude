/* Market Tracker — sign-in, account creation, 2FA and password recovery. */
"use strict";

const $ = (id) => document.getElementById(id);
const views = ["signin", "2fa", "create", "forgot", "key"];
const auth = { challenge: null, remember: false, backupMode: false, afterKey: "/", state: null };

// Same theme as the dashboard
(function theme() {
  let mode = "auto";
  try { mode = localStorage.getItem("stock-dashboard-theme") || "auto"; } catch { /* ignore */ }
  const h = new Date().getHours();
  document.body.dataset.theme = mode === "dark" || (mode !== "light" && (h >= 19 || h < 7)) ? "dark" : "light";
})();

function show(name) {
  $("loading").hidden = true;
  views.forEach((v) => { $(`view-${v}`).hidden = v !== name; });
  const view = $(`view-${name}`);
  view.querySelectorAll(".auth-error").forEach((e) => { e.hidden = true; });
  const first = view.querySelector("input:not([type=checkbox])");
  if (first) setTimeout(() => first.focus(), 30);
}

function fail(view, message) {
  const box = $(`view-${view}`).querySelector(".auth-error");
  box.textContent = message;
  box.hidden = false;
}

async function post(path, body) {
  const res = await fetch(path, {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  let data = {};
  try { data = await res.json(); } catch { /* ignore */ }
  if (!res.ok) { const err = new Error(data.error || `Request failed (${res.status})`); err.data = data; throw err; }
  return data;
}

function busy(form, on) {
  const btn = form.querySelector("button[type=submit], .btn.primary");
  if (!btn) return;
  btn.disabled = on;
  if (on) { btn.dataset.label = btn.textContent; btn.textContent = "Working…"; } else if (btn.dataset.label) btn.textContent = btn.dataset.label;
}

// ---------------------------------------------------------------- password strength
function strength(pw, user = "") {
  if (!pw) return { score: 0, label: "" };
  let score = 0;
  if (pw.length >= 10) score++;
  if (pw.length >= 14) score++;
  if (pw.length >= 18) score++;
  const classes = [/[a-z]/, /[A-Z]/, /[0-9]/, /[^A-Za-z0-9]/].filter((r) => r.test(pw)).length;
  if (classes >= 3) score++;
  if (/(.)\1{2,}/.test(pw) || /^(?:1234|abcd|qwer|pass)/i.test(pw)) score--;
  if (user && pw.toLowerCase().includes(user.toLowerCase())) score = 0;
  if (pw.length < 10) return { score: 0, label: `${10 - pw.length} more character${pw.length === 9 ? "" : "s"} needed` };
  score = Math.max(1, Math.min(4, score));
  return { score, label: ["", "Weak", "Fair", "Good", "Strong"][score] };
}
function bindStrength(inputId, meterId, userId) {
  const update = () => {
    const s = strength($(inputId).value, userId ? $(userId).value : "");
    const meter = $(meterId);
    meter.dataset.score = s.score;
    meter.querySelector("span").textContent = s.label;
  };
  $(inputId).addEventListener("input", update);
  if (userId) $(userId).addEventListener("input", update);
}

// ---------------------------------------------------------------- flows
async function signIn(evt) {
  evt.preventDefault();
  const form = evt.target;
  busy(form, true);
  auth.remember = $("si-remember").checked;
  try {
    const res = await post("/api/auth/login", { username: $("si-user").value.trim(), password: $("si-pass").value, remember: auth.remember });
    if (res.twoFactor) {
      auth.challenge = res.challenge;
      setBackupMode(false);
      $("tf-code").value = "";
      show("2fa");
    } else location.replace("/");
  } catch (e) {
    fail("signin", e.message);
    $("si-pass").select();
  } finally { busy(form, false); }
}

function setBackupMode(on) {
  auth.backupMode = on;
  const input = $("tf-code");
  input.value = "";
  input.maxLength = on ? 9 : 6;
  input.inputMode = on ? "text" : "numeric";
  input.autocomplete = on ? "off" : "one-time-code";
  input.pattern = on ? "[A-Za-z0-9-]*" : "[0-9 ]*";
  input.classList.toggle("code-input", !on);
  input.classList.toggle("mono", on);
  $("tf-label").textContent = on ? "Backup code" : "Authenticator code";
  $("tf-sub").textContent = on ? "Enter one of the backup codes you saved (e.g. abcd-2345). Each code works once."
    : "Enter the 6-digit code from your authenticator app.";
  $("tf-switch").textContent = on ? "Use the authenticator app instead" : "Use a backup code instead";
  input.focus();
}

async function verify2fa(evt) {
  evt.preventDefault();
  const form = evt.target;
  busy(form, true);
  try {
    await post("/api/auth/2fa", { challenge: auth.challenge, code: $("tf-code").value, remember: auth.remember });
    location.replace("/");
  } catch (e) {
    if (e.data?.restart) { show("signin"); fail("signin", e.message); return; }
    fail("2fa", e.message);
    $("tf-code").select();
  } finally { busy(form, false); }
}

async function createAccount(evt) {
  evt.preventDefault();
  const form = evt.target;
  const user = $("cr-user").value.trim(), pw = $("cr-pass").value;
  if (pw !== $("cr-pass2").value) { fail("create", "The two passwords don't match."); return; }
  if (strength(pw, user).score < 1) { fail("create", "Use at least 10 characters and don't include your username."); return; }
  busy(form, true);
  try {
    const path = auth.state.setupRequired ? "/api/auth/setup" : "/api/auth/signup";
    const res = await post(path, { username: user, password: pw });
    const imported = res.imported ? "Your existing watchlist.json was imported into your encrypted vault" +
      (res.imported === "imported" ? " and the unencrypted file was removed." : ". Delete the old watchlist.json file yourself.") : "";
    showKey(res.recoveryKey, user, {
      title: "Save your recovery key",
      sub: "This is the only way back in if you forget your password. It's shown once — store it in a password manager or print it.",
      imported, next: "/?welcome=1",
    });
  } catch (e) { fail("create", e.message); } finally { busy(form, false); }
}

async function recover(evt) {
  evt.preventDefault();
  const form = evt.target;
  const pw = $("fg-pass").value;
  if (pw !== $("fg-pass2").value) { fail("forgot", "The two passwords don't match."); return; }
  busy(form, true);
  try {
    const user = $("fg-user").value.trim();
    const res = await post("/api/auth/recover", { username: user, recoveryKey: $("fg-key").value, newPassword: pw });
    $("si-user").value = user;
    showKey(res.recoveryKey, user, {
      title: "Password reset — here's your new recovery key",
      sub: "The old recovery key no longer works. Save this one, then sign in with your new password.",
      next: null,
    });
  } catch (e) { fail("forgot", e.message); } finally { busy(form, false); }
}

function showKey(key, user, { title, sub, imported = "", next }) {
  $("key-title").textContent = title;
  $("key-sub").textContent = sub;
  $("key-value").textContent = key;
  $("key-imported").textContent = imported;
  $("key-imported").hidden = !imported;
  $("key-saved").checked = false;
  $("key-continue").disabled = true;
  $("key-continue").textContent = next ? "Continue to dashboard" : "Continue to sign in";
  auth.afterKey = next;
  $("key-copy").onclick = async () => {
    try { await navigator.clipboard.writeText(key); $("key-copy").textContent = "Copied"; } catch { selectText($("key-value")); }
  };
  $("key-download").onclick = () => download(`market-tracker-recovery-key-${user}.txt`,
    `Market Tracker recovery key\nAccount: ${user}\nCreated: ${new Date().toString()}\n\n${key}\n\n` +
    "Use this on the sign-in page (Forgot password?) to set a new password without losing your data.\n" +
    "Anyone with this key and your username can reset your password. Keep it private.\n");
  show("key");
}

function selectText(el) {
  const range = document.createRange();
  range.selectNodeContents(el);
  const sel = window.getSelection();
  sel.removeAllRanges(); sel.addRange(range);
}
function download(name, text) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
  const a = document.createElement("a");
  a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ---------------------------------------------------------------- init
async function init() {
  document.querySelectorAll("[data-go]").forEach((b) => {
    b.onclick = () => {
      const go = b.dataset.go;
      if (go === "signup") { setCreateMode(false); show("create"); } else show(go);
    };
  });
  document.querySelectorAll(".pw-toggle").forEach((b) => {
    b.onclick = () => {
      const input = $(b.dataset.for);
      const reveal = input.type === "password";
      input.type = reveal ? "text" : "password";
      b.textContent = reveal ? "Hide" : "Show";
      b.setAttribute("aria-label", reveal ? "Hide password" : "Show password");
    };
  });
  $("view-signin").onsubmit = signIn;
  $("view-2fa").onsubmit = verify2fa;
  $("view-create").onsubmit = createAccount;
  $("view-forgot").onsubmit = recover;
  $("tf-switch").onclick = () => setBackupMode(!auth.backupMode);
  $("tf-code").addEventListener("input", (e) => {
    if (!auth.backupMode && e.target.value.replace(/\D/g, "").length === 6) $("view-2fa").requestSubmit();
  });
  $("key-saved").onchange = (e) => { $("key-continue").disabled = !e.target.checked; };
  $("key-continue").onclick = () => { if (auth.afterKey) location.replace(auth.afterKey); else show("signin"); };
  bindStrength("cr-pass", "cr-strength", "cr-user");
  bindStrength("fg-pass", "fg-strength", "fg-user");

  try {
    const res = await fetch("/api/auth/state", { credentials: "same-origin" });
    auth.state = await res.json();
  } catch {
    $("loading").textContent = "Can't reach the Market Tracker server. Is it still running?";
    return;
  }
  const st = auth.state;
  if (st.signedIn) { location.replace("/"); return; }
  $("signup-link").hidden = !st.signupOpen || st.setupRequired;
  const foot = [];
  if (st.demo) foot.push("Demo mode — synthetic prices.");
  if (!st.https && !["localhost", "127.0.0.1", "[::1]"].includes(location.hostname))
    foot.push("This connection is not encrypted (HTTP). Only use it on a network you trust — see README › HTTPS.");
  if (st.version) foot.unshift(`Market Tracker ${st.version}.`);
  $("auth-foot").textContent = foot.join(" ");
  if (st.setupRequired) { setCreateMode(true); show("create"); } else show("signin");
}

function setCreateMode(firstRun) {
  $("create-title").textContent = firstRun ? "Welcome — create your account" : "Create an account";
  $("create-sub").textContent = firstRun
    ? "Your watchlist, positions and alert rules are encrypted with a key protected by this password." +
      (auth.state?.legacyWatchlist ? " Your existing watchlist.json will be imported and encrypted." : "")
    : "Each account has its own encrypted watchlist, positions and alerts.";
  $("create-back").hidden = firstRun;
}

init();
