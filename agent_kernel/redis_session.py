"""
Redis-backed SessionStore.

Why this module
---------------
The original ``InMemorySessionStore`` (``agent_kernel/session.py``) is fine
for a single process, but:

  * it loses state on restart
  * it can't share a session across web workers
  * it provides no cross-session isolation guarantees beyond "different keys"

This module is a drop-in implementation of the ``SessionStore`` ABC that
persists to Redis. It keeps the same layering semantics (per-layer TTL,
merge strategies, dedup) by reusing ``agent_kernel.memory.lifecycle``.

Key layout (all prefixed by ``{ns}:{session_id}``)::

    {ns}:{sid}:msgs            LIST[json]         conversation history
    {ns}:{sid}:route           HASH               last_intent / last_route / ...
    {ns}:{sid}:meta            HASH               arbitrary metadata
    {ns}:{sid}:arts            LIST[json]         execution artifacts
    {ns}:{sid}:mem:{layer}     HASH  key→json     memory items (+ TTL per item)
    {ns}:{sid}:mem:{layer}:exp ZSET  key→expire_ts per-key expiry index

The per-key expiry ZSET lets ``compact()`` evict in O(log N) without
scanning the hash.

Connection handling
-------------------
Takes a ``redis.Redis`` client at construction. No ownership of the connection
pool — caller decides reconnect / auth / TLS. This keeps the kernel unaware
of deployment specifics.

Serialization
-------------
JSON only. BaseMessage is stored via LangChain's standard
``messages_to_dict`` / ``messages_from_dict``. Non-JSON-serializable values
in memory items (e.g. datetime, bytes) are converted via a small
``_encode_json`` / ``_decode_json`` pair.

This module is import-safe even when ``redis`` is not installed — the import
is deferred until ``RedisSessionStore`` is actually constructed. The in-memory
store continues to be the default in ``create_session_store``.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

from langchain_core.messages import BaseMessage, messages_from_dict, messages_to_dict

from agent_kernel.memory.backend import MemoryBackend
from agent_kernel.memory.lifecycle import (
    LayerPolicySet,
    apply_merge,
)
from agent_kernel.memory.schema import DEFAULT_MEMORY_SCHEMA, MemorySchema
from agent_kernel.schemas import (
    AgentIdentityKey,
    IntentTypeKey,
    MemoryLayerKey,
    RiskLevel,
    RouteKey,
)
from agent_kernel.session import (
    ExecutionArtifact,
    MemoryItem,
    SessionSnapshot,
    SessionStore,
    SharedMemory,
)

if TYPE_CHECKING:  # pragma: no cover
    import redis  # noqa: F401


DEFAULT_SESSION_TTL_S = 7 * 24 * 3600  # 7 days — covers a long on-call week


def _encode_json(value: Any) -> str:
    def _default(o: Any) -> Any:
        if isinstance(o, datetime):
            return {"__dt__": o.isoformat()}
        if isinstance(o, bytes):
            return o.decode("utf-8", errors="replace")
        return str(o)
    return json.dumps(value, default=_default, ensure_ascii=False)


def _decode_json(raw: str | bytes | None) -> Any:
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return json.loads(raw, object_hook=_maybe_datetime)


def _maybe_datetime(obj: dict) -> Any:
    if "__dt__" in obj and len(obj) == 1:
        try:
            return datetime.fromisoformat(obj["__dt__"])
        except ValueError:
            return obj
    return obj


class RedisSessionStore(SessionStore):
    """Redis-backed persistent SessionStore.

    Parameters
    ----------
    client : redis.Redis
        Pre-configured client (sync). Async variant can be added later by
        splitting into an aio-wrapper; the ABC is sync today.
    namespace : str
        Key prefix. Keep short — we're chatty.
    session_ttl_s : int
        Idle expiry for an entire session. Reset on any write. 0 = no expiry.
    memory_schema / layer_policies : as in InMemorySessionStore.
    """

    def __init__(
        self,
        client: Any,
        *,
        namespace: str = "jarvis",
        session_ttl_s: int = DEFAULT_SESSION_TTL_S,
        memory_schema: MemorySchema | None = None,
        layer_policies: LayerPolicySet | None = None,
    ):
        self._r = client
        self._ns = namespace.rstrip(":")
        self._session_ttl_s = session_ttl_s
        self.memory_schema = memory_schema or DEFAULT_MEMORY_SCHEMA
        self.layer_policies = layer_policies or LayerPolicySet()

    # ------------------------------------------------------------------
    # Key helpers
    # ------------------------------------------------------------------
    def _k(self, session_id: str, suffix: str) -> str:
        return f"{self._ns}:{session_id}:{suffix}"

    def _mem_key(self, session_id: str, layer: MemoryLayerKey) -> str:
        return self._k(session_id, f"mem:{layer}")

    def _mem_exp_key(self, session_id: str, layer: MemoryLayerKey) -> str:
        return self._k(session_id, f"mem:{layer}:exp")

    def _touch(self, session_id: str) -> None:
        """Reset idle TTL on all of a session's keys after any write."""
        if self._session_ttl_s <= 0:
            return
        prefix = self._k(session_id, "")
        # SCAN to avoid KEYS in production.
        cursor = 0
        while True:
            cursor, batch = self._r.scan(cursor=cursor, match=f"{prefix}*", count=100)
            for k in batch:
                self._r.expire(k, self._session_ttl_s)
            if cursor == 0:
                break

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------
    def get(self, session_id: str) -> SessionSnapshot:
        # SessionSnapshot is a value object in the in-memory store. In Redis
        # we rehydrate on demand; callers should prefer the narrower methods.
        msgs = self.get_recent_messages(session_id, limit=10_000)
        route_hash = self._r.hgetall(self._k(session_id, "route")) or {}
        meta_hash = self._r.hgetall(self._k(session_id, "meta")) or {}
        return SessionSnapshot(
            messages=msgs,
            last_intent=_decode_str(route_hash, "last_intent"),
            last_route=_decode_str(route_hash, "last_route"),
            last_risk_level=_decode_risk(route_hash.get(b"last_risk_level") or route_hash.get("last_risk_level")),
            metadata={_to_str(k): _decode_json(v) for k, v in meta_hash.items()},
            shared_memory=self._load_shared_memory(session_id),
            artifacts=self.get_recent_artifacts(session_id, limit=10_000),
        )

    def append_messages(self, session_id: str, messages: list[BaseMessage]) -> None:
        if not messages:
            return
        serialized = [_encode_json(m) for m in messages_to_dict(messages)]
        key = self._k(session_id, "msgs")
        self._r.rpush(key, *serialized)
        self._touch(session_id)

    def get_recent_messages(self, session_id: str, limit: int = 6) -> list[BaseMessage]:
        if limit <= 0:
            return []
        raw = self._r.lrange(self._k(session_id, "msgs"), -limit, -1) or []
        if not raw:
            return []
        dicts = [_decode_json(x) for x in raw]
        return messages_from_dict(dicts)

    # ------------------------------------------------------------------
    # Route state / metadata
    # ------------------------------------------------------------------
    def update_route_state(
        self,
        session_id: str,
        *,
        intent: IntentTypeKey | None,
        route: RouteKey | None,
        risk_level: RiskLevel,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        rkey = self._k(session_id, "route")
        mapping: dict[str, str] = {
            "last_risk_level": risk_level.value if hasattr(risk_level, "value") else str(risk_level),
        }
        if intent is not None:
            mapping["last_intent"] = str(intent)
        if route is not None:
            mapping["last_route"] = str(route)
        if mapping:
            self._r.hset(rkey, mapping=mapping)
        if metadata:
            self._r.hset(
                self._k(session_id, "meta"),
                mapping={k: _encode_json(v) for k, v in metadata.items()},
            )
        self._touch(session_id)

    # ------------------------------------------------------------------
    # Memory items (with lifecycle)
    # ------------------------------------------------------------------
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

        policy = self.layer_policies.get(layer, key)
        effective_ttl = ttl_seconds if ttl_seconds is not None else policy.default_ttl_s
        clamped_conf = max(0.0, min(confidence, 1.0))
        now = datetime.now()

        # Read existing for merge logic.
        existing_raw = self._r.hget(self._mem_key(session_id, layer), key)
        existing_dict = _decode_json(existing_raw) if existing_raw else None

        new_value, bump_ts = apply_merge(
            policy=policy,
            existing=existing_dict["value"] if existing_dict else None,
            existing_timestamp=_parse_iso(existing_dict["timestamp"]) if existing_dict else None,
            existing_confidence=existing_dict["confidence"] if existing_dict else None,
            incoming_value=value,
            incoming_confidence=clamped_conf,
            now=now,
        )

        if existing_dict and not bump_ts:
            # Dedup: return existing (unchanged) item.
            return _item_from_dict(existing_dict, layer=layer, key=key)

        item = MemoryItem(
            key=key,
            value=new_value,
            layer=layer,
            writer=writer,
            source=source,
            confidence=clamped_conf,
            timestamp=now,
            ttl_seconds=effective_ttl,
        )
        self._r.hset(
            self._mem_key(session_id, layer),
            key,
            _encode_json(_item_to_dict(item)),
        )
        if effective_ttl is not None:
            expire_ts = now.timestamp() + effective_ttl
            self._r.zadd(self._mem_exp_key(session_id, layer), {key: expire_ts})
        else:
            # Permanent — remove from expiry index if present.
            self._r.zrem(self._mem_exp_key(session_id, layer), key)

        self._touch(session_id)
        return item

    def read_memory_item(self, session_id: str, layer: MemoryLayerKey, key: str) -> MemoryItem | None:
        raw = self._r.hget(self._mem_key(session_id, layer), key)
        if raw is None:
            return None
        data = _decode_json(raw)
        if data is None:
            return None
        # Enforce TTL at read time (in case compact hasn't run yet).
        ttl = data.get("ttl_seconds")
        ts = _parse_iso(data.get("timestamp"))
        if ttl is not None and ts is not None:
            if (datetime.now() - ts).total_seconds() > ttl:
                return None
        return _item_from_dict(data, layer=layer, key=key)

    def resolve_memory_value(self, session_id: str, key: str, layers: list[MemoryLayerKey]) -> Any | None:
        for layer in layers:
            item = self.read_memory_item(session_id, layer, key)
            if item is not None:
                return item.value
        return None

    def get_shared_memory(self, session_id: str) -> MemoryBackend:
        return self._load_shared_memory(session_id)

    def _load_shared_memory(self, session_id: str) -> SharedMemory:
        mem = SharedMemory(self.memory_schema)
        for layer in self.memory_schema.layers():
            raw = self._r.hgetall(self._mem_key(session_id, layer)) or {}
            layer_dict = mem.get_layer(layer)
            for k, v in raw.items():
                data = _decode_json(v)
                if data is None:
                    continue
                k_str = _to_str(k)
                layer_dict[k_str] = _item_from_dict(data, layer=layer, key=k_str)
        return mem

    # ------------------------------------------------------------------
    # Artifacts
    # ------------------------------------------------------------------
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
        self._r.rpush(
            self._k(session_id, "arts"),
            _encode_json(_artifact_to_dict(artifact)),
        )
        self._touch(session_id)

    def get_recent_artifacts(self, session_id: str, limit: int = 10) -> list[ExecutionArtifact]:
        if limit <= 0:
            return []
        raw = self._r.lrange(self._k(session_id, "arts"), -limit, -1) or []
        return [_artifact_from_dict(_decode_json(x)) for x in raw if x]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def clear(self, session_id: str) -> None:
        prefix = self._k(session_id, "")
        cursor = 0
        while True:
            cursor, batch = self._r.scan(cursor=cursor, match=f"{prefix}*", count=100)
            if batch:
                self._r.delete(*batch)
            if cursor == 0:
                break

    def compact(self, session_id: str) -> dict[MemoryLayerKey, int]:
        """Evict expired items using the ZSET expiry index (O(log N) per layer)."""
        now_ts = datetime.now().timestamp()
        evicted: dict[MemoryLayerKey, int] = {}
        for layer in self.memory_schema.layers():
            exp_key = self._mem_exp_key(session_id, layer)
            dead = self._r.zrangebyscore(exp_key, "-inf", now_ts) or []
            if not dead:
                continue
            dead_strs = [_to_str(k) for k in dead]
            self._r.hdel(self._mem_key(session_id, layer), *dead_strs)
            self._r.zremrangebyscore(exp_key, "-inf", now_ts)
            evicted[layer] = len(dead_strs)
        return evicted

    def clear_all_except(self, active_session_ids: set[str]) -> int:
        """Scan namespace and delete sessions not in ``active_session_ids``."""
        pattern = f"{self._ns}:*"
        seen: set[str] = set()
        removed = 0
        cursor = 0
        prefix = f"{self._ns}:"
        while True:
            cursor, batch = self._r.scan(cursor=cursor, match=pattern, count=200)
            for k in batch:
                k_str = _to_str(k)
                # Extract session_id from "{ns}:{sid}:..."
                if not k_str.startswith(prefix):
                    continue
                rest = k_str[len(prefix):]
                sid = rest.split(":", 1)[0]
                if sid in active_session_ids or sid in seen:
                    continue
                seen.add(sid)
                self.clear(sid)
                removed += 1
            if cursor == 0:
                break
        return removed


# ---------------------------------------------------------------------------
# (de)serialization helpers
# ---------------------------------------------------------------------------
def _item_to_dict(item: MemoryItem) -> dict[str, Any]:
    return {
        "key": item.key,
        "value": item.value,
        "layer": item.layer,
        "writer": item.writer,
        "source": item.source,
        "confidence": item.confidence,
        "timestamp": item.timestamp.isoformat(),
        "ttl_seconds": item.ttl_seconds,
    }


def _item_from_dict(d: dict[str, Any], *, layer: MemoryLayerKey, key: str) -> MemoryItem:
    return MemoryItem(
        key=d.get("key") or key,
        value=d.get("value"),
        layer=d.get("layer") or layer,
        writer=d.get("writer") or "",
        source=d.get("source") or "",
        confidence=float(d.get("confidence") or 1.0),
        timestamp=_parse_iso(d.get("timestamp")) or datetime.now(),
        ttl_seconds=d.get("ttl_seconds"),
    )


def _artifact_to_dict(a: ExecutionArtifact) -> dict[str, Any]:
    return {
        "route": a.route,
        "tool_name": a.tool_name,
        "summary": a.summary,
        "step_id": a.step_id,
        "execution_target": a.execution_target,
        "approval_receipt_id": a.approval_receipt_id,
        "payload": a.payload,
        "timestamp": a.timestamp.isoformat(),
    }


def _artifact_from_dict(d: dict[str, Any]) -> ExecutionArtifact:
    return ExecutionArtifact(
        route=d.get("route") or "",
        tool_name=d.get("tool_name") or "",
        summary=d.get("summary") or "",
        step_id=d.get("step_id") or "",
        execution_target=d.get("execution_target") or "",
        approval_receipt_id=d.get("approval_receipt_id") or "",
        payload=d.get("payload") or {},
        timestamp=_parse_iso(d.get("timestamp")) or datetime.now(),
    )


def _parse_iso(s: Any) -> datetime | None:
    if not s:
        return None
    if isinstance(s, datetime):
        return s
    try:
        return datetime.fromisoformat(str(s))
    except ValueError:
        return None


def _to_str(x: Any) -> str:
    if isinstance(x, bytes):
        return x.decode("utf-8", errors="replace")
    return str(x)


def _decode_str(hash_: dict, field: str) -> Any | None:
    # Redis may return bytes or str depending on decode_responses.
    v = hash_.get(field)
    if v is None:
        v = hash_.get(field.encode("utf-8")) if isinstance(next(iter(hash_), None), bytes) else None
    if v is None:
        return None
    return _to_str(v)


def _decode_risk(v: Any) -> RiskLevel:
    if v is None:
        return RiskLevel.LOW
    raw = _to_str(v)
    try:
        return RiskLevel(raw)
    except ValueError:
        return RiskLevel.LOW
