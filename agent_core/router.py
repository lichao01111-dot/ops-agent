"""
Intent router for OpsAgent.

The router uses deterministic keyword heuristics first and can fall back to the
lightweight router LLM when the request is ambiguous.
"""
from __future__ import annotations

from typing import Iterable

import structlog

from agent_core.schemas import AgentRoute, ChatRequest, IntentType, RiskLevel, RouteDecision

logger = structlog.get_logger()


class IntentRouter:
    """Route incoming requests into purpose-built subgraphs."""

    INDEX_KEYWORDS = (
        "索引",
        "同步文档",
        "导入文档",
        "index docs",
        "index documents",
    )

    KNOWLEDGE_KEYWORDS = (
        "知识库",
        "文档",
        "sop",
        "流程",
        "架构",
        "环境信息",
        "地址",
        "配置项",
        "mysql",
        "redis",
        "kafka",
    )
    DIAGNOSIS_KEYWORDS = (
        "为什么",
        "原因",
        "诊断",
        "分析",
        "排查",
        "根因",
        "异常",
        "失败原因",
        "crashloop",
        "oom",
        "imagepullbackoff",
        "error",
        "报错",
    )
    MUTATION_KEYWORDS = (
        "重启",
        "回滚",
        "删除",
        "扩容",
        "缩容",
        "部署",
        "发布",
        "执行",
        "触发",
        "创建job",
        "创建 job",
        "apply",
        "patch",
        "set image",
    )
    PIPELINE_CREATE_KEYWORDS = (
        "jenkinsfile",
        "pipeline",
        "流水线",
        "生成",
    )

    async def route(self, request: ChatRequest) -> RouteDecision:
        text = request.message.lower().strip()

        if self._contains_any(text, self.INDEX_KEYWORDS):
            return RouteDecision(
                intent=IntentType.KNOWLEDGE_QA,
                route=AgentRoute.MUTATION,
                risk_level=RiskLevel.MEDIUM,
                requires_approval=True,
                rationale="matched_knowledge_index_keywords",
            )

        if self._contains_any(text, self.KNOWLEDGE_KEYWORDS):
            return RouteDecision(
                intent=IntentType.KNOWLEDGE_QA,
                route=AgentRoute.KNOWLEDGE,
                rationale="matched_knowledge_keywords",
            )

        if self._contains_any(text, self.DIAGNOSIS_KEYWORDS):
            if "pod" in text or "deployment" in text or "容器" in text:
                intent = IntentType.K8S_DIAGNOSE
            elif "构建" in text or "jenkins" in text:
                intent = IntentType.PIPELINE_DEBUG
            else:
                intent = IntentType.LOG_ANALYZE
            return RouteDecision(
                intent=intent,
                route=AgentRoute.DIAGNOSIS,
                risk_level=RiskLevel.MEDIUM,
                rationale="matched_diagnosis_keywords",
            )

        if self._contains_any(text, self.MUTATION_KEYWORDS):
            return RouteDecision(
                intent=IntentType.K8S_OPERATE,
                route=AgentRoute.MUTATION,
                risk_level=RiskLevel.HIGH,
                requires_approval=True,
                rationale="matched_mutation_keywords",
            )

        if self._contains_any(text, self.PIPELINE_CREATE_KEYWORDS):
            if "状态" in text or "日志" in text or "失败" in text:
                return RouteDecision(
                    intent=IntentType.PIPELINE_STATUS,
                    route=AgentRoute.READ_ONLY_OPS,
                    rationale="pipeline_status_query",
                )
            return RouteDecision(
                intent=IntentType.PIPELINE_CREATE,
                route=AgentRoute.MUTATION,
                risk_level=RiskLevel.MEDIUM,
                requires_approval=True,
                rationale="pipeline_creation_request",
            )

        if any(token in text for token in ("pod", "deployment", "service", "日志", "构建", "jenkins", "namespace")):
            return RouteDecision(
                intent=IntentType.K8S_STATUS if "pod" in text or "deployment" in text or "service" in text else IntentType.LOG_SEARCH,
                route=AgentRoute.READ_ONLY_OPS,
                rationale="matched_read_only_ops_keywords",
            )

        llm_decision = await self._route_with_llm(request)
        if llm_decision:
            return llm_decision

        return RouteDecision(
            intent=IntentType.GENERAL_CHAT,
            route=AgentRoute.KNOWLEDGE,
            rationale="default_fallback",
        )

    def _contains_any(self, text: str, keywords: Iterable[str]) -> bool:
        return any(keyword in text for keyword in keywords)

    async def _route_with_llm(self, request: ChatRequest) -> RouteDecision | None:
        """Fallback to the lightweight router model when heuristics are inconclusive."""
        try:
            from llm_gateway import llm_gateway
        except Exception as exc:
            logger.debug("router_llm_unavailable", error=str(exc))
            return None

        router_model = llm_gateway.get_router_model()
        structured_router = router_model.with_structured_output(RouteDecision)

        prompt = (
            "你是 OpsAgent 的路由器。"
            "只做任务分类，不回答用户问题。"
            "根据用户请求输出 RouteDecision："
            "knowledge=知识/文档/环境问答；"
            "read_only_ops=只读查询；"
            "diagnosis=故障诊断；"
            "mutation=任何有副作用或需要审批的动作。"
        )

        try:
            decision = await structured_router.ainvoke(
                [{"role": "system", "content": prompt}, {"role": "user", "content": request.message}]
            )
            if isinstance(decision, RouteDecision):
                return decision
        except Exception as exc:
            logger.warning("router_llm_failed", error=str(exc))
        return None
