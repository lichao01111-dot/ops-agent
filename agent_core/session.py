"""
Session and shared memory abstraction for OpsAgent.

Phase 1 keeps everything in memory; a Redis-backed implementation can replace
this module later without changing the agent orchestration.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from threading import Lock
from typing import Any

from langchain_core.messages import BaseMessage

from agent_core.schemas import AgentIdentity, AgentRoute, IntentType, MemoryLayer, RiskLevel


SHARED_MEMORY_WRITE_PERMISSIONS: dict[AgentIdentity, set[MemoryLayer]] = {
    AgentIdentity.SYSTEM: {
        MemoryLayer.FACTS,
        MemoryLayer.OBSERVATIONS,
        MemoryLayer.HYPOTHESES,
        MemoryLayer.PLANS,
        MemoryLayer.EXECUTION,
        MemoryLayer.VERIFICATION,
    },
    AgentIdentity.ROUTER: set(),
    AgentIdentity.KNOWLEDGE: {MemoryLayer.FACTS},
    AgentIdentity.READ_OPS: {MemoryLayer.OBSERVATIONS},
    AgentIdentity.DIAGNOSIS: {MemoryLayer.HYPOTHESES},
    AgentIdentity.CHANGE_PLANNER: {MemoryLayer.PLANS},
    AgentIdentity.CHANGE_EXECUTOR: {MemoryLayer.EXECUTION},
    AgentIdentity.VERIFICATION: {MemoryLayer.VERIFICATION},
}


@dataclass
class MemoryItem:
    key: str
    value: Any
    layer: MemoryLayer
    writer: AgentIdentity
    source: str = ""
    confidence: float = 1.0
    timestamp: datetime = field(default_factory=datetime.now)
    ttl_seconds: int | None = None


@dataclass
class ExecutionArtifact:
    route: AgentRoute
    tool_name: str
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class SharedMemory:
    facts: dict[str, MemoryItem] = field(default_factory=dict)
    observations: dict[str, MemoryItem] = field(default_factory=dict)
    hypotheses: dict[str, MemoryItem] = field(default_factory=dict)
    plans: dict[str, MemoryItem] = field(default_factory=dict)
    execution: dict[str, MemoryItem] = field(default_factory=dict)
    verification: dict[str, MemoryItem] = field(default_factory=dict)

    def get_layer(self, layer: MemoryLayer) -> dict[str, MemoryItem]:
        mapping = {
            MemoryLayer.FACTS: self.facts,
            MemoryLayer.OBSERVATIONS: self.observations,
            MemoryLayer.HYPOTHESES: self.hypotheses,
            MemoryLayer.PLANS: self.plans,
            MemoryLayer.EXECUTION: self.execution,
            MemoryLayer.VERIFICATION: self.verification,
        }
        return mapping[layer]

    def clone(self) -> "SharedMemory":
        cloned = SharedMemory()
        for layer in MemoryLayer:
            cloned.get_layer(layer).update(dict(self.get_layer(layer)))
        return cloned


@dataclass
class SessionSnapshot:
    messages: list[BaseMessage] = field(default_factory=list)
    last_intent: IntentType | None = None
    last_route: AgentRoute | None = None
    last_risk_level: RiskLevel = RiskLevel.LOW
    metadata: dict[str, Any] = field(default_factory=dict)
    shared_memory: SharedMemory = field(default_factory=SharedMemory)
    artifacts: list[ExecutionArtifact] = field(default_factory=list)


class InMemorySessionStore:
    """Thread-safe in-memory session and shared memory storage."""

    def __init__(self):
        self._sessions: dict[str, SessionSnapshot] = {}
        self._lock = Lock()

    def get(self, session_id: str) -> SessionSnapshot:
        with self._lock:
            snapshot = self._sessions.get(session_id)
            if snapshot is None:
                snapshot = SessionSnapshot()
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
        intent: IntentType | None,
        route: AgentRoute | None,
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
        writer: AgentIdentity,
        layer: MemoryLayer,
        key: str,
        value: Any,
        source: str = "",
        confidence: float = 1.0,
        ttl_seconds: int | None = None,
    ) -> MemoryItem:
        if layer not in SHARED_MEMORY_WRITE_PERMISSIONS.get(writer, set()):
            raise PermissionError(f"{writer.value} is not allowed to write to {layer.value}")

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

    def read_memory_item(self, session_id: str, layer: MemoryLayer, key: str) -> MemoryItem | None:
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

    def resolve_memory_value(self, session_id: str, key: str, layers: list[MemoryLayer]) -> Any | None:
        for layer in layers:
            item = self.read_memory_item(session_id, layer, key)
            if item is not None:
                return item.value
        return None

    def get_shared_memory(self, session_id: str) -> SharedMemory:
        snapshot = self.get(session_id)
        with self._lock:
            return snapshot.shared_memory.clone()

    def append_artifact(
        self,
        session_id: str,
        *,
        route: AgentRoute,
        tool_name: str,
        summary: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        snapshot = self.get(session_id)
        artifact = ExecutionArtifact(
            route=route,
            tool_name=tool_name,
            summary=summary,
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


session_store = InMemorySessionStore()
