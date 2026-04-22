"""Ops-specific Planner subclass.

The Kernel Planner is domain-agnostic (see ``agent_kernel/planner.py``).
OpsPlanner plugs in two Ops-specific extension points:

1. ``_split_compound``: Chinese conjunction heuristics —
   "先查…然后重启", "回滚并验证", etc.  Per architecture-v2 §11 these
   keywords must NOT live in the Kernel.

2. ``_maybe_replan``: After a MUTATION step succeeds, auto-append a
   VERIFICATION step so the executor can poll/rollback/escalate without
   the user having to explicitly ask.
"""
from __future__ import annotations

import re
import uuid
from typing import Optional

from agent_kernel.planner import MAX_COMPOUND_SEGMENTS, Planner
from agent_kernel.schemas import Plan, PlanStep, PlanStepStatus, RiskLevel

# Ops-flavored conjunction heuristics. Kept minimal on purpose — the LLM
# slow path handles anything tricky. The trailing look-aheads on "再 / 并"
# try to avoid over-splitting short words.
_OPS_SPLIT_PATTERNS = [
    re.compile(r"\s*然后\s*"),
    re.compile(r"\s*接着\s*"),
    re.compile(r"\s*再\s*(?=(?:帮|把|重|触|回|生|执))"),
    re.compile(r"\s*并\s*(?=(?:帮|把|重|触|回|生|执))"),
    re.compile(r"\s*,\s*然后\s*"),
    re.compile(r"\s*，\s*然后\s*"),
]

# K8s mutation intents that require a verification follow-up
_MUTATION_INTENTS_NEEDING_VERIFY = {
    "k8s_operate",
    "k8s_restart",
    "k8s_scale",
    "k8s_rollback",
}


def split_compound_ops(message: str) -> list[str]:
    """Best-effort Ops compound split. Returns at most 3 segments."""
    segments: list[str] = [message]
    for pattern in _OPS_SPLIT_PATTERNS:
        next_segments: list[str] = []
        for segment in segments:
            pieces = [piece.strip() for piece in pattern.split(segment) if piece and piece.strip()]
            if pieces:
                next_segments.extend(pieces)
            else:
                next_segments.append(segment)
        segments = next_segments
    return Planner._dedupe_segments(segments, limit=MAX_COMPOUND_SEGMENTS)


class OpsPlanner(Planner):
    """Planner with Ops-specific compound splitting and mutation → verification replan."""

    def _split_compound(self, message: str) -> list[str]:  # noqa: D401
        return split_compound_ops(message)

    def _maybe_replan(self, plan: Plan, last_step: Optional[PlanStep]) -> PlanStep | None:
        """Auto-append a VERIFICATION step after a successful K8s mutation step.

        Conditions:
        - last_step route is "mutation" and status is SUCCEEDED
        - The intent indicates a K8s operation (not index_documents / Jenkinsfile)
        - No verification step has been added to this plan already
        """
        if last_step is None:
            return None
        if last_step.route != "mutation":
            return None
        if last_step.status != PlanStepStatus.SUCCEEDED:
            return None
        # Only for K8s mutating intents — skip for docs indexing / Jenkinsfile generation
        if str(last_step.intent) not in _MUTATION_INTENTS_NEEDING_VERIFY:
            return None
        # Guard against duplicate verification steps (e.g. after rollback within verify)
        if any(s.route == "verification" for s in plan.steps):
            return None

        step_id = f"step-verify-{uuid.uuid4().hex[:6]}"
        return PlanStep(
            step_id=step_id,
            route="verification",
            execution_target="executor:verification",
            intent="verify_mutation",
            goal=f"验证变更结果: {last_step.goal}",
            depends_on=[last_step.step_id],
            risk_level=RiskLevel.LOW,
            requires_approval=False,
        )
