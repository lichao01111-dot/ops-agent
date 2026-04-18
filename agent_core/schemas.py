"""Back-compat shim.

Historical callers used ``from agent_core.schemas import IntentType, ...``.
Kernel-level schemas now live in ``agent_kernel.schemas``; Ops-specific
enums (``IntentType`` / ``AgentRoute`` / ``MemoryLayer`` / ``AgentIdentity`` /
``Hypothesis`` / ``ServiceNode``) live in ``agent_ops.schemas``. This module
re-exports both so old imports keep working while new code should import
from the correct owner directly.
"""
from agent_kernel.schemas import *  # noqa: F401,F403
from agent_ops.schemas import (  # noqa: F401
    AgentIdentity,
    AgentRoute,
    Hypothesis,
    HypothesisVerdict,
    IntentType,
    MemoryLayer,
    ServiceNode,
)
