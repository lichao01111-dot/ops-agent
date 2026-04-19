from __future__ import annotations

from agent_kernel.approval import ApprovalDecision, ApprovalPolicy
from agent_kernel.schemas import ApprovalReceipt, PlanStep
from agent_ops.schemas import AgentRoute


class OpsApprovalPolicy(ApprovalPolicy):
    """Default Ops approval validation. Extra policy hooks can live here later."""

    def validate_receipt(
        self,
        *,
        tool_name: str,
        route: AgentRoute,
        step: PlanStep,
        context: dict,
        receipt: ApprovalReceipt,
    ) -> ApprovalDecision:
        return ApprovalDecision(approved=True, receipt=receipt)
