"""Unit tests for agent_kernel.patterns.approval_gate.ApprovalGateExecutor.

Covers the two branches the base class must handle deterministically:
approved → forwards to _execute_approved; denied → short-circuit with
the denial payload (no side-effect work).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

import pytest

from agent_kernel.approval import ApprovalDecision, ApprovalPolicy
from agent_kernel.patterns import ApprovalGateExecutor
from agent_kernel.schemas import (
    ApprovalReceipt,
    Plan,
    PlanStep,
    PlanStepStatus,
    RiskLevel,
)


class AlwaysApprove(ApprovalPolicy):
    def evaluate(self, *, tool_name, route, step, context):
        receipt = ApprovalReceipt(
            receipt_id="rc",
            step_id=step.step_id if step else "s",
            expires_at=datetime.now() + timedelta(minutes=5),
        )
        return ApprovalDecision(approved=True, receipt=receipt)


class AlwaysDeny(ApprovalPolicy):
    def evaluate(self, *, tool_name, route, step, context):
        return ApprovalDecision(approved=False, reason="no receipt")


class RecordingGate(ApprovalGateExecutor):
    def __init__(self, policy: ApprovalPolicy):
        super().__init__(node_name="mut", route_name="mutation", approval_policy=policy)
        self.called: bool = False
        self.received_receipt: Optional[ApprovalReceipt] = None

    async def _execute_approved(self, *, state, receipt, event_callback):
        self.called = True
        self.received_receipt = receipt
        return {"final_message": "done", "tool_calls": [], "sources": []}


def _state_with_mutation_step() -> dict[str, Any]:
    step = PlanStep(
        step_id="step-mut-1",
        route="mutation",
        execution_target="executor:mut",
        intent="mutate",
        goal="do risky thing",
        risk_level=RiskLevel.HIGH,
        requires_approval=True,
        status=PlanStepStatus.PENDING,
    )
    return {
        "session_id": "s",
        "user_id": "u",
        "context": {},
        "plan": Plan(plan_id="p", steps=[step]),
    }


@pytest.mark.asyncio
async def test_approved_forwards_to_subclass_with_receipt():
    gate = RecordingGate(AlwaysApprove())
    result = await gate.execute(_state_with_mutation_step())

    assert gate.called is True
    assert gate.received_receipt is not None
    assert result["final_message"] == "done"


@pytest.mark.asyncio
async def test_denied_short_circuits_without_calling_subclass():
    gate = RecordingGate(AlwaysDeny())
    result = await gate.execute(_state_with_mutation_step())

    assert gate.called is False
    assert "审批" in result["final_message"]
    assert result["needs_approval"] is True


@pytest.mark.asyncio
async def test_custom_denial_message_overridable():
    class LocalizedGate(RecordingGate):
        def _denial_message(self, decision):
            return f"BLOCKED: {decision.reason}"

    gate = LocalizedGate(AlwaysDeny())
    result = await gate.execute(_state_with_mutation_step())

    assert result["final_message"] == "BLOCKED: no receipt"
    assert gate.called is False
