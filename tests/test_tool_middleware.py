"""
Unit tests for the tool-invocation middleware stack.

Each middleware is exercised in isolation with a hand-built chain of
length 1 so we assert exactly *its* contract, not the whole pipeline.
A couple of integration-style tests at the bottom make sure the default
chain composes correctly end-to-end.

Covers the five behaviours the arch review (2026-04) flagged as
critical for a trust-worthy _invoke_tool boundary:

  * Timeout   — hard bound on runtime
  * Retry     — exponential backoff, never retry a non-idempotent mutation
  * Idempotency — short-circuit on repeat key; explicit key overrides
  * Circuit   — open after N consecutive failures, half-open after cool-down
  * Cost      — charge on success only; reject once budget is exhausted
  * Schema    — warn on drift but do not block
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from agent_kernel.schemas import ReliabilityPolicy, RetryPolicy, ToolSpec
from agent_kernel.tools.middleware import (
    CircuitBreakerMiddleware,
    CircuitOpen,
    CostBudgetExceeded,
    CostBudgetMiddleware,
    IdempotencyMiddleware,
    InMemoryCostBudgetBackend,
    InMemoryIdempotencyCache,
    InvocationContext,
    MetricsMiddleware,
    RetryMiddleware,
    SchemaVersionMiddleware,
    TimeoutMiddleware,
    ToolInvocationTimeout,
    build_default_chain,
    run_chain,
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _spec(
    name: str = "t",
    *,
    side_effect: bool = False,
    timeout_s: float | None = 5.0,
    retry: RetryPolicy | None = None,
    circuit_fail_threshold: int = 0,
    circuit_cool_down_s: float = 60.0,
    cost: int = 0,
    schema_version: str = "1.0.0",
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description="",
        side_effect=side_effect,
        reliability=ReliabilityPolicy(
            timeout_s=timeout_s,
            retry=retry or RetryPolicy(),
            circuit_fail_threshold=circuit_fail_threshold,
            circuit_cool_down_s=circuit_cool_down_s,
            cost_ceiling_tokens=cost,
        ),
        schema_version=schema_version,
    )


def _ctx(spec: ToolSpec, **overrides) -> InvocationContext:
    defaults = dict(
        tool_name=spec.name, spec=spec, arguments={}, session_id="s", user_id="u", route="r",
    )
    defaults.update(overrides)
    return InvocationContext(**defaults)


async def _run_one(mw, ctx, terminal):
    return await run_chain([mw], ctx, terminal)


# ==========================================================================
# TimeoutMiddleware
# ==========================================================================
class TestTimeout:
    @pytest.mark.asyncio
    async def test_completes_under_timeout(self):
        spec = _spec(timeout_s=1.0)

        async def terminal():
            return "ok"

        result = await _run_one(TimeoutMiddleware(), _ctx(spec), terminal)
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_raises_on_overrun(self):
        spec = _spec(timeout_s=0.05)

        async def terminal():
            await asyncio.sleep(0.5)
            return "late"

        with pytest.raises(ToolInvocationTimeout):
            await _run_one(TimeoutMiddleware(), _ctx(spec), terminal)

    @pytest.mark.asyncio
    async def test_none_disables_timeout(self):
        spec = _spec(timeout_s=None)

        async def terminal():
            await asyncio.sleep(0.01)
            return "ok"

        assert await _run_one(TimeoutMiddleware(), _ctx(spec), terminal) == "ok"


# ==========================================================================
# RetryMiddleware
# ==========================================================================
class TestRetry:
    @pytest.mark.asyncio
    async def test_single_attempt_when_no_policy(self):
        spec = _spec()  # max_attempts=1 default
        calls = 0

        async def terminal():
            nonlocal calls
            calls += 1
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            await _run_one(RetryMiddleware(), _ctx(spec), terminal)
        assert calls == 1

    @pytest.mark.asyncio
    async def test_retries_idempotent_tool(self):
        spec = _spec(retry=RetryPolicy(max_attempts=3, backoff_base_s=0.0, idempotent=True))
        calls = 0

        async def terminal():
            nonlocal calls
            calls += 1
            if calls < 3:
                raise RuntimeError("transient")
            return "ok"

        assert await _run_one(RetryMiddleware(), _ctx(spec), terminal) == "ok"
        assert calls == 3

    @pytest.mark.asyncio
    async def test_side_effect_without_idempotency_skips_retry(self):
        """A side_effect tool with no idempotency_key MUST run at most once."""
        spec = _spec(
            side_effect=True,
            retry=RetryPolicy(max_attempts=5, backoff_base_s=0.0),
        )
        calls = 0

        async def terminal():
            nonlocal calls
            calls += 1
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            await _run_one(RetryMiddleware(), _ctx(spec), terminal)
        assert calls == 1, "retry must be suppressed for unsafe side_effect"

    @pytest.mark.asyncio
    async def test_side_effect_with_idempotency_key_retries(self):
        spec = _spec(
            side_effect=True,
            retry=RetryPolicy(max_attempts=3, backoff_base_s=0.0),
        )
        calls = 0

        async def terminal():
            nonlocal calls
            calls += 1
            if calls < 2:
                raise RuntimeError("transient")
            return "ok"

        ctx = _ctx(spec, idempotency_key="approval-42")
        assert await _run_one(RetryMiddleware(), ctx, terminal) == "ok"
        assert calls == 2

    @pytest.mark.asyncio
    async def test_retry_on_exceptions_filters_non_matches(self):
        spec = _spec(
            retry=RetryPolicy(
                max_attempts=3,
                backoff_base_s=0.0,
                retry_on_exceptions=["TimeoutError"],
                idempotent=True,
            ),
        )
        calls = 0

        async def terminal():
            nonlocal calls
            calls += 1
            raise ValueError("not retryable")

        with pytest.raises(ValueError):
            await _run_one(RetryMiddleware(), _ctx(spec), terminal)
        assert calls == 1


# ==========================================================================
# IdempotencyMiddleware
# ==========================================================================
class TestIdempotency:
    @pytest.mark.asyncio
    async def test_read_only_without_key_not_cached(self):
        spec = _spec(side_effect=False)
        mw = IdempotencyMiddleware(InMemoryIdempotencyCache())
        calls = 0

        async def terminal():
            nonlocal calls
            calls += 1
            return calls

        assert await _run_one(mw, _ctx(spec), terminal) == 1
        assert await _run_one(mw, _ctx(spec), terminal) == 2  # re-executed

    @pytest.mark.asyncio
    async def test_side_effect_auto_key_shorts_repeat(self):
        spec = _spec(side_effect=True)
        mw = IdempotencyMiddleware(InMemoryIdempotencyCache())
        calls = 0

        async def terminal():
            nonlocal calls
            calls += 1
            return "result-1"

        ctx = _ctx(spec, arguments={"target": "order-svc"})
        assert await _run_one(mw, ctx, terminal) == "result-1"
        # Same args + same session → cached.
        assert await _run_one(mw, ctx, terminal) == "result-1"
        assert calls == 1

    @pytest.mark.asyncio
    async def test_explicit_key_overrides_args(self):
        spec = _spec(side_effect=True)
        mw = IdempotencyMiddleware(InMemoryIdempotencyCache())
        calls = 0

        async def terminal():
            nonlocal calls
            calls += 1
            return calls

        first = _ctx(spec, arguments={"a": 1}, idempotency_key="approval-7")
        second = _ctx(spec, arguments={"a": 99}, idempotency_key="approval-7")
        assert await _run_one(mw, first, terminal) == 1
        assert await _run_one(mw, second, terminal) == 1  # same key → same result
        assert calls == 1

    @pytest.mark.asyncio
    async def test_different_sessions_do_not_share(self):
        spec = _spec(side_effect=True)
        mw = IdempotencyMiddleware(InMemoryIdempotencyCache())
        calls = 0

        async def terminal():
            nonlocal calls
            calls += 1
            return calls

        c1 = _ctx(spec, session_id="sid-A", arguments={"x": 1})
        c2 = _ctx(spec, session_id="sid-B", arguments={"x": 1})
        assert await _run_one(mw, c1, terminal) == 1
        assert await _run_one(mw, c2, terminal) == 2


# ==========================================================================
# CircuitBreakerMiddleware
# ==========================================================================
class TestCircuit:
    @pytest.mark.asyncio
    async def test_opens_after_threshold(self):
        spec = _spec(circuit_fail_threshold=2, circuit_cool_down_s=60)
        mw = CircuitBreakerMiddleware()

        async def terminal():
            raise RuntimeError("boom")

        for _ in range(2):
            with pytest.raises(RuntimeError):
                await _run_one(mw, _ctx(spec), terminal)

        # Third call: circuit is open → fail fast.
        with pytest.raises(CircuitOpen):
            await _run_one(mw, _ctx(spec), terminal)

    @pytest.mark.asyncio
    async def test_resets_on_success(self):
        spec = _spec(circuit_fail_threshold=3, circuit_cool_down_s=60)
        mw = CircuitBreakerMiddleware()

        async def fail():
            raise RuntimeError("boom")

        async def ok():
            return "good"

        for _ in range(2):
            with pytest.raises(RuntimeError):
                await _run_one(mw, _ctx(spec), fail)
        # Successful call should reset counter.
        assert await _run_one(mw, _ctx(spec), ok) == "good"
        # Now two more failures shouldn't open yet.
        for _ in range(2):
            with pytest.raises(RuntimeError):
                await _run_one(mw, _ctx(spec), fail)
        # Still callable (3rd failure would open).
        with pytest.raises(RuntimeError):
            await _run_one(mw, _ctx(spec), fail)

    @pytest.mark.asyncio
    async def test_half_open_after_cool_down(self):
        spec = _spec(circuit_fail_threshold=1, circuit_cool_down_s=60)
        mw = CircuitBreakerMiddleware()

        async def fail():
            raise RuntimeError("boom")

        async def ok():
            return "good"

        with pytest.raises(RuntimeError):
            await _run_one(mw, _ctx(spec), fail)
        # Circuit open.
        with pytest.raises(CircuitOpen):
            await _run_one(mw, _ctx(spec), ok)
        # Simulate cool-down elapsing by rewinding opened_at.
        state = mw._states[spec.name]
        state.opened_at = datetime.now() - timedelta(seconds=120)
        # Half-open trial: successful call should close the circuit.
        assert await _run_one(mw, _ctx(spec), ok) == "good"
        assert state.opened_at is None
        assert state.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_disabled_when_threshold_zero(self):
        spec = _spec(circuit_fail_threshold=0)
        mw = CircuitBreakerMiddleware()

        async def fail():
            raise RuntimeError("boom")

        # Many failures — never opens.
        for _ in range(10):
            with pytest.raises(RuntimeError):
                await _run_one(mw, _ctx(spec), fail)


# ==========================================================================
# CostBudgetMiddleware
# ==========================================================================
class TestCostBudget:
    @pytest.mark.asyncio
    async def test_noop_when_cost_zero(self):
        spec = _spec(cost=0)
        backend = InMemoryCostBudgetBackend(default_budget=100)
        mw = CostBudgetMiddleware(backend)

        async def terminal():
            return "ok"

        assert await _run_one(mw, _ctx(spec), terminal) == "ok"
        assert backend.get("s") == 100  # untouched

    @pytest.mark.asyncio
    async def test_charges_on_success(self):
        spec = _spec(cost=30)
        backend = InMemoryCostBudgetBackend(default_budget=100)
        mw = CostBudgetMiddleware(backend)

        async def terminal():
            return "ok"

        await _run_one(mw, _ctx(spec), terminal)
        assert backend.get("s") == 70

    @pytest.mark.asyncio
    async def test_does_not_charge_on_failure(self):
        spec = _spec(cost=30)
        backend = InMemoryCostBudgetBackend(default_budget=100)
        mw = CostBudgetMiddleware(backend)

        async def terminal():
            raise RuntimeError("fail")

        with pytest.raises(RuntimeError):
            await _run_one(mw, _ctx(spec), terminal)
        assert backend.get("s") == 100  # untouched

    @pytest.mark.asyncio
    async def test_rejects_when_exhausted(self):
        spec = _spec(cost=60)
        backend = InMemoryCostBudgetBackend(default_budget=50)
        mw = CostBudgetMiddleware(backend)

        async def terminal():
            return "ok"

        with pytest.raises(CostBudgetExceeded):
            await _run_one(mw, _ctx(spec), terminal)


# ==========================================================================
# SchemaVersionMiddleware
# ==========================================================================
class TestSchemaVersion:
    @pytest.mark.asyncio
    async def test_no_drift_passes(self):
        spec = _spec(schema_version="1.2.0")
        mw = SchemaVersionMiddleware()
        ctx = _ctx(spec)
        ctx.metadata["remote_schema_version"] = "1.2.0"

        async def terminal():
            return "ok"

        assert await _run_one(mw, ctx, terminal) == "ok"

    @pytest.mark.asyncio
    async def test_drift_logs_but_does_not_block(self):
        """Drift detection is a warning, not an error — the call still runs."""
        spec = _spec(schema_version="1.2.0")
        mw = SchemaVersionMiddleware()
        ctx = _ctx(spec)
        ctx.metadata["remote_schema_version"] = "1.3.0"

        async def terminal():
            return "proceeded"

        assert await _run_one(mw, ctx, terminal) == "proceeded"


# ==========================================================================
# MetricsMiddleware
# ==========================================================================
class TestMetrics:
    @pytest.mark.asyncio
    async def test_records_success(self):
        spec = _spec()
        recorded: list[tuple[str, int]] = []

        class _Sink:
            def record(self, sample):
                recorded.append((sample.outcome, sample.duration_ms))

        async def terminal():
            return "ok"

        mw = MetricsMiddleware(sink=_Sink())
        assert await _run_one(mw, _ctx(spec), terminal) == "ok"
        assert len(recorded) == 1
        assert recorded[0][0] == "ok"

    @pytest.mark.asyncio
    async def test_records_failure_label(self):
        spec = _spec()
        recorded: list[tuple[str, int]] = []

        class _Sink:
            def record(self, sample):
                recorded.append((sample.outcome, sample.duration_ms))

        async def terminal():
            raise ValueError("nope")

        mw = MetricsMiddleware(sink=_Sink())
        with pytest.raises(ValueError):
            await _run_one(mw, _ctx(spec), terminal)
        assert recorded[0][0] == "error:ValueError"


# ==========================================================================
# Default chain integration
# ==========================================================================
class TestDefaultChain:
    @pytest.mark.asyncio
    async def test_chain_runs_happy_path(self):
        chain = build_default_chain()
        spec = _spec()

        async def terminal():
            return "ok"

        assert await run_chain(chain, _ctx(spec), terminal) == "ok"

    @pytest.mark.asyncio
    async def test_chain_timeout_propagates(self):
        chain = build_default_chain()
        spec = _spec(timeout_s=0.05)

        async def terminal():
            await asyncio.sleep(0.5)
            return "late"

        with pytest.raises(ToolInvocationTimeout):
            await run_chain(chain, _ctx(spec), terminal)

    @pytest.mark.asyncio
    async def test_chain_side_effect_idempotency_and_retry_safety(self):
        """A side_effect tool with no key must not retry even if policy says so,
        and IdempotencyMiddleware must cache the single result.
        """
        chain = build_default_chain()
        spec = _spec(
            side_effect=True,
            retry=RetryPolicy(max_attempts=5, backoff_base_s=0.0),
        )
        calls = 0

        async def terminal():
            nonlocal calls
            calls += 1
            return {"ok": True}

        ctx1 = _ctx(spec, arguments={"k": 1})
        result1 = await run_chain(chain, ctx1, terminal)
        # Cached replay hits IdempotencyMiddleware.
        ctx2 = _ctx(spec, arguments={"k": 1})
        result2 = await run_chain(chain, ctx2, terminal)
        assert result1 == result2
        assert calls == 1
