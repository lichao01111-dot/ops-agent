"""
Redis-backed SessionStore — durable, multi-process-safe session storage.

Replaces InMemorySessionStore for production deployments.  Each session is
stored under a key namespace ``{prefix}:{session_id}:*`` with a configurable
TTL so stale sessions expire automatically.

Storage layout
--------------
``{prefix}:{sid}:messages``     → Redis List  (JSON-serialised BaseMessage dicts)
``{prefix}:{sid}:route``        → Redis Hash  (flat intent / route / risk / metadata)
``{prefix}:{sid}:mem:{layer}``  → Redis Hash  (key → JSON MemoryItem)
``{prefix}:{sid}:artifacts``    → Redis List  (JSON ExecutionArtifact dicts)

All keys share the same TTL which is refreshed on every write.

Usage
-----
    from agent_kernel.session_redis import RedisSessionStore
    store = RedisSessionStore(redis_url="redis://localhost:6379/0")

Configure via env vars REDIS_URL (default redis://localhost:6379/0) and
REDIS_SESSION_TTL_SECONDS (default 604800 = 7 days).
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

import structlog
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from agent_kernel.memory.backend import MemoryBackend
from agent_kernel.memory.schema import DEFAULT_MEMORY_SCHEMA, MemorySchema
from agent_kernel.schemas import AgentIdentityKey, IntentTypeKey, MemoryLayerKey, RiskLevel, RouteKey
from agent_kernel.session import (
    ExecutionArtifact,
    MemoryItem,
    SessionSnapshot,
    SessionStore,
    SharedMemory,
)

logger = structlog.get_logger()

_DEFAULT_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
_DEFAULT_TTL = int(os.getenv("REDIS_SESSION_TTL_SECONDS", str(7 * 24 * 3600)))


def _redis_client(redis_url: str):
    """Lazy import — avoids hard dependency when Redis is unavailable."""
    import redis  # type: ignore
    return redis.from_url(redis_url, decode_responses=True)


# --------------------------------------------------------------------------
# Serialisation helpers
# --------------------------------------------------------------------------

def _ser_message(msg: BaseMessage) -> str:
    return json.dumps({"type": msg.__class__.__name__, "content": msg.content}, ensure_ascii=False)


def _deser_message(raw: str) -> BaseMessage:
    data = json.loads(raw)
    t = data.get("type", "HumanMessage")
    content = data.get("content", "")
    return AIMessage(content=content) if "AI" in t else HumanMessage(content=content)


def _ser_item(item: MemoryItem) -> str:
    return json.dumps(
        {
            "key": item.key,
            "value": item.value,
            "layer": item.layer,
            "writer": item.writer,
            "source": item.source,
            "confidence": item.confidence,
            "timestamp": item.timestamp.isoformat(),
            "ttl_seconds": item.ttl_seconds,
        },
        ensure_ascii=False,
        default=str,
    )


def _deser_item(raw: str) -> MemoryItem:
    data = json.loads(raw)
    return MemoryItem(
        key=data["key"],
        value=data["value"],
        layer=data["layer"],
        writer=data["writer"],
        source=data.get("source", ""),
        confidence=data.get("confidence", 1.0),
        timestamp=datetime.fromisoformat(data.get("timestamp", datetime.now().isoformat())),
        ttl_seconds=data.get("ttl_seconds"),
    )


def _ser_artifact(a: ExecutionArtifact) -> str:
    return json.dumps(
        {
            "route": a.route,
            "tool_name": a.tool_name,
            "summary": a.summary,
            "step_id": a.step_id,
            "execution_target": a.execution_target,
            "approval_receipt_id": a.approval_receipt_id,
            "payload": a.payload,
            "timestamp": a.timestamp.isoformat(),
        },
        ensure_ascii=False,
        default=str,
    )


def _deser_artifact(raw: str) -> ExecutionArtifact:
    data = json.loads(raw)
    return ExecutionArtifact(
        route=data["route"],
        tool_name=data["tool_name"],
        summary=data.get("summary", ""),
        step_id=data.get("step_id", ""),
        execution_target=data.get("execution_target", ""),
        approval_receipt_id=data.get("approval_receipt_id", ""),
        payload=data.get("payload", {}),
        timestamp=datetime.fromisoformat(data.get("timestamp", datetime.now().isoformat())),
    )


# --------------------------------------------------------------------------
# RedisSessionStore
# --------------------------------------------------------------------------

class RedisSessionStore(SessionStore):
    """Production-grade session store backed by Redis.

    Thread-safe: all operations use Redis atomic primitives (pipeline /
    MULTI-EXEC is not needed because individual commands are atomic).
    """

    def __init__(
        self,
        *,
        redis_url: str = _DEFAULT_REDIS_URL,
        key_prefix: str = "opsagent",
        ttl_seconds: int = _DEFAULT_TTL,
        memory_schema: MemorySchema | None = None,
    ):
        self._r = _redis_client(redis_url)
        self._prefix = key_prefix
        self._ttl = ttl_seconds
        self.memory_schema = memory_schema or DEFAULT_MEMORY_SCHEMA
        logger.info("redis_session_store_init", url=redis_url, prefix=key_prefix, ttl=ttl_seconds)

    # ---- Key builders ----

    def _k(self, session_id: str, *parts: str) -> str:
        return ":".join([self._prefix, session_id, *parts])

    def _touch(self, session_id: str) -> None:
        """Refresh TTL for all keys belonging to this session."""
        pattern = self._k(session_id, "*")
        try:
            for key in self._r.scan_iter(pattern):
                self._r.expire(key, self._ttl)
        except Exception as exc:
            logger.warning("redis_touch_failed", session_id=session_id, error=str(exc))

    # ---- SessionStore interface ----

    def get(self, session_id: str) -> SessionSnapshot:
        messages = self.get_recent_messages(session_id, limit=0)  # all
        mem = SharedMemory(self.memory_schema)
        for layer in self.memory_schema.layers():
            raw_map = self._r.hgetall(self._k(session_id, "mem", layer))
            for key, raw in raw_map.items():
                try:
                    item = _deser_item(raw)
                    mem.get_layer(layer)[key] = item
                except Exception:
                    pass
        raw_route = self._r.hgetall(self._k(session_id, "route"))
        snapshot = SessionSnapshot(
            messages=messages,
            shared_memory=mem,
            last_intent=raw_route.get("intent"),
            last_route=raw_route.get("route"),
            last_risk_level=RiskLevel(raw_route.get("risk_level", RiskLevel.LOW)),
            metadata=json.loads(raw_route.get("metadata", "{}")),
        )
        return snapshot

    def append_messages(self, session_id: str, messages: list[BaseMessage]) -> None:
        if not messages:
            return
        key = self._k(session_id, "messages")
        pipe = self._r.pipeline()
        for msg in messages:
            pipe.rpush(key, _ser_message(msg))
        pipe.expire(key, self._ttl)
        pipe.execute()

    def get_recent_messages(self, session_id: str, limit: int = 6) -> list[BaseMessage]:
        key = self._k(session_id, "messages")
        if limit <= 0:
            raws = self._r.lrange(key, 0, -1)
        else:
            raws = self._r.lrange(key, -limit, -1)
        result = []
        for raw in raws:
            try:
                result.append(_deser_message(raw))
            except Exception:
                pass
        return result

    def update_route_state(
        self,
        session_id: str,
        *,
        intent: IntentTypeKey | None,
        route: RouteKey | None,
        risk_level: RiskLevel,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        key = self._k(session_id, "route")
        update: dict[str, str] = {
            "intent": intent or "",
            "route": route or "",
            "risk_level": risk_level.value if isinstance(risk_level, RiskLevel) else str(risk_level),
        }
        if metadata:
            update["metadata"] = json.dumps(metadata, ensure_ascii=False, default=str)
        self._r.hset(key, mapping=update)
        self._r.expire(key, self._ttl)

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
        item = MemoryItem(
            key=key,
            value=value,
            layer=layer,
            writer=writer,
            source=source,
            confidence=max(0.0, min(confidence, 1.0)),
            ttl_seconds=ttl_seconds,
        )
        redis_key = self._k(session_id, "mem", layer)
        self._r.hset(redis_key, key, _ser_item(item))
        self._r.expire(redis_key, self._ttl)
        return item

    def read_memory_item(self, session_id: str, layer: MemoryLayerKey, key: str) -> MemoryItem | None:
        raw = self._r.hget(self._k(session_id, "mem", layer), key)
        if raw is None:
            return None
        try:
            item = _deser_item(raw)
        except Exception:
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
        mem = SharedMemory(self.memory_schema)
        for layer in self.memory_schema.layers():
            raw_map = self._r.hgetall(self._k(session_id, "mem", layer))
            for key, raw in raw_map.items():
                try:
                    item = _deser_item(raw)
                    mem.get_layer(layer)[key] = item
                except Exception:
                    pass
        return mem

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
        artifact = ExecutionArtifact(
            route=route,
            tool_name=tool_name,
            summary=summary,
            step_id=step_id,
            execution_target=execution_target,
            approval_receipt_id=approval_receipt_id,
            payload=payload or {},
        )
        key = self._k(session_id, "artifacts")
        self._r.rpush(key, _ser_artifact(artifact))
        self._r.expire(key, self._ttl)

    def get_recent_artifacts(self, session_id: str, limit: int = 10) -> list[ExecutionArtifact]:
        key = self._k(session_id, "artifacts")
        if limit <= 0:
            raws = self._r.lrange(key, 0, -1)
        else:
            raws = self._r.lrange(key, -limit, -1)
        result = []
        for raw in raws:
            try:
                result.append(_deser_artifact(raw))
            except Exception:
                pass
        return result

    def clear(self, session_id: str) -> None:
        pattern = self._k(session_id, "*")
        keys = list(self._r.scan_iter(pattern))
        if keys:
            self._r.delete(*keys)
        logger.info("redis_session_cleared", session_id=session_id, keys_deleted=len(keys))


def create_redis_session_store(
    *,
    redis_url: str | None = None,
    key_prefix: str = "opsagent",
    ttl_seconds: int | None = None,
    memory_schema: MemorySchema | None = None,
) -> RedisSessionStore:
    """Factory that reads defaults from env vars."""
    return RedisSessionStore(
        redis_url=redis_url or _DEFAULT_REDIS_URL,
        key_prefix=key_prefix,
        ttl_seconds=ttl_seconds or _DEFAULT_TTL,
        memory_schema=memory_schema,
    )
