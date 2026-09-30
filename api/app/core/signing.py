"""
Short, signed, expiring tokens (HMAC-SHA256 over the server's SECRET_KEY).

Used for protected previews: the one-time code handed to a preview host and
the cookie it sets. Each use has its own purpose string, so a token minted
for one purpose never verifies as another.
"""

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Dict, Optional

from app.config import settings


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mac(purpose: str, body: str) -> str:
    key = hashlib.sha256(f"{purpose}:{settings.secret_key}".encode()).digest()
    return _b64(hmac.new(key, body.encode(), hashlib.sha256).digest())


def sign(purpose: str, payload: Dict[str, Any], ttl_seconds: int) -> str:
    body = _b64(json.dumps({**payload, "exp": int(time.time()) + ttl_seconds}, separators=(",", ":")).encode())
    return f"{body}.{_mac(purpose, body)}"


def unsign(purpose: str, token: Optional[str]) -> Optional[Dict[str, Any]]:
    """The payload if the token is genuine, for this purpose, and unexpired; else None."""
    if not token or token.count(".") != 1:
        return None
    body, mac = token.split(".")
    if not hmac.compare_digest(mac, _mac(purpose, body)):
        return None
    try:
        payload = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("exp", 0) < time.time():
        return None
    return payload


def digest(purpose: str, value: str) -> str:
    """A stable keyed digest, e.g. the readiness probe's header for one host."""
    return _mac(purpose, value)
