"""
Tests for the observability sinks.

Coverage:
  * StructlogSink — emits the structured event (best-effort smoke test)
  * MultiSink — fan-out, one bad sink doesn't stop others
  * SloAlertSink — emits when over_slo, suppresses otherwise; callback fires
  * MetricsMiddleware:
      - default sink is StructlogSink (no Prom dependency in test env)
      - custom sink receives MetricSample with the right fields
      - over_slo flag computed from spec.reliability.slo_p95_ms
      - sink.start / sink.end are driven for OTel-style sinks
"""
from __future__ import annotations

import asyncio
import time

import pytest

from agent_kernel.schemas import ReliabilityPolicy, ToolSpec
from agent_kernel.tools.middleware import (
    InvocationContext,
    MetricsMiddleware,
    run_chain,
)
from agent_kernel.tools.observability import (
    MetricSample,
    MultiSink,
    SloAlertSink,
    StructlogSink,
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _spec(name: str = "t", *, slo_p95_ms: int = 0) -> ToolSpec:
    return ToolSpec(
        name=name,
        description="",
        reliability=ReliabilityPolicy(slo_p95_ms=slo_p95_ms),
    )


def _ctx(spec: ToolSpec) -> InvocationContext:
    return InvocationContext(
        tool_name=spec.name,
        spec=spec,
        arguments={},
        session_id="sess-1",
        user_id="u",
        route="diagnosis",
    )


class CapturingSink:
    """Minimal MetricsSink that just stashes every sample."""

    def __init__(self) -> None:
        self.samples: list[MetricSample] = []

    def record(self, sample: MetricSample) -> None:
        self.samples.append(sample)


# ==========================================================================
# StructlogSink
# ==========================================================================
class TestStructlogSink:
    def test_record_does_not_raise(self):
        sink = StructlogSink()
        # We don't assert against captured logs (structlog is configured
        # globally); the contract here is just "doesn't crash on a sample".
        sink.record(MetricSample(tool="t", outcome="ok", duration_ms=12))


# ==========================================================================
# MultiSink
# ==========================================================================
class TestMultiSink:
    def test_fans_out_to_every_child(self):
        a, b, c = CapturingSink(), CapturingSink(), CapturingSink()
        sink = MultiSink(children=[a, b, c])
        sample = MetricSample(tool="t", outcome="ok", duration_ms=5)
        sink.record(sample)
        assert a.samples == [sample]
        assert b.samples == [sample]
        assert c.samples == [sample]

    def test_failing_child_does_not_break_others(self):
        class _Boom:
            def record(self, sample):
                raise RuntimeError("kaboom")

        good = CapturingSink()
        sink = MultiSink(children=[_Boom(), good])
        # Must not propagate the inner exception — losing a sink should
        # never break the request path.
        sink.record(MetricSample(tool="t", outcome="ok", duration_ms=1))
        assert len(good.samples) == 1


# ==========================================================================
# SloAlertSink
# ==========================================================================
class TestSloAlertSink:
    def test_no_alert_when_under_slo(self):
        called: list[MetricSample] = []
        sink = SloAlertSink(alert_callback=called.append)
        sink.record(MetricSample(tool="t", outcome="ok", duration_ms=10, slo_p95_ms=100, over_slo=False))
        assert called == []

    def test_alert_fires_when_over_slo(self):
        called: list[MetricSample] = []
        sink = SloAlertSink(alert_callback=called.append)
        sink.record(MetricSample(tool="t", outcome="ok", duration_ms=500, slo_p95_ms=100, over_slo=True))
        assert len(called) == 1
        assert called[0].duration_ms == 500

    def test_callback_failure_swallowed(self):
        def boom(_sample):
            raise RuntimeError("alert backend down")

        sink = SloAlertSink(alert_callback=boom)
        # Must not raise, even when callback errors.
        sink.record(MetricSample(tool="t", outcome="ok", duration_ms=999, slo_p95_ms=100, over_slo=True))


# ==========================================================================
# MetricsMiddleware integration
# ==========================================================================
class TestMetricsMiddlewareWithSink:
    @pytest.mark.asyncio
    async def test_default_sink_is_structlog(self):
        # Construct without sink arg: should default to StructlogSink.
        mw = MetricsMiddleware()
        assert isinstance(mw.sink, StructlogSink)

    @pytest.mark.asyncio
    async def test_sample_carries_full_context(self):
        sink = CapturingSink()
        mw = MetricsMiddleware(sink=sink)
        spec = _spec()

        async def terminal():
            return "ok"

        await run_chain([mw], _ctx(spec), terminal)
        assert len(sink.samples) == 1
        s = sink.samples[0]
        assert s.tool == "t"
        assert s.outcome == "ok"
        assert s.session_id == "sess-1"
        assert s.user_id == "u"
        assert s.route == "diagnosis"
        assert s.attempt == 1

    @pytest.mark.asyncio
    async def test_over_slo_flag_set(self):
        sink = CapturingSink()
        mw = MetricsMiddleware(sink=sink)
        # Tight SLO of 1ms; sleep 50ms → breach.
        spec = _spec(slo_p95_ms=1)

        async def terminal():
            await asyncio.sleep(0.05)
            return "ok"

        await run_chain([mw], _ctx(spec), terminal)
        assert sink.samples[0].over_slo is True
        assert sink.samples[0].slo_p95_ms == 1

    @pytest.mark.asyncio
    async def test_over_slo_false_when_unset(self):
        sink = CapturingSink()
        mw = MetricsMiddleware(sink=sink)
        spec = _spec(slo_p95_ms=0)  # disabled

        async def terminal():
            return "ok"

        await run_chain([mw], _ctx(spec), terminal)
        assert sink.samples[0].over_slo is False

    @pytest.mark.asyncio
    async def test_otel_style_sink_start_and_end(self):
        """Sinks that expose start/end (OTel) get span lifecycle hooks."""
        events: list[tuple[str, dict]] = []

        class _SpanLikeSink:
            def start(self, *, tool, route, session_id):
                events.append(("start", {"tool": tool, "route": route}))
                return {"opaque": True}

            def end(self, span, sample):
                events.append(("end", {"span": span, "outcome": sample.outcome}))

            def record(self, sample):  # MultiSink-friendly no-op
                events.append(("record", {"outcome": sample.outcome}))

        mw = MetricsMiddleware(sink=_SpanLikeSink())
        spec = _spec()

        async def terminal():
            return "ok"

        await run_chain([mw], _ctx(spec), terminal)
        # Order matters: start → record → end
        names = [name for name, _ in events]
        assert names == ["start", "record", "end"]

    @pytest.mark.asyncio
    async def test_failure_outcome_label(self):
        sink = CapturingSink()
        mw = MetricsMiddleware(sink=sink)
        spec = _spec()

        async def terminal():
            raise ValueError("nope")

        with pytest.raises(ValueError):
            await run_chain([mw], _ctx(spec), terminal)
        assert sink.samples[0].outcome == "error:ValueError"


# ==========================================================================
# End-to-end: SloAlertSink + Prometheus-style sink fan-out via MultiSink
# ==========================================================================
class TestEndToEndMultiSink:
    @pytest.mark.asyncio
    async def test_metrics_to_logs_and_slo_alerts_simultaneously(self):
        captured = CapturingSink()
        alerts: list[MetricSample] = []
        sink = MultiSink(children=[captured, SloAlertSink(alert_callback=alerts.append)])
        mw = MetricsMiddleware(sink=sink)

        spec = _spec(slo_p95_ms=1)

        async def terminal():
            await asyncio.sleep(0.05)
            return "ok"

        await run_chain([mw], _ctx(spec), terminal)

        # CapturingSink got the sample; SloAlertSink fired its callback.
        assert len(captured.samples) == 1
        assert len(alerts) == 1
        assert alerts[0].over_slo is True
