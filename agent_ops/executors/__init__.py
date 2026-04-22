"""Ops vertical executors — the six route handlers wired into BaseAgent.

Per architecture-v2 §5.1 all Ops executors live under this package:

- KnowledgeExecutor      (route="knowledge")
- ReadOnlyOpsExecutor    (route="read_only_ops")
- InvestigatorExecutor   (route="investigation")  ← stage-0 parallel fact collection
- DiagnosisExecutor      (route="diagnosis")      ← multi-hypothesis, Ops-specific
- MutationExecutor       (route="mutation")       ← side-effecting K8s / CI changes
- VerificationExecutor   (route="verification")   ← auto-appended after mutation; poll/rollback/escalate

Two-role architecture (有限多 Agent):
  Investigator → Executor/Verifier pipeline covers 90% of on-call scenarios
  without the complexity of a full generic Supervisor pattern.
"""
from agent_ops.executors.diagnosis import DiagnosisExecutor
from agent_ops.executors.investigator import InvestigatorExecutor
from agent_ops.executors.knowledge import KnowledgeExecutor
from agent_ops.executors.mutation import MutationExecutor
from agent_ops.executors.read_only import ReadOnlyOpsExecutor
from agent_ops.executors.verification import VerificationExecutor

__all__ = [
    "DiagnosisExecutor",
    "InvestigatorExecutor",
    "KnowledgeExecutor",
    "MutationExecutor",
    "ReadOnlyOpsExecutor",
    "VerificationExecutor",
]
