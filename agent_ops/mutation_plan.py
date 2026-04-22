"""
MutationPlan — typed record linking a change action to its verification and rollback specs.

Created by MutationExecutor and stored in `session_store` PLANS layer so that
VerificationExecutor can consume it in the auto-appended verification step.

Design (architecture-v2 §9):
  - MutationAction enumerates every side-effecting operation the Ops vertical supports.
  - VerificationCriteria describes WHAT to check (tool + args) and HOW OFTEN (poll cadence).
  - RollbackSpec describes HOW to undo the mutation and WHAT to tell the operator.
  - MutationPlan ties them together with execution provenance (step_id, approval_receipt_id).
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class MutationAction(str, Enum):
    RESTART_DEPLOYMENT = "restart_deployment"
    SCALE_DEPLOYMENT = "scale_deployment"
    ROLLBACK_DEPLOYMENT = "rollback_deployment"
    INDEX_DOCUMENTS = "index_documents"
    GENERATE_JENKINSFILE = "generate_jenkinsfile"


class VerificationCriteria(BaseModel):
    """Specifies how to verify a mutation succeeded."""

    tool: str
    """Name of the read-only tool to invoke for verification (e.g. get_deployment_status)."""

    args: dict[str, Any] = Field(default_factory=dict)
    """Arguments forwarded verbatim to the verification tool."""

    success_condition: str = ""
    """Human-readable description of the success condition (for messages/logs)."""

    poll_interval_s: int = 10
    """Seconds to wait between polling attempts."""

    max_attempts: int = 6
    """Maximum number of polling attempts before declaring failure (default: 1 minute)."""

    expected_replicas: int | None = None
    """For deployment checks: ready_replicas must equal this value."""


class RollbackSpec(BaseModel):
    """Describes how to undo a mutation if verification fails."""

    tool: str
    """Name of the side-effecting tool to call for rollback."""

    args: dict[str, Any] = Field(default_factory=dict)
    """Arguments forwarded verbatim to the rollback tool."""

    escalation_message: str = ""
    """Message to surface to the operator after rollback (or when no rollback is possible)."""


class MutationPlan(BaseModel):
    """Full record of a mutation, its verification criteria, and rollback plan.

    Produced by MutationExecutor and stored in the session PLANS memory layer
    under key ``mutation_plan`` as a JSON string.
    """

    action: MutationAction
    target: str
    """Human-readable target (deployment name, docs dir, project name)."""

    namespace: str = "default"
    tool_name: str
    """The exact tool that was invoked."""

    tool_args: dict[str, Any] = Field(default_factory=dict)
    """Arguments passed to that tool."""

    verification: VerificationCriteria | None = None
    rollback: RollbackSpec | None = None

    execution_status: str = "pending"
    """pending / completed / failed — updated after the tool call."""

    mutation_step_id: str = ""
    approval_receipt_id: str = ""


# ---------------------------------------------------------------------------
# Helpers for building pre-wired MutationPlans
# ---------------------------------------------------------------------------

def build_restart_plan(
    namespace: str,
    name: str,
    approval_receipt_id: str = "",
    step_id: str = "",
) -> MutationPlan:
    """Return a MutationPlan for a deployment rolling-restart."""
    return MutationPlan(
        action=MutationAction.RESTART_DEPLOYMENT,
        target=name,
        namespace=namespace,
        tool_name="restart_deployment",
        tool_args={"namespace": namespace, "name": name},
        verification=VerificationCriteria(
            tool="get_deployment_status",
            args={"namespace": namespace, "name": name},
            success_condition=f"Deployment {name} 所有副本 Ready",
            poll_interval_s=10,
            max_attempts=6,
        ),
        rollback=RollbackSpec(
            tool="rollback_deployment",
            args={"namespace": namespace, "name": name, "revision": 0},
            escalation_message=(
                f"Deployment {name} 重启后验证失败，已触发回滚。"
                "请检查镜像和配置是否正确。"
            ),
        ),
        mutation_step_id=step_id,
        approval_receipt_id=approval_receipt_id,
    )


def build_scale_plan(
    namespace: str,
    name: str,
    replicas: int,
    approval_receipt_id: str = "",
    step_id: str = "",
) -> MutationPlan:
    """Return a MutationPlan for a deployment scale operation."""
    return MutationPlan(
        action=MutationAction.SCALE_DEPLOYMENT,
        target=name,
        namespace=namespace,
        tool_name="scale_deployment",
        tool_args={"namespace": namespace, "name": name, "replicas": replicas},
        verification=VerificationCriteria(
            tool="get_deployment_status",
            args={"namespace": namespace, "name": name},
            success_condition=f"Deployment {name} ready_replicas == {replicas}",
            poll_interval_s=10,
            max_attempts=9,
            expected_replicas=replicas,
        ),
        rollback=None,  # scale has no auto-rollback; operator decides
        mutation_step_id=step_id,
        approval_receipt_id=approval_receipt_id,
    )


def build_rollback_plan(
    namespace: str,
    name: str,
    revision: int = 0,
    approval_receipt_id: str = "",
    step_id: str = "",
) -> MutationPlan:
    """Return a MutationPlan for a deployment rollback."""
    return MutationPlan(
        action=MutationAction.ROLLBACK_DEPLOYMENT,
        target=name,
        namespace=namespace,
        tool_name="rollback_deployment",
        tool_args={"namespace": namespace, "name": name, "revision": revision},
        verification=VerificationCriteria(
            tool="get_deployment_status",
            args={"namespace": namespace, "name": name},
            success_condition=f"Deployment {name} 回滚后所有副本 Ready",
            poll_interval_s=10,
            max_attempts=6,
        ),
        rollback=RollbackSpec(
            tool="",  # no further rollback after a rollback
            args={},
            escalation_message=(
                f"Deployment {name} 回滚后验证仍然失败，请立即人工介入。"
                "建议检查历史 revision 是否可用：kubectl rollout history。"
            ),
        ),
        mutation_step_id=step_id,
        approval_receipt_id=approval_receipt_id,
    )
