"""
Memory lifecycle governance (added per arch review 2026-04).

Before
------
``MemorySchema`` only enforced *write RBAC* — who can write to which layer.
It had nothing to say about:

  * how long items should live in each layer (TTL)
  * what to do when the same key is written twice (conflict / merge)
  * whether identical writes should bump timestamps (dedup)
  * how to bulk-evict expired items (compact)

All of those gaps were called out by the external reviewer as "缺生命周期治理"
— lifecycle governance is missing.

After
-----
``LayerPolicy`` describes one layer's lifecycle rules. ``LayerPolicySet``
bundles per-layer policies; the SessionStore consults it on every write
and exposes ``compact()`` for periodic sweeping.

Policies are data, not code — verticals override defaults by constructing a
custom ``LayerPolicySet`` and passing it in when they create the store.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Mapping

from agent_kernel.schemas import MemoryLayerKey


class MergeStrategy(str, Enum):
    """How a write resolves when the same key already exists."""

    REPLACE = "replace"
    """Overwrite unconditionally (current default behaviour)."""

    KEEP_HIGHER_CONFIDENCE = "keep_higher_confidence"
    """Prefer the incoming value only if its confidence >= existing."""

    APPEND_LIST = "append_list"
    """Treat both values as lists and concatenate (cap by policy.max_len)."""

    REJECT_IF_EXISTS = "reject_if_exists"
    """First writer wins; later writes raise a MemoryConflict."""


class MemoryConflict(Exception):
    """Raised by REJECT_IF_EXISTS when a key is written twice."""


@dataclass(frozen=True)
class LayerPolicy:
    """Lifecycle rules for one memory layer.

    * ``default_ttl_s`` — used if the caller passes ``ttl_seconds=None``.
        ``None`` here means "no TTL" (permanent until session evicted).
    * ``merge`` — strategy on key collision.
    * ``max_len`` — for ``APPEND_LIST``; oldest entries are dropped beyond this.
    * ``dedup_window_s`` — if the same (key, value) is rewritten within this
        window, do NOT bump the timestamp. Keeps idempotent writes from
        refreshing TTLs.
    """

    default_ttl_s: int | None
    merge: MergeStrategy = MergeStrategy.REPLACE
    max_len: int = 50
    dedup_window_s: int = 5


# ---------------------------------------------------------------------------
# Default policies for the six built-in layers.
# Values chosen to match how executors actually use each layer:
#
#   facts         — cross-session-is bad; keep a day, dedup aggressively
#   observations  — one triage pass' worth; 1h plenty, replace scalar hints
#   hypotheses    — live during an investigation; 2h, keep highest confidence
#   plans         — one mutation cycle; 30min; replace latest plan revision
#   execution     — audit breadcrumb; 7d retention for post-mortem
#   verification  — the authoritative conclusion; long-lived
# ---------------------------------------------------------------------------
DEFAULT_LAYER_POLICIES: Mapping[MemoryLayerKey, LayerPolicy] = {
    "facts":        LayerPolicy(default_ttl_s=24 * 3600,      merge=MergeStrategy.REPLACE,              dedup_window_s=30),
    "observations": LayerPolicy(default_ttl_s=60 * 60,        merge=MergeStrategy.REPLACE,              dedup_window_s=10),
    "hypotheses":   LayerPolicy(default_ttl_s=2 * 3600,       merge=MergeStrategy.KEEP_HIGHER_CONFIDENCE),
    "plans":        LayerPolicy(default_ttl_s=30 * 60,        merge=MergeStrategy.REPLACE),
    "execution":    LayerPolicy(default_ttl_s=7 * 24 * 3600,  merge=MergeStrategy.APPEND_LIST, max_len=200),
    "verification": LayerPolicy(default_ttl_s=None,           merge=MergeStrategy.REPLACE),
}

DEFAULT_KEY_POLICIES: Mapping[tuple[MemoryLayerKey, str], LayerPolicy] = {
    ("observations", "k8s_warning_events"): LayerPolicy(
        default_ttl_s=60 * 60,
        merge=MergeStrategy.APPEND_LIST,
        max_len=20,
    ),
    ("observations", "source_refs"): LayerPolicy(
        default_ttl_s=60 * 60,
        merge=MergeStrategy.APPEND_LIST,
        max_len=50,
    ),
}


@dataclass
class LayerPolicySet:
    """Per-layer policy bundle. Pass to the SessionStore at construction."""

    policies: dict[MemoryLayerKey, LayerPolicy] = field(
        default_factory=lambda: dict(DEFAULT_LAYER_POLICIES)
    )
    key_policies: dict[tuple[MemoryLayerKey, str], LayerPolicy] = field(
        default_factory=lambda: dict(DEFAULT_KEY_POLICIES)
    )

    def get(self, layer: MemoryLayerKey, key: str = "") -> LayerPolicy:
        if key:
            override = self.key_policies.get((layer, key))
            if override is not None:
                return override
        return self.policies.get(layer) or LayerPolicy(default_ttl_s=None)

    def with_overrides(self, **overrides: LayerPolicy) -> "LayerPolicySet":
        merged = dict(self.policies)
        merged.update(overrides)
        return LayerPolicySet(policies=merged, key_policies=dict(self.key_policies))

    def with_key_overrides(self, **overrides: LayerPolicy) -> "LayerPolicySet":
        merged = dict(self.key_policies)
        for name, policy in overrides.items():
            layer, _, key = name.partition("__")
            if not layer or not key:
                raise ValueError("key overrides must use '<layer>__<key>'")
            merged[(layer, key)] = policy
        return LayerPolicySet(policies=dict(self.policies), key_policies=merged)


# ---------------------------------------------------------------------------
# Apply-merge helper. The SessionStore calls this to compute the value &
# timestamp it should actually persist, given the incoming write and the
# existing item (if any).
#
# Returns (new_value, should_bump_timestamp).
#   - should_bump_timestamp=False means: write wins, but keep the old
#     timestamp (and therefore the original TTL clock). Used for dedup.
#
# Raises MemoryConflict for REJECT_IF_EXISTS.
# ---------------------------------------------------------------------------
def apply_merge(
    *,
    policy: LayerPolicy,
    existing: Any | None,
    existing_timestamp: datetime | None,
    existing_confidence: float | None,
    incoming_value: Any,
    incoming_confidence: float,
    now: datetime,
) -> tuple[Any, bool]:
    if existing is None:
        return incoming_value, True

    # Dedup check first — same value rewritten within the window keeps old ts.
    if (
        existing == incoming_value
        and existing_timestamp is not None
        and (now - existing_timestamp).total_seconds() <= policy.dedup_window_s
    ):
        return existing, False

    if policy.merge is MergeStrategy.REPLACE:
        return incoming_value, True

    if policy.merge is MergeStrategy.REJECT_IF_EXISTS:
        raise MemoryConflict(
            f"layer policy forbids overwriting existing key (merge={policy.merge.value})"
        )

    if policy.merge is MergeStrategy.KEEP_HIGHER_CONFIDENCE:
        prev_conf = existing_confidence if existing_confidence is not None else 0.0
        if incoming_confidence >= prev_conf:
            return incoming_value, True
        return existing, False

    if policy.merge is MergeStrategy.APPEND_LIST:
        # Coerce either side to list; always append the new value.
        prev_list = list(existing) if isinstance(existing, list) else [existing]
        new_list = prev_list + [incoming_value]
        if policy.max_len and len(new_list) > policy.max_len:
            new_list = new_list[-policy.max_len:]
        return new_list, True

    # Unknown strategy — fall back to REPLACE.
    return incoming_value, True


# ---------------------------------------------------------------------------
# Compact — callable the SessionStore invokes to drop expired items.
# Takes an iterator over (key, item_like) for a single layer and returns
# the keys that should be evicted.
# ---------------------------------------------------------------------------
def expired_keys(
    items: Mapping[str, Any],
    *,
    get_timestamp: Callable[[Any], datetime],
    get_ttl_s: Callable[[Any], int | None],
    now: datetime,
) -> list[str]:
    dead: list[str] = []
    for key, item in items.items():
        ttl = get_ttl_s(item)
        if ttl is None:
            continue
        age = (now - get_timestamp(item)).total_seconds()
        if age > ttl:
            dead.append(key)
    return dead
