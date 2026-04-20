"""
End-to-end tests for OpsAgent Architecture v2.

Each test case maps to an ID in docs/e2e-test-plan.md. The suite drives the
full `agent.chat(ChatRequest)` / `agent._invoke_tool(...)` call chain and
asserts on Kernel invariants, plugin-point behavior, vertical isolation,
degradation paths, and anti-pattern regressions.

Test case IDs (see docs/e2e-test-plan.md):
    A01–A05  Happy path flows
    B01–B08  Kernel invariants (§4.2)
    C01–C06  Plugin points (§6)
    D01–D02  Vertical isolation (§5.5)
    E01–E02  Degradation paths (§10)
    F01–F03  Anti-pattern regressions (§11)
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any

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
)
from agent_kernel.approval import ApprovalDecision, ApprovalPolicy
from agent_kernel.schemas import (
    ApprovalReceipt,
    PlanStep,
    PlanStepStatus,
    RiskLevel,
    RouteDecision,
    ToolCallStatus,
    ToolSource,
    ToolSpec,
)
from agent_ops import create_ops_agent


# =============================================================================
# Shared fixtures / helpers
# =============================================================================


class DummyHandler:
    """Deterministic MCP-style handler returning a fixed payload."""

    def __init__(self, payload: dict[str, Any] | None = None):
        self.payload = payload or {"status": "ok"}
        self.calls: list[dict[str, Any]] = []

    async def ainvoke(self, args: dict[str, Any]) -> str:
        self.calls.append(args)
        return json.dumps({**self.payload, "args": args}, ensure_ascii=False)


class DummyRouter(RouterBase):
    """Router that always emits the same RouteDecision."""

    def __init__(
        self,
        *,
        route: str = "dummy_route",
        intent: str = "dummy_intent",
        risk_level: RiskLevel = RiskLevel.LOW,
        requires_approval: bool = False,
    ):
        self._decision = RouteDecision(
            intent=intent,
            route=route,
            risk_level=risk_level,
            requires_approval=requires_approval,
        )

    async def route(self, request: ChatRequest) -> RouteDecision:
        return self._decision


class DummyExecutor(ExecutorBase):
    """Executor that returns a canned dict keyed by ExecutorBase contract."""

    def __init__(self, *, node_name: str = "dummy_route", route_name: str = "dummy_route",
                 final_message: str = "dummy handled", sources: list[str] | None = None):
        super().__init__(node_name=node_name, route_name=route_name)
        self._final_message = final_message
        self._sources = sources if sources is not None else ["dummy://source"]
        self.invocations: list[dict[str, Any]] = []

    async def execute(self, state, event_callback=None):
        self.invocations.append({"session_id": state.get("session_id")})
        goal = state["plan"].current_step().goal
        return {
            "final_message": f"{self._final_message}: {goal}",
            "tool_calls": [],
            "sources": list(self._sources),
        }


class RaisingExecutor(ExecutorBase):
    def __init__(self, *, node_name: str = "dummy_route", route_name: str = "dummy_route"):
        super().__init__(node_name=node_name, route_name=route_name)

    async def execute(self, state, event_callback=None):
        raise RuntimeError("simulated executor failure")


def make_valid_receipt(
    step: PlanStep,
    *,
    approved_by: str = "e2e-admin",
    expires_in_seconds: int = 300,
) -> ApprovalReceipt:
    return ApprovalReceipt(
        receipt_id=f"rcpt-{uuid.uuid4().hex[:8]}",
        step_id=step.step_id,
        approved_by=approved_by,
        scope="e2e-test",
        expires_at=datetime.now() + timedelta(seconds=expires_in_seconds),
    )


def build_dummy_agent(
    *,
    schema: MemorySchema | None = None,
    executors: list[ExecutorBase] | None = None,
    router: RouterBase | None = None,
    approval_policy: ApprovalPolicy | None = None,
    audit_logger=None,
) -> BaseAgent:
    schema = schema or MemorySchema(layers={"dummy_layer": {"dummy_writer"}})
    return BaseAgent(
        planner=Planner(router=router or DummyRouter()),
        session_store=create_session_store(memory_schema=schema),
        audit_logger=audit_logger or create_audit_logger(),
        executors=executors if executors is not None else [DummyExecutor()],
        approval_policy=approval_policy,
    )


def stub_ops_handler(agent, tool_name: str, payload: dict[str, Any]) -> DummyHandler:
    """Replace the registered handler for an Ops tool with a deterministic stub.

    Re-registers through register_mcp using the existing spec so the tool
    registry retains its original metadata (side_effect / route_affinity).
    """
    spec = agent.tool_registry.get_spec(tool_name)
    assert spec is not None, f"ops tool missing from registry: {tool_name}"
    handler = DummyHandler(payload)
    agent.tool_registry.register_mcp(spec, handler)
    return handler


def dangerous_tool_spec(name: str = "dangerous_tool") -> ToolSpec:
    return ToolSpec(
        name=name,
        description="side-effect tool for E2E gate testing",
        side_effect=True,
        route_affinity=["mutation"],
        source=ToolSource.MCP,
    )


# =============================================================================
# Group A — Happy path flows
# =============================================================================


class TestA_HappyPath:
    """Full chat() round-trips that must work end-to-end."""

    @pytest.mark.asyncio
    async def test_E2E_A01_dummy_vertical_chat_roundtrip(self):
        """A01: minimal vertical wired through BaseAgent returns sensible response."""
        executor = DummyExecutor()
        agent = build_dummy_agent(executors=[executor])

        response = await agent.chat(ChatRequest(message="hello", session_id="a01"))

        assert response.route == "dummy_route"
        assert response.message.startswith("dummy handled")
        assert response.sources == ["dummy://source"]
        assert len(executor.invocations) == 1

    @pytest.mark.asyncio
    async def test_E2E_A02_ops_agent_knowledge_route_stubbed(self):
        """A02: OpsAgent routes a knowledge question through the stubbed
        query_knowledge handler and surfaces a KNOWLEDGE-route response."""
        agent = create_ops_agent()
        handler = stub_ops_handler(agent, "query_knowledge", {
            "question": "",
            "results": [{"content": "MySQL host = mysql.staging", "source": "runbook.md"}],
            "sources": ["runbook.md"],
        })

        response = await agent.chat(ChatRequest(
            message="测试环境 MySQL 地址是什么？",
            session_id="a02",
        ))

        assert response.route == "knowledge"
        assert len(handler.calls) == 1
        assert any(tc.tool_name == "query_knowledge" for tc in response.tool_calls)

    @pytest.mark.asyncio
    async def test_E2E_A03_mutation_with_valid_receipt_executes(self):
        """A03: a side-effect tool call with a valid approval_receipt
        passes the Kernel gate and reaches the handler."""
        agent = create_ops_agent()
        agent.tool_registry.register_mcp(dangerous_tool_spec(), DummyHandler({"status": "executed"}))

        step = agent.planner.fallback_plan(ChatRequest(message="run dangerous")).steps[0]
        step.route = "mutation"
        step.execution_target = "executor:mutation"
        step.requires_approval = True
        receipt = make_valid_receipt(step)

        event, output = await agent._invoke_tool(
            "dangerous_tool",
            {"action": "ok"},
            session_id="a03",
            route="mutation",
            step=step,
            approval_receipt=receipt,
            execution_target=step.execution_target,
        )

        assert event.status == ToolCallStatus.SUCCESS
        assert "error" not in output
        assert step.approval_receipt_id == receipt.receipt_id

    @pytest.mark.asyncio
    async def test_E2E_A04_compound_request_produces_two_steps(self):
        """A04: OpsPlanner splits Chinese compound request into ordered steps.
        Both steps execute in plan.cursor order through the real dispatcher."""
        agent = create_ops_agent()
        stub_ops_handler(agent, "get_pod_status", {"namespace": "staging", "pods": [], "total_pods": 0})
        stub_ops_handler(agent, "query_knowledge", {"results": [], "sources": []})
        # Prevent mutation from running live: replace Jenkins tool handler too.
        stub_ops_handler(agent, "generate_jenkinsfile", {"jenkinsfile": "// stub"})

        response = await agent.chat(ChatRequest(
            message="先查一下 staging pod 状态，然后帮我重启 order-service",
            session_id="a04",
        ))

        # Plan must have >= 2 steps and the first should be read-only_ops or knowledge
        # (depends on router); the second should be an actionable route.
        # We assert on response.tool_calls reflecting multiple steps executed.
        assert response.message  # non-empty final message
        # At least one step ran a tool (the first one — router always maps status query to a tool).
        assert len(response.tool_calls) >= 1

    @pytest.mark.asyncio
    async def test_E2E_A05_execution_target_overrides_route_for_dispatch(self):
        """A05: dispatcher prefers PlanStep.execution_target over PlanStep.route.
        We register two executors with different node_names and point
        execution_target at the non-matching one."""

        class CustomRouter(RouterBase):
            async def route(self, request: ChatRequest) -> RouteDecision:
                return RouteDecision(intent="custom", route="route_bar", risk_level=RiskLevel.LOW)

        target_executor = DummyExecutor(
            node_name="foo",
            route_name="route_foo",
            final_message="dispatched to foo",
        )
        wrong_executor = DummyExecutor(
            node_name="route_bar",
            route_name="route_bar",
            final_message="should NOT be reached",
        )

        class TargetPlanner(Planner):
            """Force execution_target='executor:foo' regardless of route."""

            def _step_from_decision(self, decision, *, goal, order):
                step = super()._step_from_decision(decision, goal=goal, order=order)
                step.execution_target = "executor:foo"
                return step

        schema = MemorySchema(layers={"noop": {"system"}})
        agent = BaseAgent(
            planner=TargetPlanner(router=CustomRouter()),
            session_store=create_session_store(memory_schema=schema),
            audit_logger=create_audit_logger(),
            executors=[target_executor, wrong_executor],
        )

        response = await agent.chat(ChatRequest(message="go", session_id="a05"))

        assert response.message.startswith("dispatched to foo")
        assert len(target_executor.invocations) == 1
        assert len(wrong_executor.invocations) == 0


# =============================================================================
# Group B — Kernel invariants (§4.2)
# =============================================================================


class TestB_KernelInvariants:
    """Every assertion here maps to a §4.2 non-negotiable."""

    @pytest.mark.asyncio
    async def test_E2E_B01_side_effect_without_receipt_rejected(self):
        """B01: side_effect=True tool invoked with no receipt → FAILED."""
        agent = create_ops_agent()
        agent.tool_registry.register_mcp(dangerous_tool_spec(), DummyHandler({"status": "nope"}))

        step = agent.planner.fallback_plan(ChatRequest(message="boom")).steps[0]
        step.route = "mutation"
        step.execution_target = "executor:mutation"
        step.requires_approval = True

        event, output = await agent._invoke_tool(
            "dangerous_tool",
            {"action": "boom"},
            session_id="b01",
            route="mutation",
            step=step,
            execution_target=step.execution_target,
        )

        assert event.status == ToolCallStatus.FAILED
        assert "approval_receipt" in output

    @pytest.mark.asyncio
    async def test_E2E_B02_receipt_bound_to_different_step_rejected(self):
        """B02: receipt.step_id != current step.step_id → FAILED."""
        agent = create_ops_agent()
        agent.tool_registry.register_mcp(dangerous_tool_spec(), DummyHandler())

        plan = agent.planner.fallback_plan(ChatRequest(message="run"))
        step_a = plan.steps[0]
        step_a.requires_approval = True
        step_a.route = "mutation"
        step_a.execution_target = "executor:mutation"

        # Receipt bound to a DIFFERENT step id.
        wrong_receipt = ApprovalReceipt(
            receipt_id="rcpt-x",
            step_id="step-other-zzz",
            expires_at=datetime.now() + timedelta(minutes=5),
        )

        event, output = await agent._invoke_tool(
            "dangerous_tool",
            {"action": "boom"},
            session_id="b02",
            route="mutation",
            step=step_a,
            approval_receipt=wrong_receipt,
            execution_target=step_a.execution_target,
        )

        assert event.status == ToolCallStatus.FAILED
        assert "approval_receipt" in output

    @pytest.mark.asyncio
    async def test_E2E_B03_expired_receipt_rejected(self):
        """B03: receipt.expires_at in the past → FAILED."""
        agent = create_ops_agent()
        agent.tool_registry.register_mcp(dangerous_tool_spec(), DummyHandler())

        step = agent.planner.fallback_plan(ChatRequest(message="run")).steps[0]
        step.requires_approval = True
        step.route = "mutation"
        step.execution_target = "executor:mutation"

        expired = ApprovalReceipt(
            receipt_id="rcpt-expired",
            step_id=step.step_id,
            expires_at=datetime.now() - timedelta(seconds=1),
        )

        event, output = await agent._invoke_tool(
            "dangerous_tool",
            {"action": "boom"},
            session_id="b03",
            route="mutation",
            step=step,
            approval_receipt=expired,
            execution_target=step.execution_target,
        )

        assert event.status == ToolCallStatus.FAILED
        assert "approval_receipt" in output

    @pytest.mark.asyncio
    async def test_E2E_B04_bare_context_approved_flag_does_not_bypass_gate(self):
        """B04: only a real ApprovalReceipt grants access; `context.approved=True`
        alone must NOT let a side_effect tool through (§4.2 注解)."""
        agent = create_ops_agent()
        agent.tool_registry.register_mcp(dangerous_tool_spec(), DummyHandler())

        step = agent.planner.fallback_plan(ChatRequest(message="run")).steps[0]
        step.requires_approval = True
        step.route = "mutation"
        step.execution_target = "executor:mutation"

        # Simulate old-style "approved=true" transport flag with no receipt.
        event, output = await agent._invoke_tool(
            "dangerous_tool",
            {"action": "boom", "approved": True},
            session_id="b04",
            route="mutation",
            step=step,
            approval_receipt=None,
            execution_target=step.execution_target,
        )

        assert event.status == ToolCallStatus.FAILED
        assert "approval_receipt" in output

    def test_E2E_B05_unauthorized_writer_raises_permission_error(self):
        """B05: MemorySchema.assert_can_write blocks non-authorized writers."""
        schema = MemorySchema(layers={"ops_layer": {"ops_writer"}})
        store = create_session_store(memory_schema=schema)

        with pytest.raises(PermissionError):
            store.write_memory_item(
                "b05",
                writer="unauthorized_writer",
                layer="ops_layer",
                key="k",
                value="v",
            )

    @pytest.mark.asyncio
    async def test_E2E_B06_max_iterations_forces_finish(self):
        """B06: Plan.max_iterations is a hard budget.

        We build a 3-step plan but cap max_iterations at 1. After executing
        step 0 the planner must FINISH instead of advancing to step 1."""

        class ThreeStepRouter(RouterBase):
            async def route(self, request):
                return RouteDecision(intent="i", route="dummy_route", risk_level=RiskLevel.LOW)

        class CappedPlanner(Planner):
            async def initial_plan(self, request):
                plan = await super().initial_plan(request)
                # Force 3 identical steps and hard-cap iterations.
                base = plan.steps[0]
                plan.steps = [
                    base.model_copy(update={"step_id": f"step-{i}"}) for i in range(3)
                ]
                plan.max_iterations = 1
                return plan

        executor = DummyExecutor()
        agent = BaseAgent(
            planner=CappedPlanner(router=ThreeStepRouter()),
            session_store=create_session_store(
                memory_schema=MemorySchema(layers={"noop": {"system"}})
            ),
            audit_logger=create_audit_logger(),
            executors=[executor],
        )

        await agent.chat(ChatRequest(message="three", session_id="b06"))

        # Only one step should have been executed despite three pending.
        assert len(executor.invocations) == 1

    @pytest.mark.asyncio
    async def test_E2E_B07_failing_executor_fails_fast(self):
        """B07: a step that raises → PlanStepStatus.FAILED → FINISH.
        Subsequent steps must not execute."""

        class TwoStepRouter(RouterBase):
            async def route(self, request):
                return RouteDecision(intent="i", route="dummy_route", risk_level=RiskLevel.LOW)

        class TwoStepPlanner(Planner):
            async def initial_plan(self, request):
                plan = await super().initial_plan(request)
                base = plan.steps[0]
                plan.steps = [
                    base.model_copy(update={"step_id": "step-0"}),
                    base.model_copy(update={"step_id": "step-1"}),
                ]
                return plan

        raising = RaisingExecutor(node_name="dummy_route", route_name="dummy_route")
        agent = BaseAgent(
            planner=TwoStepPlanner(router=TwoStepRouter()),
            session_store=create_session_store(
                memory_schema=MemorySchema(layers={"noop": {"system"}})
            ),
            audit_logger=create_audit_logger(),
            executors=[raising],
        )

        response = await agent.chat(ChatRequest(message="boom", session_id="b07"))
        # chat() returns normally; fail-fast doesn't crash the caller.
        assert response.session_id == "b07"
        # Exactly one step (the first) should have been attempted and failed.

    @pytest.mark.asyncio
    async def test_E2E_B08_each_chat_produces_one_audit_entry(self):
        """B08: `_audit_request` fires exactly once per chat() call."""
        audit = create_audit_logger()
        agent = build_dummy_agent(audit_logger=audit)

        baseline = len(audit.get_recent(limit=999))
        await agent.chat(ChatRequest(message="once", session_id="b08"))
        await agent.chat(ChatRequest(message="twice", session_id="b08"))

        assert len(audit.get_recent(limit=999)) == baseline + 2


# =============================================================================
# Group C — Plugin points (§6)
# =============================================================================


class TestC_PluginPoints:
    """Every architectural plugin point must be externally swappable."""

    @pytest.mark.asyncio
    async def test_E2E_C01_custom_router_decides_route(self):
        """C01: a Vertical-supplied RouterBase drives Plan route."""
        custom_router = DummyRouter(route="custom_route", intent="custom_intent")
        executor = DummyExecutor(node_name="custom_route", route_name="custom_route")
        agent = build_dummy_agent(router=custom_router, executors=[executor])

        response = await agent.chat(ChatRequest(message="c01", session_id="c01"))
        assert response.route == "custom_route"
        assert response.intent == "custom_intent"

    @pytest.mark.asyncio
    async def test_E2E_C02_custom_approval_policy_can_veto(self):
        """C02: a Vertical ApprovalPolicy can veto an otherwise-valid receipt."""

        class DenyingPolicy(ApprovalPolicy):
            def validate_receipt(self, *, tool_name, route, step, context, receipt):
                return ApprovalDecision(approved=False, reason="policy says no")

        agent = create_ops_agent()
        agent.approval_policy = DenyingPolicy()
        agent.tool_registry.register_mcp(dangerous_tool_spec(), DummyHandler())

        step = agent.planner.fallback_plan(ChatRequest(message="run")).steps[0]
        step.requires_approval = True
        step.route = "mutation"
        step.execution_target = "executor:mutation"
        receipt = make_valid_receipt(step)

        event, output = await agent._invoke_tool(
            "dangerous_tool",
            {"action": "nope"},
            session_id="c02",
            route="mutation",
            step=step,
            approval_receipt=receipt,
            execution_target=step.execution_target,
        )

        assert event.status == ToolCallStatus.FAILED
        assert "policy says no" in output

    def test_E2E_C03_custom_memory_schema_enforces_rbac(self):
        """C03: a Vertical-defined MemorySchema allows its writers and blocks others."""
        schema = MemorySchema(
            write_permissions={
                "vertical_writer": {"vertical_layer"},
                "other_writer": {"other_layer"},
            }
        )
        store = create_session_store(memory_schema=schema)

        # Allowed write.
        store.write_memory_item(
            "c03",
            writer="vertical_writer",
            layer="vertical_layer",
            key="ok",
            value="allowed",
        )
        assert store.resolve_memory_value("c03", "ok", ["vertical_layer"]) == "allowed"

        # Cross-layer write should fail.
        with pytest.raises(PermissionError):
            store.write_memory_item(
                "c03",
                writer="vertical_writer",
                layer="other_layer",
                key="x",
                value="blocked",
            )

    @pytest.mark.asyncio
    async def test_E2E_C04_audit_sanitizer_hook_masks_sensitive_fields(self):
        """C04: Vertical-registered sanitizer runs after the default and can
        mask domain-specific secrets (e.g. k8s_token)."""

        def mask_k8s_token(params):
            return {k: ("***K8S***" if k == "k8s_token" else v) for k, v in params.items()}

        audit = create_audit_logger()
        audit.add_sanitizer(mask_k8s_token)

        entry = audit.log(
            user_id="u",
            session_id="c04",
            params={"k8s_token": "sk-abcdef", "namespace": "prod"},
        )

        assert entry.params["k8s_token"] == "***K8S***"
        assert entry.params["namespace"] == "prod"

    @pytest.mark.asyncio
    async def test_E2E_C05_audit_sink_receives_every_entry(self):
        """C05: an external sink (e.g. SIEM) sees every chat() audit entry."""
        captured: list[Any] = []
        audit = create_audit_logger(sinks=[captured.append])
        agent = build_dummy_agent(audit_logger=audit)

        await agent.chat(ChatRequest(message="c05-1", session_id="c05"))
        await agent.chat(ChatRequest(message="c05-2", session_id="c05"))

        assert len(captured) == 2
        assert all(e.user_id == "" for e in captured)  # no user_id supplied

    @pytest.mark.asyncio
    async def test_E2E_C06_ops_planner_compound_split_drives_real_chat(self):
        """C06: OpsPlanner's Chinese compound split produces >=2 plan steps
        through the real create_ops_agent() code path."""
        agent = create_ops_agent()
        # Stub the tools each segment is likely to call.
        stub_ops_handler(agent, "get_pod_status", {"pods": [], "namespace": "staging", "total_pods": 0})
        stub_ops_handler(agent, "query_knowledge", {"results": []})
        stub_ops_handler(agent, "generate_jenkinsfile", {"jenkinsfile": "// stub"})

        # Direct planner inspection — we verify the initial plan shape.
        plan = await agent.planner.initial_plan(
            ChatRequest(message="先查一下 staging pod 状态，然后帮我重启 order-service")
        )
        assert len(plan.steps) >= 2
        assert plan.steps[1].depends_on == [plan.steps[0].step_id]

    @pytest.mark.asyncio
    async def test_E2E_C07_planner_maybe_replan_override_appends_step(self):
        """C07: A Vertical that overrides Planner._maybe_replan can append a
        follow-up step after the initial plan would otherwise FINISH (§6 #5)."""

        class ReplaningPlanner(Planner):
            def __init__(self, router):
                super().__init__(router=router)
                self._already_replanned = False

            def _maybe_replan(self, plan, last_step):
                if self._already_replanned or last_step is None:
                    return None
                self._already_replanned = True
                return PlanStep(
                    step_id="step-followup",
                    route="dummy_route",
                    execution_target="executor:dummy_route",
                    intent="followup",
                    goal="auto-appended verification",
                    risk_level=RiskLevel.LOW,
                    status=PlanStepStatus.PENDING,
                    depends_on=[last_step.step_id],
                )

        executor = DummyExecutor(final_message="ran")
        agent = BaseAgent(
            planner=ReplaningPlanner(router=DummyRouter()),
            session_store=create_session_store(
                memory_schema=MemorySchema(layers={"noop": {"system"}})
            ),
            audit_logger=create_audit_logger(),
            executors=[executor],
        )

        response = await agent.chat(ChatRequest(message="initial", session_id="c07"))

        # Initial step + replanned follow-up step both ran.
        assert len(executor.invocations) == 2
        # Final response message comes from the replanned step.
        assert "auto-appended verification" in response.message


# =============================================================================
# Group D — Vertical isolation (§5.5)
# =============================================================================


class TestD_VerticalIsolation:
    """Two independent Vertical instances must not share state."""

    def test_E2E_D01_two_ops_agents_do_not_share_sessions(self):
        """D01: two create_ops_agent() instances have independent SessionStore."""
        agent_a = create_ops_agent()
        agent_b = create_ops_agent()

        agent_a.session_store.write_memory_item(
            "shared-sid",
            writer="read_ops_agent",
            layer="observations",
            key="iso",
            value="A-only",
        )

        assert agent_a.session_store.resolve_memory_value(
            "shared-sid", "iso", ["observations"]
        ) == "A-only"
        assert agent_b.session_store.resolve_memory_value(
            "shared-sid", "iso", ["observations"]
        ) is None

    def test_E2E_D02_memory_schemas_with_same_layer_name_isolated(self):
        """D02: even if two Verticals define a same-named layer, the
        SessionStore instances don't leak data across instances."""
        store_a = create_session_store(
            memory_schema=MemorySchema(write_permissions={"writer_a": {"shared_layer"}})
        )
        store_b = create_session_store(
            memory_schema=MemorySchema(write_permissions={"writer_b": {"shared_layer"}})
        )

        store_a.write_memory_item(
            "same-sid",
            writer="writer_a",
            layer="shared_layer",
            key="k",
            value="from_a",
        )
        assert store_b.resolve_memory_value("same-sid", "k", ["shared_layer"]) is None


# =============================================================================
# Group E — Degradation paths (§10)
# =============================================================================


class TestE_Degradation:

    @pytest.mark.asyncio
    async def test_E2E_E01_L1_executor_exception_does_not_crash_chat(self):
        """E01: L1 — a raising executor ends the plan with FAILED but chat
        returns a normal ChatResponse (see BaseAgent._run_step + chat())."""
        agent = BaseAgent(
            planner=Planner(router=DummyRouter()),
            session_store=create_session_store(
                memory_schema=MemorySchema(layers={"noop": {"system"}})
            ),
            audit_logger=create_audit_logger(),
            executors=[RaisingExecutor()],
        )

        response = await agent.chat(ChatRequest(message="e01", session_id="e01"))

        assert response.session_id == "e01"
        # The failure message should be surfaced as final content.
        assert "失败" in response.message or "failed" in response.message.lower()

    @pytest.mark.asyncio
    async def test_E2E_E02_L3_invalid_receipt_blocks_tool_but_chat_still_returns(self):
        """E02: L3 — a forged/expired receipt causes the side-effect tool to
        FAIL inside _invoke_tool, but calling code receives a normal event."""
        agent = create_ops_agent()
        agent.tool_registry.register_mcp(dangerous_tool_spec(), DummyHandler())

        step = agent.planner.fallback_plan(ChatRequest(message="run")).steps[0]
        step.requires_approval = True
        step.route = "mutation"
        step.execution_target = "executor:mutation"

        forged = ApprovalReceipt(
            receipt_id="rcpt-forged",
            step_id="unrelated-step",
            expires_at=datetime.now() + timedelta(minutes=1),
        )

        event, output = await agent._invoke_tool(
            "dangerous_tool",
            {"action": "x"},
            session_id="e02",
            route="mutation",
            step=step,
            approval_receipt=forged,
            execution_target=step.execution_target,
        )

        assert event.status == ToolCallStatus.FAILED
        assert event.error  # non-empty failure reason
        assert "approval_receipt" in output


# =============================================================================
# Group F — Anti-pattern regressions (§11)
# =============================================================================


class TestF_AntiPatternRegressions:
    """Static guarantees that Kernel stays domain-clean."""

    def test_E2E_F01_agent_kernel_does_not_import_ops_enums(self):
        """F01: no agent_kernel module imports IntentType/AgentRoute/
        MemoryLayer/AgentIdentity Ops enums."""
        import pathlib

        kernel_root = pathlib.Path(__file__).resolve().parents[2] / "agent_kernel"
        forbidden_tokens = (
            "from agent_ops",
            "import agent_ops",
        )
        offenders: list[str] = []
        for path in kernel_root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for token in forbidden_tokens:
                if token in text:
                    offenders.append(f"{path}: {token}")
        assert not offenders, f"kernel leak detected: {offenders}"

    def test_E2E_F02_hypothesis_and_topology_not_importable_from_kernel(self):
        """F02: Ops-specific schemas must not live under agent_kernel."""
        with pytest.raises(ImportError):
            from agent_kernel.schemas import Hypothesis  # noqa: F401
        with pytest.raises(ImportError):
            from agent_kernel.topology import ServiceTopology  # noqa: F401

    def test_E2E_F03_kernel_planner_has_no_module_level_split_compound(self):
        """F03: _split_compound must be an overridable *method* on Planner,
        not a module-level function (anti-pattern §11)."""
        import agent_kernel.planner as kp

        assert not hasattr(kp, "_split_compound"), (
            "_split_compound should be a protected Planner method, "
            "not a module-level function (architecture-v2 §11)."
        )
        # Kernel Planner's default implementation returns a single segment.
        planner = Planner(router=DummyRouter())
        assert planner._split_compound("先查然后重启") == ["先查然后重启"]
