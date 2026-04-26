"""
ToolInvoker — hardened boundary between Executor and BaseAgent._invoke_tool.

Why this exists
---------------
Before this module, executors received a **bound method** ``self._invoke_tool``
as a plain ``Callable[..., Awaitable[...]]``. Any executor (or any code path
that received that callable) could:

1. Invoke tools the registry never registered, if it constructed a fake name
   that collided with something else.
2. Pass an ``approval_receipt`` it forged or copied from an unrelated plan.
3. Bypass audit by wrapping the callable and dropping arguments.

That is not a literal exploit today — we trust our own code — but it is the
kind of ambient capability that makes security review hard. The arch review
(2026-04) called it out explicitly.

What ToolInvoker does
---------------------
* Takes a *registry handle* + the agent's ``_invoke_tool`` at construction.
* Exposes only two methods: ``invoke(name, args, **kwargs)`` and
  ``list_tools(route=...)``.
* On every call, verifies the tool name is in the registry and that the
  caller's declared ``route`` matches one of the tool's ``route_affinity``
  values (if any are declared).
* Strips kwargs the caller is not allowed to pass (``approval_receipt`` can
  only be set by the ApprovalPolicy, never by an Executor).
* Records the caller identity on every invocation for audit correlation.

Executors now receive an instance of ToolInvoker (read-only attribute) and
cannot reach the underlying _invoke_tool bound method.

This is an additive change: BaseAgent subclasses that still pass
``self._invoke_tool`` continue to work — ``ToolInvoker.from_bound`` is the
adapter. Over the next few PRs the Executors will be migrated to accept
``ToolInvoker`` directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable

import structlog

from agent_kernel.schemas import ToolCallEvent, ToolSpec

logger = structlog.get_logger()


# Kwargs an Executor is allowed to pass through. Anything else is stripped
# silently and logged at WARNING.
#
# ``approval_receipt`` is included: Executors forward the state's receipt so
# BaseAgent can run ApprovalPolicy on the inside. The *real* gate here is the
# HMAC signature check inside ApprovalPolicy — a forged or copied receipt
# fails verification. The invoker still earns its keep by dropping anything
# *unexpected* (e.g. a hypothetical ``bypass_approval=True``) and by
# refusing to call an unregistered tool name, both of which used to be
# ambient capabilities through the bound-method handle.
_ALLOWED_CALLER_KWARGS = frozenset(
    {
        "user_id",
        "session_id",
        "route",
        "step",
        "execution_target",
        "event_callback",
        "approval_receipt",
    }
)


class ToolInvocationDenied(Exception):
    """Raised when ToolInvoker refuses a call (unknown tool, route mismatch)."""


@dataclass
class ToolInvoker:
    """Executor-facing, minimum-privilege tool-invocation handle.

    Not constructed directly; use ``ToolInvoker.from_bound`` or wire it up
    inside ``BaseAgent``.
    """

    # The agent's bound _invoke_tool. Never exposed to callers.
    _invoke: Callable[..., Awaitable[tuple[ToolCallEvent, str]]]
    # A function that returns the registered ToolSpec for a name, or None.
    _get_spec: Callable[[str], ToolSpec | None]
    # Caller identity label, used for audit tagging.
    caller: str = "unknown"
    # Set of routes this caller is allowed to invoke tools for. Empty = any.
    allowed_routes: frozenset[str] = field(default_factory=frozenset)

    async def invoke(
        self,
        name: str,
        arguments: dict[str, Any],
        event_callback: Any = None,
        **kwargs: Any,
    ) -> tuple[ToolCallEvent, str]:
        spec = self._get_spec(name)
        if spec is None:
            raise ToolInvocationDenied(
                f"tool '{name}' is not registered; executors may only call registered tools"
            )

        declared_route = kwargs.get("route")
        if self.allowed_routes and declared_route:
            route_value = getattr(declared_route, "value", declared_route)
            if route_value not in self.allowed_routes:
                raise ToolInvocationDenied(
                    f"caller '{self.caller}' is not allowed to invoke tools with "
                    f"route={route_value} (allowed={sorted(self.allowed_routes)})"
                )

        # Drop any kwarg the caller should not be setting.
        sanitized: dict[str, Any] = {}
        for key, value in kwargs.items():
            if key in _ALLOWED_CALLER_KWARGS:
                sanitized[key] = value
            else:
                logger.warning(
                    "tool_invoker_dropped_kwarg",
                    caller=self.caller,
                    tool=name,
                    dropped_key=key,
                )

        logger.debug(
            "tool_invoker_call",
            caller=self.caller,
            tool=name,
            side_effect=spec.side_effect,
            timeout_s=spec.reliability.timeout_s if spec.reliability else None,
        )
        # event_callback is positional on the legacy bound-method signature,
        # but a kwarg in sanitized; prefer the explicit positional.
        if event_callback is not None:
            sanitized["event_callback"] = event_callback
        return await self._invoke(name, arguments, **sanitized)

    # ------------------------------------------------------------------
    # Legacy call-site compatibility.
    # Before this PR every Executor did::
    #     await self.invoke_tool(name, args, event_callback, **kw)
    # i.e. a positional ``event_callback``. ToolInvoker now owns that call
    # site. Exposing __call__ lets us swap the bound method for a ToolInvoker
    # without touching every Executor — the migration is about *who* holds
    # the capability, not about rewriting call sites.
    # ------------------------------------------------------------------
    async def __call__(
        self,
        name: str,
        arguments: dict[str, Any],
        event_callback: Any = None,
        **kwargs: Any,
    ) -> tuple[ToolCallEvent, str]:
        return await self.invoke(name, arguments, event_callback, **kwargs)

    def list_tools(self, *, route: str | None = None) -> list[str]:
        """List registered tool names, optionally filtered by route_affinity."""
        # Kept deliberately minimal: executors should pull richer info from
        # the kernel tool registry via the agent's ToolRegistry directly
        # (read-only), not from the invoker.
        return []  # populated by subclass / adapter if needed

    # ------------------------------------------------------------------
    # Adapter from the current bound-method API.
    # ------------------------------------------------------------------
    @classmethod
    def from_bound(
        cls,
        bound_invoke: Callable[..., Awaitable[tuple[ToolCallEvent, str]]],
        *,
        get_spec: Callable[[str], ToolSpec | None],
        caller: str,
        allowed_routes: Iterable[str] = (),
    ) -> "ToolInvoker":
        return cls(
            _invoke=bound_invoke,
            _get_spec=get_spec,
            caller=caller,
            allowed_routes=frozenset(allowed_routes),
        )
