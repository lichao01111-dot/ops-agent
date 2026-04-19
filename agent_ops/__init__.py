from agent_ops.tool_setup import BUILTIN_TOOL_META, register_ops_builtins
from agent_ops.agent import OpsAgent, OpsAgentStreaming
from agent_ops.executors import (
    DiagnosisExecutor,
    KnowledgeExecutor,
    MutationExecutor,
    ReadOnlyOpsExecutor,
)
from agent_ops.memory import OPS_MEMORY_SCHEMA
from agent_ops.router import IntentRouter
from agent_ops.topology import ServiceTopology, get_topology, load_topology_from_file, reload_topology
from agent_kernel.audit import create_audit_logger
from agent_kernel.session import create_session_store
from agent_kernel.tools.mcp_gateway import create_mcp_client
from agent_kernel.tools.registry import create_tool_registry


def create_ops_agent() -> OpsAgent:
    registry = create_tool_registry()
    register_ops_builtins(registry)
    audit_logger = create_audit_logger()
    session_store = create_session_store(memory_schema=OPS_MEMORY_SCHEMA)
    mcp_client = create_mcp_client(registry=registry)
    return OpsAgent(
        session_store=session_store,
        tool_registry=registry,
        audit_logger=audit_logger,
        mcp_client=mcp_client,
    )


def create_ops_agent_streaming() -> OpsAgentStreaming:
    registry = create_tool_registry()
    register_ops_builtins(registry)
    audit_logger = create_audit_logger()
    session_store = create_session_store(memory_schema=OPS_MEMORY_SCHEMA)
    mcp_client = create_mcp_client(registry=registry)
    return OpsAgentStreaming(
        session_store=session_store,
        tool_registry=registry,
        audit_logger=audit_logger,
        mcp_client=mcp_client,
    )

__all__ = [
    "BUILTIN_TOOL_META",
    "DiagnosisExecutor",
    "IntentRouter",
    "KnowledgeExecutor",
    "MutationExecutor",
    "OpsAgent",
    "OpsAgentStreaming",
    "ReadOnlyOpsExecutor",
    "ServiceTopology",
    "create_ops_agent",
    "create_ops_agent_streaming",
    "get_topology",
    "load_topology_from_file",
    "reload_topology",
    "register_ops_builtins",
]
