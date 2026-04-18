"""Back-compat shim.

Historical callers used ``from tools.registry import BUILTIN_TOOL_META,
ToolRegistry, create_tool_registry``. The kernel-level ``ToolRegistry`` and the
default singleton now live in ``agent_kernel.tools.registry``; the Ops
metadata (``BUILTIN_TOOL_META``) and the built-in registration helper live
in ``agent_ops.tool_setup``. This module re-exports both so old imports keep
working.
"""
from agent_kernel.tools.registry import ToolRegistry, create_tool_registry
from agent_ops.tool_setup import BUILTIN_TOOL_META, register_ops_builtins

__all__ = [
    "BUILTIN_TOOL_META",
    "ToolRegistry",
    "create_tool_registry",
    "register_ops_builtins",
]
