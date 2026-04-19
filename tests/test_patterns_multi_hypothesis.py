"""Unit tests for agent_kernel.patterns.multi_hypothesis.MultiHypothesisExecutor.

Verifies the abstract base contracts the 5-stage pipeline correctly without any
domain knowledge — using a tiny in-test "calculator diagnosis" subclass that
proposes whether the symptom comes from `add` or `mul` overflow.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import pytest

from agent_kernel.patterns import MultiHypothesisExecutor
from agent_kernel.schemas import ToolCallEvent, ToolCallStatus


# ---------- Test fixtures ----------


@dataclass
class FakeHypothesis:
    hypothesis_id: str
    statement: str
    evidence_tools: list[str] = field(default_factory=list)
    score: float = 0.0
    evidence_summary: str = ""


async def fake_invoke_tool(name, args, event_callback=None, **_kwargs):
    event = ToolCallEvent(
        tool_name=name, action=name, params=args, status=ToolCallStatus.SUCCESS
    )
    return event, f"OK:{name}:{args}"


class CalculatorDiagnoser(MultiHypothesisExecutor[FakeHypothesis]):
    def __init__(self, *, propose: list[FakeHypothesis]):
        super().__init__(
            node_name="calc_diagnosis",
            route_name="diagnosis",
            invoke_tool=fake_invoke_tool,
        )
        self._propose = propose
        self.persisted: list[FakeHypothesis] = []
        self.persist_top: Optional[FakeHypothesis] = None

    async def _collect_symptoms(self, *, state, goal, event_callback):
        event, output = await self._invoke_tool(
            "look_at_value", {"goal": goal}, event_callback
        )
        return [event], {"value": 42}

    async def _generate_hypotheses(self, *, goal, symptoms, state):
        return list(self._propose)

    def _evidence_args_for(self, tool_name, hypothesis, symptoms, state):
        if tool_name == "skip_me":
            return None
        return {"hyp": hypothesis.hypothesis_id, "tool": tool_name}

    def _score_and_summarize(self, goal, hypotheses, symptoms):
        for h in hypotheses:
            h.score = 1.0 if "overflow" in h.statement else 0.5
        ranked = sorted(hypotheses, key=lambda h: h.score, reverse=True)
        top = ranked[0] if ranked else None
        return top, f"top={top.statement if top else 'none'}"

    def _persist(self, *, state, hypotheses, top, summary):
        self.persisted = list(hypotheses)
        self.persist_top = top


# ---------- Tests ----------


@pytest.mark.asyncio
async def test_full_pipeline_runs_all_stages():
    diag = CalculatorDiagnoser(
        propose=[
            FakeHypothesis(
                "h1", "add overflow", evidence_tools=["check_a", "check_b"]
            ),
            FakeHypothesis("h2", "stale cache", evidence_tools=["check_c"]),
        ]
    )
    state = {"session_id": "s1", "user_id": "u1", "plan": None}
    result = await diag.execute(state)

    # symptom (1) + h1 evidence (2) + h2 evidence (1) = 4 tool calls
    assert len(result["tool_calls"]) == 4
    # Final message comes from _score_and_summarize
    assert result["final_message"].startswith("top=add overflow")
    # Persistence hook ran with the top hypothesis
    assert diag.persist_top is not None and diag.persist_top.hypothesis_id == "h1"
    # All hypotheses surfaced back to caller
    assert len(result["hypotheses"]) == 2
    # evidence_summary auto-populated
    assert all(h.evidence_summary for h in diag.persisted)


@pytest.mark.asyncio
async def test_no_hypotheses_triggers_fallback():
    diag = CalculatorDiagnoser(propose=[])
    state = {"session_id": "s1", "user_id": "u1", "plan": None}
    result = await diag.execute(state)
    # Default fallback message
    assert "未能生成" in result["final_message"]
    # Symptom call still recorded
    assert len(result["tool_calls"]) == 1
    assert result["hypotheses"] == []
    # Persist NOT invoked
    assert diag.persist_top is None


@pytest.mark.asyncio
async def test_evidence_args_none_skips_tool():
    diag = CalculatorDiagnoser(
        propose=[FakeHypothesis("h1", "x", evidence_tools=["skip_me", "real_tool"])]
    )
    state = {"session_id": "s1", "user_id": "u1", "plan": None}
    result = await diag.execute(state)
    # symptom (1) + only "real_tool" (1, "skip_me" skipped) = 2
    assert len(result["tool_calls"]) == 2


@pytest.mark.asyncio
async def test_generation_exception_falls_back():
    class Boomer(CalculatorDiagnoser):
        async def _generate_hypotheses(self, **_):
            raise RuntimeError("LLM down")

    diag = Boomer(propose=[])
    state = {"session_id": "s1", "user_id": "u1", "plan": None}
    result = await diag.execute(state)
    assert "未能生成" in result["final_message"]
    assert diag.persist_top is None


@pytest.mark.asyncio
async def test_evidence_tool_exception_does_not_break_pipeline():
    async def flaky_invoke(name, args, event_callback=None, **_kwargs):
        if name == "boom":
            raise RuntimeError("tool exploded")
        return await fake_invoke_tool(name, args, event_callback, **_kwargs)

    diag = CalculatorDiagnoser(
        propose=[FakeHypothesis("h1", "x overflow", evidence_tools=["boom", "ok"])]
    )
    diag._invoke_tool = flaky_invoke  # type: ignore[assignment]

    state = {"session_id": "s1", "user_id": "u1", "plan": None}
    result = await diag.execute(state)
    # symptom (1) + only "ok" (1) = 2; "boom" swallowed
    assert len(result["tool_calls"]) == 2
    assert diag.persist_top is not None
