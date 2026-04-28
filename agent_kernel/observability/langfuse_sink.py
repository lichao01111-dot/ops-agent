from __future__ import annotations

import hashlib
import re
import time
from typing import Any, Callable

import structlog

from agent_kernel.audit import default_sanitize_params
from agent_kernel.observability import LLMContext, LLMOutput, MetricSample, StageContext, TraceContext

logger = structlog.get_logger()

Sanitizer = Callable[[dict[str, Any]], dict[str, Any]]


_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")


def _scrub_text(value: str) -> str:
    value = _EMAIL_RE.sub("***EMAIL***", value)
    value = _PHONE_RE.sub("***PHONE***", value)
    return value


class LangfuseSink:
    """Langfuse-backed sink for agent traces, stages, tools, and LLM calls.

    The sink is deliberately defensive: all SDK calls are isolated and never
    propagate into the request path.
    """

    def __init__(
        self,
        *,
        public_key: str,
        secret_key: str,
        host: str = "",
        sample_rate: float = 1.0,
        sanitizer: Sanitizer | None = None,
        release: str = "",
        enabled_verticals: set[str] | None = None,
        failure_disable_seconds: int = 300,
    ) -> None:
        self._sample_rate = max(0.0, min(float(sample_rate), 1.0))
        self._sanitizer = sanitizer or default_sanitize_params
        self._release = release or None
        self._enabled_verticals = enabled_verticals
        self._failure_disable_seconds = failure_disable_seconds
        self._disabled_until = 0.0
        self._client = None

        try:
            from langfuse import Langfuse  # type: ignore

            kwargs: dict[str, Any] = {
                "public_key": public_key,
                "secret_key": secret_key,
            }
            if host:
                kwargs["host"] = host
            # release: process-level tag, lands in traces.release column.
            # propagate_attributes(version=...) sets the per-trace version
            # column independently — both are useful for grouping deploys.
            if release:
                kwargs["release"] = release
            self._client = Langfuse(**kwargs)
        except Exception as exc:  # pragma: no cover - optional dependency/backend
            logger.warning("langfuse_init_failed", error=str(exc))
            self._disabled_until = time.monotonic() + self._failure_disable_seconds

    # ------------------------------------------------------------------
    # Handle shape
    # ------------------------------------------------------------------
    # All sink methods return / accept handles shaped as:
    #   {"span": LangfuseSpan|LangfuseGeneration, "ctx_managers": [..]}
    # where ``ctx_managers`` is a stack of OTel context managers we
    # manually entered (root: start_as_current_observation +
    # propagate_attributes). Children (stages, LLM) leave this list
    # empty since they do not change "current" context.
    #
    # The ``parent`` argument may also be a MultiSink fan-out list of
    # ``[(sink, child_handle), ...]`` tuples (when called via
    # MetricsMiddleware). ``_extract_parent_span`` handles all shapes.

    def _extract_parent_span(self, parent: Any) -> Any:
        """Unwrap dict handle / MultiSink list / raw span to a LangfuseSpan."""
        if parent is None:
            return None
        if isinstance(parent, dict):
            return parent.get("span")
        if isinstance(parent, list):
            # MultiSink fan-out: find the entry whose child_handle is
            # one of OUR dict handles (has "span" + "ctx_managers" keys).
            for entry in parent:
                try:
                    _, child = entry
                except Exception:
                    continue
                if (
                    isinstance(child, dict)
                    and "span" in child
                    and "ctx_managers" in child
                ):
                    return child.get("span")
            return None
        # Legacy path: raw span object.
        return parent

    def trace_start(self, ctx: TraceContext) -> Any:
        if not self._client or not self._enabled_for_trace(ctx):
            return None

        meta = {**ctx.metadata, "vertical": ctx.vertical}

        def _start() -> dict[str, Any]:
            from langfuse import propagate_attributes  # type: ignore

            ctx_stack: list[Any] = []

            # 1) Make the root span the "current" OTel span. We enter the
            #    CM manually and stash it for trace_end to exit later.
            span_cm = self._client.start_as_current_observation(
                name=ctx.name,
                as_type="span",
                input=self._scrub(ctx.input),
                metadata=meta,
            )
            span = span_cm.__enter__()
            ctx_stack.append(span_cm)

            # 2) Propagate trace-level user_id/session_id/version to this
            #    span AND every child span created within this context.
            #    This is what makes the Langfuse UI's Sessions / Users
            #    tabs actually show data.
            attrs_kwargs: dict[str, Any] = {}
            if ctx.user_id:
                attrs_kwargs["user_id"] = ctx.user_id
            if ctx.session_id:
                attrs_kwargs["session_id"] = ctx.session_id
            if self._release:
                attrs_kwargs["version"] = self._release
            if attrs_kwargs:
                attrs_cm = propagate_attributes(**attrs_kwargs)
                attrs_cm.__enter__()
                ctx_stack.append(attrs_cm)

            return {"span": span, "ctx_managers": ctx_stack}

        return self._safe_call("trace_start", _start)

    def trace_end(self, handle: Any, output: Any, error: Exception | None) -> None:
        if not handle:
            return
        span = handle.get("span")
        ctx_stack: list[Any] = handle.get("ctx_managers", [])

        def _end() -> None:
            if span is not None:
                span.update(
                    output=self._scrub(output) if output is not None else None,
                    level="ERROR" if error else "DEFAULT",
                    status_message=str(error) if error else None,
                )
                # Echo to trace-level output so the Trace card in the UI
                # shows the final answer (otherwise the root-span output
                # is hidden behind a click).
                try:
                    span.set_trace_io(output=self._scrub(output) if output is not None else None)
                except Exception:
                    pass
            # Exit context managers in reverse order. The outer
            # start_as_current_observation CM auto-calls span.end().
            for cm in reversed(ctx_stack):
                try:
                    cm.__exit__(None, None, None)
                except Exception as exc:
                    logger.warning("langfuse_ctx_exit_failed", error=str(exc))

        self._safe_call("trace_end", _end)

    # ------------------------------------------------------------------
    # Children: stages + LLM + events
    # ------------------------------------------------------------------

    def stage_start(self, parent: Any, ctx: StageContext) -> Any:
        parent_span = self._extract_parent_span(parent)
        if parent_span is None:
            return None
        meta = {**ctx.metadata, "stage_kind": ctx.stage_kind, "route": ctx.route}

        def _start() -> dict[str, Any]:
            span = parent_span.start_observation(
                name=ctx.name,
                as_type="span",
                metadata=meta,
            )
            return {"span": span, "ctx_managers": []}

        return self._safe_call("stage_start", _start)

    def stage_end(self, handle: Any, output: Any, error: Exception | None) -> None:
        if not handle:
            return
        span = handle.get("span")
        if span is None:
            return

        def _end() -> None:
            span.update(
                output=self._scrub(output) if output is not None else None,
                level="ERROR" if error else "DEFAULT",
                status_message=str(error) if error else None,
            )
            span.end()

        self._safe_call("stage_end", _end)

    def llm_start(self, parent: Any, ctx: LLMContext) -> Any:
        parent_span = self._extract_parent_span(parent)
        if parent_span is None:
            return None
        metadata = dict(ctx.metadata)
        if ctx.prompt_name:
            metadata["prompt_name"] = ctx.prompt_name
        if ctx.prompt_version is not None:
            metadata["prompt_version"] = ctx.prompt_version

        def _start() -> dict[str, Any]:
            obs = parent_span.start_observation(
                name=f"llm:{ctx.purpose}",
                as_type="generation",
                model=ctx.model,
                model_parameters=ctx.model_parameters,
                input=self._scrub(ctx.input_messages),
                metadata=metadata,
            )
            return {"span": obs, "ctx_managers": []}

        return self._safe_call("llm_start", _start)

    def llm_end(self, handle: Any, output: LLMOutput, error: Exception | None) -> None:
        if not handle:
            return
        obs = handle.get("span")
        if obs is None:
            return

        def _end() -> None:
            usage_details = {
                "input": output.input_tokens,
                "output": output.output_tokens,
                "total": output.input_tokens + output.output_tokens,
            }
            update_kwargs: dict[str, Any] = {
                "output": self._scrub(output.completion),
                "usage_details": usage_details,
                "level": "ERROR" if error else "DEFAULT",
                "status_message": str(error) if error else None,
            }
            if output.finish_reason:
                update_kwargs["metadata"] = {"finish_reason": output.finish_reason}
            obs.update(**update_kwargs)
            obs.end()

        self._safe_call("llm_end", _end)

    def event(self, parent: Any, name: str, metadata: dict[str, Any]) -> None:
        parent_span = self._extract_parent_span(parent)
        if parent_span is None:
            return
        self._safe_call(
            "event",
            lambda: parent_span.create_event(name=name, metadata=self._scrub(metadata)),
        )

    def record(self, sample: MetricSample) -> None:
        return None

    def _enabled_for_trace(self, ctx: TraceContext) -> bool:
        if time.monotonic() < self._disabled_until:
            return False
        if self._enabled_verticals and ctx.vertical not in self._enabled_verticals:
            return False
        if self._sample_rate >= 1.0:
            return True
        if self._sample_rate <= 0.0:
            return False
        digest = hashlib.sha256(ctx.trace_id.encode("utf-8")).hexdigest()
        bucket = int(digest[:8], 16) / 0xFFFFFFFF
        return bucket <= self._sample_rate

    def _safe_call(self, operation: str, fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("langfuse_sink_failed", operation=operation, error=str(exc))
            self._disabled_until = time.monotonic() + self._failure_disable_seconds
            return None

    def _scrub(self, value: Any) -> Any:
        if isinstance(value, str):
            return _scrub_text(value)
        if isinstance(value, list):
            return [self._scrub(item) for item in value]
        if isinstance(value, tuple):
            return [self._scrub(item) for item in value]
        if isinstance(value, dict):
            candidate = {str(k): self._scrub(v) for k, v in value.items()}
            if self._sanitizer:
                try:
                    candidate = self._sanitizer(candidate)
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning("langfuse_sanitizer_failed", error=str(exc))
            return candidate
        return value
