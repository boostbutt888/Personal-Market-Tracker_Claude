"""Accounts, sessions, two-factor sign-in and encrypted per-user vaults.

Standard library only - no pip installs.

How the keys fit together
-------------------------
* Password  --PBKDF2-HMAC-SHA256 (600k iterations, random salt)-->  master key (32 bytes)
* master key  --HMAC-->  `auth` verifier (stored, compared in constant time)
                 --HMAC-->  key-encryption key (KEK, never stored)
* Every account has a random 256-bit data key (DEK). It is stored twice, each copy sealed:
  once with the password KEK and once with a key derived from the one-time recovery key.
* The DEK seals the user's vault file (watchlist, positions, alert rules, notification
  channel secrets, notification history) and the TOTP secret.

Sealing = encrypt-then-MAC: HMAC-SHA256 in counter mode produces the keystream and a
second, independent HMAC-SHA256 key authenticates nonce + ciphertext + context. Any
tampering or a wrong key is detected before anything is decrypted.

The DEK only exists in server memory after a successful sign-in. It stays unlocked while
the server runs (so alert rules keep being checked after the browser is closed) until
the server stops or the user chooses "Lock vault".
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import struct
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

KDF_ITERATIONS = int(os.environ.get("STOCK_DASHBOARD_KDF_ITERATIONS", "600000"))
RECOVERY_KDF_ITERATIONS = 100_000
SESSION_HOURS = 12            # normal sign-in
REMEMBER_DAYS = 30            # "keep me signed in on this device"
IDLE_MINUTES = 60             # normal sign-in expires after this much inactivity
CHALLENGE_SECONDS = 300       # time allowed to enter the 2FA code
MAX_FAILS_PER_USER = 5        # then the account is locked for LOCK_MINUTES
MAX_FAILS_PER_IP = 20
LOCK_MINUTES = 15
AUDIT_KEEP = 60
ISSUER = "Market Tracker"

USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,32}$")
# usernames become folder names, so refuse the ones Windows reserves
RESERVED_NAMES = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}
COMMON_PASSWORDS = {
    "password", "password1", "password123", "passw0rd", "12345678", "123456789", "1234567890",
    "qwerty123", "qwertyuiop", "iloveyou", "letmein123", "welcome123", "admin12345", "abc12345",
    "11111111", "00000000", "trustno1", "sunshine1", "princess1", "football1", "monkey123",
    "dragon123", "baseball1", "starwars1", "whatever1", "superman1", "1q2w3e4r5t", "zaq12wsx",
    "stockmarket", "marketracker", "markettracker", "singapore1", "changeme123",
}


class AuthError(Exception):
    """User-facing error with an HTTP status."""

    def __init__(self, message: str, status: int = 400, **extra):
        super().__init__(message)
        self.status = status
        self.extra = extra


class CryptoError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Primitive helpers
# --------------------------------------------------------------------------- #
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _norm_password(password: str) -> bytes:
    return unicodedata.normalize("NFKC", password).encode("utf-8")


def subkey(key: bytes, label: str) -> bytes:
    return hmac.new(key, b"market-tracker/v1/" + label.encode("ascii"), hashlib.sha256).digest()


def _keystream_xor(key: bytes, nonce: bytes, data: bytes) -> bytes:
    out = bytearray(len(data))
    for block_index, offset in enumerate(range(0, len(data), 32)):
        pad = hmac.new(key, nonce + struct.pack(">Q", block_index), hashlib.sha256).digest()
        chunk = data[offset:offset + 32]
        mixed = int.from_bytes(chunk, "big") ^ int.from_bytes(pad[:len(chunk)], "big")
        out[offset:offset + len(chunk)] = mixed.to_bytes(len(chunk), "big")
    return bytes(out)


def _mac_input(context: str, nonce: bytes, ciphertext: bytes) -> bytes:
    ctx = context.encode("utf-8")
    return b"v1" + struct.pack(">I", len(ctx)) + ctx + nonce + ciphertext


def seal(key: bytes, plaintext: bytes, context: str) -> str:
    """Encrypt-then-MAC. `context` binds the ciphertext to its purpose (e.g. 'vault:<id>')."""
    nonce = secrets.token_bytes(16)
    ciphertext = _keystream_xor(subkey(key, "enc"), nonce, plaintext)
    tag = hmac.new(subkey(key, "mac"), _mac_input(context, nonce, ciphertext), hashlib.sha256).digest()
    return "v1." + b64e(nonce + ciphertext + tag)


def unseal(key: bytes, token: str, context: str) -> bytes:
    if not isinstance(token, str) or not token.startswith("v1."):
        raise CryptoError("Unknown ciphertext format")
    try:
        raw = b64d(token[3:])
    except (ValueError, TypeError) as exc:
        raise CryptoError("Corrupt ciphertext") from exc
    if len(raw) < 48:
        raise CryptoError("Corrupt ciphertext")
    nonce, ciphertext, tag = raw[:16], raw[16:-32], raw[-32:]
    expected = hmac.new(subkey(key, "mac"), _mac_input(context, nonce, ciphertext), hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected):
        raise CryptoError("Authentication failed (wrong key or tampered data)")
    return _keystream_xor(subkey(key, "enc"), nonce, ciphertext)


def derive_master(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", _norm_password(password), salt, iterations)


# --------------------------------------------------------------------------- #
# TOTP (RFC 6238) - works with Google Authenticator, Microsoft Authenticator,
# 1Password, Authy, Bitwarden, etc.
# --------------------------------------------------------------------------- #
def totp_new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def totp_at(secret: str, step: int, digits: int = 6) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(value).zfill(digits)


def totp_match(secret: str, code: str, last_step: int = 0, window: int = 1) -> int | None:
    """Return the matched time step, or None. Rejects steps <= last_step (replay)."""
    code = re.sub(r"\s", "", code or "")
    if not re.fullmatch(r"\d{6}", code):
        return None
    current = int(time.time() // 30)
    for step in range(current - window, current + window + 1):
        if step > last_step and hmac.compare_digest(totp_at(secret, step), code):
            return step
    return None


def otpauth_uri(secret: str, username: str) -> str:
    from urllib.parse import quote
    label = quote(f"{ISSUER}:{username}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(ISSUER)}&algorithm=SHA1&digits=6&period=30"


# --------------------------------------------------------------------------- #
# Backup codes and recovery key
# --------------------------------------------------------------------------- #
_CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"   # no 0/o/1/l/i
_KEY_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def _random_text(alphabet: str, length: int) -> str:
    return "".join(secrets.choice(alphabet) for _ in range(length))


def new_backup_codes(count: int = 10) -> list[str]:
    return [f"{_random_text(_CODE_ALPHABET, 4)}-{_random_text(_CODE_ALPHABET, 4)}" for _ in range(count)]


def _norm_code(code: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (code or "").lower())


def hash_backup_code(code: str, salt: bytes) -> str:
    return hashlib.sha256(salt + _norm_code(code).encode()).hexdigest()


def new_recovery_key() -> str:
    raw = _random_text(_KEY_ALPHABET, 28)   # ~138 bits
    return "-".join(raw[i:i + 4] for i in range(0, 28, 4))


def _norm_recovery_key(key: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (key or "").upper())


# --------------------------------------------------------------------------- #
# Password policy
# --------------------------------------------------------------------------- #
def password_problem(password: str, username: str = "") -> str | None:
    if not isinstance(password, str) or len(password) < 10:
        return "Use at least 10 characters."
    if len(password) > 200:
        return "Use 200 characters or fewer."
    lowered = password.lower()
    if lowered in COMMON_PASSWORDS:
        return "That password is too common. Pick something less guessable."
    if len(set(password)) < 5:
        return "Use a wider mix of characters."
    if username and len(username) >= 3 and username.lower() in lowered:
        return "Don't include your username in the password."
    return None


# --------------------------------------------------------------------------- #
# Throttling (brute-force protection)
# --------------------------------------------------------------------------- #
class Throttle:
    def __init__(self):
        self._fails: dict[str, list[float]] = {}
        self._locked: dict[str, float] = {}
        self._lock = threading.Lock()

    def locked_for(self, key: str) -> int:
        with self._lock:
            until = self._locked.get(key, 0)
            remaining = until - time.time()
            if remaining <= 0:
                self._locked.pop(key, None)
                return 0
            return int(remaining) + 1

    def fail(self, key: str, limit: int) -> bool:
        """Record a failure. Returns True if this failure triggered a lockout."""
        now = time.time()
        with self._lock:
            window = [t for t in self._fails.get(key, []) if now - t < LOCK_MINUTES * 60]
            window.append(now)
            self._fails[key] = window
            if len(window) >= limit:
                self._locked[key] = now + LOCK_MINUTES * 60
                self._fails[key] = []
                return True
            return False

    def fails(self, key: str) -> int:
        with self._lock:
            return len([t for t in self._fails.get(key, []) if time.time() - t < LOCK_MINUTES * 60])

    def clear(self, key: str):
        with self._lock:
            self._fails.pop(key, None)
            self._locked.pop(key, None)


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #
@dataclass
class Session:
    public_id: str
    user_id: str
    csrf: str
    created: float
    last_seen: float
    expires: float
    remember: bool
    ip: str
    agent: str
    pending_totp: str | None = None     # secret being set up, not yet confirmed

    def idle_expired(self, now: float) -> bool:
        return not self.remember and now - self.last_seen > IDLE_MINUTES * 60


@dataclass
class Challenge:
    user_id: str
    dek: bytes
    remember: bool
    expires: float
    attempts: int = 0


def _short_agent(agent: str) -> str:
    agent = agent or ""
    browser = next((b for b in ("Edg", "OPR", "Chrome", "Firefox", "Safari") if b in agent), "Browser")
    browser = {"Edg": "Edge", "OPR": "Opera"}.get(browser, browser)
    system = next((s for s in ("Windows", "Android", "iPhone", "iPad", "Mac OS", "Linux") if s in agent), "")
    system = {"Mac OS": "macOS"}.get(system, system)
    return f"{browser} on {system}" if system else browser


# --------------------------------------------------------------------------- #
# The account service
# --------------------------------------------------------------------------- #
class AccountService:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        # Layout: data/server.json (server-wide settings) and one folder per user:
        #   data/users/<username>/account.json  - sign-in record (salted verifier, wrapped keys, 2FA, activity)
        #   data/users/<username>/vault.dat     - that user's encrypted watchlist, positions, rules, settings
        self.server_file = data_dir / "server.json"
        self.users_dir = data_dir / "users"
        self.legacy_users_file = data_dir / "users.json"      # v0.2 layout, migrated on first start
        self.legacy_vault_dir = data_dir / "vaults"
        self._lock = threading.RLock()
        self._vault_locks: dict[str, threading.Lock] = {}
        self._sessions: dict[str, Session] = {}         # sha256(token) -> Session
        self._challenges: dict[str, Challenge] = {}     # sha256(token) -> Challenge
        self._keys: dict[str, bytes] = {}               # user_id -> unlocked DEK
        self.throttle = Throttle()
        self._dummy_salt = secrets.token_bytes(16)
        self._db = self._load()

    # ---- persistence ------------------------------------------------------- #
    def user_dir(self, user: dict) -> Path:
        return self.users_dir / user["username"].lower()

    def _load(self) -> dict:
        db = {"version": 2, "settings": {"allowSignup": False}, "users": {}}
        try:
            server = json.loads(self.server_file.read_text(encoding="utf-8"))
            if isinstance(server.get("settings"), dict):
                db["settings"].update(server["settings"])
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
        if self.users_dir.is_dir():
            for folder in sorted(self.users_dir.iterdir()):
                try:
                    user = json.loads((folder / "account.json").read_text(encoding="utf-8"))
                    if isinstance(user, dict) and user.get("id") and user.get("username"):
                        db["users"][user["id"]] = user
                except (OSError, json.JSONDecodeError):
                    continue
        if not db["users"] and self.legacy_users_file.exists():
            self._migrate_legacy(db)
        return db

    def _migrate_legacy(self, db: dict):
        """v0.2 kept every account in data/users.json and vaults in data/vaults/<id>.vault.
        Move each user into their own folder; the old index is kept as users.json.migrated."""
        try:
            old = json.loads(self.legacy_users_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(old, dict) or not isinstance(old.get("users"), dict):
            return
        db["settings"].update(old.get("settings") or {})
        self._db = db
        for uid, user in old["users"].items():
            db["users"][uid] = user
            old_vault = self.legacy_vault_dir / f"{uid}.vault"
            if old_vault.exists():
                target = self.user_dir(user) / "vault.dat"
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(old_vault, target)
        self._save()
        try:
            os.replace(self.legacy_users_file, self.legacy_users_file.with_name("users.json.migrated"))
            self.legacy_vault_dir.rmdir()
        except OSError:
            pass

    def _write_private(self, path: Path, text: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)

    def _save(self):
        self._write_private(self.server_file, json.dumps({"version": 2, "settings": self._db["settings"]}, indent=2))
        for user in self._db["users"].values():
            self._write_private(self.user_dir(user) / "account.json", json.dumps(user, indent=2))

    # ---- lookups ----------------------------------------------------------- #
    def setup_required(self) -> bool:
        return not self._db["users"]

    def signup_open(self) -> bool:
        return self.setup_required() or bool(self._db["settings"].get("allowSignup"))

    def set_signup(self, allowed: bool):
        with self._lock:
            self._db["settings"]["allowSignup"] = bool(allowed)
            self._save()

    def user(self, user_id: str) -> dict | None:
        return self._db["users"].get(user_id)

    def find_user(self, username: str) -> dict | None:
        wanted = (username or "").strip().lower()
        return next((u for u in self._db["users"].values() if u["username"].lower() == wanted), None)

    def all_user_ids(self) -> list[str]:
        return list(self._db["users"].keys())

    def unlocked_user_ids(self) -> list[str]:
        return [uid for uid in self._keys if uid in self._db["users"]]

    def is_unlocked(self, user_id: str) -> bool:
        return user_id in self._keys

    # ---- audit ------------------------------------------------------------- #
    def audit(self, user: dict | None, event: str, ip: str = "", detail: str = "", save: bool = True):
        if not user:
            return
        log = user.setdefault("audit", [])
        log.insert(0, {"at": now_iso(), "event": event, "ip": ip, "detail": detail})
        del log[AUDIT_KEEP:]
        if save:
            self._save()

    # ---- registration ------------------------------------------------------ #
    def register(self, username: str, password: str, ip: str = "", agent: str = "") -> tuple[dict, str, bytes]:
        username = (username or "").strip()
        if not USERNAME_RE.fullmatch(username) or username[0] in ".-" or username[-1] == "." \
                or username.lower().split(".")[0] in RESERVED_NAMES:
            raise AuthError("Usernames are 3-32 letters, numbers, dots, dashes or underscores.")
        problem = password_problem(password, username)
        if problem:
            raise AuthError(problem)
        with self._lock:
            if not self.signup_open():
                raise AuthError("New accounts are turned off. Ask the owner to allow sign-ups.", 403)
            if self.find_user(username):
                raise AuthError("That username is taken.", 409)
            first = self.setup_required()
            user_id = secrets.token_hex(8)
            dek = secrets.token_bytes(32)
            salt = secrets.token_bytes(16)
            master = derive_master(password, salt, KDF_ITERATIONS)
            recovery_key = new_recovery_key()
            user = {
                "id": user_id,
                "username": username,
                "role": "owner" if first else "member",
                "createdAt": now_iso(),
                "passwordChangedAt": now_iso(),
                "kdf": {"alg": "pbkdf2-sha256", "salt": b64e(salt), "iterations": KDF_ITERATIONS},
                "verifier": b64e(subkey(master, "auth")),
                "wrappedKey": seal(subkey(master, "kek"), dek, f"dek:{user_id}"),
                "recovery": self._wrap_recovery(user_id, dek, recovery_key),
                "totp": {"enabled": False, "secret": None, "lastStep": 0},
                "backupCodes": {"salt": "", "hashes": []},
                "audit": [],
            }
            self._db["users"][user_id] = user
            self.audit(user, "account_created", ip, _short_agent(agent), save=False)
            self._save()
            self._keys[user_id] = dek
        return user, recovery_key, dek

    def _wrap_recovery(self, user_id: str, dek: bytes, recovery_key: str) -> dict:
        salt = secrets.token_bytes(16)
        rk = hashlib.pbkdf2_hmac("sha256", _norm_recovery_key(recovery_key).encode(), salt, RECOVERY_KDF_ITERATIONS)
        return {"salt": b64e(salt), "iterations": RECOVERY_KDF_ITERATIONS,
                "wrappedKey": seal(rk, dek, f"recovery:{user_id}")}

    # ---- password check ---------------------------------------------------- #
    def _unlock_with_password(self, user: dict | None, password: str) -> bytes | None:
        """Return the DEK if the password is right. Constant-ish time for unknown users."""
        if not user:
            derive_master(password or "", self._dummy_salt, KDF_ITERATIONS)
            return None
        kdf = user["kdf"]
        master = derive_master(password or "", b64d(kdf["salt"]), int(kdf["iterations"]))
        if not hmac.compare_digest(subkey(master, "auth"), b64d(user["verifier"])):
            return None
        try:
            return unseal(subkey(master, "kek"), user["wrappedKey"], f"dek:{user['id']}")
        except CryptoError:
            return None

    def _check_throttle(self, username: str, ip: str):
        for key in (f"user:{(username or '').lower()}", f"ip:{ip}"):
            wait = self.throttle.locked_for(key)
            if wait:
                minutes = max(1, round(wait / 60))
                raise AuthError(f"Too many failed attempts. Try again in about {minutes} minute{'s' if minutes > 1 else ''}.",
                                429, retryAfter=wait)

    def _record_failure(self, username: str, ip: str, user: dict | None, reason: str):
        locked = self.throttle.fail(f"user:{(username or '').lower()}", MAX_FAILS_PER_USER)
        ip_locked = self.throttle.fail(f"ip:{ip}", MAX_FAILS_PER_IP)
        with self._lock:
            self.audit(user, "account_locked" if locked else "sign_in_failed", ip, reason)
        if locked or ip_locked:
            raise AuthError(f"Too many failed attempts. Sign-in is paused for {LOCK_MINUTES} minutes.", 429,
                            retryAfter=LOCK_MINUTES * 60)

    # ---- sign in ----------------------------------------------------------- #
    def sign_in(self, username: str, password: str, remember: bool, ip: str, agent: str) -> dict:
        """Step 1. Returns {'token':..} or {'challenge':..} when 2FA is on."""
        self._check_throttle(username, ip)
        user = self.find_user(username)
        dek = self._unlock_with_password(user, password)
        if dek is None:
            self._record_failure(username, ip, user, "wrong password" if user else "")
            time.sleep(0.3)
            remaining = MAX_FAILS_PER_USER - self.throttle.fails(f"user:{(username or '').lower()}")
            hint = f" {remaining} attempt{'s' if remaining != 1 else ''} left before a {LOCK_MINUTES}-minute lock." \
                if user and 0 < remaining <= 2 else ""
            raise AuthError("Username or password is incorrect." + hint, 401)
        if user["totp"].get("enabled"):
            token = secrets.token_urlsafe(32)
            with self._lock:
                self._purge()
                self._challenges[self._h(token)] = Challenge(user["id"], dek, remember, time.time() + CHALLENGE_SECONDS)
            return {"challenge": token, "twoFactor": True}
        return {"token": self._start_session(user, dek, remember, ip, agent, "password")}

    def verify_challenge(self, challenge: str, code: str, ip: str, agent: str) -> str:
        """Step 2: TOTP code or a backup code. Returns a session token."""
        with self._lock:
            ch = self._challenges.get(self._h(challenge or ""))
            if not ch or ch.expires < time.time():
                self._challenges.pop(self._h(challenge or ""), None)
                raise AuthError("That sign-in has expired. Start again.", 401, restart=True)
            user = self.user(ch.user_id)
        self._check_throttle(user["username"], ip)
        method = self._check_second_factor(user, ch.dek, code)
        if not method:
            ch.attempts += 1
            self._record_failure(user["username"], ip, user, "wrong 2FA code")
            if ch.attempts >= 5:
                with self._lock:
                    self._challenges.pop(self._h(challenge), None)
                raise AuthError("Too many wrong codes. Start again.", 401, restart=True)
            raise AuthError("That code didn't work. Check the time on your phone and try the newest code.", 401)
        with self._lock:
            self._challenges.pop(self._h(challenge), None)
        return self._start_session(user, ch.dek, ch.remember, ip, agent, method)

    def _check_second_factor(self, user: dict, dek: bytes, code: str) -> str | None:
        """Returns 'totp' / 'backup code' on success. Consumes backup codes and TOTP steps."""
        with self._lock:
            totp = user["totp"]
            if totp.get("enabled") and totp.get("secret"):
                secret = unseal(dek, totp["secret"], f"totp:{user['id']}").decode()
                step = totp_match(secret, code, int(totp.get("lastStep") or 0))
                if step:
                    totp["lastStep"] = step
                    self._save()
                    return "totp"
            codes = user.get("backupCodes") or {}
            if codes.get("hashes") and len(_norm_code(code)) == 8:
                hashed = hash_backup_code(code, b64d(codes["salt"]))
                for stored in list(codes["hashes"]):
                    if hmac.compare_digest(stored, hashed):
                        codes["hashes"].remove(stored)
                        self.audit(user, "backup_code_used", "", f"{len(codes['hashes'])} left", save=False)
                        self._save()
                        return "backup code"
        return None

    def _start_session(self, user: dict, dek: bytes, remember: bool, ip: str, agent: str, method: str) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            self._purge()
            self._keys[user["id"]] = dek
            self._sessions[self._h(token)] = Session(
                public_id=secrets.token_hex(6), user_id=user["id"], csrf=secrets.token_urlsafe(24),
                created=now, last_seen=now,
                expires=now + (REMEMBER_DAYS * 86400 if remember else SESSION_HOURS * 3600),
                remember=bool(remember), ip=ip, agent=_short_agent(agent))
            self.audit(user, "signed_in", ip, f"{method} · {_short_agent(agent)}")
        self.throttle.clear(f"user:{user['username'].lower()}")
        return token

    # ---- sessions ---------------------------------------------------------- #
    @staticmethod
    def _h(token: str) -> str:
        return hashlib.sha256((token or "").encode()).hexdigest()

    def _purge(self):
        now = time.time()
        for key, s in list(self._sessions.items()):
            if s.expires < now or s.idle_expired(now):
                del self._sessions[key]
        for key, c in list(self._challenges.items()):
            if c.expires < now:
                del self._challenges[key]

    def session(self, token: str | None, touch: bool = True) -> Session | None:
        """Look up a live session. `touch=False` (background polling) doesn't reset the idle timer."""
        if not token:
            return None
        now = time.time()
        with self._lock:
            s = self._sessions.get(self._h(token))
            if not s:
                return None
            if s.expires < now or s.idle_expired(now) or s.user_id not in self._db["users"] \
                    or s.user_id not in self._keys:
                del self._sessions[self._h(token)]
                return None
            if touch:
                s.last_seen = now
            return s

    def sign_out(self, token: str):
        with self._lock:
            s = self._sessions.pop(self._h(token or ""), None)
            if s:
                self.audit(self.user(s.user_id), "signed_out", s.ip)

    def list_sessions(self, user_id: str, current: Session) -> list[dict]:
        with self._lock:
            self._purge()
            rows = [s for s in self._sessions.values() if s.user_id == user_id]
        rows.sort(key=lambda s: s.last_seen, reverse=True)
        fmt = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds")  # noqa: E731
        return [{"id": s.public_id, "current": s is current, "ip": s.ip, "device": s.agent,
                 "signedInAt": fmt(s.created), "lastSeenAt": fmt(s.last_seen), "remember": s.remember}
                for s in rows]

    def revoke_session(self, user_id: str, public_id: str) -> bool:
        with self._lock:
            for key, s in list(self._sessions.items()):
                if s.user_id == user_id and s.public_id == public_id:
                    del self._sessions[key]
                    self.audit(self.user(user_id), "session_revoked", "", s.agent)
                    return True
        return False

    def revoke_other_sessions(self, user_id: str, keep: Session | None) -> int:
        with self._lock:
            doomed = [k for k, s in self._sessions.items() if s.user_id == user_id and s is not keep]
            for k in doomed:
                del self._sessions[k]
            return len(doomed)

    def lock_vault(self, user_id: str, ip: str = ""):
        """Sign out everywhere and forget the data key (pauses background alerts)."""
        with self._lock:
            self.revoke_other_sessions(user_id, None)
            self._keys.pop(user_id, None)
            self.audit(self.user(user_id), "vault_locked", ip)

    # ---- password management ---------------------------------------------- #
    def change_password(self, session: Session, current: str, new: str, ip: str) -> int:
        user = self.user(session.user_id)
        self._check_throttle(user["username"], ip)
        dek = self._unlock_with_password(user, current)
        if dek is None:
            self._record_failure(user["username"], ip, user, "wrong current password (password change)")
            raise AuthError("Your current password is incorrect.", 403)
        if current == new:
            raise AuthError("The new password must be different from the current one.")
        problem = password_problem(new, user["username"])
        if problem:
            raise AuthError(problem)
        self._rewrap(user, dek, new)
        with self._lock:
            ended = self.revoke_other_sessions(user["id"], session)
            self.audit(user, "password_changed", ip, f"{ended} other device(s) signed out" if ended else "")
        return ended

    def _rewrap(self, user: dict, dek: bytes, new_password: str):
        salt = secrets.token_bytes(16)
        master = derive_master(new_password, salt, KDF_ITERATIONS)
        with self._lock:
            user["kdf"] = {"alg": "pbkdf2-sha256", "salt": b64e(salt), "iterations": KDF_ITERATIONS}
            user["verifier"] = b64e(subkey(master, "auth"))
            user["wrappedKey"] = seal(subkey(master, "kek"), dek, f"dek:{user['id']}")
            user["passwordChangedAt"] = now_iso()
            self._save()

    def reset_with_recovery_key(self, username: str, recovery_key: str, new_password: str, ip: str) -> str:
        """Forgot password: the recovery key unwraps the data key so nothing is lost.
        Returns a fresh recovery key (the old one is spent)."""
        self._check_throttle(username, ip)
        user = self.find_user(username)
        dek = None
        if user:
            rec = user["recovery"]
            rk = hashlib.pbkdf2_hmac("sha256", _norm_recovery_key(recovery_key).encode(), b64d(rec["salt"]),
                                     int(rec["iterations"]))
            try:
                dek = unseal(rk, rec["wrappedKey"], f"recovery:{user['id']}")
            except CryptoError:
                dek = None
        if dek is None:
            self._record_failure(username, ip, user, "wrong recovery key")
            time.sleep(0.3)
            raise AuthError("Username or recovery key is incorrect.", 401)
        problem = password_problem(new_password, user["username"])
        if problem:
            raise AuthError(problem)
        self._rewrap(user, dek, new_password)
        fresh = new_recovery_key()
        with self._lock:
            user["recovery"] = self._wrap_recovery(user["id"], dek, fresh)
            self.revoke_other_sessions(user["id"], None)
            self.audit(user, "password_reset_with_recovery_key", ip, "all sessions signed out", save=False)
            self._save()
        self.throttle.clear(f"user:{user['username'].lower()}")
        return fresh

    def verify_password(self, session: Session, password: str, ip: str) -> bytes:
        user = self.user(session.user_id)
        self._check_throttle(user["username"], ip)
        dek = self._unlock_with_password(user, password)
        if dek is None:
            self._record_failure(user["username"], ip, user, "wrong password (confirmation)")
            raise AuthError("Password is incorrect.", 403)
        return dek

    def new_recovery_key_for(self, session: Session, password: str, ip: str) -> str:
        dek = self.verify_password(session, password, ip)
        fresh = new_recovery_key()
        with self._lock:
            user = self.user(session.user_id)
            user["recovery"] = self._wrap_recovery(user["id"], dek, fresh)
            self.audit(user, "recovery_key_replaced", ip, save=False)
            self._save()
        return fresh

    # ---- two-factor -------------------------------------------------------- #
    def totp_begin(self, session: Session) -> dict:
        user = self.user(session.user_id)
        if user["totp"].get("enabled"):
            raise AuthError("Two-factor sign-in is already on.", 409)
        secret = totp_new_secret()
        session.pending_totp = secret
        grouped = " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))
        return {"secret": grouped, "uri": otpauth_uri(secret, user["username"])}

    def totp_enable(self, session: Session, code: str, ip: str) -> list[str]:
        secret = session.pending_totp
        if not secret:
            raise AuthError("Start the set-up again.", 409)
        step = totp_match(secret, code, 0)
        if not step:
            raise AuthError("That code didn't match. Make sure your phone's clock is set automatically.", 400)
        dek = self._keys.get(session.user_id)
        if not dek:
            raise AuthError("Session locked. Sign in again.", 401)
        codes = new_backup_codes()
        salt = secrets.token_bytes(16)
        with self._lock:
            user = self.user(session.user_id)
            user["totp"] = {"enabled": True, "secret": seal(dek, secret.encode(), f"totp:{user['id']}"),
                            "lastStep": step, "enabledAt": now_iso()}
            user["backupCodes"] = {"salt": b64e(salt), "hashes": [hash_backup_code(c, salt) for c in codes],
                                   "createdAt": now_iso()}
            session.pending_totp = None
            self.audit(user, "two_factor_enabled", ip, save=False)
            self._save()
        return codes

    def totp_disable(self, session: Session, password: str, code: str, ip: str):
        dek = self.verify_password(session, password, ip)
        user = self.user(session.user_id)
        if not user["totp"].get("enabled"):
            return
        if not self._check_second_factor(user, dek, code):
            self._record_failure(user["username"], ip, user, "wrong 2FA code (disable)")
            raise AuthError("That authenticator or backup code didn't work.", 403)
        with self._lock:
            user["totp"] = {"enabled": False, "secret": None, "lastStep": 0}
            user["backupCodes"] = {"salt": "", "hashes": []}
            self.audit(user, "two_factor_disabled", ip, save=False)
            self._save()

    def regenerate_backup_codes(self, session: Session, password: str, ip: str) -> list[str]:
        self.verify_password(session, password, ip)
        user = self.user(session.user_id)
        if not user["totp"].get("enabled"):
            raise AuthError("Turn on two-factor sign-in first.", 409)
        codes = new_backup_codes()
        salt = secrets.token_bytes(16)
        with self._lock:
            user["backupCodes"] = {"salt": b64e(salt), "hashes": [hash_backup_code(c, salt) for c in codes],
                                   "createdAt": now_iso()}
            self.audit(user, "backup_codes_regenerated", ip, save=False)
            self._save()
        return codes

    # ---- account summary --------------------------------------------------- #
    def profile(self, session: Session) -> dict:
        user = self.user(session.user_id)
        return {
            "username": user["username"],
            "role": user.get("role", "member"),
            "createdAt": user.get("createdAt"),
            "passwordChangedAt": user.get("passwordChangedAt"),
            "twoFactor": bool(user["totp"].get("enabled")),
            "twoFactorSince": user["totp"].get("enabledAt"),
            "backupCodesLeft": len((user.get("backupCodes") or {}).get("hashes") or []),
            "csrf": session.csrf,
            "sessionExpiresAt": datetime.fromtimestamp(session.expires, timezone.utc).isoformat(timespec="seconds"),
            "remember": session.remember,
            "idleMinutes": None if session.remember else IDLE_MINUTES,
            "allowSignup": bool(self._db["settings"].get("allowSignup")),
        }

    def activity(self, user_id: str) -> list[dict]:
        return list((self.user(user_id) or {}).get("audit") or [])

    # ---- encrypted vault --------------------------------------------------- #
    def _vault_path(self, user_id: str) -> Path:
        return self.user_dir(self._db["users"][user_id]) / "vault.dat"

    def vault_lock(self, user_id: str) -> threading.Lock:
        with self._lock:
            return self._vault_locks.setdefault(user_id, threading.Lock())

    def read_vault(self, user_id: str) -> dict:
        dek = self._keys.get(user_id)
        if not dek:
            raise AuthError("Vault is locked. Sign in again.", 401)
        path = self._vault_path(user_id)
        if not path.exists():
            return {}
        envelope = json.loads(path.read_text(encoding="utf-8"))
        return json.loads(unseal(dek, envelope["data"], f"vault:{user_id}").decode("utf-8"))

    def write_vault(self, user_id: str, data: dict):
        dek = self._keys.get(user_id)
        if not dek:
            raise AuthError("Vault is locked. Sign in again.", 401)
        envelope = {"format": "market-tracker-vault", "version": 1, "cipher": "hmac-sha256-ctr+hmac-sha256",
                    "updatedAt": now_iso(),
                    "data": seal(dek, json.dumps(data, separators=(",", ":")).encode("utf-8"), f"vault:{user_id}")}
        self._write_private(self._vault_path(user_id), json.dumps(envelope))

    def update_vault(self, user_id: str, mutate):
        """Read-modify-write under a per-user lock. `mutate(data)` edits in place and may return a value."""
        with self.vault_lock(user_id):
            data = self.read_vault(user_id)
            result = mutate(data)
            self.write_vault(user_id, data)
            return result
