"""
Tests for ``ToolInvoker`` — the minimum-privilege boundary between an
Executor and ``BaseAgent._invoke_tool``.

What the invoker must enforce:
  * unregistered tool name → ``ToolInvocationDenied``
  * declared ``route`` outside ``allowed_routes`` → ``ToolInvocationDenied``
  * unexpected kwargs (e.g. ``bypass_approval=True``) → dropped silently,
    never reach the underlying ``_invoke_tool``
  * legacy positional ``event_callback`` signature still works (we kept
    ``ToolInvoker`` awaitable so Executors don't need to be rewritten)
  * approved kwargs (including ``approval_receipt``) are forwarded verbatim
  * a successful call returns ``(ToolCallEvent, output)``

Uses a tiny stub for the underlying bound ``_invoke_tool`` so we inspect
exactly what the invoker forwards.
"""
from __future__ import annotations

from typing import Any

import pytest

from agent_kernel.schemas import ApprovalReceipt, ToolCallEvent, ToolCallStatus, ToolSpec
from agent_kernel.tools.invoker import (
    ToolInvocationDenied,
    ToolInvoker,
    _ALLOWED_CALLER_KWARGS,
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _spec(name: str, **overrides: Any) -> ToolSpec:
    base: dict[str, Any] = {
        "name": name,
        "description": "",
        "route_affinity": [],
    }
    base.update(overrides)
    return ToolSpec(**base)


class _CapturingBound:
    """Fake bound `_invoke_tool` that records every call it receives."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    async def __call__(
        self,
        name: str,
        arguments: dict[str, Any],
        **kwargs: Any,
    ) -> tuple[ToolCallEvent, str]:
        self.calls.append((name, dict(arguments), dict(kwargs)))
        return (
            ToolCallEvent(tool_name=name, action=name, status=ToolCallStatus.SUCCESS),
            "ok",
        )


def _make(
    registry: dict[str, ToolSpec],
    *,
    caller: str = "test",
    allowed: tuple[str, ...] = (),
) -> tuple[ToolInvoker, _CapturingBound]:
    bound = _CapturingBound()
    invoker = ToolInvoker.from_bound(
        bound,
        get_spec=lambda n: registry.get(n),
        caller=caller,
        allowed_routes=allowed,
    )
    return invoker, bound


# --------------------------------------------------------------------------
# Unregistered tool
# --------------------------------------------------------------------------
class TestRegistryGate:
    async def test_unknown_tool_rejected(self):
        invoker, bound = _make({})
        with pytest.raises(ToolInvocationDenied) as info:
            await invoker.invoke("no_such_tool", {})
        assert "not registered" in str(info.value)
        assert bound.calls == []  # never reached underlying handler

    async def test_known_tool_accepted(self):
        invoker, bound = _make({"get_pod_status": _spec("get_pod_status")})
        event, output = await invoker.invoke("get_pod_status", {"pod": "p1"})
        assert event.status == ToolCallStatus.SUCCESS
        assert output == "ok"
        assert len(bound.calls) == 1


# --------------------------------------------------------------------------
# Route affinity
# --------------------------------------------------------------------------
class TestRouteGate:
    async def test_route_outside_allowed_raises(self):
        invoker, bound = _make(
            {"restart_deployment": _spec("restart_deployment", side_effect=True)},
            caller="read_only_executor",
            allowed=("read_only_ops",),
        )
        with pytest.raises(ToolInvocationDenied) as info:
            await invoker.invoke(
                "restart_deployment",
                {"deployment": "order"},
                route="mutation",
            )
        assert "read_only_executor" in str(info.value)
        assert bound.calls == []

    async def test_route_inside_allowed_passes(self):
        invoker, bound = _make(
            {"get_pod_status": _spec("get_pod_status")},
            caller="read_only_executor",
            allowed=("read_only_ops",),
        )
        await invoker.invoke("get_pod_status", {}, route="read_only_ops")
        assert len(bound.calls) == 1

    async def test_empty_allowed_routes_is_permissive(self):
        """An empty allowed_routes frozenset means "no restriction"."""
        invoker, bound = _make(
            {"any_tool": _spec("any_tool")},
            allowed=(),
        )
        await invoker.invoke("any_tool", {}, route="whatever")
        assert len(bound.calls) == 1

    async def test_enum_route_is_resolved_by_value(self):
        """Route enums (having .value) must be compared by their string value."""

        class _RouteEnum:
            value = "diagnosis"

        invoker, bound = _make(
            {"diagnose": _spec("diagnose")},
            allowed=("diagnosis",),
        )
        await invoker.invoke("diagnose", {}, route=_RouteEnum())
        assert len(bound.calls) == 1


# --------------------------------------------------------------------------
# Kwarg sanitisation
# --------------------------------------------------------------------------
class TestKwargSanitisation:
    async def test_unknown_kwarg_dropped(self):
        invoker, bound = _make({"t": _spec("t")})
        await invoker.invoke(
            "t", {},
            user_id="u",
            route="read_only_ops",
            bypass_approval=True,   # forbidden — must be dropped
            __private=42,           # forbidden — must be dropped
        )
        assert bound.calls, "underlying bound should still have been called"
        _, _, forwarded = bound.calls[0]
        assert "bypass_approval" not in forwarded
        assert "__private" not in forwarded
        # legitimate kwargs still forwarded
        assert forwarded.get("user_id") == "u"
        assert forwarded.get("route") == "read_only_ops"

    async def test_allowed_kwargs_pass_through(self):
        invoker, bound = _make({"t": _spec("t")})
        receipt = ApprovalReceipt(receipt_id="r1", step_id="s1")
        await invoker.invoke(
            "t", {},
            user_id="u",
            session_id="sess",
            route="mutation",
            step=None,
            execution_target="executor:mutation",
            approval_receipt=receipt,
        )
        _, _, forwarded = bound.calls[0]
        assert forwarded["approval_receipt"] is receipt
        assert forwarded["execution_target"] == "executor:mutation"

    def test_allow_list_matches_docstring(self):
        """Regression guard: the docstring on _ALLOWED_CALLER_KWARGS enumerates
        the exact fields we claim to permit. If this set drifts, update both."""
        assert _ALLOWED_CALLER_KWARGS == frozenset(
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


# --------------------------------------------------------------------------
# Legacy positional event_callback (Executors rely on this)
# --------------------------------------------------------------------------
class TestCallableCompat:
    async def test_positional_event_callback_forwarded(self):
        captured: list[str] = []

        async def cb(*args, **kwargs):
            captured.append("called")

        invoker, bound = _make({"t": _spec("t")})
        # ``await invoker(name, args, cb, ...)`` is the legacy Executor shape
        await invoker("t", {"k": 1}, cb, route="read_only_ops")
        _, _, forwarded = bound.calls[0]
        assert forwarded.get("event_callback") is cb

    async def test_call_equivalent_to_invoke(self):
        invoker, bound = _make({"t": _spec("t")})
        await invoker("t", {"k": 1})
        await invoker.invoke("t", {"k": 1})
        assert len(bound.calls) == 2
