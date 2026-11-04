"""Single-user password login: scrypt hash from the environment, server-side sessions in SQLite."""

import hashlib
import hmac
import logging
import os
import secrets
import threading
import time
from urllib.parse import urlsplit

from fastapi import Request

from app.db import connect

log = logging.getLogger("zeit.auth")

COOKIE = "zeit_session"
SESSION_SECONDS = 30 * 24 * 3600
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Reachable without a session. Everything else (including unknown paths) needs one.
PUBLIC_PATHS = {"/", "/login", "/healthz", "/version.txt", "/robots.txt", "/api/v1/auth/login"}
PUBLIC_STATIC = {"/static/style.css", "/static/theme.js", "/static/icon.svg", "/static/login.js"}

# scrypt parameters for new hashes. Format (no "$"): scrypt:<N>:<r>:<p>:<salt hex>:<hash hex>
_N, _R, _P, _LEN = 2**14, 8, 1, 32


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int, length: int) -> bytes:
    return hashlib.scrypt(
        password.encode(), salt=salt, n=n, r=r, p=p, dklen=length, maxmem=256 * n * r + 2**20
    )


def hash_password(password: str, n: int = _N) -> str:
    salt = secrets.token_bytes(16)
    return f"scrypt:{n}:{_R}:{_P}:{salt.hex()}:{_scrypt(password, salt, n, _R, _P, _LEN).hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check. False for a missing or malformed hash (fail closed)."""
    try:
        scheme, n, r, p, salt, digest = stored.strip().split(":")
        if scheme != "scrypt":
            raise ValueError("scheme")
        n, r, p = int(n), int(r), int(p)
        if not (2 <= n <= 2**20 and n & (n - 1) == 0 and 1 <= r <= 32 and 1 <= p <= 16):
            raise ValueError("params")
        expected = bytes.fromhex(digest)
        actual = _scrypt(password, bytes.fromhex(salt), n, r, p, len(expected))
    except (ValueError, TypeError, MemoryError):
        log.warning("ZEIT_PASSWORD_HASH is missing or malformed: logins are rejected")
        return False
    return hmac.compare_digest(actual, expected)


class Busy(Exception):
    """All hash slots are taken; the caller answers 429 without hashing."""


# Each scrypt call needs 16 MiB and the container has 256 MiB: never more than 2 at once.
MAX_HASHES = 2
BUSY_RETRY_AFTER = 2
_hash_slots = threading.BoundedSemaphore(MAX_HASHES)


def check_login(username: str, password: str) -> bool:
    """Raises Busy (before any hashing) when MAX_HASHES hashes are already running.

    The hash always runs, also for a wrong or unconfigured username, so the answer time does not
    reveal the username. Missing ZEIT_USERNAME or ZEIT_PASSWORD_HASH: always False (fail closed).
    """
    if not _hash_slots.acquire(blocking=False):
        raise Busy
    try:
        expected_user = os.environ.get("ZEIT_USERNAME", "").strip()
        stored = os.environ.get("ZEIT_PASSWORD_HASH", "").strip()
        if stored:
            password_ok = verify_password(password, stored)
        else:
            # Still burn comparable time, so a missing hash is not distinguishable by timing.
            _scrypt(password, b"\0" * 16, _N, _R, _P, _LEN)
            password_ok = False
        user_ok = hmac.compare_digest(username.encode(), expected_user.encode()) and bool(expected_user)
        return user_ok and password_ok
    finally:
        _hash_slots.release()


def cookie_secure() -> bool:
    return os.environ.get("ZEIT_COOKIE_SECURE", "1").strip().lower() not in {"0", "false", "no", "off"}


def client_ip(request: Request) -> str:
    """Behind Traefik the last X-Forwarded-For entry is the one the proxy appended (not spoofable)."""
    forwarded = request.headers.get("x-forwarded-for", "")
    last = forwarded.split(",")[-1].strip()
    if last:
        return last
    return request.client.host if request.client else "unknown"


class Throttle:
    """Per-IP growing wait after failed logins: 3 free tries, then 5 s, 10 s, 20 s ... capped at 15 min.

    Only the offending IP waits, and the state is forgotten an hour after its last failure, so an
    attacker cannot lock the owner out for good. Kept in memory: a restart resets it.
    """

    FREE, BASE, CAP, FORGET, MAX_IPS = 3, 5, 900, 3600, 10_000

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, tuple[int, float, float]] = {}  # ip -> (fails, locked_until, last_fail)

    def wait_seconds(self, ip: str) -> int:
        with self._lock:
            entry = self._state.get(ip)
            remaining = entry[1] - time.monotonic() if entry else 0
            return int(remaining) + 1 if remaining > 0 else 0

    def begin(self, ip: str) -> int:
        """Atomically: seconds to wait (nothing counted), or 0 after counting one attempt for this IP.

        Counting before the hash means parallel requests cannot all slip past the lock. A successful
        login takes it back via success(), a request rejected as busy via undo().
        """
        with self._lock:
            entry = self._state.get(ip)
            remaining = entry[1] - time.monotonic() if entry else 0
            if remaining > 0:
                return int(remaining) + 1
            self._count(ip)
            return 0

    def undo(self, ip: str) -> None:
        with self._lock:
            entry = self._state.get(ip)
            if not entry:
                return
            fails, _, last = entry
            if fails <= 1:
                del self._state[ip]
            else:
                self._state[ip] = (fails - 1, 0.0, last)

    def failure(self, ip: str) -> None:
        with self._lock:
            self._count(ip)

    def _count(self, ip: str) -> None:
        """Caller holds the lock."""
        now = time.monotonic()
        if len(self._state) >= self.MAX_IPS:
            self._state = {k: v for k, v in self._state.items() if now - v[2] < self.FORGET}
        fails, _, last = self._state.get(ip, (0, 0.0, now))
        fails = 1 if now - last >= self.FORGET else fails + 1
        delay = 0 if fails <= self.FREE else min(self.BASE * 2 ** (fails - self.FREE - 1), self.CAP)
        self._state[ip] = (fails, now + delay, now)

    def success(self, ip: str) -> None:
        with self._lock:
            self._state.pop(ip, None)

    def reset(self) -> None:
        with self._lock:
            self._state.clear()


throttle = Throttle()


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(now: int) -> str:
    token = secrets.token_urlsafe(32)
    conn = connect()
    try:
        conn.execute("DELETE FROM sessions WHERE expires_ts <= ?", (now,))
        conn.execute(
            "INSERT INTO sessions (token_hash, created_ts, expires_ts) VALUES (?, ?, ?)",
            (_digest(token), now, now + SESSION_SECONDS),
        )
    finally:
        conn.close()
    return token


def session_valid(token: str | None, now: int) -> bool:
    if not token:
        return False
    conn = connect()
    try:
        row = conn.execute(
            "SELECT expires_ts FROM sessions WHERE token_hash = ?", (_digest(token),)
        ).fetchone()
    finally:
        conn.close()
    return bool(row) and row["expires_ts"] > now


def delete_session(token: str | None) -> None:
    if not token:
        return
    conn = connect()
    try:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_digest(token),))
    finally:
        conn.close()


def origin_ok(request: Request) -> bool:
    """Writing requests must come from our own origin (Origin host == Host). Missing Origin is refused."""
    origin = request.headers.get("origin")
    if not origin or origin == "null":
        return False
    return urlsplit(origin).netloc.lower() == request.headers.get("host", "").lower()


def is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path in PUBLIC_STATIC
