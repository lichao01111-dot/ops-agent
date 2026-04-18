"""
OpsAgent 公共类型定义: Agent Kernel 领域无关 Schema
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, NewType, Optional

from pydantic import BaseModel, Field

# ===== String Type Aliases (Open Contracts) =====
# Using NewType or str to keep Kernel decoupled from Vertical-specific enums
IntentTypeKey = NewType("IntentTypeKey", str)
RouteKey = NewType("RouteKey", str)
MemoryLayerKey = NewType("MemoryLayerKey", str)
AgentIdentityKey = NewType("AgentIdentityKey", str)


class RouteCatalog:
    RESERVED = {"finish"}
    BUILTIN = {"knowledge", "read_only_ops", "diagnosis", "mutation"}


class MemoryLayerCatalog:
    BUILTIN = {"facts", "observations", "hypotheses", "plans", "execution", "verification"}


class AgentIdentityCatalog:
    BUILTIN = {
        "system",
        "router",
        "knowledge_agent",
        "read_ops_agent",
        "diagnosis_agent",
        "change_planner",
        "change_executor",
        "verification_agent",
    }


# ===== Enums (Domain Agnostic) =====

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


# ===== Request / Response =====

class ChatRequest(BaseModel):
    message: str
    session_id: str = ""
    user_id: str = ""
    user_role: UserRole = UserRole.VIEWER
    context: dict[str, Any] = Field(default_factory=dict)


class ApprovalReceipt(BaseModel):
    receipt_id: str
    step_id: str
    approved_by: str = ""
    scope: str = ""
    approved_at: datetime = Field(default_factory=datetime.now)
    expires_at: Optional[datetime] = None


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
    intent: Optional[IntentTypeKey] = None
    route: Optional[RouteKey] = None
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
    intent: Optional[IntentTypeKey] = None
    route: Optional[RouteKey] = None
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
    intent: IntentTypeKey
    route: RouteKey
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


# ===== Planner / Plan =====

class PlanStepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class PlanStep(BaseModel):
    step_id: str
    route: RouteKey
    execution_target: str = ""
    intent: IntentTypeKey
    goal: str = ""
    inputs: dict[str, Any] = Field(default_factory=dict)
    risk_level: RiskLevel = RiskLevel.LOW
    requires_approval: bool = False
    approval_receipt_id: str = ""
    depends_on: list[str] = Field(default_factory=list)
    status: PlanStepStatus = PlanStepStatus.PENDING
    result_summary: str = ""
    tool_calls: list[str] = Field(default_factory=list)


class PlanDecision(str, Enum):
    CONTINUE = "continue"
    REPLAN = "replan"
    FINISH = "finish"


class Plan(BaseModel):
    plan_id: str
    rationale: str = ""
    steps: list[PlanStep] = Field(default_factory=list)
    cursor: int = 0
    iterations: int = 0
    max_iterations: int = 6
    done: bool = False
    final_message: str = ""

    def current_step(self) -> Optional[PlanStep]:
        if 0 <= self.cursor < len(self.steps):
            return self.steps[self.cursor]
        return None

    def remaining(self) -> list[PlanStep]:
        return [step for step in self.steps if step.status == PlanStepStatus.PENDING]


# ===== Tool Registry / MCP =====

class ToolSource(str, Enum):
    LOCAL = "local"
    MCP = "mcp"


class ToolSpec(BaseModel):
    name: str
    description: str
    tags: list[str] = Field(default_factory=list)
    route_affinity: list[str] = Field(default_factory=list)
    side_effect: bool = False
    source: ToolSource = ToolSource.LOCAL
    parameters_schema: dict[str, Any] = Field(default_factory=dict)

    model_config = {"arbitrary_types_allowed": True}
