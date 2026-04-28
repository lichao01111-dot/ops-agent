from __future__ import annotations

from contextvars import ContextVar
from typing import Any


current_trace_id: ContextVar[str] = ContextVar("current_trace_id", default="")
current_trace_handle: ContextVar[Any] = ContextVar("current_trace_handle", default=None)
current_observation_handle: ContextVar[Any] = ContextVar("current_observation_handle", default=None)

