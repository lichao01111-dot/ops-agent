"""
Session and shared memory abstraction for OpsAgent.

Phase 1 keeps everything in memory; a Redis-backed implementation can replace
this module later without changing the agent orchestration.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from threading import Lock
from typing import Any

from langchain_core.messages import BaseMessage

from agent_kernel.memory.backend import MemoryBackend
from agent_kernel.memory.lifecycle import (
    LayerPolicySet,
    apply_merge,
    expired_keys,
)
from agent_kernel.memory.schema import DEFAULT_MEMORY_SCHEMA, MemorySchema
from agent_kernel.schemas import AgentIdentityKey, IntentTypeKey, MemoryLayerKey, RiskLevel, RouteKey


@dataclass
class MemoryItem:
    key: str
    value: Any
    layer: MemoryLayerKey
    writer: AgentIdentityKey
    source: str = ""
    confidence: float = 1.0
    timestamp: datetime = field(default_factory=datetime.now)
    ttl_seconds: int | None = None


@dataclass
class ExecutionArtifact:
    route: RouteKey
    tool_name: str
    summary: str
    step_id: str = ""
    execution_target: str = ""
    approval_receipt_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)


class SharedMemory(MemoryBackend):
    def __init__(self, memory_schema: MemorySchema):
        self._layers: dict[MemoryLayerKey, dict[str, MemoryItem]] = {
            layer: {} for layer in memory_schema.layers()
        }
        self._schema = memory_schema

    def get_layer(self, layer: MemoryLayerKey) -> dict[str, MemoryItem]:
        if layer not in self._layers:
            self._layers[layer] = {}
        return self._layers[layer]

    def clone(self) -> "SharedMemory":
        cloned = SharedMemory(self._schema)
        for layer, items in self._layers.items():
            cloned.get_layer(layer).update(dict(items))
        return cloned


@dataclass
class SessionSnapshot:
    messages: list[BaseMessage] = field(default_factory=list)
    last_intent: IntentTypeKey | None = None
    last_route: RouteKey | None = None
    last_risk_level: RiskLevel = RiskLevel.LOW
    metadata: dict[str, Any] = field(default_factory=dict)
    shared_memory: MemoryBackend | None = None
    artifacts: list[ExecutionArtifact] = field(default_factory=list)


class SessionStore(ABC):
    """Session + artifact + shared-memory storage contract."""

    @abstractmethod
    def get(self, session_id: str) -> SessionSnapshot:
        raise NotImplementedError

    @abstractmethod
    def append_messages(self, session_id: str, messages: list[BaseMessage]) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_recent_messages(self, session_id: str, limit: int = 6) -> list[BaseMessage]:
        raise NotImplementedError

    @abstractmethod
    def update_route_state(
        self,
        session_id: str,
        *,
        intent: IntentTypeKey | None,
        route: RouteKey | None,
        risk_level: RiskLevel,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def write_memory_item(
        self,
        session_id: str,
        *,
        writer: AgentIdentityKey,
        layer: MemoryLayerKey,
        key: str,
        value: Any,
        source: str = "",
        confidence: float = 1.0,
        ttl_seconds: int | None = None,
    ) -> MemoryItem:
        raise NotImplementedError

    @abstractmethod
    def read_memory_item(self, session_id: str, layer: MemoryLayerKey, key: str) -> MemoryItem | None:
        raise NotImplementedError

    @abstractmethod
    def resolve_memory_value(self, session_id: str, key: str, layers: list[MemoryLayerKey]) -> Any | None:
        raise NotImplementedError

    @abstractmethod
    def get_shared_memory(self, session_id: str) -> MemoryBackend:
        raise NotImplementedError

    @abstractmethod
    def append_artifact(
        self,
        session_id: str,
        *,
        route: RouteKey,
        tool_name: str,
        summary: str,
        step_id: str = "",
        execution_target: str = "",
        approval_receipt_id: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_recent_artifacts(self, session_id: str, limit: int = 10) -> list[ExecutionArtifact]:
        raise NotImplementedError

    @abstractmethod
    def clear(self, session_id: str) -> None:
        raise NotImplementedError

    # Lifecycle maintenance — default no-op so existing custom stores don't
    # break. New backends (Redis) override for real behaviour.
    def compact(self, session_id: str) -> dict[MemoryLayerKey, int]:
        return {}

    def clear_all_except(self, active_session_ids: set[str]) -> int:
        return 0


class InMemorySessionStore(SessionStore):
    """Thread-safe in-memory session and shared memory storage.

    Lifecycle governance (2026-04):
      * Per-layer TTL defaults come from ``LayerPolicySet``.
      * Writes go through ``apply_merge`` to honour dedup + merge strategies.
      * ``compact(session_id)`` drops expired items for that session.
      * ``clear_all_except(active_ids)`` prevents stale sessions from lingering.
    """

    def __init__(
        self,
        *,
        memory_schema: MemorySchema | None = None,
        layer_policies: LayerPolicySet | None = None,
    ):
        self._sessions: dict[str, SessionSnapshot] = {}
        self._lock = Lock()
        self.memory_schema = memory_schema or DEFAULT_MEMORY_SCHEMA
        self.layer_policies = layer_policies or LayerPolicySet()

    def get(self, session_id: str) -> SessionSnapshot:
        with self._lock:
            snapshot = self._sessions.get(session_id)
            if snapshot is None:
                snapshot = SessionSnapshot(shared_memory=SharedMemory(self.memory_schema))
                self._sessions[session_id] = snapshot
            return snapshot

    def append_messages(self, session_id: str, messages: list[BaseMessage]) -> None:
        snapshot = self.get(session_id)
        with self._lock:
            snapshot.messages.extend(messages)

    def get_recent_messages(self, session_id: str, limit: int = 6) -> list[BaseMessage]:
        snapshot = self.get(session_id)
        with self._lock:
            if limit <= 0:
                return []
            return list(snapshot.messages[-limit:])

    def update_route_state(
        self,
        session_id: str,
        *,
        intent: IntentTypeKey | None,
        route: RouteKey | None,
        risk_level: RiskLevel,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        snapshot = self.get(session_id)
        with self._lock:
            snapshot.last_intent = intent
            snapshot.last_route = route
            snapshot.last_risk_level = risk_level
            if metadata:
                snapshot.metadata.update(metadata)

    def write_memory_item(
        self,
        session_id: str,
        *,
        writer: AgentIdentityKey,
        layer: MemoryLayerKey,
        key: str,
        value: Any,
        source: str = "",
        confidence: float = 1.0,
        ttl_seconds: int | None = None,
    ) -> MemoryItem:
        self.memory_schema.assert_can_write(writer=writer, layer=layer)

        snapshot = self.get(session_id)
        policy = self.layer_policies.get(layer, key)
        # If caller didn't specify, fall back to the layer's default TTL.
        effective_ttl = ttl_seconds if ttl_seconds is not None else policy.default_ttl_s
        clamped_confidence = max(0.0, min(confidence, 1.0))

        with self._lock:
            layer_dict = snapshot.shared_memory.get_layer(layer)
            existing = layer_dict.get(key)

            now = datetime.now()
            new_value, bump_ts = apply_merge(
                policy=policy,
                existing=existing.value if existing else None,
                existing_timestamp=existing.timestamp if existing else None,
                existing_confidence=existing.confidence if existing else None,
                incoming_value=value,
                incoming_confidence=clamped_confidence,
                now=now,
            )

            if existing and not bump_ts:
                # Dedup / keep-existing path — don't create a new item; return
                # the old one unchanged. TTL clock is preserved.
                return existing

            item = MemoryItem(
                key=key,
                value=new_value,
                layer=layer,
                writer=writer,
                source=source,
                confidence=clamped_confidence,
                timestamp=now,
                ttl_seconds=effective_ttl,
            )
            layer_dict[key] = item
            return item

    # ------------------------------------------------------------------
    # Lifecycle maintenance (added per arch review 2026-04)
    # ------------------------------------------------------------------
    def compact(self, session_id: str) -> dict[MemoryLayerKey, int]:
        """Drop expired items for the session. Returns count per layer."""
        snapshot = self.get(session_id)
        now = datetime.now()
        evicted: dict[MemoryLayerKey, int] = {}
        with self._lock:
            for layer in list(self.memory_schema.layers()):
                layer_dict = snapshot.shared_memory.get_layer(layer)
                dead = expired_keys(
                    layer_dict,
                    get_timestamp=lambda it: it.timestamp,
                    get_ttl_s=lambda it: it.ttl_seconds,
                    now=now,
                )
                for k in dead:
                    layer_dict.pop(k, None)
                if dead:
                    evicted[layer] = len(dead)
        return evicted

    def clear_all_except(self, active_session_ids: set[str]) -> int:
        """Evict every session not in ``active_session_ids``. Returns count.

        Prevents cross-session pollution and unbounded growth when callers
        never explicitly ``clear()``.
        """
        removed = 0
        with self._lock:
            for sid in list(self._sessions.keys()):
                if sid not in active_session_ids:
                    self._sessions.pop(sid, None)
                    removed += 1
        return removed

    def read_memory_item(self, session_id: str, layer: MemoryLayerKey, key: str) -> MemoryItem | None:
        snapshot = self.get(session_id)
        with self._lock:
            item = snapshot.shared_memory.get_layer(layer).get(key)
            if item is None:
                return None
            if item.ttl_seconds is not None:
                age = (datetime.now() - item.timestamp).total_seconds()
                if age > item.ttl_seconds:
                    return None
            return item

    def resolve_memory_value(self, session_id: str, key: str, layers: list[MemoryLayerKey]) -> Any | None:
        for layer in layers:
            item = self.read_memory_item(session_id, layer, key)
            if item is not None:
                return item.value
        return None

    def get_shared_memory(self, session_id: str) -> MemoryBackend:
        snapshot = self.get(session_id)
        with self._lock:
            return snapshot.shared_memory.clone()

    def append_artifact(
        self,
        session_id: str,
        *,
        route: RouteKey,
        tool_name: str,
        summary: str,
        step_id: str = "",
        execution_target: str = "",
        approval_receipt_id: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        snapshot = self.get(session_id)
        artifact = ExecutionArtifact(
            route=route,
            tool_name=tool_name,
            summary=summary,
            step_id=step_id,
            execution_target=execution_target,
            approval_receipt_id=approval_receipt_id,
            payload=payload or {},
        )
        with self._lock:
            snapshot.artifacts.append(artifact)

    def get_recent_artifacts(self, session_id: str, limit: int = 10) -> list[ExecutionArtifact]:
        snapshot = self.get(session_id)
        with self._lock:
            if limit <= 0:
                return []
            return list(snapshot.artifacts[-limit:])

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

def create_session_store(*, memory_schema: MemorySchema | None = None) -> SessionStore:
    """Auto-select backend.

    If ``settings.redis_url`` is set AND a quick PING succeeds, return a
    Redis-backed store. Otherwise fall back to in-memory. Either way,
    callers see the same SessionStore interface.

    Two escape hatches:
      * env ``JARVIS_SESSION_BACKEND=memory`` forces in-memory
        (used by pytest auto-detection — Redis is shared keyspace, which
        breaks per-instance isolation contracts asserted by tests)
      * ``PYTEST_CURRENT_TEST`` set → also forces in-memory

    The Redis probe is intentionally swallowed on any error — a healthy
    local dev experience must not require Redis.
    """
    import os
    if os.environ.get("JARVIS_SESSION_BACKEND", "").lower() == "memory":
        return InMemorySessionStore(memory_schema=memory_schema or DEFAULT_MEMORY_SCHEMA)
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return InMemorySessionStore(memory_schema=memory_schema or DEFAULT_MEMORY_SCHEMA)

    try:
        from config import settings  # local import to avoid a hard cycle
        url = getattr(settings, "redis_url", "") or ""
        if url:
            import redis  # type: ignore
            client = redis.Redis.from_url(url, socket_timeout=1.5)
            client.ping()
            from agent_kernel.redis_session import RedisSessionStore  # lazy
            import structlog  # type: ignore
            structlog.get_logger().info("session_store_selected", backend="redis", url=url)
            return RedisSessionStore(
                client,
                memory_schema=memory_schema or DEFAULT_MEMORY_SCHEMA,
            )
    except Exception as exc:  # pragma: no cover - env-dependent
        try:
            import structlog  # type: ignore
            structlog.get_logger().warning(
                "session_store_redis_unavailable_fallback_inmem",
                error=str(exc),
            )
        except Exception:
            pass
    return InMemorySessionStore(memory_schema=memory_schema or DEFAULT_MEMORY_SCHEMA)
