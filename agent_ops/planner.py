"""Ops-specific Planner subclass.

The Kernel Planner is domain-agnostic (see ``agent_kernel/planner.py``).
OpsPlanner plugs in the Chinese conjunction heuristics that belong to the
Ops vertical — "先查…然后重启", "回滚并验证", etc. Per architecture-v2 §11
these keywords must NOT live in the Kernel.
"""
from __future__ import annotations

import re

from agent_kernel.planner import MAX_COMPOUND_SEGMENTS, Planner

# Ops-flavored conjunction heuristics. Kept minimal on purpose — the LLM
# slow path handles anything tricky. The trailing look-aheads on "再 / 并"
# try to avoid over-splitting short words.
_OPS_SPLIT_PATTERNS = [
    re.compile(r"\s*然后\s*"),
    re.compile(r"\s*接着\s*"),
    re.compile(r"\s*再\s*(?=(?:帮|把|重|触|回|生|执))"),
    re.compile(r"\s*并\s*(?=(?:帮|把|重|触|回|生|执))"),
    re.compile(r"\s*,\s*然后\s*"),
    re.compile(r"\s*，\s*然后\s*"),
]


def split_compound_ops(message: str) -> list[str]:
    """Best-effort Ops compound split. Returns at most 3 segments."""
    segments: list[str] = [message]
    for pattern in _OPS_SPLIT_PATTERNS:
        next_segments: list[str] = []
        for segment in segments:
            pieces = [piece.strip() for piece in pattern.split(segment) if piece and piece.strip()]
            if pieces:
                next_segments.extend(pieces)
            else:
                next_segments.append(segment)
        segments = next_segments
    return Planner._dedupe_segments(segments, limit=MAX_COMPOUND_SEGMENTS)


class OpsPlanner(Planner):
    """Planner with Ops-specific compound splitting."""

    def _split_compound(self, message: str) -> list[str]:  # noqa: D401
        return split_compound_ops(message)
