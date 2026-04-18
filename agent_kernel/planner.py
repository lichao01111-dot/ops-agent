"""
Planner for OpsAgent.

Replaces the one-shot ``IntentRouter`` with a planner that produces a ``Plan``
consisting of one or more ``PlanStep``s. After each step executes, the planner
decides to continue, replan (append new steps), or finish.

Design goals:
- Preserve single-intent latency: simple queries still produce a 1-step plan
  via the keyword-based fast path (wrapping :class:`IntentRouter`).
- Support genuine multi-intent requests (e.g. "check X, then restart it") by
  splitting on explicit conjunctions.
- Support replan: diagnosis steps can hint at follow-up read-only or mutation
  steps without forcing the user to repeat themselves.
- Keep plan state inside ``AgentState`` (LangGraph), not in shared memory.
"""
from __future__ import annotations

import re
import uuid
from typing import Any, Optional

import structlog

from agent_kernel.router import RouterBase
from agent_kernel.schemas import (
    ChatRequest,
    IntentTypeKey,
    Plan,
    PlanDecision,
    PlanStep,
    PlanStepStatus,
    RiskLevel,
    RouteDecision,
    RouteKey,
)

logger = structlog.get_logger()


# Simple conjunction heuristics for splitting compound requests. Kept minimal
# on purpose — the LLM slow path handles anything tricky.
_SPLIT_PATTERNS = [
    re.compile(r"\s*然后\s*"),
    re.compile(r"\s*接着\s*"),
    re.compile(r"\s*再\s*(?=(?:帮|把|重|触|回|生|执))"),
    re.compile(r"\s*并\s*(?=(?:帮|把|重|触|回|生|执))"),
    re.compile(r"\s*,\s*然后\s*"),
    re.compile(r"\s*，\s*然后\s*"),
]


def _split_compound(message: str) -> list[str]:
    """Best-effort split of compound asks. Returns at most 3 segments."""
    segments: list[str] = [message]
    for pattern in _SPLIT_PATTERNS:
        next_segments: list[str] = []
        for segment in segments:
            pieces = [piece.strip() for piece in pattern.split(segment) if piece and piece.strip()]
            if pieces:
                next_segments.extend(pieces)
            else:
                next_segments.append(segment)
        segments = next_segments
    # Filter duplicates while preserving order, cap at 3.
    deduped: list[str] = []
    for segment in segments:
        if segment and segment not in deduped:
            deduped.append(segment)
        if len(deduped) >= 3:
            break
    return deduped


class Planner:
    """Produce and advance multi-step plans."""

    def __init__(self, router: RouterBase):
        self.router = router

    async def initial_plan(self, request: ChatRequest) -> Plan:
        segments = _split_compound(request.message)
        steps: list[PlanStep] = []

        if len(segments) <= 1:
            decision = await self.router.route(request)
            steps.append(self._step_from_decision(decision, goal=request.message, order=0))
        else:
            prior_step_id: str | None = None
            for index, segment in enumerate(segments):
                sub_request = request.model_copy(update={"message": segment})
                decision = await self.router.route(sub_request)
                step = self._step_from_decision(decision, goal=segment, order=index)
                if prior_step_id:
                    step.depends_on = [prior_step_id]
                steps.append(step)
                prior_step_id = step.step_id

        rationale = "fast_path_keyword_routing" if len(segments) <= 1 else "compound_request_split"
        plan = Plan(plan_id=str(uuid.uuid4()), rationale=rationale, steps=steps)
        logger.info(
            "plan_created",
            plan_id=plan.plan_id,
            step_count=len(plan.steps),
            routes=[step.route for step in plan.steps],
            rationale=rationale,
        )
        return plan

    def advance(self, plan: Plan, *, last_step: Optional[PlanStep]) -> PlanDecision:
        """Decide what to do after a step completes.

        v1 logic:
        - If any pending step remains, CONTINUE.
        - Otherwise, check replan triggers on the last completed step.
        - Otherwise, FINISH.
        """
        plan.iterations += 1
        if plan.iterations >= plan.max_iterations:
            logger.info("plan_max_iterations_reached", plan_id=plan.plan_id)
            plan.done = True
            return PlanDecision.FINISH

        # Fail-fast: a FAILED step terminates the plan unless a recovery hook
        # appends a new step (future work). v1 treats FAILED as terminal.
        if last_step and last_step.status == PlanStepStatus.FAILED:
            plan.done = True
            return PlanDecision.FINISH

        if any(step.status == PlanStepStatus.PENDING for step in plan.steps):
            plan.cursor = next(
                idx for idx, step in enumerate(plan.steps)
                if step.status == PlanStepStatus.PENDING
            )
            return PlanDecision.CONTINUE

        replan_step = self._maybe_replan(plan, last_step)
        if replan_step is not None:
            plan.steps.append(replan_step)
            plan.cursor = len(plan.steps) - 1
            logger.info(
                "plan_replanned",
                plan_id=plan.plan_id,
                new_step=replan_step.step_id,
                route=replan_step.route,
            )
            return PlanDecision.REPLAN

        plan.done = True
        return PlanDecision.FINISH

    def _maybe_replan(self, plan: Plan, last_step: Optional[PlanStep]) -> PlanStep | None:
        """Conservative replan: only trigger on well-defined hints.

        Current hooks:
        - knowledge step that wrote `service` and the original request suggests
          follow-up ops -> append a read_only_ops step. (Skipped in v1 to keep
          tests deterministic; placeholder for future enhancement.)
        - diagnosis step whose summary suggests a read-only re-check. (Also
          skipped in v1.)

        Returning None means "no replan"; FINISH will be issued.
        """
        return None

    def _step_from_decision(self, decision: RouteDecision, *, goal: str, order: int) -> PlanStep:
        step_id = f"step-{order}-{uuid.uuid4().hex[:6]}"
        return PlanStep(
            step_id=step_id,
            route=decision.route,
            execution_target=f"executor:{decision.route}",
            intent=decision.intent,
            goal=goal,
            inputs={},
            risk_level=decision.risk_level,
            requires_approval=decision.requires_approval,
            status=PlanStepStatus.PENDING,
        )

    @staticmethod
    def fallback_plan(request: ChatRequest) -> Plan:
        """Emergency fallback when the router fails outright."""
        step = PlanStep(
            step_id=f"step-fallback-{uuid.uuid4().hex[:6]}",
            route="knowledge",
            execution_target="executor:knowledge",
            intent="general_chat",
            goal=request.message,
            risk_level=RiskLevel.LOW,
        )
        return Plan(plan_id=str(uuid.uuid4()), rationale="fallback", steps=[step])
