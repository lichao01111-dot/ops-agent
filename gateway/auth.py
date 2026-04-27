"""
Minimal HMAC-token auth for the JARVIS gateway.

Scope: dev / staging single-tenant. Token is a signed JSON payload, not a
session id, so the server stays stateless. Production should swap this
for SSO/OIDC.

Token shape (after base64url decode):
    {
      "sub": "<user_id>",
      "username": "<username>",
      "role": "<viewer|operator|admin>",
      "iat": <unix>,
      "exp": <unix>,
      "sig": "<hex hmac-sha256(secret, header.payload)>",
    }

Implementation deliberately tiny — no PyJWT dependency.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Optional

from config import settings


# Demo accounts. Replace with a real user store / SSO in production.
# Password is plain SHA256 — fine for dev, NOT a real auth system.
_DEMO_USERS: dict[str, dict[str, str]] = {
    "admin": {
        "user_id": "u-admin",
        "password_sha256": hashlib.sha256(b"admin").hexdigest(),
        "role": "admin",
        "display_name": "Admin",
    },
    "operator": {
        "user_id": "u-operator",
        "password_sha256": hashlib.sha256(b"operator").hexdigest(),
        "role": "operator",
        "display_name": "Operator",
    },
    "viewer": {
        "user_id": "u-viewer",
        "password_sha256": hashlib.sha256(b"viewer").hexdigest(),
        "role": "viewer",
        "display_name": "Viewer",
    },
}

DEFAULT_TTL_S = 8 * 3600  # 8 hours


@dataclass(frozen=True)
class AuthIdentity:
    user_id: str
    username: str
    role: str


def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64u_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _sign(payload: bytes) -> str:
    return hmac.new(
        settings.secret_key.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()


def authenticate(username: str, password: str) -> Optional[AuthIdentity]:
    user = _DEMO_USERS.get(username)
    if not user:
        return None
    if hashlib.sha256(password.encode("utf-8")).hexdigest() != user["password_sha256"]:
        return None
    return AuthIdentity(
        user_id=user["user_id"],
        username=username,
        role=user["role"],
    )


def issue_token(identity: AuthIdentity, *, ttl_s: int = DEFAULT_TTL_S) -> str:
    now = int(time.time())
    body = {
        "sub": identity.user_id,
        "username": identity.username,
        "role": identity.role,
        "iat": now,
        "exp": now + ttl_s,
    }
    body_bytes = json.dumps(body, separators=(",", ":")).encode("utf-8")
    body_b64 = _b64u_encode(body_bytes)
    sig = _sign(body_b64.encode("ascii"))
    return f"{body_b64}.{sig}"


def verify_token(token: str) -> Optional[AuthIdentity]:
    if not token or "." not in token:
        return None
    body_b64, sig = token.rsplit(".", 1)
    expected = _sign(body_b64.encode("ascii"))
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        body = json.loads(_b64u_decode(body_b64))
    except Exception:
        return None
    if int(body.get("exp", 0)) < int(time.time()):
        return None
    return AuthIdentity(
        user_id=body.get("sub", ""),
        username=body.get("username", ""),
        role=body.get("role", "viewer"),
    )


def display_name_for(username: str) -> str:
    return _DEMO_USERS.get(username, {}).get("display_name", username)
