from __future__ import annotations

from collections.abc import Mapping, Set as AbstractSet

from agent_kernel.schemas import AgentIdentityKey, MemoryLayerKey


class MemorySchema:
    """Kernel-level memory RBAC contract."""

    def __init__(
        self,
        *,
        layers: Mapping[MemoryLayerKey, AbstractSet[AgentIdentityKey]] | None = None,
        write_permissions: Mapping[AgentIdentityKey, AbstractSet[MemoryLayerKey]] | None = None,
    ):
        self._write_permissions = {
            writer: set(layer_set)
            for writer, layer_set in (write_permissions or {}).items()
        }
        self._layer_writers: dict[MemoryLayerKey, set[AgentIdentityKey]] = {
            layer: set(writers)
            for layer, writers in (layers or {}).items()
        }
        if self._layer_writers and not self._write_permissions:
            for layer, writers in self._layer_writers.items():
                for writer in writers:
                    self._write_permissions.setdefault(writer, set()).add(layer)
        elif self._write_permissions and not self._layer_writers:
            for writer, layer_set in self._write_permissions.items():
                for layer in layer_set:
                    self._layer_writers.setdefault(layer, set()).add(writer)
        
    def layers(self) -> set[MemoryLayerKey]:
        return set(self._layer_writers)

    def can_write(self, *, writer: AgentIdentityKey, layer: MemoryLayerKey) -> bool:
        return layer in self._write_permissions.get(writer, set())

    def assert_can_write(self, *, writer: AgentIdentityKey, layer: MemoryLayerKey) -> None:
        if not self.can_write(writer=writer, layer=layer):
            raise PermissionError(f"{writer} is not allowed to write to {layer}")

    def allowed_layers_for(self, writer: AgentIdentityKey) -> set[MemoryLayerKey]:
        return set(self._write_permissions.get(writer, set()))

    def allowed_writers_for(self, layer: MemoryLayerKey) -> set[AgentIdentityKey]:
        return set(self._layer_writers.get(layer, set()))


# Compatibility default for tests / legacy call sites. Vertical agents should
# still inject their own schema explicitly.
DEFAULT_MEMORY_SCHEMA = MemorySchema(
    write_permissions={
        "system": {
            "facts",
            "observations",
            "hypotheses",
            "plans",
            "execution",
            "verification",
        },
        "router": set(),
        "knowledge_agent": {"facts"},
        "read_ops_agent": {"observations"},
        "diagnosis_agent": {"hypotheses"},
        "change_planner": {"plans"},
        "change_executor": {"execution"},
        "verification_agent": {"verification"},
    }
)
