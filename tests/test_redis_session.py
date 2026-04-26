"""
Tests for RedisSessionStore.

No real Redis required — uses a tiny in-memory fake (``MiniRedis``) that
implements the exact subset of commands RedisSessionStore depends on.
This keeps the test hermetic and fast while still exercising the real
RedisSessionStore code path (serialization, key layout, lifecycle).
"""
from __future__ import annotations

import time
from typing import Any

import pytest
from langchain_core.messages import HumanMessage

from agent_kernel.memory.lifecycle import LayerPolicy, LayerPolicySet, MergeStrategy
from agent_kernel.memory.schema import MemorySchema
from agent_kernel.redis_session import RedisSessionStore
from agent_kernel.schemas import RiskLevel


# --------------------------------------------------------------------------
# Minimal in-memory Redis clone. Covers: hset/hget/hgetall/hdel, rpush/lrange,
# zadd/zrangebyscore/zremrangebyscore/zrem, scan, expire, delete.
# All values stored as bytes to match redis-py's default behaviour.
# --------------------------------------------------------------------------
class MiniRedis:
    def __init__(self) -> None:
        self._h: dict[str, dict[str, bytes]] = {}
        self._l: dict[str, list[bytes]] = {}
        self._z: dict[str, dict[str, float]] = {}

    @staticmethod
    def _b(x: Any) -> bytes:
        if isinstance(x, bytes):
            return x
        return str(x).encode("utf-8")

    # ----- hash -----
    def hset(self, key: str, field: str | None = None, value: Any = None, *, mapping: dict | None = None) -> int:
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

    def hget(self, key: str, field: str) -> bytes | None:
        return self._h.get(key, {}).get(field)

    def hgetall(self, key: str) -> dict[bytes, bytes]:
        return {k.encode("utf-8"): v for k, v in self._h.get(key, {}).items()}

    def hdel(self, key: str, *fields: str) -> int:
        d = self._h.get(key, {})
        removed = 0
        for f in fields:
            if f in d:
                d.pop(f)
                removed += 1
        return removed

    # ----- list -----
    def rpush(self, key: str, *values: Any) -> int:
        lst = self._l.setdefault(key, [])
        for v in values:
            lst.append(self._b(v))
        return len(lst)

    def lrange(self, key: str, start: int, stop: int) -> list[bytes]:
        lst = self._l.get(key, [])
        if stop == -1:
            return lst[start:]
        return lst[start: stop + 1] if stop >= 0 else lst[start:stop + 1 or None]

    # ----- zset -----
    def zadd(self, key: str, mapping: dict[str, float]) -> int:
        d = self._z.setdefault(key, {})
        added = 0
        for k, v in mapping.items():
            if k not in d:
                added += 1
            d[k] = v
        return added

    def zrangebyscore(self, key: str, min_s: Any, max_s: Any) -> list[bytes]:
        d = self._z.get(key, {})
        lo = float("-inf") if min_s == "-inf" else float(min_s)
        hi = float("inf") if max_s == "+inf" else float(max_s)
        return [k.encode("utf-8") for k, s in d.items() if lo <= s <= hi]

    def zremrangebyscore(self, key: str, min_s: Any, max_s: Any) -> int:
        d = self._z.get(key, {})
        lo = float("-inf") if min_s == "-inf" else float(min_s)
        hi = float("inf") if max_s == "+inf" else float(max_s)
        victims = [k for k, s in d.items() if lo <= s <= hi]
        for k in victims:
            d.pop(k)
        return len(victims)

    def zrem(self, key: str, *members: str) -> int:
        d = self._z.get(key, {})
        return sum(1 for m in members if d.pop(m, None) is not None)

    # ----- key -----
    def expire(self, key: str, seconds: int) -> int:
        # No-op for the fake: tests don't rely on redis-side expiry.
        return 1

    def delete(self, *keys: Any) -> int:
        removed = 0
        for raw in keys:
            k = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            if k in self._h or k in self._l or k in self._z:
                self._h.pop(k, None)
                self._l.pop(k, None)
                self._z.pop(k, None)
                removed += 1
        return removed

    def scan(self, cursor: int = 0, *, match: str = "*", count: int = 100) -> tuple[int, list[bytes]]:
        # Single-shot: return everything matching, cursor=0 signals end.
        import fnmatch
        keys = set(self._h) | set(self._l) | set(self._z)
        hits = [k.encode("utf-8") for k in keys if fnmatch.fnmatch(k, match)]
        return 0, hits


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
def _open_schema() -> MemorySchema:
    layers = ("facts", "observations", "hypotheses", "plans", "execution", "verification")
    return MemorySchema(write_permissions={"system": set(layers)})


@pytest.fixture
def store() -> RedisSessionStore:
    return RedisSessionStore(
        MiniRedis(),
        namespace="t",
        session_ttl_s=3600,
        memory_schema=_open_schema(),
    )


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------
class TestRedisMessages:
    def test_append_and_read_back(self, store: RedisSessionStore):
        store.append_messages("s", [HumanMessage(content="hi")])
        msgs = store.get_recent_messages("s", limit=10)
        assert len(msgs) == 1
        assert msgs[0].content == "hi"


class TestRedisMemory:
    def test_write_and_read_item_roundtrip(self, store: RedisSessionStore):
        store.write_memory_item(
            "s", writer="system", layer="facts", key="svc", value="order-service",
            confidence=0.9,
        )
        item = store.read_memory_item("s", "facts", "svc")
        assert item is not None
        assert item.value == "order-service"
        assert item.confidence == 0.9
        # Default TTL for facts is 24h per DEFAULT_LAYER_POLICIES.
        assert item.ttl_seconds == 24 * 3600

    def test_observations_scalars_replace(self, store: RedisSessionStore):
        store.write_memory_item("s", writer="system", layer="observations", key="namespace", value="staging")
        store.write_memory_item("s", writer="system", layer="observations", key="namespace", value="prod")
        item = store.read_memory_item("s", "observations", "namespace")
        assert item and item.value == "prod"

    def test_observations_event_keys_append(self, store: RedisSessionStore):
        store.write_memory_item("s", writer="system", layer="observations", key="k8s_warning_events", value="e1")
        store.write_memory_item("s", writer="system", layer="observations", key="k8s_warning_events", value="e2")
        item = store.read_memory_item("s", "observations", "k8s_warning_events")
        assert item and item.value == ["e1", "e2"]

    def test_keep_higher_confidence(self, store: RedisSessionStore):
        store.write_memory_item(
            "s", writer="system", layer="hypotheses", key="h",
            value="strong", confidence=0.9,
        )
        store.write_memory_item(
            "s", writer="system", layer="hypotheses", key="h",
            value="weak", confidence=0.3,
        )
        item = store.read_memory_item("s", "hypotheses", "h")
        assert item and item.value == "strong"

    def test_ttl_enforced_on_read(self, store: RedisSessionStore):
        store.write_memory_item(
            "s", writer="system", layer="facts", key="tmp", value="v",
            ttl_seconds=1,
        )
        time.sleep(1.1)
        assert store.read_memory_item("s", "facts", "tmp") is None

    def test_compact_drops_expired(self, store: RedisSessionStore):
        store.write_memory_item(
            "s", writer="system", layer="facts", key="k1", value="v",
            ttl_seconds=1,
        )
        store.write_memory_item(
            "s", writer="system", layer="facts", key="k2", value="v",
            ttl_seconds=3600,
        )
        time.sleep(1.1)
        evicted = store.compact("s")
        assert evicted.get("facts") == 1
        assert store.read_memory_item("s", "facts", "k2") is not None


class TestRedisIsolation:
    def test_cross_session_isolation(self, store: RedisSessionStore):
        store.write_memory_item("A", writer="system", layer="facts", key="k", value="a")
        store.write_memory_item("B", writer="system", layer="facts", key="k", value="b")
        assert store.read_memory_item("A", "facts", "k").value == "a"
        assert store.read_memory_item("B", "facts", "k").value == "b"

    def test_clear_all_except_removes_stale(self, store: RedisSessionStore):
        store.write_memory_item("active", writer="system", layer="facts", key="k", value="v")
        store.write_memory_item("stale", writer="system", layer="facts", key="k", value="v")
        removed = store.clear_all_except({"active"})
        assert removed == 1
        assert store.read_memory_item("active", "facts", "k") is not None
        assert store.read_memory_item("stale", "facts", "k") is None


class TestRedisRouteAndArtifacts:
    def test_route_state_roundtrip(self, store: RedisSessionStore):
        store.update_route_state(
            "s",
            intent="K8S_DIAGNOSE",
            route="diagnosis",
            risk_level=RiskLevel.MEDIUM,
            metadata={"svc": "order"},
        )
        snap = store.get("s")
        assert snap.last_route == "diagnosis"
        assert snap.last_risk_level == RiskLevel.MEDIUM
        assert snap.metadata.get("svc") == "order"

    def test_artifact_append_and_recent(self, store: RedisSessionStore):
        store.append_artifact(
            "s", route="mutation", tool_name="restart_deployment",
            summary="restart OK", step_id="1",
        )
        store.append_artifact(
            "s", route="verification", tool_name="get_deployment_status",
            summary="verified", step_id="2",
        )
        recent = store.get_recent_artifacts("s", limit=10)
        assert len(recent) == 2
        assert recent[-1].tool_name == "get_deployment_status"
