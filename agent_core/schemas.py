"""
OpsAgent 公共类型定义
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ===== Enums =====

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


class UserRole(str, Enum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"


class ToolCallStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    NEEDS_CONFIRMATION = "needs_confirmation"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


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


# ===== Request / Response =====

class ChatRequest(BaseModel):
    message: str
    session_id: str = ""
    user_id: str = ""
    user_role: UserRole = UserRole.VIEWER
    context: dict[str, Any] = Field(default_factory=dict)


class ToolCallEvent(BaseModel):
    tool_name: str
    action: str
    params: dict[str, Any] = Field(default_factory=dict)
    status: ToolCallStatus = ToolCallStatus.PENDING
    result: Any = None
    error: Optional[str] = None
    duration_ms: Optional[int] = None


class ChatResponse(BaseModel):
    session_id: str
    message: str
    intent: Optional[IntentType] = None
    route: Optional[AgentRoute] = None
    risk_level: RiskLevel = RiskLevel.LOW
    needs_approval: bool = False
    tool_calls: list[ToolCallEvent] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)  # RAG 引用来源
    tokens_used: int = 0
    timestamp: datetime = Field(default_factory=datetime.now)


# ===== Audit Log =====

class AuditEntry(BaseModel):
    timestamp: datetime = Field(default_factory=datetime.now)
    user_id: str
    session_id: str
    intent: Optional[IntentType] = None
    route: Optional[AgentRoute] = None
    risk_level: Optional[RiskLevel] = None
    needs_approval: bool = False
    tool_name: Optional[str] = None
    action: Optional[str] = None
    tool_calls: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    result_summary: str = ""
    success: bool = True
    duration_ms: int = 0


class RouteDecision(BaseModel):
    intent: IntentType
    route: AgentRoute
    risk_level: RiskLevel = RiskLevel.LOW
    requires_approval: bool = False
    rationale: str = ""


# ===== Tool Registration =====

class ToolInfo(BaseModel):
    name: str
    description: str
    version: str = "1.0.0"
    enabled: bool = True
    health: str = "unknown"  # healthy / degraded / unhealthy / unknown
