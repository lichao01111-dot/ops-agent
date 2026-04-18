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


class InMemorySessionStore(SessionStore):
    """Thread-safe in-memory session and shared memory storage."""

    def __init__(self, *, memory_schema: MemorySchema | None = None):
        self._sessions: dict[str, SessionSnapshot] = {}
        self._lock = Lock()
        self.memory_schema = memory_schema or DEFAULT_MEMORY_SCHEMA

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
        item = MemoryItem(
            key=key,
            value=value,
            layer=layer,
            writer=writer,
            source=source,
            confidence=max(0.0, min(confidence, 1.0)),
            ttl_seconds=ttl_seconds,
        )
        with self._lock:
            snapshot.shared_memory.get_layer(layer)[key] = item
        return item

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
    return InMemorySessionStore(memory_schema=memory_schema or DEFAULT_MEMORY_SCHEMA)
