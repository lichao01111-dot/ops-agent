"""
Planner for the Agent Kernel.

Produces a ``Plan`` consisting of one or more ``PlanStep``s from a
``ChatRequest``. After each step executes the planner decides CONTINUE /
REPLAN / FINISH.

Extension points (architecture-v2 §6 #5):
    - ``_split_compound(message) -> list[str]``
        Default returns a single segment (no split). Vertical subclasses
        override this to encode domain-specific compound request splitting
        (Ops uses Chinese conjunction heuristics like "然后 / 接着"; CSM
        might split on "退款 / 并且").
    - ``_maybe_replan(plan, last_step) -> PlanStep | None``
        Default returns None. Vertical subclasses append a follow-up step
        here (e.g. diagnosis → read-only verification).

Why subclass instead of injecting splitter/hook callables? Both pieces
routinely need multiple Vertical-private helpers; subclassing keeps the
extension surface cohesive and matches §11 ("_split_compound 做成 Vertical
可覆写的 Planner 方法").

Design invariants preserved:
- Single-intent latency: one ``_split_compound`` segment → one Plan step.
- max_iterations is a hard budget (§4.2).
- FAILED step fail-fast by default.
- Plan state lives in ``AgentState`` (LangGraph), not shared memory.
"""
from __future__ import annotations

import uuid
from typing import Optional

import structlog

from agent_kernel.router import RouterBase
from agent_kernel.schemas import (
    ChatRequest,
    Plan,
    PlanDecision,
    PlanStep,
    PlanStepStatus,
    RiskLevel,
    RouteDecision,
)

logger = structlog.get_logger()


MAX_COMPOUND_SEGMENTS = 3


class Planner:
    """Produce and advance multi-step plans.

    Vertical agents that need compound-request splitting or replan logic
    subclass this and override the protected hooks below.
    """

    def __init__(self, router: RouterBase):
        self.router = router

    # ----- Extension point: compound splitting -----

    def _split_compound(self, message: str) -> list[str]:
        """Split a compound user message into ordered sub-asks.

        Default implementation returns ``[message]`` unchanged — the Kernel
        is domain-agnostic and must not guess at Chinese / English
        conjunction heuristics. Vertical subclasses override this; see
        ``agent_ops.planner.OpsPlanner`` for the Ops implementation.

        Contract:
            - Return at least one non-empty segment.
            - Preserve input order.
            - Should cap length at :data:`MAX_COMPOUND_SEGMENTS`; subclasses
              can reuse :meth:`_dedupe_segments` as a helper.
        """
        return [message]

    @staticmethod
    def _dedupe_segments(segments: list[str], limit: int = MAX_COMPOUND_SEGMENTS) -> list[str]:
        """Utility for subclasses: filter empties, dedupe, cap at *limit*."""
        deduped: list[str] = []
        for segment in segments:
            segment = segment.strip() if segment else ""
            if segment and segment not in deduped:
                deduped.append(segment)
            if len(deduped) >= limit:
                break
        return deduped

    async def initial_plan(self, request: ChatRequest) -> Plan:
        segments = self._split_compound(request.message)
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
        """Vertical override point — append a follow-up step after the plan
        would otherwise FINISH.

        This is plugin point §6 #5 in architecture-v2. The Kernel's default
        implementation always returns ``None`` (zero domain knowledge), so
        plans terminate naturally once every PENDING step has run. Vertical
        subclasses override this to encode domain-specific replan triggers,
        e.g.:

            class OpsPlanner(Planner):
                def _maybe_replan(self, plan, last_step):
                    if (
                        last_step
                        and last_step.route == "diagnosis"
                        and "verify" in last_step.result_summary.lower()
                    ):
                        return PlanStep(
                            step_id=...,
                            route="read_only_ops",
                            execution_target="executor:read_only_ops",
                            intent=...,
                            goal="verify diagnosis",
                            depends_on=[last_step.step_id],
                        )
                    return None

        Contract:
        - Return ``None`` for the common case (no follow-up needed).
        - Returned ``PlanStep`` is appended to ``plan.steps`` and becomes the
          new cursor; ``advance`` will issue ``REPLAN`` and the kernel routes
          back through the planner node before dispatching it.
        - Each replan still counts toward ``plan.max_iterations`` — it cannot
          create an infinite loop.
        - Must NOT mutate ``plan.steps`` directly; only return a new step.
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
