"""
Vertical-agnostic Tool Registry.

Wraps every tool (local LangChain @tool or MCP remote) in a uniform ``ToolSpec``
so executors can:

- look tools up by name without caring about the source
- retrieve top-k candidate tools for a given goal + route, replacing the old
  hardcoded per-route allowlists

Route names are passed as plain strings (``RouteKey``-like) so the kernel does
not need to know the closed set of route identifiers owned by any particular
vertical agent. A vertical registers its tools during its own bootstrap (see
``agent_ops`` for the Ops example); the kernel only owns the generic scoring
and dispatch logic.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

import structlog

from agent_kernel.schemas import ToolSource, ToolSpec

logger = structlog.get_logger()


_WORD_RE = re.compile(r"[A-Za-z0-9_\-]+|[\u4e00-\u9fa5]+")


def _tokenize(text: str) -> set[str]:
    if not text:
        return set()
    return {token.lower() for token in _WORD_RE.findall(text) if token}


def _route_value(route: Any) -> str | None:
    if route is None:
        return None
    return getattr(route, "value", route) if not isinstance(route, str) else route


@dataclass
class RegistryEntry:
    spec: ToolSpec
    handler: Any  # LangChain BaseTool or async callable (for MCP)


class ToolRegistry:
    """Central registry for all tools (local + MCP).

    The registry is intentionally vertical-agnostic: it knows nothing about
    Ops routes like ``mutation`` or ``diagnosis``. Callers decide whether to
    surface side-effect tools via ``include_side_effects``.
    """

    def __init__(self):
        self._entries: dict[str, RegistryEntry] = {}

    def register_local(
        self,
        tool: Any,
        *,
        tags: Iterable[str] | None = None,
        route_affinity: Iterable[Any] | None = None,
        side_effect: bool = False,
    ) -> ToolSpec:
        name = getattr(tool, "name", None) or tool.__class__.__name__
        description = getattr(tool, "description", "") or ""
        parameters_schema: dict[str, Any] = {}
        args_schema = getattr(tool, "args_schema", None)
        if args_schema is not None:
            try:
                parameters_schema = args_schema.model_json_schema()
            except Exception:
                parameters_schema = {}

        spec = ToolSpec(
            name=name,
            description=description,
            tags=list(tags or []),
            route_affinity=[_route_value(r) or "" for r in (route_affinity or []) if _route_value(r)],
            side_effect=side_effect,
            source=ToolSource.LOCAL,
            parameters_schema=parameters_schema,
        )
        self._entries[name] = RegistryEntry(spec=spec, handler=tool)
        return spec

    def register_mcp(self, spec: ToolSpec, handler: Any) -> None:
        spec_copy = spec.model_copy(update={"source": ToolSource.MCP})
        self._entries[spec.name] = RegistryEntry(spec=spec_copy, handler=handler)

    def get_handler(self, name: str) -> Any | None:
        entry = self._entries.get(name)
        return entry.handler if entry else None

    def get_spec(self, name: str) -> ToolSpec | None:
        entry = self._entries.get(name)
        return entry.spec if entry else None

    def all_specs(self) -> list[ToolSpec]:
        return [entry.spec for entry in self._entries.values()]

    def retrieve(
        self,
        *,
        goal: str,
        route: Any | None,
        hints: Iterable[str] = (),
        top_k: int = 6,
        include_side_effects: bool = False,
    ) -> list[ToolSpec]:
        """Score + rank tools for the given goal / route.

        ``include_side_effects``:
          - True  -> surface both read-only and side-effect tools
          - False -> only read-only tools (default)

        The kernel does not infer ``include_side_effects`` from the route name
        to avoid baking in vertical-specific semantics like "mutation". The
        caller (vertical agent / planner) is responsible for deciding.
        """
        route_value = _route_value(route)
        query_tokens = _tokenize(goal) | {h.lower() for h in hints}
        scored: list[tuple[float, ToolSpec]] = []
        for entry in self._entries.values():
            spec = entry.spec
            if spec.side_effect and not include_side_effects:
                continue

            score = 0.0
            spec_routes = {_route_value(r) for r in spec.route_affinity if _route_value(r)}
            if route_value and route_value in spec_routes:
                score += 1.0
            tag_tokens = {tag.lower() for tag in spec.tags}
            if tag_tokens and query_tokens:
                overlap = tag_tokens & query_tokens
                score += 1.5 * len(overlap)
            desc_tokens = _tokenize(spec.description)
            if desc_tokens and query_tokens:
                score += 0.5 * len(desc_tokens & query_tokens)
            # Stable tiebreak for deterministic tests.
            scored.append((score, spec))

        scored.sort(key=lambda pair: (-pair[0], pair[1].name))
        # Always keep the top_k, even if score==0, so the LLM still has options.
        return [spec for _, spec in scored[:top_k]]

    def filter_by_route(self, route: Any) -> list[ToolSpec]:
        route_value = _route_value(route)
        if not route_value:
            return []
        return [
            entry.spec
            for entry in self._entries.values()
            if route_value in {_route_value(r) for r in entry.spec.route_affinity}
        ]

def create_tool_registry() -> ToolRegistry:
    return ToolRegistry()
