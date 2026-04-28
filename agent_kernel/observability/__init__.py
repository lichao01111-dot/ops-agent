from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class MetricSample:
    """One tool-invocation measurement, emitted once by MetricsMiddleware."""

    tool: str
    outcome: str
    duration_ms: int
    slo_p95_ms: int = 0
    over_slo: bool = False
    session_id: str = ""
    user_id: str = ""
    route: str = ""
    attempt: int = 1
    extra: dict[str, Any] = field(default_factory=dict)


class MetricsSink(Protocol):
    def record(self, sample: MetricSample) -> None: ...


@dataclass(frozen=True)
class TraceContext:
    trace_id: str
    name: str
    session_id: str
    user_id: str
    vertical: str
    input: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StageContext:
    stage_kind: str
    name: str
    route: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMContext:
    purpose: str
    model: str
    model_parameters: dict[str, Any]
    input_messages: list[dict[str, Any]]
    prompt_name: str | None = None
    prompt_version: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMOutput:
    completion: str
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str | None = None


class StageObservabilitySink(Protocol):
    def trace_start(self, ctx: TraceContext) -> Any: ...
    def trace_end(self, handle: Any, output: Any, error: Exception | None) -> None: ...
    def stage_start(self, parent: Any, ctx: StageContext) -> Any: ...
    def stage_end(self, handle: Any, output: Any, error: Exception | None) -> None: ...
    def llm_start(self, parent: Any, ctx: LLMContext) -> Any: ...
    def llm_end(self, handle: Any, output: LLMOutput, error: Exception | None) -> None: ...
    def event(self, parent: Any, name: str, metadata: dict[str, Any]) -> None: ...


__all__ = [
    "LLMContext",
    "LLMOutput",
    "MetricSample",
    "MetricsSink",
    "StageContext",
    "StageObservabilitySink",
    "TraceContext",
]

