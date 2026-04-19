"""
Approval-gated executor pattern.

Architecture-v2 §6 #10 lists ``ApprovalGateExecutor`` as an optional
base class that formalizes the "mutation step must present a valid
``ApprovalReceipt`` before any side-effect tool fires" contract in §4.2 #1.

Verticals that implement a mutation executor can inherit from this base
to get the gate-check for free; the subclass only needs to implement
the actual tool invocation logic (``_execute_approved``).

Design notes:
- The gate runs **before** ``_execute_approved``, so no side-effect can
  leak if the receipt is absent / wrong-step / expired.
- The gate DOES NOT by itself verify ``requires_approval`` on the step —
  that lives on ``ApprovalPolicy.evaluate`` which the Vertical's
  ``_invoke_tool`` already consults. This class covers the *pre-flight*
  check so the executor can short-circuit cleanly with a denial message
  instead of racing to call a tool that will then fail.
- If you need fine-grained denial strings, override ``_denial_message``.
"""
from __future__ import annotations

import abc
from typing import Any, Awaitable, Callable

import structlog

from agent_kernel.approval import ApprovalDecision, ApprovalPolicy
from agent_kernel.executor import ExecutorBase
from agent_kernel.schemas import ApprovalReceipt

logger = structlog.get_logger()

EventCallback = Callable[[str, dict[str, Any]], Awaitable[None]]


class ApprovalGateExecutor(ExecutorBase, abc.ABC):
    """Executor base that validates an ``ApprovalReceipt`` before executing.

    Subclasses implement:
        _execute_approved(state, receipt, event_callback) -> dict

    If no valid receipt is available the executor short-circuits with a
    deterministic denial payload (``final_message`` explains why). Nothing
    is dispatched to the registered tools in that branch.
    """

    def __init__(
        self,
        *,
        node_name: str,
        route_name: str,
        approval_policy: ApprovalPolicy,
    ):
        super().__init__(node_name=node_name, route_name=route_name)
        self._approval_policy = approval_policy

    async def execute(
        self,
        state: dict[str, Any],
        event_callback: EventCallback | None = None,
    ) -> dict[str, Any]:
        plan = state.get("plan")
        step = plan.current_step() if plan else None
        context = state.get("context", {}) or {}

        decision = self._approval_policy.evaluate(
            tool_name=self.node_name,
            route=step.route if step else None,
            step=step,
            context=context,
        )

        if not decision.approved:
            message = self._denial_message(decision)
            logger.info(
                "approval_gate_denied",
                executor=self.node_name,
                step_id=step.step_id if step else "",
                reason=decision.reason,
            )
            return {
                "final_message": message,
                "tool_calls": [],
                "sources": [],
                "needs_approval": True,
            }

        return await self._execute_approved(
            state=state,
            receipt=decision.receipt,
            event_callback=event_callback,
        )

    # ---------- Subclass hooks ----------

    @abc.abstractmethod
    async def _execute_approved(
        self,
        *,
        state: dict[str, Any],
        receipt: ApprovalReceipt | None,
        event_callback: EventCallback | None,
    ) -> dict[str, Any]:
        """Run the actual side-effect work once approval is validated."""

    def _denial_message(self, decision: ApprovalDecision) -> str:
        """Default denial message — override to localize or template."""
        return f"该操作需要审批：{decision.reason}"
