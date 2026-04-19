"""
Generic multi-hypothesis execution pattern.

Architecture-v2 §5.3 / §6 #10:

> "Kernel 只提供抽象 ... 子类实现：_collect_symptoms / _score_hypothesis /
> _summarize ... agent_ops/executors/diagnosis.py 继承它，填入 Ops 的
> 症状采集、Ops 的打分规则。"

This module supplies that abstract base. It is **opt-in** — verticals can
either inherit from `MultiHypothesisExecutor` to get the 5-stage pipeline
for free, or implement `ExecutorBase.execute` from scratch.

The class is deliberately:
- generic over the hypothesis payload type (so different verticals can
  carry domain-specific fields without leaking those types into Kernel)
- agnostic to LLM / tool registry / topology (those are injected via
  callables; Kernel imports nothing domain-specific)
- pure orchestration: ordering, fan-out, fail-safe degradation. All
  domain heuristics live in subclass overrides.

Pipeline stages (all subclass-overridable hooks):

    1. _collect_symptoms      ─ deterministic read tools every diagnosis needs
    2. _generate_hypotheses   ─ produce up to N hypotheses (LLM or rules)
    3. _gather_evidence       ─ run each hypothesis's evidence in parallel
    4. _score_and_synthesize  ─ rank, pick top, build human-readable summary
    5. _persist               ─ write hypotheses + top-pick into memory

Failure handling: any stage returning empty / raising falls back to
`_fallback`, which the subclass can use to surface partial results
instead of going silent. This satisfies the §10 L1 degradation rule.
"""
from __future__ import annotations

import abc
import asyncio
from typing import Any, Awaitable, Callable, Generic, Optional, Protocol, TypeVar

import structlog

from agent_kernel.executor import ExecutorBase
from agent_kernel.schemas import ToolCallEvent

logger = structlog.get_logger()

EventCallback = Callable[[str, dict[str, Any]], Awaitable[None]]


class HypothesisProtocol(Protocol):
    """Minimum shape a vertical-specific Hypothesis must satisfy.

    Verticals are free to extend with arbitrary fields (suspected_target,
    evidence_tools, verdict, …) — the Kernel pattern only reads the few
    attributes listed here.
    """

    hypothesis_id: str
    statement: str
    score: float
    evidence_summary: str


H = TypeVar("H", bound=HypothesisProtocol)


class MultiHypothesisExecutor(ExecutorBase, Generic[H], abc.ABC):
    """Skeleton for the multi-hypothesis diagnosis pattern.

    Subclasses MUST implement:
        - ``_collect_symptoms(state, goal) -> (events, payloads)``
        - ``_generate_hypotheses(goal, symptoms) -> list[H]``
        - ``_evidence_args_for(hypothesis, symptoms, state) -> dict | None``
            per evidence-tool argument synthesis (return None to skip)
        - ``_score_and_summarize(goal, hypotheses, symptoms) -> (top, summary)``
        - ``_persist(state, hypotheses, top, summary) -> None``

    Subclasses MAY override:
        - ``_fallback(state, goal, symptom_events) -> dict``
            invoked when ``_generate_hypotheses`` returns empty or raises
        - ``_evidence_tools_for(hypothesis) -> list[str]``
            defaults to ``hypothesis.evidence_tools`` if present, else []

    Constructor injection deliberately keeps the Kernel decoupled:
        - ``invoke_tool``: the agent's audited tool dispatcher
        - any other dependency (LLM, retriever, topology) is passed through
          subclass __init__ — not stored on the base.
    """

    def __init__(
        self,
        *,
        node_name: str,
        route_name: str,
        invoke_tool: Callable[..., Awaitable[tuple[ToolCallEvent, str]]],
    ):
        super().__init__(node_name=node_name, route_name=route_name)
        self._invoke_tool = invoke_tool

    # ---------- Public entry: execute() ----------

    async def execute(
        self,
        state: dict[str, Any],
        event_callback: EventCallback | None = None,
    ) -> dict[str, Any]:
        plan = state.get("plan")
        step = plan.current_step() if plan else None
        goal = step.goal if step else ""

        symptom_events, symptom_payloads = await self._collect_symptoms(
            state=state, goal=goal, event_callback=event_callback
        )

        try:
            hypotheses = await self._generate_hypotheses(
                goal=goal, symptoms=symptom_payloads, state=state
            )
        except Exception as exc:
            logger.warning("hypothesis_generation_raised", error=str(exc))
            hypotheses = []

        if not hypotheses:
            return await self._fallback(
                state=state, goal=goal, symptom_events=symptom_events
            )

        evidence_events, enriched = await self._collect_evidence_parallel(
            state=state,
            hypotheses=hypotheses,
            event_callback=event_callback,
            symptoms=symptom_payloads,
        )

        top, summary = self._score_and_summarize(goal, enriched, symptom_payloads)
        try:
            self._persist(state=state, hypotheses=enriched, top=top, summary=summary)
        except Exception as exc:
            # Persistence failure is non-fatal; degraded path still returns the answer.
            logger.warning("hypothesis_persist_failed", error=str(exc))

        return {
            "final_message": summary,
            "tool_calls": symptom_events + evidence_events,
            "sources": [],
            "hypotheses": [self._hypothesis_to_dict(h) for h in enriched],
        }

    # ---------- Abstract subclass hooks ----------

    @abc.abstractmethod
    async def _collect_symptoms(
        self,
        *,
        state: dict[str, Any],
        goal: str,
        event_callback: EventCallback | None,
    ) -> tuple[list[ToolCallEvent], dict[str, Any]]:
        """Stage 1 — fire deterministic read tools to surface symptoms."""

    @abc.abstractmethod
    async def _generate_hypotheses(
        self,
        *,
        goal: str,
        symptoms: dict[str, Any],
        state: dict[str, Any],
    ) -> list[H]:
        """Stage 2 — propose up to N hypotheses (LLM-driven or rule-based)."""

    @abc.abstractmethod
    def _evidence_args_for(
        self,
        tool_name: str,
        hypothesis: H,
        symptoms: dict[str, Any],
        state: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        """Stage 3 helper — synthesize args for an evidence tool, or skip."""

    @abc.abstractmethod
    def _score_and_summarize(
        self,
        goal: str,
        hypotheses: list[H],
        symptoms: dict[str, Any],
    ) -> tuple[Optional[H], str]:
        """Stage 4 — rank hypotheses and produce a final user-facing summary."""

    @abc.abstractmethod
    def _persist(
        self,
        *,
        state: dict[str, Any],
        hypotheses: list[H],
        top: Optional[H],
        summary: str,
    ) -> None:
        """Stage 5 — write hypotheses + top-pick to memory."""

    # ---------- Overridable hooks ----------

    async def _fallback(
        self,
        *,
        state: dict[str, Any],
        goal: str,
        symptom_events: list[ToolCallEvent],
    ) -> dict[str, Any]:
        """Default degraded path: return a generic message + collected symptoms."""
        return {
            "final_message": (
                "未能生成多条诊断假设；已基于初步症状返回结果。"
                "请补充上下文或重试以获得更深入的分析。"
            ),
            "tool_calls": symptom_events,
            "sources": [],
            "hypotheses": [],
        }

    def _evidence_tools_for(self, hypothesis: H) -> list[str]:
        """Default: read ``evidence_tools`` if present, else nothing."""
        return list(getattr(hypothesis, "evidence_tools", []) or [])

    def _hypothesis_to_dict(self, hypothesis: H) -> dict[str, Any]:
        """Best-effort serialization for streaming back to the caller."""
        if hasattr(hypothesis, "model_dump"):
            return hypothesis.model_dump()  # type: ignore[no-any-return]
        if hasattr(hypothesis, "dict"):
            return hypothesis.dict()  # type: ignore[no-any-return]
        return {"hypothesis_id": hypothesis.hypothesis_id, "statement": hypothesis.statement}

    # ---------- Stage 3 implementation (parallel evidence) ----------

    async def _collect_evidence_parallel(
        self,
        *,
        state: dict[str, Any],
        hypotheses: list[H],
        event_callback: EventCallback | None,
        symptoms: dict[str, Any],
    ) -> tuple[list[ToolCallEvent], list[H]]:
        tasks = [
            self._collect_for_hypothesis(
                state=state,
                hypothesis=hypothesis,
                event_callback=event_callback,
                symptoms=symptoms,
            )
            for hypothesis in hypotheses
        ]
        gathered = await asyncio.gather(*tasks, return_exceptions=False)

        events: list[ToolCallEvent] = []
        enriched: list[H] = []
        for hypothesis, (h_events, h_payloads) in zip(hypotheses, gathered):
            # Subclass may pre-attach evidence_summary; otherwise leave default.
            if hasattr(hypothesis, "evidence_summary") and not getattr(
                hypothesis, "evidence_summary", ""
            ):
                hypothesis.evidence_summary = self._summarize_evidence(h_payloads)
            enriched.append(hypothesis)
            events.extend(h_events)
        return events, enriched

    async def _collect_for_hypothesis(
        self,
        *,
        state: dict[str, Any],
        hypothesis: H,
        event_callback: EventCallback | None,
        symptoms: dict[str, Any],
    ) -> tuple[list[ToolCallEvent], dict[str, Any]]:
        events: list[ToolCallEvent] = []
        payloads: dict[str, Any] = {}
        for tool_name in self._evidence_tools_for(hypothesis):
            args = self._evidence_args_for(tool_name, hypothesis, symptoms, state)
            if args is None:
                continue
            try:
                event, output = await self._invoke_tool(
                    tool_name,
                    args,
                    event_callback,
                    user_id=state.get("user_id", ""),
                    session_id=state.get("session_id", ""),
                )
                events.append(event)
                payloads[tool_name] = output
            except Exception as exc:
                logger.warning(
                    "evidence_tool_failed",
                    hypothesis=getattr(hypothesis, "hypothesis_id", "?"),
                    tool=tool_name,
                    error=str(exc),
                )
        return events, payloads

    def _summarize_evidence(self, payloads: dict[str, Any]) -> str:
        """Default trivial joiner; override for richer formatting."""
        if not payloads:
            return ""
        return "; ".join(f"{k}={str(v)[:80]}" for k, v in payloads.items())
