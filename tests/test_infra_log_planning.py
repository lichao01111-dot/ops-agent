from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.messages import HumanMessage

from agent_kernel.schemas import ChatRequest, Plan, PlanStep, RiskLevel, ToolCallEvent, ToolCallStatus
from agent_kernel import create_session_store
from agent_ops.executors.read_only import ReadOnlyOpsExecutor
from agent_ops.planner import OpsPlanner
from agent_ops.router import IntentRouter


class FakeStructuredPlanner:
    def __init__(self, draft):
        self.draft = draft
        self.calls = []

    async def ainvoke(self, messages, **kwargs):
        self.calls.append(messages)
        return self.draft


class FakePlannerLLM:
    def __init__(self, draft):
        self.structured = FakeStructuredPlanner(draft)

    def with_structured_output(self, schema):
        return self.structured


@pytest.mark.asyncio
async def test_infra_log_request_splits_into_knowledge_then_log_step():
    planner = OpsPlanner(router=IntentRouter(), llm_provider=lambda: None)

    plan = await planner.initial_plan(
        ChatRequest(
            message="帮我检查一下生产环境的mysql的日志 是否有什么异常",
            session_id="infra-log-plan",
        )
    )

    assert [step.route for step in plan.steps] == ["knowledge", "read_only_ops"]
    assert "数据库地址" in plan.steps[0].goal
    assert "日志" in plan.steps[1].goal
    assert plan.steps[1].depends_on == [plan.steps[0].step_id]


@pytest.mark.asyncio
async def test_rule_split_wins_before_llm_planner():
    from agent_ops.planner import PlanDraft, PlanStepDraft

    fake_llm = FakePlannerLLM(
        PlanDraft(steps=[PlanStepDraft(route="knowledge", goal="不应该使用这个 LLM 结果")])
    )
    planner = OpsPlanner(router=IntentRouter(), llm_provider=lambda: fake_llm)

    plan = await planner.initial_plan(
        ChatRequest(
            message="帮我检查一下生产环境的mysql的日志 是否有什么异常",
            session_id="infra-log-rule-first",
        )
    )

    assert [step.route for step in plan.steps] == ["knowledge", "read_only_ops"]
    assert fake_llm.structured.calls == []


@pytest.mark.asyncio
async def test_llm_planner_fallback_handles_implicit_multi_step():
    from agent_ops.planner import PlanDraft, PlanStepDraft

    fake_llm = FakePlannerLLM(
        PlanDraft(
            rationale="需要先找配置再查日志",
            steps=[
                PlanStepDraft(route="knowledge", goal="查询生产环境订单库地址"),
                PlanStepDraft(route="read_only_ops", goal="查询生产环境订单库相关日志"),
            ],
        )
    )
    planner = OpsPlanner(router=IntentRouter(), llm_provider=lambda: fake_llm)

    plan = await planner.initial_plan(
        ChatRequest(
            message="看看生产订单库有没有异常",
            session_id="infra-log-llm-fallback",
        )
    )

    assert plan.rationale.startswith("llm_planner_fallback")
    assert [step.route for step in plan.steps] == ["knowledge", "read_only_ops"]
    assert plan.steps[1].depends_on == [plan.steps[0].step_id]
    assert fake_llm.structured.calls


@pytest.mark.asyncio
async def test_read_only_infra_log_step_uses_component_as_log_service():
    calls: list[tuple[str, dict[str, Any]]] = []

    async def invoke_tool(tool_name: str, args: dict[str, Any], *_, **__) -> tuple[ToolCallEvent, str]:
        calls.append((tool_name, args))
        return (
            ToolCallEvent(
                tool_name=tool_name,
                action=tool_name,
                params=args,
                status=ToolCallStatus.SUCCESS,
            ),
            json.dumps(
                {
                    "service": args.get("service"),
                    "level": args.get("level"),
                    "count": 0,
                    "logs": [],
                },
                ensure_ascii=False,
            ),
        )

    step = PlanStep(
        step_id="step-log",
        route="read_only_ops",
        execution_target="executor:read_only_ops",
        intent="log_search",
        goal="查询生产环境的mysql相关日志是否异常",
        risk_level=RiskLevel.LOW,
    )
    state = {
        "messages": [HumanMessage(content="帮我检查一下生产环境的mysql的日志 是否有什么异常")],
        "session_id": "infra-log-exec",
        "user_id": "tester",
        "context": {},
        "plan": Plan(plan_id="plan-infra-log", steps=[step]),
    }

    result = await ReadOnlyOpsExecutor(invoke_tool, create_session_store()).execute(state)

    assert calls == [
        (
            "search_logs",
            {
                "service": "mysql",
                "time_range_minutes": 60,
                "level": "ERROR",
                "keyword": "",
                "limit": 50,
            },
        )
    ]
    assert result["tool_calls"][0].tool_name == "search_logs"
