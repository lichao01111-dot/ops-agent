from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

class IntentType(str, Enum):
    PIPELINE_CREATE = "pipeline_create"
    PIPELINE_STATUS = "pipeline_status"
    PIPELINE_DEBUG = "pipeline_debug"
    K8S_STATUS = "k8s_status"
    K8S_DIAGNOSE = "k8s_diagnose"
    K8S_OPERATE = "k8s_operate"
    LOG_SEARCH = "log_search"
    LOG_ANALYZE = "log_analyze"
    KNOWLEDGE_QA = "knowledge_qa"
    GENERAL_CHAT = "general_chat"

class AgentRoute(str, Enum):
    KNOWLEDGE = "knowledge"
    READ_ONLY_OPS = "read_only_ops"
    DIAGNOSIS = "diagnosis"
    MUTATION = "mutation"

class MemoryLayer(str, Enum):
    FACTS = "facts"
    OBSERVATIONS = "observations"
    HYPOTHESES = "hypotheses"
    PLANS = "plans"
    EXECUTION = "execution"
    VERIFICATION = "verification"

class AgentIdentity(str, Enum):
    SYSTEM = "system"
    ROUTER = "router"
    KNOWLEDGE = "knowledge_agent"
    READ_OPS = "read_ops_agent"
    DIAGNOSIS = "diagnosis_agent"
    CHANGE_PLANNER = "change_planner"
    CHANGE_EXECUTOR = "change_executor"
    VERIFICATION = "verification_agent"


# ===== Multi-hypothesis Diagnosis =====

class HypothesisVerdict(str, Enum):
    UNVERIFIED = "unverified"
    SUPPORTED = "supported"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"


class Hypothesis(BaseModel):
    hypothesis_id: str
    statement: str
    suspected_target: str = ""
    evidence_tools: list[str] = Field(default_factory=list)
    score: float = 0.0
    verdict: HypothesisVerdict = HypothesisVerdict.UNVERIFIED
    evidence_summary: str = ""


# ===== Service Topology =====

class ServiceNode(BaseModel):
    name: str
    namespace: str = "default"
    env: str = "default"
    owner: str = ""
    runtime: str = ""
    dependencies: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
