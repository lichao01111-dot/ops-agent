from __future__ import annotations

from typing import Any

from agent_kernel.approval import ApprovalDecision, ApprovalPolicy
from agent_kernel.schemas import ApprovalReceipt, PlanStep, RouteKey
from agent_ops.schemas import AgentRoute

# Side-effecting tools that VerificationExecutor may invoke autonomously
# (auto-rollback after a failed verification). These are pre-authorized by
# the original mutation's approval receipt — no second human gate needed.
_VERIFICATION_AUTO_APPROVED_TOOLS = frozenset({"rollback_deployment"})


class OpsApprovalPolicy(ApprovalPolicy):
    """Default Ops approval validation.

    Policy overrides (beyond the kernel default):
    - ``verification`` route + rollback tool: pre-authorized (original mutation
      approval covers the compensating action).
    """

    def evaluate(
        self,
        *,
        tool_name: str,
        route: RouteKey | None,
        step: PlanStep | None,
        context: dict[str, Any],
    ) -> ApprovalDecision:
        # Auto-rollbacks triggered by VerificationExecutor are pre-authorized.
        # The user already approved the mutation; we don't gate the compensating
        # action with a second human receipt.
        if (
            str(route) == AgentRoute.VERIFICATION
            and tool_name in _VERIFICATION_AUTO_APPROVED_TOOLS
        ):
            return ApprovalDecision(
                approved=True,
                reason="auto_rollback_pre_authorized_by_mutation_approval",
            )
        return super().evaluate(
            tool_name=tool_name,
            route=route,
            step=step,
            context=context,
        )

    def validate_receipt(
        self,
        *,
        tool_name: str,
        route: RouteKey,
        step: PlanStep,
        context: dict,
        receipt: ApprovalReceipt,
    ) -> ApprovalDecision:
        return ApprovalDecision(approved=True, receipt=receipt)
