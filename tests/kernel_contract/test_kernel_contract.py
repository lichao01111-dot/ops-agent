from __future__ import annotations

import json

import pytest

from agent_kernel import (
    BaseAgent,
    ChatRequest,
    ExecutorBase,
    MemorySchema,
    Planner,
    RouterBase,
    create_audit_logger,
    create_session_store,
    create_tool_registry,
)
from agent_kernel.schemas import RiskLevel, RouteDecision, ToolCallStatus, ToolSource, ToolSpec
from agent_ops import create_ops_agent


class DummyRouter(RouterBase):
    async def route(self, request: ChatRequest) -> RouteDecision:
        return RouteDecision(
            intent="dummy_intent",
            route="dummy_route",
            risk_level=RiskLevel.LOW,
        )


class DummyExecutor(ExecutorBase):
    def __init__(self):
        super().__init__(node_name="dummy_route", route_name="dummy_route")

    async def execute(self, state):
        return {
            "final_message": f"dummy handled: {state['plan'].current_step().goal}",
            "tool_calls": [],
            "sources": ["dummy://source"],
        }


@pytest.mark.asyncio
async def test_dummy_vertical_can_register_independent_route_and_memory_layer():
    schema = MemorySchema(
        layers={
            "dummy_layer": {"dummy_writer"},
        }
    )
    session_store = create_session_store(memory_schema=schema)
    planner = Planner(router=DummyRouter())
    agent = BaseAgent(
        planner=planner,
        session_store=session_store,
        audit_logger=create_audit_logger(),
        executors=[DummyExecutor()],
    )

    response = await agent.chat(ChatRequest(message="hello", session_id="dummy-s1"))

    session_store.write_memory_item(
        "dummy-s1",
        writer="dummy_writer",
        layer="dummy_layer",
        key="dummy_key",
        value="dummy_value",
    )

    assert response.route == "dummy_route"
    assert response.message == "dummy handled: hello"
    assert session_store.resolve_memory_value("dummy-s1", "dummy_key", ["dummy_layer"]) == "dummy_value"


@pytest.mark.asyncio
async def test_side_effect_tool_without_approval_receipt_fails():
    agent = create_ops_agent()
    registry = create_tool_registry()

    class DummyHandler:
        async def ainvoke(self, args):
            return json.dumps({"status": "unexpected_success", "args": args}, ensure_ascii=False)

    registry.register_mcp(
        ToolSpec(
            name="dangerous_tool",
            description="side effect tool",
            side_effect=True,
            route_affinity=["mutation"],
            source=ToolSource.MCP,
        ),
        DummyHandler(),
    )
    agent.tool_registry = registry

    step = agent.planner.fallback_plan(ChatRequest(message="run dangerous action")).steps[0]
    step.route = "mutation"
    step.execution_target = "executor:mutation"
    step.requires_approval = True

    event, output = await agent._invoke_tool(
        "dangerous_tool",
        {"action": "boom"},
        session_id="danger-s1",
        route="mutation",
        step=step,
        execution_target=step.execution_target,
    )

    assert event.status == ToolCallStatus.FAILED
    assert "approval_receipt" in output


def test_vertical_session_store_instances_do_not_share_data():
    ops_store = create_session_store(
        memory_schema=MemorySchema(write_permissions={"ops_writer": {"facts"}})
    )
    doc_store = create_session_store(
        memory_schema=MemorySchema(write_permissions={"doc_writer": {"doc_context"}})
    )

    ops_store.write_memory_item(
        "shared-session",
        writer="ops_writer",
        layer="facts",
        key="namespace",
        value="staging",
    )

    assert doc_store.resolve_memory_value("shared-session", "namespace", ["facts", "doc_context"]) is None


@pytest.mark.asyncio
async def test_base_agent_dynamic_executor_wiring_is_not_hardcoded():
    planner = Planner(router=DummyRouter())
    session_store = create_session_store(
        memory_schema=MemorySchema(write_permissions={"dummy_writer": {"dummy_layer"}})
    )
    agent = BaseAgent(
        planner=planner,
        session_store=session_store,
        audit_logger=create_audit_logger(),
        executors=[DummyExecutor()],
    )

    response = await agent.chat(ChatRequest(message="dynamic route", session_id="route-s1"))

    assert response.route == "dummy_route"
    assert response.sources == ["dummy://source"]
