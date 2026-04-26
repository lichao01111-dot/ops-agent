"""
Tests for memory lifecycle governance (added per arch review 2026-04).

Covers:
  * per-layer default TTL applies when caller passes ttl_seconds=None
  * REPLACE strategy overwrites
  * KEEP_HIGHER_CONFIDENCE refuses a lower-confidence overwrite
  * APPEND_LIST concatenates writes and respects max_len
  * REJECT_IF_EXISTS raises on the second write
  * dedup keeps the original timestamp when the same (key,value) is
    rewritten within dedup_window_s
  * compact() drops expired items
  * clear_all_except() prevents cross-session pollution
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

import pytest

from agent_kernel.memory.lifecycle import (
    DEFAULT_LAYER_POLICIES,
    LayerPolicy,
    LayerPolicySet,
    MemoryConflict,
    MergeStrategy,
    apply_merge,
)
from agent_kernel.memory.schema import MemorySchema
from agent_kernel.session import InMemorySessionStore


# --------------------------------------------------------------------------
# A permissive schema that lets a single writer touch every built-in layer.
# --------------------------------------------------------------------------
def _open_schema() -> MemorySchema:
    layers = ("facts", "observations", "hypotheses", "plans", "execution", "verification")
    return MemorySchema(write_permissions={"system": set(layers)})


@pytest.fixture
def store() -> InMemorySessionStore:
    return InMemorySessionStore(memory_schema=_open_schema())


# --------------------------------------------------------------------------
# apply_merge unit tests — independent of the store
# --------------------------------------------------------------------------
class TestApplyMerge:
    def test_first_write_always_accepted(self):
        policy = LayerPolicy(default_ttl_s=None)
        new, bump = apply_merge(
            policy=policy, existing=None, existing_timestamp=None,
            existing_confidence=None, incoming_value="x", incoming_confidence=1.0,
            now=datetime.now(),
        )
        assert new == "x" and bump is True

    def test_replace_overwrites(self):
        policy = LayerPolicy(default_ttl_s=None, merge=MergeStrategy.REPLACE)
        now = datetime.now()
        new, bump = apply_merge(
            policy=policy, existing="old", existing_timestamp=now - timedelta(hours=1),
            existing_confidence=0.9, incoming_value="new", incoming_confidence=0.5, now=now,
        )
        assert new == "new" and bump is True

    def test_reject_if_exists_raises(self):
        policy = LayerPolicy(default_ttl_s=None, merge=MergeStrategy.REJECT_IF_EXISTS)
        with pytest.raises(MemoryConflict):
            apply_merge(
                policy=policy, existing="old", existing_timestamp=datetime.now(),
                existing_confidence=1.0, incoming_value="new", incoming_confidence=1.0,
                now=datetime.now(),
            )

    def test_keep_higher_confidence_keeps_existing_if_lower(self):
        policy = LayerPolicy(default_ttl_s=None, merge=MergeStrategy.KEEP_HIGHER_CONFIDENCE)
        now = datetime.now()
        new, bump = apply_merge(
            policy=policy, existing="strong", existing_timestamp=now - timedelta(hours=1),
            existing_confidence=0.9, incoming_value="weak", incoming_confidence=0.3, now=now,
        )
        assert new == "strong" and bump is False

    def test_keep_higher_confidence_accepts_if_higher_or_equal(self):
        policy = LayerPolicy(default_ttl_s=None, merge=MergeStrategy.KEEP_HIGHER_CONFIDENCE)
        now = datetime.now()
        new, bump = apply_merge(
            policy=policy, existing="weak", existing_timestamp=now - timedelta(hours=1),
            existing_confidence=0.3, incoming_value="strong", incoming_confidence=0.8, now=now,
        )
        assert new == "strong" and bump is True

    def test_append_list_concatenates_and_caps(self):
        policy = LayerPolicy(default_ttl_s=None, merge=MergeStrategy.APPEND_LIST, max_len=3)
        now = datetime.now()
        new, bump = apply_merge(
            policy=policy, existing=["a", "b", "c"], existing_timestamp=now - timedelta(hours=1),
            existing_confidence=1.0, incoming_value="d", incoming_confidence=1.0, now=now,
        )
        assert new == ["b", "c", "d"]  # oldest dropped
        assert bump is True

    def test_dedup_window_keeps_timestamp(self):
        policy = LayerPolicy(default_ttl_s=None, dedup_window_s=10)
        now = datetime.now()
        # Same value, 2s after initial write → should NOT bump timestamp.
        new, bump = apply_merge(
            policy=policy, existing="same", existing_timestamp=now - timedelta(seconds=2),
            existing_confidence=1.0, incoming_value="same", incoming_confidence=1.0, now=now,
        )
        assert new == "same" and bump is False

    def test_dedup_window_expires(self):
        policy = LayerPolicy(default_ttl_s=None, dedup_window_s=5)
        now = datetime.now()
        # 60s later, same value → bump allowed (timestamp refresh).
        new, bump = apply_merge(
            policy=policy, existing="same", existing_timestamp=now - timedelta(seconds=60),
            existing_confidence=1.0, incoming_value="same", incoming_confidence=1.0, now=now,
        )
        assert new == "same" and bump is True


# --------------------------------------------------------------------------
# Store integration — lifecycle applied through write_memory_item
# --------------------------------------------------------------------------
class TestStoreLifecycle:
    def test_default_ttl_injected_when_none(self, store: InMemorySessionStore):
        # "facts" default is 24h (86_400s) per DEFAULT_LAYER_POLICIES.
        item = store.write_memory_item(
            "sid-1", writer="system", layer="facts", key="svc", value="order",
        )
        assert item.ttl_seconds == DEFAULT_LAYER_POLICIES["facts"].default_ttl_s

    def test_caller_ttl_overrides_default(self, store: InMemorySessionStore):
        item = store.write_memory_item(
            "sid-1", writer="system", layer="facts", key="svc", value="order",
            ttl_seconds=60,
        )
        assert item.ttl_seconds == 60

    def test_plans_replace_latest_revision(self, store: InMemorySessionStore):
        store.write_memory_item("sid-1", writer="system", layer="plans", key="p", value={"a": 1})
        store.write_memory_item("sid-1", writer="system", layer="plans", key="p", value={"a": 2})
        item = store.read_memory_item("sid-1", "plans", "p")
        assert item is not None
        assert item.value == {"a": 2}

    def test_observations_scalars_replace(self, store: InMemorySessionStore):
        store.write_memory_item("sid-1", writer="system", layer="observations", key="namespace", value="staging")
        store.write_memory_item("sid-1", writer="system", layer="observations", key="namespace", value="prod")
        item = store.read_memory_item("sid-1", "observations", "namespace")
        assert item is not None
        assert item.value == "prod"

    def test_observations_event_keys_append_list(self, store: InMemorySessionStore):
        store.write_memory_item("sid-1", writer="system", layer="observations", key="k8s_warning_events", value="e1")
        store.write_memory_item("sid-1", writer="system", layer="observations", key="k8s_warning_events", value="e2")
        item = store.read_memory_item("sid-1", "observations", "k8s_warning_events")
        assert item is not None
        assert item.value == ["e1", "e2"]

    def test_hypotheses_keep_higher_confidence(self, store: InMemorySessionStore):
        store.write_memory_item(
            "sid-1", writer="system", layer="hypotheses", key="h1",
            value="OOMKilled", confidence=0.9,
        )
        store.write_memory_item(
            "sid-1", writer="system", layer="hypotheses", key="h1",
            value="NetworkTimeout", confidence=0.4,
        )
        item = store.read_memory_item("sid-1", "hypotheses", "h1")
        assert item is not None and item.value == "OOMKilled"

    def test_ttl_enforced_on_read(self, store: InMemorySessionStore):
        # 1s TTL → expire → read returns None.
        store.write_memory_item(
            "sid-1", writer="system", layer="facts", key="tmp", value="v",
            ttl_seconds=1,
        )
        time.sleep(1.1)
        assert store.read_memory_item("sid-1", "facts", "tmp") is None

    def test_compact_removes_expired(self, store: InMemorySessionStore):
        store.write_memory_item(
            "sid-1", writer="system", layer="facts", key="k1", value="v",
            ttl_seconds=1,
        )
        store.write_memory_item(
            "sid-1", writer="system", layer="facts", key="k2", value="v",
            ttl_seconds=3600,
        )
        time.sleep(1.1)
        evicted = store.compact("sid-1")
        assert evicted.get("facts") == 1
        assert store.read_memory_item("sid-1", "facts", "k1") is None
        assert store.read_memory_item("sid-1", "facts", "k2") is not None


# --------------------------------------------------------------------------
# Cross-session isolation
# --------------------------------------------------------------------------
class TestSessionIsolation:
    def test_writes_to_one_session_invisible_to_another(self, store: InMemorySessionStore):
        store.write_memory_item("sid-A", writer="system", layer="facts", key="svc", value="order")
        assert store.read_memory_item("sid-A", "facts", "svc") is not None
        assert store.read_memory_item("sid-B", "facts", "svc") is None

    def test_clear_all_except_evicts_inactive(self, store: InMemorySessionStore):
        store.write_memory_item("active", writer="system", layer="facts", key="k", value="v")
        store.write_memory_item("stale-1", writer="system", layer="facts", key="k", value="v")
        store.write_memory_item("stale-2", writer="system", layer="facts", key="k", value="v")

        removed = store.clear_all_except({"active"})
        assert removed == 2
        assert store.read_memory_item("active", "facts", "k") is not None
        assert store.read_memory_item("stale-1", "facts", "k") is None
        assert store.read_memory_item("stale-2", "facts", "k") is None


# --------------------------------------------------------------------------
# LayerPolicySet overrides
# --------------------------------------------------------------------------
class TestPolicyOverrides:
    def test_override_single_layer(self):
        custom = LayerPolicySet().with_overrides(
            facts=LayerPolicy(default_ttl_s=5, merge=MergeStrategy.REJECT_IF_EXISTS),
        )
        store = InMemorySessionStore(memory_schema=_open_schema(), layer_policies=custom)
        item = store.write_memory_item("s", writer="system", layer="facts", key="k", value="v")
        assert item.ttl_seconds == 5
        with pytest.raises(MemoryConflict):
            store.write_memory_item("s", writer="system", layer="facts", key="k", value="v2")
