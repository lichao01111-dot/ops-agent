"""
OpsAgent 单元测试
"""
import json
import pytest
import pytest_asyncio

from agent_core.router import IntentRouter
from agent_core.schemas import AgentIdentity, AgentRoute, ChatRequest, IntentType, MemoryLayer, RiskLevel, UserRole
from agent_core.audit import AuditLogger
from agent_core.session import InMemorySessionStore


# ===== Audit Logger Tests =====

class TestAuditLogger:

    def test_log_entry(self):
        logger = AuditLogger()
        entry = logger.log(
            user_id="test@company.com",
            session_id="test-session",
            intent=IntentType.K8S_STATUS,
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
            payload={"namespace": "staging", "total_pods": 1},
        )
        artifacts = store.get_recent_artifacts("s1", limit=1)
        assert len(artifacts) == 1
        assert artifacts[0].tool_name == "get_pod_status"
        assert artifacts[0].route == AgentRoute.DIAGNOSIS


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
