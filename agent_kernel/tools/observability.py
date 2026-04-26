"""
Observability sinks for ``MetricsMiddleware``.

Why a separate module
---------------------
The middleware's only job is to *measure* (duration, outcome, SLO
breach). Where those measurements land — structlog, Prometheus, OTel,
a JSON file — is a deployment concern that differs between dev, staging,
and production. Splitting the sink out keeps the middleware cheap and
dependency-light (no Prometheus import in tests), and lets ops swap
backends via a single constructor argument.

Design
------
Every sink implements :class:`MetricsSink`::

    def record(self, sample: MetricSample) -> None: ...

``MetricSample`` is a plain dataclass — all the context a sink could
want (tool name, outcome, duration, SLO target, attempt number).

Built-in sinks:

  * :class:`StructlogSink` — default, mirrors the old behaviour
  * :class:`PrometheusSink` — counter + histogram + slo-breach counter;
    lazy-imports ``prometheus_client`` so the dependency is optional
  * :class:`OTelTracingSink` — attaches tool invocations as spans; lazy
    imports ``opentelemetry``
  * :class:`SloAlertSink` — emits a structured ``tool_slo_breach``
    warning (and optionally calls an alert callback) when p95 is blown
  * :class:`MultiSink` — compose any combination of the above

``MetricsMiddleware`` accepts a ``sink`` kwarg; pass a ``MultiSink`` to
fan out. The default remains ``StructlogSink`` so no config change is
needed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Protocol

import structlog

logger = structlog.get_logger()


# =============================================================================
# Sample + protocol
# =============================================================================

@dataclass(frozen=True)
class MetricSample:
    """One tool-invocation measurement, emitted once by MetricsMiddleware."""
    tool: str
    outcome: str                  # "ok" | "error:<ExcType>"
    duration_ms: int
    slo_p95_ms: int = 0           # 0 = no SLO declared
    over_slo: bool = False
    session_id: str = ""
    user_id: str = ""
    route: str = ""
    attempt: int = 1
    extra: dict[str, Any] = field(default_factory=dict)


class MetricsSink(Protocol):
    def record(self, sample: MetricSample) -> None: ...


# =============================================================================
# Structlog sink — default, preserves old behaviour
# =============================================================================

class StructlogSink:
    """Log one structured event per sample. Safe default for all envs."""

    def record(self, sample: MetricSample) -> None:
        logger.info(
            "tool_invocation_metric",
            tool=sample.tool,
            outcome=sample.outcome,
            duration_ms=sample.duration_ms,
            over_slo=sample.over_slo,
            session_id=sample.session_id,
            attempt=sample.attempt,
        )


# =============================================================================
# MultiSink — fan out to several backends
# =============================================================================

@dataclass
class MultiSink:
    """Forward each sample to every child sink.

    Children are called in order; if one raises, it's logged and the
    remaining children still get the sample. Losing a sink should never
    break the request path.
    """
    children: list[MetricsSink] = field(default_factory=list)

    def record(self, sample: MetricSample) -> None:
        for sink in self.children:
            try:
                sink.record(sample)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "metrics_sink_failed",
                    sink_type=type(sink).__name__,
                    error=str(exc),
                )


# =============================================================================
# SloAlertSink — route SLO breaches to a dedicated stream
# =============================================================================

@dataclass
class SloAlertSink:
    """Emit a dedicated ``tool_slo_breach`` warning when p95 is blown,
    and optionally invoke a callback (e.g. push to PagerDuty).

    This is separate from the generic ``tool_invocation_metric`` event so
    alerting pipelines can filter on a single, stable event name without
    parsing every invocation log.
    """
    alert_callback: Callable[[MetricSample], None] | None = None

    def record(self, sample: MetricSample) -> None:
        if not sample.over_slo:
            return
        logger.warning(
            "tool_slo_breach",
            tool=sample.tool,
            duration_ms=sample.duration_ms,
            slo_p95_ms=sample.slo_p95_ms,
            outcome=sample.outcome,
            session_id=sample.session_id,
        )
        if self.alert_callback:
            try:
                self.alert_callback(sample)
            except Exception as exc:  # pragma: no cover - defensive
                logger.error(
                    "slo_alert_callback_failed",
                    tool=sample.tool,
                    error=str(exc),
                )


# =============================================================================
# PrometheusSink — lazy-imports prometheus_client
# =============================================================================

class PrometheusSink:
    """Export tool metrics as Prometheus counters + histograms.

    Metrics emitted:

      * ``tool_invocations_total{tool, outcome, route}`` (counter)
      * ``tool_invocation_duration_ms{tool, route}``     (histogram)
      * ``tool_slo_breach_total{tool}``                  (counter)

    The ``prometheus_client`` library is imported lazily on first use so
    environments without it still work. A helpful error is raised only
    if you actually try to construct a PrometheusSink when the package
    is missing.
    """

    _DEFAULT_BUCKETS = (5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10_000)

    def __init__(self, *, buckets: Iterable[float] | None = None, registry: Any = None) -> None:
        try:
            from prometheus_client import Counter, Histogram  # type: ignore
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "PrometheusSink requires the 'prometheus_client' package. "
                "Install it or use StructlogSink / MultiSink without it."
            ) from exc

        kwargs: dict[str, Any] = {}
        if registry is not None:
            kwargs["registry"] = registry

        self._invocations = Counter(
            "tool_invocations_total",
            "Total tool invocations grouped by outcome.",
            labelnames=("tool", "outcome", "route"),
            **kwargs,
        )
        self._duration = Histogram(
            "tool_invocation_duration_ms",
            "Tool invocation wall-clock duration in milliseconds.",
            labelnames=("tool", "route"),
            buckets=tuple(buckets) if buckets else self._DEFAULT_BUCKETS,
            **kwargs,
        )
        self._slo_breach = Counter(
            "tool_slo_breach_total",
            "Tool invocations that exceeded their declared SLO target.",
            labelnames=("tool",),
            **kwargs,
        )

    def record(self, sample: MetricSample) -> None:
        self._invocations.labels(
            tool=sample.tool,
            outcome=sample.outcome,
            route=sample.route or "unknown",
        ).inc()
        self._duration.labels(
            tool=sample.tool,
            route=sample.route or "unknown",
        ).observe(sample.duration_ms)
        if sample.over_slo:
            self._slo_breach.labels(tool=sample.tool).inc()


# =============================================================================
# OTelTracingSink — lazy-imports opentelemetry
# =============================================================================

class OTelTracingSink:
    """Attach each tool invocation as a tracing span.

    This sink is unusual in that a span must span the tool's *runtime*,
    not just the post-hoc measurement. It exposes two hooks:

      * ``start(ctx) -> handle`` — called by MetricsMiddleware before
        ``next_()``; returns an opaque handle.
      * ``end(handle, sample)`` — called in ``finally``; attaches the
        measurement attributes and closes the span.

    ``MetricsSink.record`` is implemented as a no-op for OTel — the
    span lifecycle is driven by the middleware. When used inside a
    MultiSink, OTel will just be skipped during record().
    """

    def __init__(self, *, tracer_name: str = "agent_kernel.tools") -> None:
        try:
            from opentelemetry import trace  # type: ignore
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "OTelTracingSink requires the 'opentelemetry-api' package."
            ) from exc
        self._tracer = trace.get_tracer(tracer_name)

    def start(self, *, tool: str, route: str, session_id: str) -> Any:
        span = self._tracer.start_span(
            name=f"tool.{tool}",
            attributes={
                "agent.tool": tool,
                "agent.route": route or "unknown",
                "agent.session_id": session_id,
            },
        )
        return span

    def end(self, span: Any, sample: MetricSample) -> None:
        if span is None:
            return
        try:
            span.set_attribute("agent.outcome", sample.outcome)
            span.set_attribute("agent.duration_ms", sample.duration_ms)
            span.set_attribute("agent.over_slo", sample.over_slo)
            span.set_attribute("agent.attempt", sample.attempt)
            if sample.outcome != "ok":
                # OTel status codes live in trace.StatusCode — look up lazily.
                from opentelemetry.trace import Status, StatusCode  # type: ignore
                span.set_status(Status(StatusCode.ERROR, sample.outcome))
        finally:
            span.end()

    # Treat record() as a no-op so OTel can live inside a MultiSink without
    # double-emitting. The span is driven by start/end above.
    def record(self, sample: MetricSample) -> None:
        return None
