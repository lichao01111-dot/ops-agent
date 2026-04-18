from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from agent_kernel.schemas import MemoryLayerKey


class MemoryBackend(ABC):
    """Per-session memory backend contract owned by the Kernel."""

    @abstractmethod
    def get_layer(self, layer: MemoryLayerKey) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def clone(self) -> "MemoryBackend":
        raise NotImplementedError
