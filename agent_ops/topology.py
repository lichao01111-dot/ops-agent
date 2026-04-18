"""
Service topology for OpsAgent.

Topology acts as a read-only prior for the diagnosis executor. It provides:
- direct service metadata (namespace / owner / runtime)
- forward dependencies (A -> B) used to enumerate "B broke, A was affected"
- reverse dependencies (dependents of B) used to infer blast radius

v1 loads from ``config/topology.yaml``. Missing file yields an empty topology
(diagnosis degrades gracefully, it just loses the cross-service prior).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import structlog
import yaml

from agent_ops.schemas import ServiceNode

logger = structlog.get_logger()


class ServiceTopology:
    """In-memory service graph. Thread-safe for read-only access."""

    def __init__(self, nodes: Optional[list[ServiceNode]] = None):
        self._nodes: dict[str, ServiceNode] = {}
        self._dependents: dict[str, set[str]] = {}
        for node in nodes or []:
            self.add(node)

    def add(self, node: ServiceNode) -> None:
        self._nodes[node.name] = node
        for dep in node.dependencies:
            self._dependents.setdefault(dep, set()).add(node.name)

    def get(self, name: str) -> Optional[ServiceNode]:
        return self._nodes.get(name)

    def dependents(self, name: str) -> list[ServiceNode]:
        return [self._nodes[n] for n in self._dependents.get(name, set()) if n in self._nodes]

    def neighbors(self, name: str, depth: int = 1) -> list[ServiceNode]:
        """Return services reachable within ``depth`` forward or backward hops."""
        if depth <= 0 or name not in self._nodes:
            return []
        seen: set[str] = set()
        frontier: set[str] = {name}
        for _ in range(depth):
            next_frontier: set[str] = set()
            for current in frontier:
                node = self._nodes.get(current)
                if node:
                    next_frontier.update(node.dependencies)
                next_frontier.update(n.name for n in self.dependents(current))
            next_frontier -= seen | {name}
            seen.update(next_frontier)
            frontier = next_frontier
            if not frontier:
                break
        return [self._nodes[n] for n in seen if n in self._nodes]

    def all_nodes(self) -> list[ServiceNode]:
        return list(self._nodes.values())

    def describe(self, name: str) -> str:
        """One-line description suitable for injecting into a prompt."""
        node = self.get(name)
        if not node:
            return ""
        deps = ", ".join(node.dependencies) or "none"
        rdeps = ", ".join(n.name for n in self.dependents(name)) or "none"
        return (
            f"service={node.name} ns={node.namespace} env={node.env} runtime={node.runtime} "
            f"dependencies=[{deps}] dependents=[{rdeps}]"
        )


def load_topology_from_file(path: str | Path) -> ServiceTopology:
    """Load topology from a YAML file. Returns empty topology if file is missing."""
    file_path = Path(path)
    if not file_path.exists():
        logger.info("topology_file_missing", path=str(file_path))
        return ServiceTopology()

    try:
        raw = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        logger.warning("topology_parse_failed", path=str(file_path), error=str(exc))
        return ServiceTopology()

    nodes_raw = raw.get("services", []) if isinstance(raw, dict) else []
    nodes: list[ServiceNode] = []
    for entry in nodes_raw:
        if not isinstance(entry, dict):
            continue
        try:
            nodes.append(ServiceNode(**entry))
        except Exception as exc:
            logger.warning("topology_node_skipped", entry=entry, error=str(exc))
    logger.info("topology_loaded", node_count=len(nodes), path=str(file_path))
    return ServiceTopology(nodes)


DEFAULT_TOPOLOGY_PATH = Path(__file__).resolve().parent.parent / "config" / "topology.yaml"

_topology_singleton: ServiceTopology | None = None


def get_topology() -> ServiceTopology:
    """Lazy-loaded global topology. Reload via :func:`reload_topology`."""
    global _topology_singleton
    if _topology_singleton is None:
        _topology_singleton = load_topology_from_file(DEFAULT_TOPOLOGY_PATH)
    return _topology_singleton


def reload_topology(path: str | Path | None = None) -> ServiceTopology:
    global _topology_singleton
    _topology_singleton = load_topology_from_file(path or DEFAULT_TOPOLOGY_PATH)
    return _topology_singleton
