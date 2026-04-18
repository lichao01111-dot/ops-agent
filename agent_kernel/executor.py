from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable


class ExecutorBase(ABC):
    """Execution node contract consumed by BaseAgent."""

    def __init__(self, *, node_name: str, route_name: str):
        self.node_name = node_name
        self.route_name = route_name

    @abstractmethod
    async def execute(self, state: Any) -> dict:
        raise NotImplementedError

    def as_node(self) -> Callable[[Any], Awaitable[dict]]:
        return self.execute


class FunctionExecutor(ExecutorBase):
    """Adapter for binding an existing async handler into ExecutorBase."""

    def __init__(
        self,
        *,
        node_name: str,
        route_name: str,
        handler: Callable[[Any], Awaitable[dict]],
    ):
        super().__init__(node_name=node_name, route_name=route_name)
        self._handler = handler

    async def execute(self, state: Any) -> dict:
        return await self._handler(state)
