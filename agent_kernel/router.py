from __future__ import annotations

from abc import ABC, abstractmethod

from agent_kernel.schemas import ChatRequest, RouteDecision


class RouterBase(ABC):
    """Intent router contract for vertical agents."""

    @abstractmethod
    async def route(self, request: ChatRequest) -> RouteDecision:
        raise NotImplementedError
