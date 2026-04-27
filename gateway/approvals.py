"""
Approval-decision endpoint helpers.

When the agent emits an SSE event signalling that a step needs approval,
the frontend calls /api/approval/decision. This module:

  1. Generates a HMAC-signed approval receipt (compatible with
     ``ApprovalPolicy.resolve_receipt``).
  2. Maintains an in-process registry of "pending requests" so the
     gateway can validate that the request_id referenced by the frontend
     was actually issued by us (anti-forgery).

Receipt format matches ``agent_kernel.schemas.ApprovalReceipt`` after
``model_dump()``: a JSON-able dict the next chat turn passes back via
``ChatRequest.context["approval_receipt"]``.
"""
from __future__ import annotations

import hashlib
import hmac
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from config import settings


DEFAULT_RECEIPT_TTL_S = 30 * 60  # 30 min


@dataclass
class PendingApproval:
    request_id: str
    session_id: str
    user_id: str
    step_id: str
    action: str
    risk_level: str
    payload: dict
    created_at: float = field(default_factory=time.time)
    decided: bool = False


class ApprovalRegistry:
    """In-process pending approvals. Replace with Redis for multi-replica."""

    def __init__(self, ttl_s: int = 3600) -> None:
        self._items: dict[str, PendingApproval] = {}
        self._ttl_s = ttl_s
        self._lock = threading.RLock()

    def issue(
        self,
        *,
        session_id: str,
        user_id: str,
        step_id: str,
        action: str,
        risk_level: str,
        payload: dict,
    ) -> PendingApproval:
        rid = f"appr-{uuid.uuid4().hex[:12]}"
        item = PendingApproval(
            request_id=rid,
            session_id=session_id,
            user_id=user_id,
            step_id=step_id,
            action=action,
            risk_level=risk_level,
            payload=payload,
        )
        with self._lock:
            self._gc_locked()
            self._items[rid] = item
        return item

    def get(self, request_id: str) -> Optional[PendingApproval]:
        with self._lock:
            self._gc_locked()
            return self._items.get(request_id)

    def mark_decided(self, request_id: str) -> None:
        with self._lock:
            it = self._items.get(request_id)
            if it is not None:
                it.decided = True

    def _gc_locked(self) -> None:
        now = time.time()
        stale = [k for k, v in self._items.items() if now - v.created_at > self._ttl_s]
        for k in stale:
            self._items.pop(k, None)


def sign_receipt(*, step_id: str, approved_by: str, scope: str = "") -> dict:
    """Return a dict compatible with ``ApprovalReceipt(**dict)``.

    ``receipt_id`` is a HMAC over (step_id || approved_by || nonce) so
    even if the in-process registry is wiped (process restart), an
    attacker cannot forge one without the secret.
    """
    nonce = uuid.uuid4().hex
    body = f"{step_id}|{approved_by}|{nonce}".encode("utf-8")
    sig = hmac.new(
        settings.secret_key.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    receipt_id = f"rcpt-{nonce}-{sig[:16]}"
    return {
        "receipt_id": receipt_id,
        "step_id": step_id,
        "approved_by": approved_by,
        "scope": scope,
        "approved_at": datetime.now().isoformat(),
        "expires_at": (datetime.now() + timedelta(seconds=DEFAULT_RECEIPT_TTL_S)).isoformat(),
    }
