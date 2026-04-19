"""Optional executor pattern library.

These are *opt-in* abstract base classes that capture commonly-recurring
multi-stage execution shapes (multi-hypothesis diagnosis, chained reads,
approval-gated mutation, etc.). A vertical may inherit from one of them
to skip writing the boilerplate, but they are NOT required by the Kernel
and contain ZERO domain knowledge.

See architecture-v2.md §5.3 / §6 #10 for the rationale.
"""
from agent_kernel.patterns.approval_gate import ApprovalGateExecutor
from agent_kernel.patterns.multi_hypothesis import (
    HypothesisProtocol,
    MultiHypothesisExecutor,
)

__all__ = [
    "ApprovalGateExecutor",
    "HypothesisProtocol",
    "MultiHypothesisExecutor",
]
