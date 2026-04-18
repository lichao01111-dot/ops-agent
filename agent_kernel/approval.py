from __future__ import annotations

from abc import ABC
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from agent_kernel.schemas import ApprovalReceipt, PlanStep, RouteKey


class ApprovalDecision(BaseModel):
    approved: bool
    reason: str = ""
    receipt: ApprovalReceipt | None = None
    evaluated_at: datetime = Field(default_factory=datetime.now)


class ApprovalPolicy(ABC):
    """Validates approval receipts for side-effect execution."""

    def resolve_receipt(
        self,
        *,
        step: PlanStep | None,
        context: dict[str, Any],
    ) -> ApprovalReceipt | None:
        if not step or not step.requires_approval:
            return None
        raw_receipt = context.get("approval_receipt")
        if not isinstance(raw_receipt, dict):
            return None
        try:
            receipt = ApprovalReceipt(**raw_receipt)
        except Exception:
            return None
        if receipt.step_id != step.step_id:
            return None
        if receipt.expires_at is not None and receipt.expires_at <= datetime.now():
            return None
        step.approval_receipt_id = receipt.receipt_id
        return receipt

    def evaluate(
        self,
        *,
        tool_name: str,
        route: RouteKey | None,
        step: PlanStep | None,
        context: dict[str, Any],
    ) -> ApprovalDecision:
        if step is None:
            return ApprovalDecision(
                approved=False,
                reason=f"{tool_name} 缺少执行 step，上下文不完整。",
            )
        if not step.requires_approval:
            return ApprovalDecision(
                approved=False,
                reason=f"{tool_name} 对应的 step 未声明 requires_approval，拒绝执行。",
            )

        receipt = self.resolve_receipt(step=step, context=context)
        if receipt is None:
            return ApprovalDecision(
                approved=False,
                reason=f"{tool_name} 缺少有效的 approval_receipt，拒绝执行。",
            )

        return self.validate_receipt(
            tool_name=tool_name,
            route=route,
            step=step,
            context=context,
            receipt=receipt,
        )

    def validate_receipt(
        self,
        *,
        tool_name: str,
        route: RouteKey,
        step: PlanStep,
        context: dict[str, Any],
        receipt: ApprovalReceipt,
    ) -> ApprovalDecision:
        return ApprovalDecision(approved=True, receipt=receipt)
