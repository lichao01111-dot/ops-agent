from agent_kernel.schemas import ChatRequest, ChatResponse

__all__ = ["OpsAgent", "OpsAgentStreaming", "ChatRequest", "ChatResponse", "create_ops_agent", "create_ops_agent_streaming"]


def __getattr__(name: str):
    if name in {"OpsAgent", "OpsAgentStreaming", "create_ops_agent", "create_ops_agent_streaming"}:
        from agent_ops import OpsAgent, OpsAgentStreaming, create_ops_agent, create_ops_agent_streaming

        exports = {
            "OpsAgent": OpsAgent,
            "OpsAgentStreaming": OpsAgentStreaming,
            "create_ops_agent": create_ops_agent,
            "create_ops_agent_streaming": create_ops_agent_streaming,
        }
        return exports[name]
    raise AttributeError(f"module 'agent_core' has no attribute {name!r}")
