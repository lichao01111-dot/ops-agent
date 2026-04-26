"""
Tool-invocation middleware stack.

Architecture
------------
Every tool call is wrapped by a chain of middlewares, each implementing::

    async def __call__(self, ctx: InvocationContext, next_: Next) -> Any: ...

The terminal ``next_`` runs the actual handler. The default outer-to-inner
order is::

    Metrics              # record duration + outcome
      → Idempotency      # short-circuit on repeat key (bypasses everything below)
        → CostBudget     # check per-session budget
          → Circuit      # fail-fast after N consecutive failures
            → Retry      # re-attempt per RetryPolicy (safely)
              → SchemaVersion  # MCP drift detection
                → Timeout       # asyncio.wait_for(spec.reliability.timeout_s)
                  → handler

Idempotency lives outside CostBudget / Circuit so a cache hit does not
double-charge the budget or burn circuit trials — a replay returns the
stored answer cheaply.

State & backends
----------------
* Idempotency, circuit, and cost-budget state is kept in pluggable backends.
  Defaults are in-process (works out-of-the-box, good enough for a single
  replica). Swap them for Redis-backed implementations in production.
* Each middleware is independent — omitting one doesn't break the others.

Returns
-------
Chain return type is ``Any`` (not ``str``) because LangChain tools can return
structured payloads. Callers are responsible for stringification/serialization.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Protocol

import structlog

from agent_kernel.schemas import ToolSpec

logger = structlog.get_logger()

Next = Callable[[], Awaitable[Any]]


# =============================================================================
# Invocation context — shared state for one tool call
# =============================================================================

@dataclass
class InvocationContext:
    tool_name: str
    spec: ToolSpec
    arguments: dict[str, Any]
    session_id: str = ""
    user_id: str = ""
    route: str = ""
    # Explicit idempotency_key supplied by caller. Without this, RetryMiddleware
    # refuses to retry a side_effect tool even if the policy allows retries —
    # retrying a non-idempotent mutation is how we cause double-writes.
    idempotency_key: str | None = None
    attempt: int = 1
    # Freeform metadata middlewares can stash state on.
    metadata: dict[str, Any] = field(default_factory=dict)


class Middleware(Protocol):
    async def __call__(self, ctx: InvocationContext, next_: Next) -> Any: ...


# =============================================================================
# Common exceptions
# =============================================================================

class ToolInvocationTimeout(Exception):
    """Raised by TimeoutMiddleware when ``spec.reliability.timeout_s`` elapses."""


class CircuitOpen(Exception):
    """Raised by CircuitBreakerMiddleware when the circuit for a tool is open."""

    def __init__(self, message: str, *, tool: str = "", cool_down_remaining_s: float = 0.0) -> None:
        super().__init__(message)
        self.tool = tool
        self.cool_down_remaining_s = cool_down_remaining_s


class CostBudgetExceeded(Exception):
    """Raised by CostBudgetMiddleware when a session exhausts its token budget."""


class SchemaVersionMismatch(Exception):
    """Raised when remote MCP tool reports a version incompatible with registry."""


# =============================================================================
# 1. TimeoutMiddleware
# =============================================================================

class TimeoutMiddleware:
    """Bounds every invocation by ``spec.reliability.timeout_s``.

    ``timeout_s=None`` → no-op (permits truly long-running tools to opt out).
    """

    DEFAULT_TIMEOUT_S: float = 30.0

    async def __call__(self, ctx: InvocationContext, next_: Next) -> Any:
        timeout = (
            ctx.spec.reliability.timeout_s
            if ctx.spec.reliability
            else self.DEFAULT_TIMEOUT_S
        )
        if timeout is None:
            return await next_()
        try:
            return await asyncio.wait_for(next_(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            logger.warning(
                "tool_invocation_timeout",
                tool=ctx.tool_name,
                timeout_s=timeout,
                session_id=ctx.session_id,
            )
            raise ToolInvocationTimeout(
                f"tool '{ctx.tool_name}' timed out after {timeout}s"
            ) from exc


# =============================================================================
# 2. RetryMiddleware
# =============================================================================

class RetryMiddleware:
    """Exponential-backoff retry honouring ``spec.reliability.retry``.

    Safety rules:
      * ``side_effect=True`` and no ``idempotency_key`` → retries are DISABLED
        (we log this once per invocation). Retrying a non-idempotent mutation
        is how double-deploys happen.
      * If ``retry_on_exceptions`` is non-empty, only matching exceptions
        trigger a retry; others propagate immediately.
    """

    async def __call__(self, ctx: InvocationContext, next_: Next) -> Any:
        policy = ctx.spec.reliability.retry if ctx.spec.reliability else None
        max_attempts = max(policy.max_attempts, 1) if policy else 1

        if max_attempts <= 1:
            return await next_()

        if ctx.spec.side_effect and not (policy and policy.idempotent) and not ctx.idempotency_key:
            # Cannot safely retry — run once and return.
            logger.info(
                "retry_skipped_side_effect_without_idempotency",
                tool=ctx.tool_name,
                session_id=ctx.session_id,
            )
            return await next_()

        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            ctx.attempt = attempt
            try:
                return await next_()
            except Exception as exc:
                exc_name = type(exc).__name__
                if policy.retry_on_exceptions and exc_name not in policy.retry_on_exceptions:
                    raise
                last_exc = exc
                if attempt < max_attempts:
                    backoff = policy.backoff_base_s * (policy.backoff_factor ** (attempt - 1))
                    logger.info(
                        "retry_attempt_failed",
                        tool=ctx.tool_name,
                        attempt=attempt,
                        next_backoff_s=backoff,
                        error=str(exc),
                    )
                    await asyncio.sleep(backoff)
        assert last_exc is not None
        raise last_exc


# =============================================================================
# 3. IdempotencyMiddleware
# =============================================================================

class IdempotencyCache(Protocol):
    def get(self, key: str) -> Any | None: ...
    def set(self, key: str, value: Any, ttl_s: int) -> None: ...


@dataclass
class InMemoryIdempotencyCache:
    """Process-local cache. TTL enforced on read."""

    _store: dict[str, tuple[datetime, Any]] = field(default_factory=dict)

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expire_at, value = entry
        if datetime.now() >= expire_at:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any, ttl_s: int) -> None:
        self._store[key] = (datetime.now() + timedelta(seconds=ttl_s), value)


class IdempotencyMiddleware:
    """Short-circuit a retry of the same logical request.

    Key construction:
      * If ``ctx.idempotency_key`` is explicitly set: use it verbatim.
      * Otherwise, for side-effect tools only, derive from tool name + args
        hash — this prevents accidental double-fires when a user double-clicks
        "confirm" but does NOT try to dedupe arbitrary read calls.
      * Read-only tools without an explicit key are not cached.
    """

    DEFAULT_TTL_S: int = 300  # 5 minutes

    def __init__(self, cache: IdempotencyCache | None = None, *, ttl_s: int = DEFAULT_TTL_S):
        self.cache: IdempotencyCache = cache or InMemoryIdempotencyCache()
        self.ttl_s = ttl_s

    async def __call__(self, ctx: InvocationContext, next_: Next) -> Any:
        key = self._derive_key(ctx)
        if key is None:
            return await next_()

        cached = self.cache.get(key)
        if cached is not None:
            logger.info(
                "idempotency_hit",
                tool=ctx.tool_name,
                session_id=ctx.session_id,
                key_prefix=key[:12],
            )
            return cached

        result = await next_()
        self.cache.set(key, result, self.ttl_s)
        return result

    def _derive_key(self, ctx: InvocationContext) -> str | None:
        if ctx.idempotency_key:
            return f"explicit:{ctx.tool_name}:{ctx.idempotency_key}"
        if ctx.spec.side_effect:
            # Stable digest over canonical json of args.
            try:
                digest = hashlib.sha256(
                    json.dumps(ctx.arguments, sort_keys=True, default=str).encode("utf-8")
                ).hexdigest()[:16]
                return f"auto:{ctx.tool_name}:{ctx.session_id}:{digest}"
            except (TypeError, ValueError):
                return None
        return None


# =============================================================================
# 4. CircuitBreakerMiddleware
# =============================================================================

@dataclass
class _CircuitState:
    consecutive_failures: int = 0
    opened_at: datetime | None = None


class CircuitStateBackend(Protocol):
    """Pluggable persistence for circuit state, keyed by tool_name.

    In-process default is fine for a single replica; swap for
    :class:`RedisCircuitStateBackend` (see ``redis_middleware.py``) when
    multiple replicas need to share the circuit across the fleet.
    """

    def get(self, tool_name: str) -> _CircuitState: ...
    def record_failure(self, tool_name: str, *, now: datetime) -> _CircuitState: ...
    def reset(self, tool_name: str) -> None: ...


@dataclass
class InMemoryCircuitStateBackend:
    _states: dict[str, _CircuitState] = field(default_factory=lambda: defaultdict(_CircuitState))

    def get(self, tool_name: str) -> _CircuitState:
        return self._states[tool_name]

    def record_failure(self, tool_name: str, *, now: datetime) -> _CircuitState:
        state = self._states[tool_name]
        state.consecutive_failures += 1
        return state

    def reset(self, tool_name: str) -> None:
        # Mutate in place so that callers holding the _CircuitState
        # reference from a prior get() still see the reset values.
        state = self._states[tool_name]
        state.consecutive_failures = 0
        state.opened_at = None


class CircuitBreakerMiddleware:
    """Fail fast when a tool repeatedly errors.

    Opens after ``spec.reliability.circuit_fail_threshold`` consecutive
    failures; rejects subsequent calls for ``circuit_cool_down_s`` seconds.
    When the cool-down expires we transition to *half-open*: the next call
    is allowed through; on success the circuit resets, on failure it reopens.

    State lives in the ``CircuitStateBackend`` — in-process by default,
    Redis in production so multiple replicas share the circuit.
    """

    def __init__(self, backend: CircuitStateBackend | None = None) -> None:
        self.backend: CircuitStateBackend = backend or InMemoryCircuitStateBackend()

    # Preserved for backward-compat with existing half-open tests that poke
    # ``mw._states[tool].opened_at = ...``. New code should go through backend.
    @property
    def _states(self) -> dict[str, _CircuitState]:  # pragma: no cover - compat only
        inner = getattr(self.backend, "_states", None)
        return inner if isinstance(inner, dict) else {}

    async def __call__(self, ctx: InvocationContext, next_: Next) -> Any:
        if not ctx.spec.reliability:
            return await next_()
        threshold = ctx.spec.reliability.circuit_fail_threshold
        if threshold <= 0:
            return await next_()

        state = self.backend.get(ctx.tool_name)
        cooldown = ctx.spec.reliability.circuit_cool_down_s
        now = datetime.now()

        if state.opened_at is not None:
            elapsed = (now - state.opened_at).total_seconds()
            if elapsed < cooldown:
                remaining = cooldown - elapsed
                logger.warning(
                    "circuit_open_rejected",
                    tool=ctx.tool_name,
                    elapsed_s=elapsed,
                    cool_down_s=cooldown,
                )
                raise CircuitOpen(
                    f"circuit for '{ctx.tool_name}' is open "
                    f"({int(remaining)}s remaining)",
                    tool=ctx.tool_name,
                    cool_down_remaining_s=remaining,
                )
            # Cool-down elapsed → half-open: allow one trial call through.

        try:
            result = await next_()
        except Exception:
            state = self.backend.record_failure(ctx.tool_name, now=now)
            if state.consecutive_failures >= threshold:
                state.opened_at = now
                logger.warning(
                    "circuit_opened",
                    tool=ctx.tool_name,
                    failures=state.consecutive_failures,
                )
            raise
        else:
            self.backend.reset(ctx.tool_name)
            return result


# =============================================================================
# 5. CostBudgetMiddleware
# =============================================================================

class CostBudgetBackend(Protocol):
    def get(self, session_id: str) -> int: ...
    def deduct(self, session_id: str, amount: int) -> int: ...
    def set(self, session_id: str, total: int) -> None: ...


@dataclass
class InMemoryCostBudgetBackend:
    """Process-local budget ledger.  In production, replace with Redis."""

    default_budget: int = 100_000
    _ledgers: dict[str, int] = field(default_factory=dict)

    def get(self, session_id: str) -> int:
        if session_id not in self._ledgers:
            self._ledgers[session_id] = self.default_budget
        return self._ledgers[session_id]

    def deduct(self, session_id: str, amount: int) -> int:
        self.get(session_id)  # ensure initialized
        self._ledgers[session_id] -= amount
        return self._ledgers[session_id]

    def set(self, session_id: str, total: int) -> None:
        self._ledgers[session_id] = total


class CostBudgetMiddleware:
    """Deduct ``spec.reliability.cost_ceiling_tokens`` from session's budget.

    Budget is only charged on *successful* invocations — failed ones don't
    consume quota. This matches how downstream LLM APIs bill.
    """

    def __init__(self, backend: CostBudgetBackend | None = None):
        self.backend: CostBudgetBackend = backend or InMemoryCostBudgetBackend()

    async def __call__(self, ctx: InvocationContext, next_: Next) -> Any:
        if not ctx.spec.reliability:
            return await next_()
        cost = ctx.spec.reliability.cost_ceiling_tokens
        if cost <= 0 or not ctx.session_id:
            return await next_()

        remaining = self.backend.get(ctx.session_id)
        if remaining < cost:
            logger.warning(
                "cost_budget_exceeded",
                session_id=ctx.session_id,
                remaining=remaining,
                requested=cost,
                tool=ctx.tool_name,
            )
            raise CostBudgetExceeded(
                f"session budget exhausted: {remaining} < {cost} for '{ctx.tool_name}'"
            )

        result = await next_()
        # Charge only on success.
        self.backend.deduct(ctx.session_id, cost)
        return result


# =============================================================================
# 6. SchemaVersionMiddleware  (MCP drift detection)
# =============================================================================

class SchemaVersionMiddleware:
    """Log a warning when a remote tool's advertised schema_version diverges
    from the one registered locally.

    Currently only emits a structured audit event — it does NOT block the
    call. This is deliberate: schema drift on a patch version (1.2.0 → 1.2.1)
    is usually backward-compatible; blocking would be too heavy-handed until
    we have a real MCP discovery handshake.
    """

    async def __call__(self, ctx: InvocationContext, next_: Next) -> Any:
        advertised = ctx.metadata.get("remote_schema_version")
        registered = ctx.spec.schema_version
        if advertised and advertised != registered:
            logger.warning(
                "mcp_schema_drift_detected",
                tool=ctx.tool_name,
                registered=registered,
                advertised=advertised,
            )
        return await next_()


# =============================================================================
# 7. MetricsMiddleware
# =============================================================================

class MetricsMiddleware:
    """Records outcome + duration and forwards samples to one or more sinks.

    The default sink is :class:`StructlogSink` (preserves pre-refactor
    behaviour: one ``tool_invocation_metric`` event per call). Callers
    can plug in ``PrometheusSink``, ``OTelTracingSink``, ``SloAlertSink``,
    or a ``MultiSink`` that fans out to all of them — see
    :mod:`agent_kernel.tools.observability`.

    Subclasses can still override ``_record`` if they want to intercept
    the sample before it reaches the sink.
    """

    def __init__(self, sink: Any = None) -> None:
        # Late import avoids a circular module load if observability grows a
        # back-reference to middleware.
        from agent_kernel.tools.observability import StructlogSink
        self.sink = sink or StructlogSink()

    async def __call__(self, ctx: InvocationContext, next_: Next) -> Any:
        started = time.perf_counter()
        outcome = "ok"
        # OTel-style sinks that need to wrap the call open a span BEFORE
        # next_(). We optionally call ``.start`` if the sink exposes it.
        span = None
        if hasattr(self.sink, "start") and callable(getattr(self.sink, "start")):
            try:
                span = self.sink.start(
                    tool=ctx.tool_name,
                    route=ctx.route,
                    session_id=ctx.session_id,
                )
            except Exception:  # pragma: no cover - defensive
                span = None
        try:
            return await next_()
        except Exception as exc:
            outcome = f"error:{type(exc).__name__}"
            raise
        finally:
            duration_ms = int((time.perf_counter() - started) * 1000)
            sample = self._make_sample(ctx, outcome, duration_ms)
            self._record(sample)
            if span is not None and hasattr(self.sink, "end"):
                try:
                    self.sink.end(span, sample)
                except Exception:  # pragma: no cover - defensive
                    pass

    def _make_sample(self, ctx: InvocationContext, outcome: str, duration_ms: int) -> Any:
        from agent_kernel.tools.observability import MetricSample
        slo = ctx.spec.reliability.slo_p95_ms if ctx.spec.reliability else 0
        over_slo = bool(slo and duration_ms > slo)
        return MetricSample(
            tool=ctx.tool_name,
            outcome=outcome,
            duration_ms=duration_ms,
            slo_p95_ms=slo,
            over_slo=over_slo,
            session_id=ctx.session_id,
            user_id=ctx.user_id,
            route=ctx.route,
            attempt=ctx.attempt,
        )

    def _record(self, sample: Any) -> None:
        self.sink.record(sample)


# =============================================================================
# Chain builder + runner
# =============================================================================

def build_default_chain(
    *,
    idempotency_cache: IdempotencyCache | None = None,
    cost_backend: CostBudgetBackend | None = None,
    circuit_backend: CircuitStateBackend | None = None,
    metrics_middleware: "MetricsMiddleware | None" = None,
) -> list[Middleware]:
    """Recommended order — outermost entries see the call first and the result last.

    Idempotency is intentionally *outside* CostBudget and Circuit so a cache
    hit returns immediately without double-charging the budget or consuming
    a circuit trial. Metrics stays outermost to record every call (hits
    included) with realistic end-to-end duration.
    """
    return [
        metrics_middleware or MetricsMiddleware(),
        IdempotencyMiddleware(cache=idempotency_cache),
        CostBudgetMiddleware(backend=cost_backend),
        CircuitBreakerMiddleware(backend=circuit_backend),
        RetryMiddleware(),
        SchemaVersionMiddleware(),
        TimeoutMiddleware(),
    ]


async def run_chain(
    middlewares: list[Middleware],
    ctx: InvocationContext,
    terminal: Callable[[], Awaitable[Any]],
) -> Any:
    call: Next = terminal
    for mw in reversed(middlewares):
        call = _wrap(mw, ctx, call)
    return await call()


def _wrap(mw: Middleware, ctx: InvocationContext, nxt: Next) -> Next:
    async def wrapped() -> Any:
        return await mw(ctx, nxt)
    return wrapped
