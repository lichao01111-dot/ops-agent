"""Ops vertical executors — the five route handlers wired into BaseAgent.

Per architecture-v2 §5.1 all Ops executors live under this package:

- KnowledgeExecutor     (route="knowledge")
- ReadOnlyOpsExecutor   (route="read_only_ops")
- DiagnosisExecutor     (route="diagnosis")       ← multi-hypothesis, Ops-specific
- MutationExecutor      (route="mutation")        ← side-effecting K8s / CI changes
- VerificationExecutor  (route="verification")    ← auto-appended after mutation; poll/rollback/escalate
"""
from agent_ops.executors.diagnosis import DiagnosisExecutor
from agent_ops.executors.knowledge import KnowledgeExecutor
from agent_ops.executors.mutation import MutationExecutor
from agent_ops.executors.read_only import ReadOnlyOpsExecutor
from agent_ops.executors.verification import VerificationExecutor

__all__ = [
    "DiagnosisExecutor",
    "KnowledgeExecutor",
    "MutationExecutor",
    "ReadOnlyOpsExecutor",
    "VerificationExecutor",
]
