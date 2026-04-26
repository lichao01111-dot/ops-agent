"""
MCP gateway for OpsAgent — uniform attachment point for remote MCP servers.

Local tools flow through :mod:`tools.registry`; remote MCP tools flow
through :class:`MCPClient`, which registers them as ``ToolSource.MCP``
entries in the same registry. From the executor's perspective, both look
identical — both go through the ``ToolInvoker`` → middleware chain.

Non-functional concerns this module owns
----------------------------------------
The arch review (2026-04) called out three specific gaps that a "real
MCP integration" must cover:

1. **Secret injection**:
   per-server bearer / api-key / mtls config flows in via a pluggable
   :class:`SecretProvider`. Secrets never appear in ``ToolSpec`` or
   logs — the gateway holds them, the proxy attaches them at call-time.

2. **Token rotation**:
   short-lived tokens are refreshed transparently. ``SecretProvider``
   returns a ``Secret`` carrying its own ``expires_at``; the proxy
   re-fetches when the cached secret is within ``REFRESH_LEAD_S`` of
   expiring. No call-site changes.

3. **Schema-hash drift detection**:
   when a server publishes a tool spec, we compute a stable hash over
   the parameters schema and store it. On every subsequent ``load_tools``
   we recompute and compare; a mismatch is logged as
   ``mcp_schema_drift_detected`` with the registered/advertised hashes
   so on-call can decide whether to bump ``schema_version``.

What's still out of scope for this module
-----------------------------------------
* Real transport (stdio / WebSocket / SSE) — the protocol bindings live
  in transport-specific subclasses; this gateway only enforces the
  contract above. ``InMemoryMCPTransport`` (used in tests) is the
  reference implementation.
* mTLS handshake — orthogonal to the secret abstraction; can be added
  by a SecretProvider that returns cert paths instead of bearer tokens.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Protocol

import structlog

from agent_kernel.schemas import ToolSource, ToolSpec
from agent_kernel.tools.registry import ToolRegistry

logger = structlog.get_logger()


# Backwards-compat alias kept so existing call sites that imported
# MCPInvoker keep working. New code should accept (name, args, headers).
MCPInvoker = Callable[[str, dict[str, Any]], Awaitable[str]]
MCPInvokerWithAuth = Callable[[str, dict[str, Any], dict[str, str]], Awaitable[str]]


# =============================================================================
# Secret abstraction
# =============================================================================

@dataclass(frozen=True)
class Secret:
    """A short-lived credential with a renewal hint.

    Empty ``expires_at`` (0.0) means "never expires" — used for static
    bearer tokens. Anything > 0 is a unix timestamp; the gateway will
    re-fetch when ``time.time() >= expires_at - REFRESH_LEAD_S``.
    """
    header_name: str = "Authorization"
    header_value: str = ""
    expires_at: float = 0.0

    def as_headers(self) -> dict[str, str]:
        if not self.header_value:
            return {}
        return {self.header_name: self.header_value}

    def is_expiring(self, now: float, lead_s: float) -> bool:
        return self.expires_at > 0 and now >= (self.expires_at - lead_s)


class SecretProvider(Protocol):
    """Pluggable per-server credential source.

    Implementations: read from env vars, from a vault, from a token
    exchange endpoint, etc. The provider is responsible for caching
    on its end if the upstream call is expensive — the gateway does
    its own short-circuit cache (see :class:`MCPClient`) but always
    asks the provider when the cached secret is near expiry.
    """
    async def fetch(self, server_name: str) -> Secret: ...


@dataclass
class StaticSecretProvider:
    """Fixed bearer-token provider — fine for dev / single-tenant prod.

    Maps server_name → static token. Returns an empty Secret for unknown
    servers (no auth header injected); the remote may still reject
    unauthenticated calls of course.
    """
    tokens: dict[str, str] = field(default_factory=dict)
    header_name: str = "Authorization"

    async def fetch(self, server_name: str) -> Secret:
        token = self.tokens.get(server_name, "")
        if not token:
            return Secret()
        return Secret(header_name=self.header_name, header_value=f"Bearer {token}")


@dataclass
class CallbackSecretProvider:
    """Adapter for any ``async def fetch(server_name) -> Secret`` callable.

    Use this when the secret comes from a vault or token-exchange endpoint
    you've already wrapped in an async helper.
    """
    callback: Callable[[str], Awaitable[Secret]]

    async def fetch(self, server_name: str) -> Secret:
        return await self.callback(server_name)


# =============================================================================
# Server config
# =============================================================================

@dataclass
class MCPServerConfig:
    name: str
    url: str
    # Static fallback token; used only when no SecretProvider is supplied.
    auth_token: str = ""
    tags: list[str] = field(default_factory=list)


# =============================================================================
# Schema-hash drift detection
# =============================================================================

def compute_schema_hash(parameters_schema: dict[str, Any], description: str = "") -> str:
    """Stable SHA256 over the contract-relevant parts of a ToolSpec.

    Used by :class:`MCPClient` to spot remote contract drift between
    discoveries. Description is included because parameter docstrings are
    part of the implicit contract for LLM tool selection.
    """
    payload = json.dumps(
        {"schema": parameters_schema or {}, "description": description or ""},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# =============================================================================
# Transport contract
# =============================================================================

class MCPTransport(Protocol):
    """Pluggable transport between the gateway and a remote MCP server.

    Real transports (stdio / WebSocket / SSE) implement this interface.
    Tests use :class:`InMemoryMCPTransport`. The gateway never speaks
    HTTP directly — it only knows how to ask a transport for tools and
    how to invoke them.
    """

    async def discover(self, server: MCPServerConfig, headers: dict[str, str]) -> list[dict[str, Any]]:
        """Return a list of {name, description, parameters_schema, side_effect, schema_version} dicts."""
        ...

    async def invoke(
        self,
        server: MCPServerConfig,
        tool_name: str,
        arguments: dict[str, Any],
        headers: dict[str, str],
    ) -> str:
        ...


# =============================================================================
# MCPClient — gateway proper
# =============================================================================

class MCPClient:
    """Gateway between MCP servers and the local ToolRegistry.

    Construction::

        client = MCPClient(
            registry,
            secret_provider=StaticSecretProvider({"k8s-mcp": "TOKEN-XYZ"}),
            transport=InMemoryMCPTransport(...),
        )
        client.register_server(MCPServerConfig(name="k8s-mcp", url="..."))
        await client.load_tools()
    """

    REFRESH_LEAD_S: float = 30.0  # refresh secrets 30s before expiry

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        secret_provider: SecretProvider | None = None,
        transport: MCPTransport | None = None,
    ):
        self._servers: dict[str, MCPServerConfig] = {}
        self._registry = registry
        self._secret_provider = secret_provider
        self._transport = transport
        # tool_name → (server_name, parameters_hash) for drift detection.
        self._tool_hashes: dict[str, tuple[str, str]] = {}
        # server_name → cached Secret, refreshed on expiry.
        self._secret_cache: dict[str, Secret] = {}

    # ------------------------------------------------------------------
    # Server registration
    # ------------------------------------------------------------------
    def register_server(self, config: MCPServerConfig) -> None:
        self._servers[config.name] = config
        logger.info("mcp_server_registered", name=config.name, url=config.url)

    def registered_servers(self) -> list[MCPServerConfig]:
        return list(self._servers.values())

    # ------------------------------------------------------------------
    # Discovery + drift detection
    # ------------------------------------------------------------------
    async def load_tools(self) -> list[ToolSpec]:
        """Discover every server's tools and (re)register them.

        Re-discovery is safe: existing tools whose schema hash matches
        are left alone; mismatches log ``mcp_schema_drift_detected`` and
        re-register the spec with the new hash.
        """
        if not self._servers:
            return []
        if self._transport is None:
            logger.info("mcp_load_tools_no_transport", server_count=len(self._servers))
            return []

        registered: list[ToolSpec] = []
        for server in self._servers.values():
            headers = await self._headers_for(server)
            try:
                discovered = await self._transport.discover(server, headers)
            except Exception as exc:
                logger.error(
                    "mcp_discover_failed",
                    server=server.name,
                    error=str(exc),
                )
                continue

            for entry in discovered:
                spec = self._register_one(server, entry)
                if spec is not None:
                    registered.append(spec)

        return registered

    def _register_one(
        self,
        server: MCPServerConfig,
        entry: dict[str, Any],
    ) -> ToolSpec | None:
        name = entry.get("name")
        if not name:
            logger.warning("mcp_discover_skipped_unnamed_tool", server=server.name)
            return None
        description = entry.get("description", "")
        parameters_schema = entry.get("parameters_schema", {}) or {}
        side_effect = bool(entry.get("side_effect", False))
        schema_version = entry.get("schema_version", "1.0.0")
        route_affinity = entry.get("route_affinity", []) or []
        tags = entry.get("tags", []) or []

        new_hash = compute_schema_hash(parameters_schema, description)
        existing = self._tool_hashes.get(name)
        if existing is not None:
            existing_server, existing_hash = existing
            if existing_server == server.name and existing_hash != new_hash:
                logger.warning(
                    "mcp_schema_drift_detected",
                    tool=name,
                    server=server.name,
                    registered_hash=existing_hash,
                    advertised_hash=new_hash,
                )
            elif existing_server != server.name:
                logger.warning(
                    "mcp_tool_name_collision",
                    tool=name,
                    previous_server=existing_server,
                    new_server=server.name,
                )

        spec = ToolSpec(
            name=name,
            description=description,
            tags=list(tags) + list(server.tags),
            route_affinity=list(route_affinity),
            side_effect=side_effect,
            source=ToolSource.MCP,
            parameters_schema=parameters_schema,
            schema_version=schema_version,
        )

        async def _proxy(args: dict[str, Any], _name: str = name, _server: MCPServerConfig = server) -> str:
            headers = await self._headers_for(_server)
            return await self._transport.invoke(_server, _name, args or {}, headers)

        class _MCPHandler:
            def __init__(self, tool_name: str, tool_description: str, schema_hash: str):
                self.name = tool_name
                self.description = tool_description
                self.schema_hash = schema_hash

            async def ainvoke(self, args: dict[str, Any]) -> str:
                return await _proxy(args)

        self._registry.register_mcp(spec, _MCPHandler(name, description, new_hash))
        self._tool_hashes[name] = (server.name, new_hash)
        return spec

    # ------------------------------------------------------------------
    # Manual single-tool registration (for tests / hand-wired tools)
    # ------------------------------------------------------------------
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
        schema_version: str = "1.0.0",
    ) -> ToolSpec:
        """Attach a single remote tool without going through a transport.

        ``invoker`` must be ``async def fn(name, args) -> str``.
        Authentication for this entry point is the caller's responsibility
        (since they brought their own invoker callable).
        """
        spec = ToolSpec(
            name=name,
            description=description,
            tags=tags or [],
            route_affinity=route_affinity or [],
            side_effect=side_effect,
            source=ToolSource.MCP,
            parameters_schema=parameters_schema or {},
            schema_version=schema_version,
        )

        async def _proxy(**kwargs: Any) -> str:
            return await invoker(name, kwargs)

        class _MCPHandler:
            def __init__(self, tool_name: str, tool_description: str):
                self.name = tool_name
                self.description = tool_description

            async def ainvoke(self, args: dict[str, Any]) -> str:
                return await _proxy(**(args or {}))

        self._registry.register_mcp(spec, _MCPHandler(name, description))
        self._tool_hashes[name] = (
            "<manual>",
            compute_schema_hash(parameters_schema or {}, description),
        )
        return spec

    # ------------------------------------------------------------------
    # Secret resolution + rotation
    # ------------------------------------------------------------------
    async def _headers_for(self, server: MCPServerConfig) -> dict[str, str]:
        secret = await self._resolve_secret(server)
        return secret.as_headers()

    async def _resolve_secret(self, server: MCPServerConfig) -> Secret:
        cached = self._secret_cache.get(server.name)
        now = time.time()
        if cached is not None and not cached.is_expiring(now, self.REFRESH_LEAD_S):
            return cached

        if self._secret_provider is None:
            # Fall back to static auth_token from server config.
            if server.auth_token:
                fresh = Secret(header_value=f"Bearer {server.auth_token}")
            else:
                fresh = Secret()
        else:
            try:
                fresh = await self._secret_provider.fetch(server.name)
            except Exception as exc:
                logger.error(
                    "mcp_secret_fetch_failed",
                    server=server.name,
                    error=str(exc),
                )
                # Keep using the stale cached secret rather than dropping
                # auth entirely — the remote will tell us if it's actually
                # expired and we'll retry next request.
                if cached is not None:
                    return cached
                return Secret()

        self._secret_cache[server.name] = fresh
        if cached is not None and cached.header_value != fresh.header_value:
            logger.info("mcp_secret_rotated", server=server.name)
        return fresh

    def cached_secret(self, server_name: str) -> Secret | None:
        """Test/debug accessor; not part of the runtime hot path."""
        return self._secret_cache.get(server_name)


def create_mcp_client(
    *,
    registry: ToolRegistry,
    secret_provider: SecretProvider | None = None,
    transport: MCPTransport | None = None,
) -> MCPClient:
    return MCPClient(registry=registry, secret_provider=secret_provider, transport=transport)


# =============================================================================
# In-process transport for tests and fixtures
# =============================================================================

@dataclass
class InMemoryMCPTransport:
    """Reference transport for tests and developer fixtures.

    Holds a list of tool descriptors per server and a synchronous handler
    callable. Records every (server, tool, args, headers) tuple it
    receives so tests can assert on the wire-level behaviour without
    spinning up real network sockets.
    """
    tools_by_server: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    handlers: dict[str, Callable[[dict[str, Any]], str]] = field(default_factory=dict)
    require_auth: bool = True
    invocations: list[dict[str, Any]] = field(default_factory=list)
    discoveries: list[dict[str, Any]] = field(default_factory=list)

    async def discover(self, server: MCPServerConfig, headers: dict[str, str]) -> list[dict[str, Any]]:
        self.discoveries.append({"server": server.name, "headers": dict(headers)})
        if self.require_auth and "Authorization" not in headers:
            raise PermissionError(f"unauthorized discover from {server.name}")
        return list(self.tools_by_server.get(server.name, []))

    async def invoke(
        self,
        server: MCPServerConfig,
        tool_name: str,
        arguments: dict[str, Any],
        headers: dict[str, str],
    ) -> str:
        self.invocations.append(
            {
                "server": server.name,
                "tool": tool_name,
                "arguments": dict(arguments),
                "headers": dict(headers),
            }
        )
        if self.require_auth and "Authorization" not in headers:
            raise PermissionError(f"unauthorized invoke {tool_name}")
        handler = self.handlers.get(tool_name)
        if handler is None:
            raise KeyError(f"no handler for {tool_name}")
        return handler(arguments)
