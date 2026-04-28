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
from typing import Any, Callable, Optional

import structlog
from pydantic import BaseModel, Field

from agent_kernel.schemas import ChatRequest, RouteDecision
from agent_kernel.planner import MAX_COMPOUND_SEGMENTS, Planner
from agent_kernel.schemas import Plan, PlanStep, PlanStepStatus, RiskLevel
from agent_ops.schemas import AgentRoute, IntentType
from llm_gateway.prompt_registry import prompt_registry

logger = structlog.get_logger()


_PLANNER_PROMPT = (
    "你是 OpsAgent 的任务规划器，只负责把用户请求拆成有序步骤。"
    "不要回答用户问题，不要调用工具。"
    "route 只能使用 knowledge/read_only_ops/diagnosis/mutation。"
    "knowledge 用于查询文档、环境信息、数据库地址、配置来源。"
    "read_only_ops 用于查日志、Pod、Deployment、Jenkins 状态。"
    "diagnosis 用于根因分析。mutation 用于重启、扩缩容、回滚、发布等有副作用动作。"
    "如果后一步依赖前一步的信息，必须拆成多个 steps；最多 3 步。"
    "涉及副作用的步骤只能标 mutation，审批由系统处理。"
)

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

_INFRA_LOG_COMPONENTS = ("mysql", "redis", "kafka", "postgres", "postgresql")
_ALLOWED_LLM_ROUTES = {
    AgentRoute.KNOWLEDGE.value,
    AgentRoute.READ_ONLY_OPS.value,
    AgentRoute.DIAGNOSIS.value,
    AgentRoute.MUTATION.value,
}


class PlanStepDraft(BaseModel):
    route: str = Field(description="One of: knowledge, read_only_ops, diagnosis, mutation")
    goal: str = Field(description="Concrete sub-task to execute in Chinese")
    intent_hint: str = Field(default="", description="Optional intent hint")


class PlanDraft(BaseModel):
    rationale: str = ""
    steps: list[PlanStepDraft] = Field(default_factory=list, max_length=MAX_COMPOUND_SEGMENTS)


def _split_infra_log_lookup(message: str) -> list[str] | None:
    lowered = message.lower()
    if "日志" not in message and "log" not in lowered:
        return None
    component = next((token for token in _INFRA_LOG_COMPONENTS if token in lowered), "")
    if not component:
        return None
    env = "生产环境" if "生产" in message or "prod" in lowered or "production" in lowered else ""
    env_prefix = f"{env}的" if env else ""
    return [
        f"查出{env_prefix}{component}数据库地址",
        f"查询{env_prefix}{component}相关日志是否异常",
    ]


def split_compound_ops(message: str) -> list[str]:
    """Best-effort Ops compound split. Returns at most 3 segments."""
    infra_log_segments = _split_infra_log_lookup(message)
    if infra_log_segments:
        return infra_log_segments

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

    def __init__(self, router, llm_provider: Callable[[], Any] | None = None):
        super().__init__(router=router)
        self._llm_provider = llm_provider

    def _split_compound(self, message: str) -> list[str]:  # noqa: D401
        return split_compound_ops(message)

    async def initial_plan(self, request: ChatRequest) -> Plan:
        rule_segments = self._split_compound(request.message)
        if len(rule_segments) > 1:
            return await super().initial_plan(request)

        llm_plan = await self._initial_plan_with_llm(request)
        if llm_plan is not None:
            return llm_plan

        return await super().initial_plan(request)

    def _should_try_llm_planner(self, message: str) -> bool:
        lowered = message.lower()
        dependency_tokens = (
            "查出",
            "找到",
            "定位",
            "根据",
            "相关",
            "对应",
            "地址",
            "配置",
            "日志",
            "异常",
            "生产",
            "prod",
            "mysql",
            "redis",
            "kafka",
            "postgres",
        )
        if any(token in message or token in lowered for token in dependency_tokens):
            return True
        return False

    async def _initial_plan_with_llm(self, request: ChatRequest) -> Plan | None:
        if not self._llm_provider or not self._should_try_llm_planner(request.message):
            return None

        try:
            llm = self._llm_provider()
            structured = llm.with_structured_output(PlanDraft)
            prompt = prompt_registry.get_prompt("ops/planner/initial_plan", _PLANNER_PROMPT)
            draft = await structured.ainvoke(
                [
                    {"role": "system", "content": prompt.text},
                    {"role": "user", "content": request.message},
                ],
                prompt_meta=prompt.meta,
            )
        except Exception as exc:
            logger.warning("planner_llm_failed", error=str(exc))
            return None

        if not isinstance(draft, PlanDraft):
            return None

        steps = await self._steps_from_llm_draft(request, draft)
        if len(steps) <= 1:
            return None

        return Plan(
            plan_id=str(uuid.uuid4()),
            rationale=f"llm_planner_fallback:{draft.rationale or 'implicit_multi_step'}",
            steps=steps,
        )

    async def _steps_from_llm_draft(self, request: ChatRequest, draft: PlanDraft) -> list[PlanStep]:
        steps: list[PlanStep] = []
        prior_step_id: str | None = None
        seen_goals: set[str] = set()

        for index, item in enumerate(draft.steps[:MAX_COMPOUND_SEGMENTS]):
            goal = item.goal.strip()
            route = item.route.strip()
            if not goal or goal in seen_goals or route not in _ALLOWED_LLM_ROUTES:
                continue
            seen_goals.add(goal)

            if route == AgentRoute.MUTATION.value:
                decision = self._mutation_decision(goal)
            else:
                sub_request = request.model_copy(update={"message": goal})
                decision = await self.router.route(sub_request)
                if decision.route != route:
                    decision = decision.model_copy(
                        update={
                            "route": route,
                            "rationale": f"llm_planner_route:{decision.rationale}",
                            "confidence": min(decision.confidence, 0.8),
                            "source": "llm_planner",
                        }
                    )

            step = self._step_from_decision(decision, goal=goal, order=index)
            if prior_step_id:
                step.depends_on = [prior_step_id]
            steps.append(step)
            prior_step_id = step.step_id

        return steps

    @staticmethod
    def _mutation_decision(goal: str) -> RouteDecision:
        lowered = goal.lower()
        intent = IntentType.K8S_OPERATE
        if "重启" in goal or "restart" in lowered:
            intent = IntentType.K8S_RESTART
        elif any(token in goal or token in lowered for token in ("扩容", "缩容", "scale", "replicas")):
            intent = IntentType.K8S_SCALE
        elif "回滚" in goal or "rollback" in lowered:
            intent = IntentType.K8S_ROLLBACK
        return RouteDecision(
            intent=intent,
            route=AgentRoute.MUTATION,
            risk_level=RiskLevel.HIGH,
            requires_approval=True,
            rationale="llm_planner_mutation_guard",
            confidence=0.7,
            source="llm_planner",
        )

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
