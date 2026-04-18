"""
MCP gateway stub for OpsAgent.

Purpose: provide a uniform place to attach remote MCP servers. Local tools flow
through :mod:`tools.registry`; remote MCP tools flow through :class:`MCPClient`
which registers them as ``ToolSource.MCP`` entries in the same registry.

v1 is intentionally a stub: it defines the contract and wires an empty loader
path so the rest of the architecture can treat "local tools" and "MCP tools"
identically. A real MCP transport (stdio / websocket / sse) can be plugged in
later without touching executors.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import structlog

from agent_kernel.schemas import ToolSource, ToolSpec
from agent_kernel.tools.registry import ToolRegistry

logger = structlog.get_logger()


MCPInvoker = Callable[[str, dict[str, Any]], Awaitable[str]]


@dataclass
class MCPServerConfig:
    name: str
    url: str
    auth_token: str = ""
    tags: list[str] = field(default_factory=list)


class MCPClient:
    """Light stub. Real transport is deliberately out of scope for v1.

    Usage:
        client = MCPClient()
        client.register_server(MCPServerConfig(name="k8s-mcp", url="..."))
        await client.load_tools()  # no-op in v1

    Subclass or monkeypatch :meth:`load_tools` when wiring a real transport.
    """

    def __init__(self, registry: ToolRegistry):
        self._servers: dict[str, MCPServerConfig] = {}
        self._registry = registry

    def register_server(self, config: MCPServerConfig) -> None:
        self._servers[config.name] = config
        logger.info("mcp_server_registered", name=config.name, url=config.url)

    def registered_servers(self) -> list[MCPServerConfig]:
        return list(self._servers.values())

    async def load_tools(self) -> list[ToolSpec]:
        """Fetch tool specs from every registered server and add to registry.

        v1 stub returns empty. Override in a subclass to implement real RPC.
        """
        if not self._servers:
            return []
        logger.info("mcp_load_tools_stub", server_count=len(self._servers))
        return []

    def register_remote_tool(
        self,
        *,
        name: str,
        description: str,
        invoker: MCPInvoker,
        tags: list[str] | None = None,
        route_affinity: list[Any] | None = None,
        side_effect: bool = False,
        parameters_schema: dict[str, Any] | None = None,
    ) -> ToolSpec:
        """Attach a single remote tool without implementing a full transport.

        ``invoker`` must be ``async def fn(name, args) -> str`` returning the
        tool output as a string (same contract as local LangChain tools).
        """
        spec = ToolSpec(
            name=name,
            description=description,
            tags=tags or [],
            route_affinity=route_affinity or [],
            side_effect=side_effect,
            source=ToolSource.MCP,
            parameters_schema=parameters_schema or {},
        )

        async def _proxy(**kwargs: Any) -> str:
            return await invoker(name, kwargs)

        # Synthesize a minimal LangChain-tool-compatible handler. Executors only
        # call ``ainvoke`` on it; we implement just enough surface for that.
        class _MCPHandler:
            def __init__(self, tool_name: str, tool_description: str):
                self.name = tool_name
                self.description = tool_description

            async def ainvoke(self, args: dict[str, Any]) -> str:
                return await _proxy(**(args or {}))

        self._registry.register_mcp(spec, _MCPHandler(name, description))
        return spec

def create_mcp_client(*, registry: ToolRegistry) -> MCPClient:
    return MCPClient(registry=registry)
