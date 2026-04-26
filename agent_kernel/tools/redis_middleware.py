"""
Redis-backed implementations of the three pluggable middleware backends:

* :class:`RedisIdempotencyCache`   (IdempotencyCache)
* :class:`RedisCostBudgetBackend`  (CostBudgetBackend)
* :class:`RedisCircuitStateBackend` (CircuitStateBackend)

These are necessary in production because each of the three pieces of
state — "have I already processed this idempotency key", "how much of
this session's budget is left", "is the circuit for tool X open" —
**must be shared across replicas**. In-process dicts mean replica A
accepts a retry that replica B would have rejected, which defeats the
point of the middleware.

Design choices
--------------
* Redis client is injected, not constructed, so tests can use a fake
  (see ``tests/test_redis_middleware.py``'s ``MiniRedis``) and production
  code can share an existing pool.
* All keys carry a ``namespace:`` prefix so multiple agents/stacks can
  coexist in one Redis.
* Serialization is deliberately JSON text, not pickle: payloads are
  cheap (tool results are already strings) and JSON is safe to share
  across Python versions and languages.
* TTLs are applied via ``EXPIRE`` — Redis will drop stale state on its
  own. No background sweeper needed.

The module is optional: ``agent_kernel.tools.middleware`` never imports
it, so environments without redis-py keep working.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from agent_kernel.tools.middleware import (
    CircuitStateBackend,
    CostBudgetBackend,
    IdempotencyCache,
    _CircuitState,
)


# --------------------------------------------------------------------------
# IdempotencyCache
# --------------------------------------------------------------------------
@dataclass
class RedisIdempotencyCache(IdempotencyCache):
    """Redis-backed short-circuit cache.

    Stores the cached result as JSON text under ``{ns}:idem:{key}`` with a
    TTL set by the middleware (default 5 min). On a cache hit the SAME
    value is returned to any replica — double-fires from retries are
    prevented fleet-wide, not just within one process.
    """

    redis: Any
    namespace: str = "jarvis"

    def _key(self, raw: str) -> str:
        return f"{self.namespace}:idem:{raw}"

    def get(self, key: str) -> Any | None:
        blob = self.redis.get(self._key(key))
        if blob is None:
            return None
        if isinstance(blob, bytes):
            blob = blob.decode("utf-8")
        try:
            return json.loads(blob)
        except (TypeError, ValueError):
            return None

    def set(self, key: str, value: Any, ttl_s: int) -> None:
        try:
            blob = json.dumps(value, default=str)
        except (TypeError, ValueError):
            # Caller's result isn't JSON-serialisable → skip caching rather
            # than blow up the invocation. Better to replay the call than
            # crash on a weird return type.
            return
        self.redis.set(self._key(key), blob, ex=ttl_s)


# --------------------------------------------------------------------------
# CostBudgetBackend
# --------------------------------------------------------------------------
@dataclass
class RedisCostBudgetBackend(CostBudgetBackend):
    """Redis-backed session budget ledger.

    Uses ``SET key value NX`` to lazily initialise a session's budget on
    first read, then ``DECRBY`` for atomic charges. Two replicas charging
    the same session at the same instant cannot overshoot past ``0``
    because ``DECRBY`` is atomic on the Redis side.

    Session budgets expire after ``session_ttl_s`` of inactivity — which
    matches the RedisSessionStore TTL so we don't leak ledgers.
    """

    redis: Any
    namespace: str = "jarvis"
    default_budget: int = 100_000
    session_ttl_s: int = 7 * 24 * 3600

    def _key(self, session_id: str) -> str:
        return f"{self.namespace}:cost:{session_id}"

    def get(self, session_id: str) -> int:
        key = self._key(session_id)
        val = self.redis.get(key)
        if val is None:
            # SET NX ensures one replica wins the initialisation race.
            self.redis.set(key, self.default_budget, ex=self.session_ttl_s, nx=True)
            val = self.redis.get(key)
        if val is None:
            return 0
        if isinstance(val, bytes):
            val = val.decode("utf-8")
        try:
            return int(val)
        except (TypeError, ValueError):
            return 0

    def deduct(self, session_id: str, amount: int) -> int:
        self.get(session_id)  # initialise if absent
        key = self._key(session_id)
        remaining = self.redis.decrby(key, amount)
        # Refresh idle TTL so active sessions don't expire mid-flight.
        self.redis.expire(key, self.session_ttl_s)
        if isinstance(remaining, bytes):
            remaining = int(remaining.decode("utf-8"))
        return int(remaining)

    def set(self, session_id: str, total: int) -> None:
        self.redis.set(self._key(session_id), total, ex=self.session_ttl_s)


# --------------------------------------------------------------------------
# CircuitStateBackend
# --------------------------------------------------------------------------
@dataclass
class RedisCircuitStateBackend(CircuitStateBackend):
    """Redis-backed circuit state, keyed by tool name and shared fleet-wide.

    Persistence layout for tool ``T``:

        {ns}:circuit:T     HASH  { failures: int, opened_at: float_ts | "" }

    ``record_failure`` uses ``HINCRBY`` for atomic increment; the cool-down
    open transition is recorded by the middleware setting ``opened_at``
    (we persist it back here via ``_persist``).

    An inactive tool's state expires after ``idle_ttl_s`` so circuits
    silently recover after long downtime windows.
    """

    redis: Any
    namespace: str = "jarvis"
    idle_ttl_s: int = 24 * 3600

    def _key(self, tool_name: str) -> str:
        return f"{self.namespace}:circuit:{tool_name}"

    def get(self, tool_name: str) -> _CircuitState:
        data = self.redis.hgetall(self._key(tool_name))
        if not data:
            return _CircuitState()
        decoded = {
            (k.decode("utf-8") if isinstance(k, bytes) else k):
            (v.decode("utf-8") if isinstance(v, bytes) else v)
            for k, v in data.items()
        }
        failures = int(decoded.get("failures", "0") or 0)
        opened_ts = decoded.get("opened_at", "")
        opened_at = datetime.fromtimestamp(float(opened_ts)) if opened_ts else None
        state = _CircuitState(consecutive_failures=failures, opened_at=opened_at)
        # Return a proxy whose attribute writes are persisted back — that
        # lets the middleware still do ``state.opened_at = now`` without
        # caring about the backend.
        return _PersistingCircuitState._from(self, tool_name, state)

    def record_failure(self, tool_name: str, *, now: datetime) -> _CircuitState:
        key = self._key(tool_name)
        new_failures = self.redis.hincrby(key, "failures", 1)
        if isinstance(new_failures, bytes):
            new_failures = int(new_failures.decode("utf-8"))
        self.redis.expire(key, self.idle_ttl_s)
        state = _CircuitState(consecutive_failures=int(new_failures))
        return _PersistingCircuitState._from(self, tool_name, state)

    def reset(self, tool_name: str) -> None:
        self.redis.delete(self._key(tool_name))

    # Internal hook used by _PersistingCircuitState.
    def _persist(self, tool_name: str, state: _CircuitState) -> None:
        key = self._key(tool_name)
        mapping = {
            "failures": str(state.consecutive_failures),
            "opened_at": str(state.opened_at.timestamp()) if state.opened_at else "",
        }
        self.redis.hset(key, mapping=mapping)
        self.redis.expire(key, self.idle_ttl_s)


class _PersistingCircuitState(_CircuitState):
    """_CircuitState subclass that writes attribute changes through to Redis.

    Middleware does ``state.opened_at = now`` to record the open
    transition; without this wrapper that assignment would only mutate
    the in-memory copy, losing the transition across replicas.
    """

    __slots__ = ("_backend", "_tool")

    def __init__(self, consecutive_failures: int = 0, opened_at: datetime | None = None) -> None:
        super().__init__(consecutive_failures=consecutive_failures, opened_at=opened_at)
        self._backend: RedisCircuitStateBackend | None = None
        self._tool: str = ""

    @classmethod
    def _from(
        cls,
        backend: "RedisCircuitStateBackend",
        tool: str,
        source: _CircuitState,
    ) -> "_PersistingCircuitState":
        wrapped = cls(
            consecutive_failures=source.consecutive_failures,
            opened_at=source.opened_at,
        )
        wrapped._backend = backend
        wrapped._tool = tool
        return wrapped

    def __setattr__(self, name: str, value: Any) -> None:
        super().__setattr__(name, value)
        if name in {"consecutive_failures", "opened_at"} and getattr(self, "_backend", None):
            self._backend._persist(self._tool, self)


# --------------------------------------------------------------------------
# Convenience builder
# --------------------------------------------------------------------------
def build_redis_middleware_backends(
    redis_client: Any,
    *,
    namespace: str = "jarvis",
) -> dict[str, Any]:
    """Return the three backends pre-wired to a shared Redis client.

    Plug the return value into ``build_default_chain(...)``::

        backends = build_redis_middleware_backends(redis_client)
        chain = build_default_chain(**backends)
    """
    return {
        "idempotency_cache": RedisIdempotencyCache(redis_client, namespace=namespace),
        "cost_backend": RedisCostBudgetBackend(redis_client, namespace=namespace),
        "circuit_backend": RedisCircuitStateBackend(redis_client, namespace=namespace),
    }
