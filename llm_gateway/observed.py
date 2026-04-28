from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import structlog

from agent_kernel.observability import LLMContext, LLMOutput, StageObservabilitySink
from agent_kernel.observability._context import current_observation_handle

logger = structlog.get_logger()


@dataclass(frozen=True)
class PromptMeta:
    name: str
    version: int | None = None


def _message_to_dict(message: Any) -> dict[str, Any]:
    if isinstance(message, dict):
        return dict(message)
    role = getattr(message, "type", None) or getattr(message, "role", None) or type(message).__name__
    content = getattr(message, "content", message)
    return {"role": str(role), "content": content}


def _normalise_messages(messages: Any) -> list[dict[str, Any]]:
    if isinstance(messages, str):
        return [{"role": "user", "content": messages}]
    if isinstance(messages, list):
        return [_message_to_dict(message) for message in messages]
    if isinstance(messages, tuple):
        return [_message_to_dict(message) for message in messages]
    return [_message_to_dict(messages)]


def _extract_text(resp: Any) -> str:
    content = getattr(resp, "content", resp)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(item.get("text", "") if isinstance(item, dict) else str(item) for item in content)
    return str(content)


def _extract_usage(resp: Any) -> tuple[int, int]:
    usage = getattr(resp, "usage_metadata", None) or {}
    if not isinstance(usage, dict):
        return 0, 0
    input_tokens = usage.get("input_tokens") or usage.get("prompt_tokens") or 0
    output_tokens = usage.get("output_tokens") or usage.get("completion_tokens") or 0
    return int(input_tokens or 0), int(output_tokens or 0)


def _extract_finish_reason(resp: Any) -> str | None:
    metadata = getattr(resp, "response_metadata", None) or {}
    if not isinstance(metadata, dict):
        return None
    value = metadata.get("finish_reason") or metadata.get("stop_reason")
    return str(value) if value else None


class ObservedChatModel:
    """Duck-typed wrapper around a LangChain chat model.

    It intentionally does not subclass LangChain internals, keeping provider
    compatibility broad while preserving `.ainvoke()` and
    `.with_structured_output()` for existing call sites.
    """

    def __init__(
        self,
        inner: Any,
        *,
        model_name: str,
        purpose: str,
        sink: StageObservabilitySink | None = None,
        model_parameters: dict[str, Any] | None = None,
    ) -> None:
        self._inner = inner
        self._model_name = model_name
        self._purpose = purpose
        self._sink = sink
        self._model_parameters = model_parameters or {}

    async def ainvoke(self, messages: Any, *args: Any, prompt_meta: PromptMeta | None = None, **kwargs: Any) -> Any:
        handle = None
        parent = current_observation_handle.get()
        if self._sink and parent is not None:
            try:
                handle = self._sink.llm_start(
                    parent,
                    LLMContext(
                        purpose=self._purpose,
                        model=self._model_name,
                        model_parameters=self._model_parameters,
                        input_messages=_normalise_messages(messages),
                        prompt_name=prompt_meta.name if prompt_meta else None,
                        prompt_version=prompt_meta.version if prompt_meta else None,
                        metadata={},
                    ),
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("observed_llm_start_failed", error=str(exc))
                handle = None

        started = time.perf_counter()
        resp: Any = None
        error: Exception | None = None
        try:
            resp = await self._inner.ainvoke(messages, *args, **kwargs)
            return resp
        except Exception as exc:
            error = exc
            raise
        finally:
            if self._sink and handle is not None:
                try:
                    input_tokens, output_tokens = _extract_usage(resp) if resp is not None else (0, 0)
                    self._sink.llm_end(
                        handle,
                        LLMOutput(
                            completion=_extract_text(resp) if resp is not None and error is None else "",
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            finish_reason=_extract_finish_reason(resp) if resp is not None else None,
                        ),
                        error,
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning("observed_llm_end_failed", error=str(exc))
            logger.info(
                "llm_invocation_metric",
                model=self._model_name,
                purpose=self._purpose,
                outcome="error" if error else "ok",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )

    def with_structured_output(self, schema: Any, *args: Any, **kwargs: Any) -> "ObservedChatModel":
        return ObservedChatModel(
            self._inner.with_structured_output(schema, *args, **kwargs),
            model_name=self._model_name,
            purpose=f"{self._purpose}:structured",
            sink=self._sink,
            model_parameters={**self._model_parameters, "structured_schema": getattr(schema, "__name__", str(schema))},
        )

    def unwrap(self) -> Any:
        return self._inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

