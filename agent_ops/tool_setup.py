"""
Ops-vertical tool registration.

Owns the Ops-specific curated metadata (tags, route affinity, side_effect) and
the helper that registers every local LangChain tool into a ``ToolRegistry``.

This module exists so that ``agent_kernel.tools.registry`` stays vertical-
agnostic: the kernel never learns tool names like ``query_knowledge`` or
routes like ``mutation``.
"""
from __future__ import annotations

from weakref import WeakSet
from typing import Any

import structlog

from agent_kernel.tools.registry import ToolRegistry, create_tool_registry
from agent_ops.schemas import AgentRoute

logger = structlog.get_logger()


BUILTIN_TOOL_META: dict[str, dict[str, Any]] = {
    "query_knowledge": {
        "tags": ["knowledge", "rag", "docs", "sop", "faq", "环境", "架构", "配置"],
        "route_affinity": [AgentRoute.KNOWLEDGE, AgentRoute.DIAGNOSIS],
        "side_effect": False,
    },
    "index_documents": {
        "tags": ["knowledge", "index", "admin", "索引", "文档"],
        "route_affinity": [AgentRoute.MUTATION],
        "side_effect": True,
    },
    "get_pod_status": {
        "tags": ["k8s", "pod", "status", "状态", "namespace"],
        "route_affinity": [AgentRoute.READ_ONLY_OPS, AgentRoute.DIAGNOSIS],
        "side_effect": False,
    },
    "get_deployment_status": {
        "tags": ["k8s", "deployment", "status", "副本"],
        "route_affinity": [AgentRoute.READ_ONLY_OPS, AgentRoute.DIAGNOSIS],
        "side_effect": False,
    },
    "get_configmap": {
        "tags": ["k8s", "configmap", "配置", "datasource", "jdbc", "database", "连接串"],
        "route_affinity": [AgentRoute.READ_ONLY_OPS],
        "side_effect": False,
    },
    "get_secret": {
        "tags": ["k8s", "secret", "配置", "datasource", "jdbc", "database", "连接串"],
        "route_affinity": [AgentRoute.READ_ONLY_OPS],
        "side_effect": False,
    },
    "get_deployment_config_refs": {
        "tags": ["k8s", "deployment", "configmap", "secret", "envFrom", "env"],
        "route_affinity": [AgentRoute.READ_ONLY_OPS],
        "side_effect": False,
    },
    "get_deployment_env": {
        "tags": ["k8s", "deployment", "env", "jdbc", "datasource", "database"],
        "route_affinity": [AgentRoute.READ_ONLY_OPS],
        "side_effect": False,
    },
    "get_service_info": {
        "tags": ["k8s", "service", "endpoint", "svc"],
        "route_affinity": [AgentRoute.READ_ONLY_OPS, AgentRoute.DIAGNOSIS],
        "side_effect": False,
    },
    "get_pod_logs": {
        "tags": ["k8s", "pod", "logs", "日志", "crashloop"],
        "route_affinity": [AgentRoute.READ_ONLY_OPS, AgentRoute.DIAGNOSIS],
        "side_effect": False,
    },
    "diagnose_pod": {
        "tags": ["k8s", "pod", "diagnose", "诊断", "crashloop", "oom", "imagepullbackoff"],
        "route_affinity": [AgentRoute.DIAGNOSIS],
        "side_effect": False,
    },
    "search_logs": {
        "tags": ["logs", "search", "日志", "error", "elk", "loki"],
        "route_affinity": [AgentRoute.READ_ONLY_OPS, AgentRoute.DIAGNOSIS],
        "side_effect": False,
    },
    "get_error_statistics": {
        "tags": ["logs", "stats", "errors", "统计", "聚合"],
        "route_affinity": [AgentRoute.READ_ONLY_OPS, AgentRoute.DIAGNOSIS],
        "side_effect": False,
    },
    "query_jenkins_build": {
        "tags": ["jenkins", "build", "pipeline", "构建"],
        "route_affinity": [AgentRoute.READ_ONLY_OPS, AgentRoute.DIAGNOSIS],
        "side_effect": False,
    },
    "get_jenkins_build_log": {
        "tags": ["jenkins", "build", "log", "构建日志"],
        "route_affinity": [AgentRoute.READ_ONLY_OPS, AgentRoute.DIAGNOSIS],
        "side_effect": False,
    },
    "generate_jenkinsfile": {
        "tags": ["jenkins", "pipeline", "generate", "流水线", "jenkinsfile"],
        "route_affinity": [AgentRoute.MUTATION],
        "side_effect": True,
    },
    "restart_deployment": {
        "tags": ["k8s", "deployment", "restart", "重启", "滚动重启", "rolling restart"],
        "route_affinity": [AgentRoute.MUTATION],
        "side_effect": True,
    },
    "scale_deployment": {
        "tags": ["k8s", "deployment", "scale", "扩容", "缩容", "副本", "replicas"],
        "route_affinity": [AgentRoute.MUTATION],
        "side_effect": True,
    },
    "rollback_deployment": {
        "tags": ["k8s", "deployment", "rollback", "回滚", "undo", "恢复版本"],
        "route_affinity": [AgentRoute.MUTATION, AgentRoute.VERIFICATION],
        "side_effect": True,
    },
    "get_k8s_events": {
        "tags": ["k8s", "events", "incident", "诊断", "报警", "event"],
        "route_affinity": [AgentRoute.DIAGNOSIS, AgentRoute.READ_ONLY_OPS],
        "side_effect": False,
    },
}


_REGISTERED: WeakSet[ToolRegistry] = WeakSet()


def register_ops_builtins(registry: ToolRegistry | None = None) -> ToolRegistry:
    """Register every local Ops LangChain tool into ``registry``.

    Idempotent per-registry: repeated calls on the same registry instance are
    a no-op. Returns the registry so it can be chained.
    """
    target = registry or create_tool_registry()
    if target in _REGISTERED:
        return target

    from tools import ALL_TOOLS  # late import: tools/ is Ops-owned

    for tool in ALL_TOOLS:
        meta = BUILTIN_TOOL_META.get(tool.name, {})
        target.register_local(
            tool,
            tags=meta.get("tags", []),
            route_affinity=meta.get("route_affinity", []),
            side_effect=meta.get("side_effect", False),
        )
    _REGISTERED.add(target)
    logger.info("ops_tool_registry_initialized", count=len(target.all_specs()))
    return target
