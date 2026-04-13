from agent_core.schemas import ChatRequest, ChatResponse

__all__ = ["OpsAgent", "OpsAgentStreaming", "ChatRequest", "ChatResponse"]


def __getattr__(name: str):
    if name in {"OpsAgent", "OpsAgentStreaming"}:
        from agent_core.agent import OpsAgent, OpsAgentStreaming

        exports = {
            "OpsAgent": OpsAgent,
            "OpsAgentStreaming": OpsAgentStreaming,
        }
        return exports[name]
    raise AttributeError(f"module 'agent_core' has no attribute {name!r}")
