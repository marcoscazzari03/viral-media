"""Login for a single admin: scrypt password hash + optional TOTP code (Google Authenticator), signed session
cookie, brute-force throttling. Stateless: changing the password or PORTAL_SECRET logs every session out."""
import base64
import hashlib
import hmac
import json
import secrets
import struct
import time
from collections import deque

from . import config

COOKIE = "usv_session"


# ------------------------------------------------------------------ password (scrypt, stdlib)
# Format without "$" (docker compose would read it as a variable in .env): scrypt:n:r:p:salt:hash
def hash_password(password: str, n: int = 2 ** 15, r: int = 8, p: int = 1) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=128 * 1024 * 1024, dklen=32)
    b64 = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")  # noqa: E731
    return f"scrypt:{n}:{r}:{p}:{b64(salt)}:{b64(dk)}"


def check_password(password: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt, want = stored.split(":")
        if kind != "scrypt":
            return False
        unb64 = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))  # noqa: E731
        dk = hashlib.scrypt(password.encode(), salt=unb64(salt), n=int(n), r=int(r), p=int(p),
                            maxmem=128 * 1024 * 1024, dklen=32)
        return hmac.compare_digest(dk, unb64(want))
    except (ValueError, TypeError):
        return False


# ------------------------------------------------------------------ TOTP (RFC 6238, 6 digits, 30 s)
def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_at(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    off = digest[-1] & 0x0F
    return f"{(struct.unpack('>I', digest[off:off + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


_used_steps: dict[int, float] = {}


def check_totp(secret: str, code: str) -> bool:
    code = "".join(c for c in code if c.isdigit())
    if len(code) != 6:
        return False
    now = int(time.time() // 30)
    for step in (now - 1, now, now + 1):  # 30 s of clock drift either way
        if hmac.compare_digest(totp_at(secret, step), code):
            if step in _used_steps:  # a code works once
                return False
            _used_steps[step] = time.time()
            for s in [s for s in _used_steps if s < now - 2]:
                del _used_steps[s]
            return True
    return False


# ------------------------------------------------------------------ session cookie
def _key() -> bytes:
    # the password hash is part of the key: a new password invalidates every open session
    return hashlib.sha256((config.PORTAL_SECRET + "|" + config.PORTAL_PASSWORD_HASH).encode()).digest()


def make_session() -> str:
    body = base64.urlsafe_b64encode(json.dumps(
        {"exp": int(time.time()) + config.SESSION_DAYS * 86400, "sid": secrets.token_hex(8)}).encode()).decode()
    return body + "." + hmac.new(_key(), body.encode(), hashlib.sha256).hexdigest()


def valid_session(value: str | None) -> bool:
    if not value or "." not in value or len(config.PORTAL_SECRET) < 32 or not config.PORTAL_PASSWORD_HASH:
        return False
    body, sig = value.rsplit(".", 1)
    if not hmac.compare_digest(hmac.new(_key(), body.encode(), hashlib.sha256).hexdigest(), sig):
        return False
    try:
        return json.loads(base64.urlsafe_b64decode(body))["exp"] > time.time()
    except (ValueError, KeyError, TypeError):
        return False


# ------------------------------------------------------------------ brute-force throttling
WINDOW_S, PER_IP, GLOBAL = 15 * 60, 5, 20
_fails: dict[str, deque] = {}
_all_fails: deque = deque()


def _trim(q: deque) -> None:
    while q and q[0] < time.time() - WINDOW_S:
        q.popleft()


def blocked(ip: str) -> bool:
    q = _fails.setdefault(ip, deque())
    _trim(q)
    _trim(_all_fails)
    return len(q) >= PER_IP or len(_all_fails) >= GLOBAL


def record_failure(ip: str) -> None:
    _fails.setdefault(ip, deque()).append(time.time())
    _all_fails.append(time.time())


def clear_failures(ip: str) -> None:
    _fails.pop(ip, None)
