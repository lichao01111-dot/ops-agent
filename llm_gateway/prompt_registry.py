from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from config import settings
from llm_gateway.observed import PromptMeta

logger = structlog.get_logger()


@dataclass(frozen=True)
class PromptRender:
    text: str
    meta: PromptMeta
    source: str


class PromptRegistry:
    """Langfuse prompt registry adapter with code fallback.

    The registry is intentionally optional: if prompt management is disabled,
    Langfuse is unavailable, or a prompt cannot be fetched, callers receive the
    local fallback template.
    """

    def __init__(
        self,
        *,
        enabled: bool,
        public_key: str = "",
        secret_key: str = "",
        host: str = "",
        label: str = "production",
        fallback_on_error: bool = True,
    ) -> None:
        self._enabled = enabled
        self._label = label
        self._fallback_on_error = fallback_on_error
        self._client = None

        if not enabled:
            return
        if not public_key or not secret_key:
            logger.warning("prompt_registry_disabled_missing_langfuse_keys")
            self._enabled = False
            return

        try:
            from langfuse import Langfuse  # type: ignore

            kwargs: dict[str, Any] = {"public_key": public_key, "secret_key": secret_key}
            if host:
                kwargs["host"] = host
            self._client = Langfuse(**kwargs)
        except Exception as exc:  # pragma: no cover - optional dependency/backend
            logger.warning("prompt_registry_init_failed", error=str(exc))
            self._enabled = False

    @classmethod
    def from_settings(cls) -> "PromptRegistry":
        return cls(
            enabled=settings.langfuse_prompt_management_enabled,
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
            label=settings.langfuse_prompt_label,
            fallback_on_error=settings.langfuse_prompt_fallback_on_error,
        )

    def get_prompt(self, prompt_name: str, fallback_template: str, **variables: Any) -> PromptRender:
        if self._enabled and self._client is not None:
            try:
                prompt = self._client.get_prompt(prompt_name, label=self._label)
                text = prompt.compile(**variables)
                version = getattr(prompt, "version", None)
                return PromptRender(
                    text=str(text),
                    meta=PromptMeta(name=prompt_name, version=int(version) if version is not None else None),
                    source="langfuse",
                )
            except Exception as exc:
                logger.warning("prompt_registry_get_failed", prompt=prompt_name, error=str(exc))
                if not self._fallback_on_error:
                    raise

        return PromptRender(
            text=self._render_fallback(fallback_template, variables),
            meta=PromptMeta(name=prompt_name, version=None),
            source="fallback",
        )

    @staticmethod
    def _render_fallback(template: str, variables: dict[str, Any]) -> str:
        try:
            return template.format(**variables)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("prompt_fallback_format_failed", error=str(exc))
            return template


prompt_registry = PromptRegistry.from_settings()
