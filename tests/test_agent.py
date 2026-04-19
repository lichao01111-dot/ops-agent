"""
OpsAgent 单元测试
"""
import json
from types import SimpleNamespace
import pytest
import pytest_asyncio

from agent_kernel.memory import MemorySchema
from agent_kernel.audit import AuditLogger, create_audit_logger
from agent_kernel.schemas import (
    ChatRequest,
    ChatResponse,
    PlanStep,
    RiskLevel,
    ToolCallEvent,
    ToolCallStatus,
    UserRole,
)
from agent_kernel.session import InMemorySessionStore, create_session_store
from agent_kernel.tools.registry import create_tool_registry
from agent_ops.risk_policy import OpsApprovalPolicy
from agent_ops import create_ops_agent, create_ops_agent_streaming
from agent_ops.agent import OpsAgent, OpsAgentStreaming
from agent_ops.memory_schema import OPS_MEMORY_SCHEMA
from agent_ops.router import IntentRouter
from agent_ops.tool_setup import register_ops_builtins
from agent_ops.schemas import (
    AgentIdentity,
    AgentRoute,
    Hypothesis,
    HypothesisVerdict,
    IntentType,
    MemoryLayer,
    ServiceNode,
)


# ===== Audit Logger Tests =====

class TestAuditLogger:

    def test_log_entry(self):
        logger = AuditLogger()
        entry = logger.log(
            user_id="test@company.com",
            session_id="test-session",
            intent="k8s_status",
            tool_name="k8s-tool",
            action="get_pods",
            params={"namespace": "staging"},
            result_summary="Found 3 pods",
            success=True,
            duration_ms=150,
        )
        assert entry.user_id == "test@company.com"
        assert entry.tool_name == "k8s-tool"
        assert entry.success is True

    def test_sanitize_params(self):
        logger = AuditLogger()
        sanitized = logger._sanitize_params({
            "namespace": "staging",
            "api_token": "secret-value-123",
            "password": "my-password",
            "normal_field": "visible",
        })
        assert sanitized["namespace"] == "staging"
        assert sanitized["api_token"] == "***REDACTED***"
        assert sanitized["password"] == "***REDACTED***"
        assert sanitized["normal_field"] == "visible"

    def test_get_recent(self):
        logger = AuditLogger()
        for i in range(10):
            logger.log(user_id=f"user-{i}", session_id=f"s-{i}")
        entries = logger.get_recent(5)
        assert len(entries) == 5
        assert entries[-1].user_id == "user-9"

    def test_get_by_user(self):
        logger = AuditLogger()
        logger.log(user_id="alice", session_id="s1")
        logger.log(user_id="bob", session_id="s2")
        logger.log(user_id="alice", session_id="s3")
        entries = logger.get_by_user("alice")
        assert len(entries) == 2


# ===== Schema Tests =====

class TestSchemas:

    def test_chat_request_defaults(self):
        req = ChatRequest(message="hello")
        assert req.user_role == UserRole.VIEWER
        assert req.session_id == ""
        assert req.context == {}

    def test_chat_request_with_context(self):
        req = ChatRequest(
            message="查一下 Pod 状态",
            session_id="abc123",
            user_id="dev@company.com",
            user_role=UserRole.OPERATOR,
            context={"project": "user-service", "env": "staging"},
        )
        assert req.user_role == UserRole.OPERATOR
        assert req.context["project"] == "user-service"


class TestSharedMemory:

    def test_write_and_resolve_memory(self):
        store = InMemorySessionStore()
        store.write_memory_item(
            "s1",
            writer=AgentIdentity.KNOWLEDGE,
            layer=MemoryLayer.FACTS,
            key="namespace",
            value="staging",
            source="query_knowledge",
            confidence=0.9,
        )
        assert store.resolve_memory_value("s1", "namespace", [MemoryLayer.FACTS]) == "staging"

    def test_memory_write_permission(self):
        store = InMemorySessionStore()
        with pytest.raises(PermissionError):
            store.write_memory_item(
                "s1",
                writer=AgentIdentity.READ_OPS,
                layer=MemoryLayer.FACTS,
                key="namespace",
                value="staging",
            )

    def test_memory_layer_fallback(self):
        store = InMemorySessionStore()
        store.write_memory_item(
            "s1",
            writer=AgentIdentity.KNOWLEDGE,
            layer=MemoryLayer.FACTS,
            key="service",
            value="order-service",
            source="query_knowledge",
            confidence=0.9,
        )
        store.write_memory_item(
            "s1",
            writer=AgentIdentity.READ_OPS,
            layer=MemoryLayer.OBSERVATIONS,
            key="service",
            value="payment-service",
            source="get_pod_status",
            confidence=0.8,
        )
        assert store.resolve_memory_value(
            "s1",
            "service",
            [MemoryLayer.OBSERVATIONS, MemoryLayer.FACTS],
        ) == "payment-service"
        assert store.resolve_memory_value(
            "s1",
            "service",
            [MemoryLayer.FACTS, MemoryLayer.OBSERVATIONS],
        ) == "order-service"

    def test_append_and_read_recent_artifacts(self):
        store = InMemorySessionStore()
        store.append_artifact(
            "s1",
            route=AgentRoute.DIAGNOSIS,
            tool_name="get_pod_status",
            summary="namespace=staging pods=1",
            step_id="step-1",
            execution_target="executor:diagnosis",
            approval_receipt_id="receipt-1",
            payload={"namespace": "staging", "total_pods": 1},
        )
        artifacts = store.get_recent_artifacts("s1", limit=1)
        assert len(artifacts) == 1
        assert artifacts[0].tool_name == "get_pod_status"
        assert artifacts[0].route == AgentRoute.DIAGNOSIS
        assert artifacts[0].step_id == "step-1"
        assert artifacts[0].execution_target == "executor:diagnosis"
        assert artifacts[0].approval_receipt_id == "receipt-1"

    def test_session_store_instances_do_not_share_state(self):
        store_a = InMemorySessionStore()
        store_b = InMemorySessionStore()
        store_a.write_memory_item(
            "s1",
            writer=AgentIdentity.KNOWLEDGE,
            layer=MemoryLayer.FACTS,
            key="namespace",
            value="staging",
        )
        assert store_b.resolve_memory_value("s1", "namespace", [MemoryLayer.FACTS]) is None

    def test_custom_memory_schema_is_enforced(self):
        store = InMemorySessionStore(
            memory_schema=MemorySchema(
                write_permissions={
                    AgentIdentity.KNOWLEDGE: {MemoryLayer.FACTS},
                }
            )
        )
        store.write_memory_item(
            "s1",
            writer=AgentIdentity.KNOWLEDGE,
            layer=MemoryLayer.FACTS,
            key="service",
            value="order-service",
        )
        with pytest.raises(PermissionError):
            store.write_memory_item(
                "s1",
                writer=AgentIdentity.READ_OPS,
                layer=MemoryLayer.OBSERVATIONS,
                key="pod_name",
                value="order-123",
            )


class TestApprovalPolicy:

    def test_ops_approval_policy_accepts_matching_receipt(self):
        policy = OpsApprovalPolicy()
        step = PlanStep(
            step_id="step-1",
            route=AgentRoute.MUTATION,
            execution_target="executor:mutation",
            intent=IntentType.PIPELINE_CREATE,
            requires_approval=True,
        )
        decision = policy.evaluate(
            tool_name="generate_jenkinsfile",
            route=AgentRoute.MUTATION,
            step=step,
            context={"approval_receipt": {"receipt_id": "r-1", "step_id": "step-1"}},
        )
        assert decision.approved is True
        assert decision.receipt is not None
        assert decision.receipt.receipt_id == "r-1"

    def test_ops_approval_policy_rejects_mismatched_receipt(self):
        policy = OpsApprovalPolicy()
        step = PlanStep(
            step_id="step-1",
            route=AgentRoute.MUTATION,
            execution_target="executor:mutation",
            intent=IntentType.PIPELINE_CREATE,
            requires_approval=True,
        )
        decision = policy.evaluate(
            tool_name="generate_jenkinsfile",
            route=AgentRoute.MUTATION,
            step=step,
            context={"approval_receipt": {"receipt_id": "r-1", "step_id": "step-2"}},
        )
        assert decision.approved is False
        assert "approval_receipt" in decision.reason


class TestIntentRouter:

    @pytest.mark.asyncio
    async def test_route_knowledge_request(self):
        router = IntentRouter()
        decision = await router.route(ChatRequest(message="测试环境 MySQL 地址是什么"))
        assert decision.route == AgentRoute.KNOWLEDGE
        assert decision.intent == IntentType.KNOWLEDGE_QA
        assert decision.risk_level == RiskLevel.LOW

    @pytest.mark.asyncio
    async def test_route_diagnosis_request(self):
        router = IntentRouter()
        decision = await router.route(ChatRequest(message="帮我分析 staging pod 为什么一直 CrashLoopBackOff"))
        assert decision.route == AgentRoute.DIAGNOSIS
        assert decision.intent == IntentType.K8S_DIAGNOSE
        assert decision.risk_level == RiskLevel.MEDIUM

    @pytest.mark.asyncio
    async def test_route_mutation_request(self):
        router = IntentRouter()
        decision = await router.route(ChatRequest(message="帮我重启 staging 的 order-service"))
        assert decision.route == AgentRoute.MUTATION
        assert decision.requires_approval is True
        assert decision.risk_level == RiskLevel.HIGH

    @pytest.mark.asyncio
    async def test_route_index_documents_request(self):
        router = IntentRouter()
        decision = await router.route(ChatRequest(message="帮我索引 docs 目录下的文档"))
        assert decision.route == AgentRoute.MUTATION
        assert decision.requires_approval is True


# ===== Jenkins Tool Tests =====

class TestJenkinsTool:

    @pytest.mark.asyncio
    async def test_generate_jenkinsfile_java(self):
        from tools.jenkins_tool import generate_jenkinsfile
        result = await generate_jenkinsfile.ainvoke({
            "project_name": "user-service",
            "language": "java_maven",
            "repo_url": "https://git.example.com/user-service.git",
            "branch": "main",
            "deploy_env": "staging",
            "namespace": "staging",
        })
        data = json.loads(result)
        assert data["status"] == "generated"
        assert data["language"] == "java_maven"
        assert "mvn clean package" in data["jenkinsfile"]

    @pytest.mark.asyncio
    async def test_generate_jenkinsfile_alias(self):
        from tools.jenkins_tool import generate_jenkinsfile
        result = await generate_jenkinsfile.ainvoke({
            "project_name": "web-app",
            "language": "react",
        })
        data = json.loads(result)
        assert data["language"] == "nodejs"
        assert "npm" in data["jenkinsfile"]

    @pytest.mark.asyncio
    async def test_generate_jenkinsfile_unknown(self):
        from tools.jenkins_tool import generate_jenkinsfile
        result = await generate_jenkinsfile.ainvoke({
            "project_name": "test",
            "language": "rust",
        })
        assert "不支持" in result


# ===== Config Tests =====

class TestConfig:

    def test_namespace_parsing(self):
        from config.settings import Settings
        s = Settings(
            k8s_allowed_namespaces="dev, staging, default",
            k8s_readonly_namespaces="prod, production",
        )
        assert s.allowed_namespaces == ["dev", "staging", "default"]
        assert s.readonly_namespaces == ["prod", "production"]


# ===== Planner Tests =====

class TestPlanner:

    @pytest.mark.asyncio
    async def test_initial_plan_single_intent(self):
        from agent_ops.planner import OpsPlanner
        planner = OpsPlanner(router=IntentRouter())
        plan = await planner.initial_plan(ChatRequest(message="测试环境 MySQL 地址"))
        assert len(plan.steps) == 1
        assert plan.steps[0].route == AgentRoute.KNOWLEDGE
        assert plan.steps[0].execution_target == "executor:knowledge"
        assert plan.cursor == 0
        assert plan.done is False

    @pytest.mark.asyncio
    async def test_initial_plan_compound_request(self):
        from agent_ops.planner import OpsPlanner
        planner = OpsPlanner(router=IntentRouter())
        plan = await planner.initial_plan(
            ChatRequest(message="先查一下 staging pod 状态，然后帮我重启 order-service")
        )
        # Compound split should produce >=2 steps, with dependency chain
        assert len(plan.steps) >= 2
        assert plan.steps[1].depends_on == [plan.steps[0].step_id]

    def test_kernel_planner_does_not_split_by_default(self):
        """Kernel Planner has no domain keywords — compound splitting is a
        Vertical concern per architecture-v2 §11."""
        from agent_kernel.planner import Planner
        planner = Planner(router=IntentRouter())
        segments = planner._split_compound("先查 pod 状态，然后帮我重启 order-service")
        assert segments == ["先查 pod 状态，然后帮我重启 order-service"]

    def test_ops_planner_splits_compound_heuristic(self):
        from agent_ops.planner import split_compound_ops
        segments = split_compound_ops("先查 pod 状态，然后帮我重启 order-service")
        assert len(segments) == 2
        assert "pod" in segments[0]
        assert "重启" in segments[1]

    @pytest.mark.asyncio
    async def test_advance_continues_when_pending_remains(self):
        from agent_ops.planner import OpsPlanner
        from agent_kernel.schemas import PlanDecision, PlanStepStatus
        planner = OpsPlanner(router=IntentRouter())
        plan = await planner.initial_plan(
            ChatRequest(message="先查 pod 状态，然后帮我重启 order-service")
        )
        plan.steps[0].status = PlanStepStatus.SUCCEEDED
        decision = planner.advance(plan, last_step=plan.steps[0])
        assert decision == PlanDecision.CONTINUE
        assert plan.cursor == 1

    @pytest.mark.asyncio
    async def test_advance_finishes_single_step(self):
        from agent_ops.planner import OpsPlanner
        from agent_kernel.schemas import PlanDecision, PlanStepStatus
        planner = OpsPlanner(router=IntentRouter())
        plan = await planner.initial_plan(ChatRequest(message="MySQL 地址"))
        plan.steps[0].status = PlanStepStatus.SUCCEEDED
        decision = planner.advance(plan, last_step=plan.steps[0])
        assert decision == PlanDecision.FINISH
        assert plan.done is True

    @pytest.mark.asyncio
    async def test_advance_fails_fast_on_failed_step(self):
        from agent_ops.planner import OpsPlanner
        from agent_kernel.schemas import PlanDecision, PlanStepStatus
        planner = OpsPlanner(router=IntentRouter())
        plan = await planner.initial_plan(
            ChatRequest(message="先查 pod 状态，然后帮我重启 order-service")
        )
        plan.steps[0].status = PlanStepStatus.FAILED
        decision = planner.advance(plan, last_step=plan.steps[0])
        assert decision == PlanDecision.FINISH
        assert plan.done is True


# ===== Tool Registry Tests =====

class TestToolRegistry:

    def test_builtins_registered(self):
        tool_registry = create_tool_registry()
        register_ops_builtins(tool_registry)
        specs = tool_registry.all_specs()
        names = {s.name for s in specs}
        assert "query_knowledge" in names
        assert "diagnose_pod" in names
        assert "generate_jenkinsfile" in names

    def test_retrieve_diagnosis_scoring(self):
        tool_registry = create_tool_registry()
        register_ops_builtins(tool_registry)
        specs = tool_registry.retrieve(
            goal="帮我诊断 pod crashloop",
            route=AgentRoute.DIAGNOSIS,
            top_k=5,
        )
        names = [s.name for s in specs]
        # diagnose_pod should rank highly: route affinity + tag overlap
        assert "diagnose_pod" in names
        assert names[0] == "diagnose_pod"

    def test_retrieve_knowledge_route_excludes_side_effects(self):
        tool_registry = create_tool_registry()
        register_ops_builtins(tool_registry)
        specs = tool_registry.retrieve(
            goal="测试环境的 MySQL 地址",
            route=AgentRoute.KNOWLEDGE,
            top_k=5,
        )
        side_effect_names = {s.name for s in specs if s.side_effect}
        # KNOWLEDGE route should not surface side-effect tools
        assert side_effect_names == set()

    def test_retrieve_mutation_route_surfaces_side_effects_when_requested(self):
        tool_registry = create_tool_registry()
        register_ops_builtins(tool_registry)
        # Per v2 \u00a74.4 + \u00a711 anti-pattern: the kernel no longer knows that
        # "mutation" means side-effect. The caller must opt in explicitly.
        specs = tool_registry.retrieve(
            goal="生成 jenkinsfile",
            route=AgentRoute.MUTATION,
            top_k=5,
            include_side_effects=True,
        )
        assert "generate_jenkinsfile" in {s.name for s in specs}

    def test_filter_by_route(self):
        tool_registry = create_tool_registry()
        register_ops_builtins(tool_registry)
        diagnosis_specs = tool_registry.filter_by_route(AgentRoute.DIAGNOSIS)
        assert any(s.name == "diagnose_pod" for s in diagnosis_specs)
        # knowledge_tool has DIAGNOSIS in affinity too, so it should appear
        assert any(s.name == "query_knowledge" for s in diagnosis_specs)


# ===== Service Topology Tests =====

class TestServiceTopology:

    def test_add_and_get(self):
        from agent_ops.topology import ServiceTopology
        topology = ServiceTopology([
            ServiceNode(name="order-service", namespace="staging", dependencies=["mysql", "redis"]),
            ServiceNode(name="mysql", namespace="staging"),
        ])
        assert topology.get("order-service").namespace == "staging"
        assert topology.get("missing") is None

    def test_dependents(self):
        from agent_ops.topology import ServiceTopology
        topology = ServiceTopology([
            ServiceNode(name="order-service", dependencies=["mysql"]),
            ServiceNode(name="payment-service", dependencies=["mysql"]),
            ServiceNode(name="mysql"),
        ])
        dependents = topology.dependents("mysql")
        names = {node.name for node in dependents}
        assert names == {"order-service", "payment-service"}

    def test_neighbors_depth_one(self):
        from agent_ops.topology import ServiceTopology
        topology = ServiceTopology([
            ServiceNode(name="A", dependencies=["B"]),
            ServiceNode(name="B", dependencies=["C"]),
            ServiceNode(name="C"),
        ])
        neighbors = {node.name for node in topology.neighbors("B", depth=1)}
        assert neighbors == {"A", "C"}

    def test_describe_formats_known_node(self):
        from agent_ops.topology import ServiceTopology
        topology = ServiceTopology([
            ServiceNode(name="order-service", namespace="staging", env="staging",
                        runtime="java", dependencies=["mysql"]),
            ServiceNode(name="mysql", namespace="staging"),
        ])
        desc = topology.describe("order-service")
        assert "service=order-service" in desc
        assert "dependencies=[mysql]" in desc

    def test_describe_unknown_returns_empty(self):
        from agent_ops.topology import ServiceTopology
        assert ServiceTopology().describe("nope") == ""


# ===== Diagnosis Memory Writes =====

class TestDiagnosisMemory:

    def test_hypothesis_memory_writes(self):
        """Verify DiagnosisExecutor._write_memory writes per-hypothesis entries,
        plus top_hypothesis_id / likely_root_cause / diagnosis_summary."""
        from agent_ops.executors.diagnosis import DiagnosisExecutor
        from agent_ops.topology import ServiceTopology
        session_store = create_session_store(memory_schema=OPS_MEMORY_SCHEMA)

        executor = DiagnosisExecutor(
            invoke_tool=lambda *a, **kw: None,  # unused in this direct test
            llm_provider=lambda: None,
            tool_retriever=lambda **kw: [],
            topology=ServiceTopology(),
            session_store_instance=session_store,
        )
        h1 = Hypothesis(
            hypothesis_id="h-1",
            statement="OOM killed",
            suspected_target="order-service",
            score=2.5,
            verdict=HypothesisVerdict.SUPPORTED,
            evidence_summary="search_logs: count=12",
        )
        h2 = Hypothesis(
            hypothesis_id="h-2",
            statement="Image pull failed",
            suspected_target="order-service",
            score=0.8,
            verdict=HypothesisVerdict.INCONCLUSIVE,
        )
        session_id = "diag-memory-test"
        executor._write_memory(session_id=session_id, hypotheses=[h1, h2], top=h1,
                               summary="Top: OOM killed")

        memory = session_store.get_shared_memory(session_id).get_layer(MemoryLayer.HYPOTHESES)
        assert "hypothesis:h-1" in memory
        assert "hypothesis:h-2" in memory
        assert memory["top_hypothesis_id"].value == "h-1"
        assert memory["likely_root_cause"].value == "OOM killed"
        assert "OOM" in memory["diagnosis_summary"].value


class TestAgentRuntimeContracts:

    @pytest.mark.asyncio
    async def test_invoke_tool_uses_registry_handler(self):
        agent = OpsAgent.__new__(OpsAgent)
        agent.session_store = create_session_store(memory_schema=OPS_MEMORY_SCHEMA)
        agent.approval_policy = OpsApprovalPolicy()
        agent.tool_registry = create_tool_registry()
        agent.audit_logger = create_audit_logger()

        captured = {}

        class FakeHandler:
            async def ainvoke(self, args):
                captured["args"] = args
                return json.dumps({"status": "ok", "args": args}, ensure_ascii=False)

        original_entry = agent.tool_registry._entries.get("test_registry_tool")
        try:
            agent.tool_registry._entries["test_registry_tool"] = SimpleNamespace(
                spec=SimpleNamespace(side_effect=False),
                handler=FakeHandler(),
            )
            event, output = await agent._invoke_tool(
                "test_registry_tool",
                {"value": 42},
                session_id="s1",
                route=AgentRoute.KNOWLEDGE,
            )
        finally:
            if original_entry is None:
                agent.tool_registry._entries.pop("test_registry_tool", None)
            else:
                agent.tool_registry._entries["test_registry_tool"] = original_entry

        assert captured["args"] == {"value": 42}
        assert event.status == ToolCallStatus.SUCCESS
        assert json.loads(output)["status"] == "ok"

    @pytest.mark.asyncio
    async def test_invoke_tool_emits_audit_entry_with_sanitized_params(self):
        agent = OpsAgent.__new__(OpsAgent)
        agent.session_store = create_session_store(memory_schema=OPS_MEMORY_SCHEMA)
        agent.approval_policy = OpsApprovalPolicy()
        agent.tool_registry = create_tool_registry()
        agent.audit_logger = create_audit_logger()

        class FakeHandler:
            async def ainvoke(self, args):
                return json.dumps({"status": "ok"}, ensure_ascii=False)

        original_entry = agent.tool_registry._entries.get("test_audit_tool")
        try:
            agent.tool_registry._entries["test_audit_tool"] = SimpleNamespace(
                spec=SimpleNamespace(side_effect=False),
                handler=FakeHandler(),
            )
            event, _ = await agent._invoke_tool(
                "test_audit_tool",
                {"api_token": "secret-value", "query": "mysql"},
                user_id="alice",
                session_id="audit-s1",
                route=AgentRoute.KNOWLEDGE,
            )
        finally:
            if original_entry is None:
                agent.tool_registry._entries.pop("test_audit_tool", None)
            else:
                agent.tool_registry._entries["test_audit_tool"] = original_entry

        assert event.status == ToolCallStatus.SUCCESS
        entry = agent.audit_logger.get_recent(1)[0]
        assert entry.user_id == "alice"
        assert entry.tool_name == "test_audit_tool"
        assert entry.params["api_token"] == "***REDACTED***"
        assert entry.params["query"] == "mysql"

    @pytest.mark.asyncio
    async def test_chat_stream_emits_gateway_friendly_events(self):
        class FakeStreamingAgent(OpsAgentStreaming):
            async def chat(self, request: ChatRequest) -> ChatResponse:
                return ChatResponse(
                    session_id=request.session_id,
                    message="done",
                    tool_calls=[
                        ToolCallEvent(
                            tool_name="query_knowledge",
                            action="query_knowledge",
                            status=ToolCallStatus.SUCCESS,
                            result="ok",
                        )
                    ],
                )

        agent = FakeStreamingAgent.__new__(FakeStreamingAgent)
        events = []
        async for event in agent.chat_stream(ChatRequest(message="hi", session_id="s-1")):
            events.append(event)

        assert events[0]["event"] == "start"
        assert events[0]["data"]["session_id"] == "s-1"
        assert events[1]["event"] == "tool_call"
        assert events[1]["data"]["tool"] == "query_knowledge"
        assert events[-1]["event"] == "final"
        assert events[-1]["data"]["message"] == "done"

    def test_create_ops_agent_factory_wires_dependencies(self):
        agent = create_ops_agent()
        assert agent.tool_registry is not None
        assert agent.audit_logger is not None
        assert agent.session_store is not None

    def test_create_ops_agent_streaming_factory_returns_streaming_agent(self):
        agent = create_ops_agent_streaming()
        assert isinstance(agent, OpsAgentStreaming)
        assert agent.tool_registry is not None
