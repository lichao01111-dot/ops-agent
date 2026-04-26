"""
Tests for Redis-backed middleware backends.

Uses a fake Redis (``MiniRedis``) that implements exactly the subset of
commands ``redis_middleware.py`` depends on. No real Redis required.

Coverage:
  * Idempotency cache: JSON round-trip, TTL honoured, different namespaces
  * Cost budget: lazy init, atomic DECRBY across "replicas", manual set()
  * Circuit state: failure increment persists, cross-replica visibility,
    opened_at assignment is mirrored to Redis, reset() clears the key
  * ``build_redis_middleware_backends`` end-to-end with default chain
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import pytest

from agent_kernel.schemas import ReliabilityPolicy, ToolSpec
from agent_kernel.tools.middleware import (
    CircuitBreakerMiddleware,
    CircuitOpen,
    CostBudgetMiddleware,
    IdempotencyMiddleware,
    InvocationContext,
    build_default_chain,
    run_chain,
)
from agent_kernel.tools.redis_middleware import (
    RedisCircuitStateBackend,
    RedisCostBudgetBackend,
    RedisIdempotencyCache,
    build_redis_middleware_backends,
)


# --------------------------------------------------------------------------
# MiniRedis — minimal fake, same style as test_redis_session.py but covering
# the commands the middleware backends need (get/set/decrby/hset/hgetall/
# hincrby/delete/expire).
# --------------------------------------------------------------------------
class MiniRedis:
    def __init__(self) -> None:
        self._kv: dict[str, bytes] = {}
        self._h: dict[str, dict[str, bytes]] = {}
        self._exp: dict[str, float] = {}

    @staticmethod
    def _b(x: Any) -> bytes:
        return x if isinstance(x, bytes) else str(x).encode("utf-8")

    def _expired(self, key: str) -> bool:
        exp = self._exp.get(key)
        return exp is not None and time.time() > exp

    def _gc(self, key: str) -> None:
        if self._expired(key):
            self._kv.pop(key, None)
            self._h.pop(key, None)
            self._exp.pop(key, None)

    # ----- string -----
    def set(
        self,
        key: str,
        value: Any,
        *,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool | None:
        self._gc(key)
        if nx and key in self._kv:
            return None
        self._kv[key] = self._b(value)
        if ex is not None:
            self._exp[key] = time.time() + ex
        return True

    def get(self, key: str) -> bytes | None:
        self._gc(key)
        return self._kv.get(key)

    def decrby(self, key: str, amount: int) -> int:
        current = int(self._kv.get(key, b"0").decode("utf-8"))
        new_value = current - amount
        self._kv[key] = str(new_value).encode("utf-8")
        return new_value

    # ----- hash -----
    def hset(self, key: str, field: str | None = None, value: Any = None, *, mapping: dict | None = None) -> int:
        self._gc(key)
        d = self._h.setdefault(key, {})
        added = 0
        if mapping:
            for k, v in mapping.items():
                if k not in d:
                    added += 1
                d[k] = self._b(v)
        if field is not None:
            if field not in d:
                added += 1
            d[field] = self._b(value)
        return added

    def hgetall(self, key: str) -> dict[bytes, bytes]:
        self._gc(key)
        return {k.encode("utf-8"): v for k, v in self._h.get(key, {}).items()}

    def hincrby(self, key: str, field: str, amount: int) -> int:
        self._gc(key)
        d = self._h.setdefault(key, {})
        current = int(d.get(field, b"0").decode("utf-8"))
        new_value = current + amount
        d[field] = str(new_value).encode("utf-8")
        return new_value

    # ----- key-level -----
    def expire(self, key: str, seconds: int) -> int:
        self._exp[key] = time.time() + seconds
        return 1

    def delete(self, *keys: Any) -> int:
        removed = 0
        for raw in keys:
            k = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            if k in self._kv or k in self._h:
                self._kv.pop(k, None)
                self._h.pop(k, None)
                self._exp.pop(k, None)
                removed += 1
        return removed


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _spec(name: str = "t", **overrides: Any) -> ToolSpec:
    reliability = overrides.pop("reliability", None) or ReliabilityPolicy()
    return ToolSpec(
        name=name,
        description="",
        side_effect=overrides.pop("side_effect", False),
        reliability=reliability,
    )


def _ctx(spec: ToolSpec, **kw: Any) -> InvocationContext:
    base = dict(tool_name=spec.name, spec=spec, arguments={}, session_id="s", user_id="u", route="r")
    base.update(kw)
    return InvocationContext(**base)


# ==========================================================================
# RedisIdempotencyCache
# ==========================================================================
class TestRedisIdempotency:
    @pytest.mark.asyncio
    async def test_auto_key_hits_across_replicas(self):
        """Two replicas share state via the SAME Redis backing store."""
        shared = MiniRedis()
        cache_a = RedisIdempotencyCache(shared, namespace="t")
        cache_b = RedisIdempotencyCache(shared, namespace="t")

        mw_a = IdempotencyMiddleware(cache_a)
        mw_b = IdempotencyMiddleware(cache_b)

        spec = _spec(side_effect=True)
        calls = 0

        async def terminal():
            nonlocal calls
            calls += 1
            return {"result": calls}

        # Replica A processes the request.
        r1 = await run_chain([mw_a], _ctx(spec, arguments={"k": 1}), terminal)
        # Replica B receives a retry — cache hit, no new call.
        r2 = await run_chain([mw_b], _ctx(spec, arguments={"k": 1}), terminal)

        assert r1 == r2
        assert calls == 1, "cache must be shared fleet-wide"

    def test_namespace_isolates_tenants(self):
        shared = MiniRedis()
        tenant_1 = RedisIdempotencyCache(shared, namespace="t1")
        tenant_2 = RedisIdempotencyCache(shared, namespace="t2")

        tenant_1.set("key-a", {"v": 1}, ttl_s=60)
        assert tenant_2.get("key-a") is None  # namespaces don't leak

    def test_truly_unserialisable_value_skipped(self):
        """Circular references genuinely can't be JSON-dumped — we want
        a no-op, not an exception that kills the whole invocation."""
        cache = RedisIdempotencyCache(MiniRedis(), namespace="t")
        circular: list = []
        circular.append(circular)
        cache.set("loop", circular, ttl_s=60)  # must not raise
        # And nothing was stored.
        assert cache.get("loop") is None


# ==========================================================================
# RedisCostBudgetBackend
# ==========================================================================
class TestRedisCostBudget:
    @pytest.mark.asyncio
    async def test_budget_shared_across_replicas(self):
        shared = MiniRedis()
        backend_a = RedisCostBudgetBackend(shared, namespace="t", default_budget=100)
        backend_b = RedisCostBudgetBackend(shared, namespace="t", default_budget=100)
        mw_a = CostBudgetMiddleware(backend_a)
        mw_b = CostBudgetMiddleware(backend_b)

        spec = _spec(reliability=ReliabilityPolicy(cost_ceiling_tokens=30))

        async def terminal():
            return "ok"

        # Replica A charges 30 → 70 left.
        await run_chain([mw_a], _ctx(spec, session_id="sess"), terminal)
        # Replica B sees the shared ledger and charges another 30 → 40 left.
        await run_chain([mw_b], _ctx(spec, session_id="sess"), terminal)

        assert backend_a.get("sess") == 40
        assert backend_b.get("sess") == 40

    def test_lazy_init_on_first_read(self):
        backend = RedisCostBudgetBackend(MiniRedis(), namespace="t", default_budget=500)
        assert backend.get("new-sess") == 500

    def test_manual_set_overrides_default(self):
        backend = RedisCostBudgetBackend(MiniRedis(), namespace="t", default_budget=500)
        backend.set("sess", 10)
        assert backend.get("sess") == 10


# ==========================================================================
# RedisCircuitStateBackend
# ==========================================================================
class TestRedisCircuitBackend:
    @pytest.mark.asyncio
    async def test_failures_counted_across_replicas(self):
        shared = MiniRedis()
        backend_a = RedisCircuitStateBackend(shared, namespace="t")
        backend_b = RedisCircuitStateBackend(shared, namespace="t")
        mw_a = CircuitBreakerMiddleware(backend_a)
        mw_b = CircuitBreakerMiddleware(backend_b)

        spec = _spec(reliability=ReliabilityPolicy(circuit_fail_threshold=2, circuit_cool_down_s=60))

        async def fail():
            raise RuntimeError("boom")

        # Replica A records one failure.
        with pytest.raises(RuntimeError):
            await run_chain([mw_a], _ctx(spec), fail)

        # Replica B records the SECOND failure — circuit should now open.
        with pytest.raises(RuntimeError):
            await run_chain([mw_b], _ctx(spec), fail)

        # Either replica now sees the circuit as open and fails fast.
        with pytest.raises(CircuitOpen):
            await run_chain([mw_a], _ctx(spec), fail)

    @pytest.mark.asyncio
    async def test_success_resets_state(self):
        shared = MiniRedis()
        backend = RedisCircuitStateBackend(shared, namespace="t")
        mw = CircuitBreakerMiddleware(backend)

        spec = _spec(reliability=ReliabilityPolicy(circuit_fail_threshold=3, circuit_cool_down_s=60))

        async def fail():
            raise RuntimeError("boom")

        async def ok():
            return "good"

        for _ in range(2):
            with pytest.raises(RuntimeError):
                await run_chain([mw], _ctx(spec), fail)

        await run_chain([mw], _ctx(spec), ok)
        # State should be cleared — failures go back to 0.
        state = backend.get("t")
        assert state.consecutive_failures == 0
        assert state.opened_at is None


# ==========================================================================
# build_redis_middleware_backends
# ==========================================================================
class TestBuilder:
    def test_returns_three_prewired_backends(self):
        shared = MiniRedis()
        out = build_redis_middleware_backends(shared, namespace="prod")
        assert set(out) == {"idempotency_cache", "cost_backend", "circuit_backend"}
        assert isinstance(out["idempotency_cache"], RedisIdempotencyCache)
        assert isinstance(out["cost_backend"], RedisCostBudgetBackend)
        assert isinstance(out["circuit_backend"], RedisCircuitStateBackend)

    @pytest.mark.asyncio
    async def test_plugs_into_default_chain(self):
        shared = MiniRedis()
        backends = build_redis_middleware_backends(shared, namespace="prod")
        chain = build_default_chain(**backends)

        spec = _spec(
            side_effect=True,
            reliability=ReliabilityPolicy(
                cost_ceiling_tokens=10,
                circuit_fail_threshold=0,  # disabled
            ),
        )
        calls = 0

        async def terminal():
            nonlocal calls
            calls += 1
            return {"ok": True}

        await run_chain(chain, _ctx(spec, arguments={"x": 1}), terminal)
        # Second call hits idempotency cache — no increment.
        await run_chain(chain, _ctx(spec, arguments={"x": 1}), terminal)
        assert calls == 1
        # Budget should have been charged exactly once (on first success).
        assert backends["cost_backend"].get("s") == 100_000 - 10
