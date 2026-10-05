"""Password hashing (scrypt) and bearer tokens.

Hashing is CPU-bound; callers run it in a worker thread (hashlib.scrypt releases the
GIL) so it never blocks the event loop.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading

N, R, P = 2 ** 14, 8, 1
MAXMEM = 64 * 1024 * 1024

# Fixture passwords are re-hashed on every reset. Remember the last hash per
# (user id, password) so repeated resets stay fast; the key is an HMAC under a
# per-process secret, so no plaintext is kept.
_cache_secret = secrets.token_bytes(32)
_cache: dict[bytes, str] = {}
_cache_lock = threading.Lock()


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=N, r=R, p=P, maxmem=MAXMEM)
    return f"scrypt${N}${R}${P}${_b64(salt)}${_b64(digest)}"


def hash_fixture_password(user_id: str, password: str) -> str:
    k = hmac.new(_cache_secret, f"{user_id}\0{password}".encode("utf-8"), hashlib.sha256).digest()
    with _cache_lock:
        hit = _cache.get(k)
    if hit is not None:
        return hit
    h = hash_password(password)
    with _cache_lock:
        if len(_cache) > 10000:
            _cache.clear()
        _cache[k] = h
    return h


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        got = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt),
                             n=int(n), r=int(r), p=int(p), maxmem=MAXMEM, dklen=len(expected))
    except Exception:
        return False
    return hmac.compare_digest(got, expected)


_DUMMY = hash_password(secrets.token_hex(8))


def burn_verify() -> None:
    """Equalise timing for unknown emails."""
    verify_password("x", _DUMMY)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
