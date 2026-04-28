from __future__ import annotations

from agent_kernel.audit import default_sanitize_params
from agent_kernel.observability import StageObservabilitySink
from agent_kernel.observability.langfuse_sink import LangfuseSink
from config import settings


def build_stage_observability_sink(vertical: str = "ops") -> StageObservabilitySink | None:
    if not settings.langfuse_enabled:
        return None
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        return None

    enabled_verticals = {
        item.strip()
        for item in settings.langfuse_enabled_verticals.split(",")
        if item.strip()
    } or None

    return LangfuseSink(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
        sample_rate=settings.langfuse_sample_rate,
        sanitizer=default_sanitize_params,
        release=settings.langfuse_release,
        enabled_verticals=enabled_verticals,
    )

