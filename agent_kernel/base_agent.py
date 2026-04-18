from __future__ import annotations

import time
import uuid
from typing import Any, Annotated, Awaitable, Callable, Optional, TypedDict

import structlog
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

from agent_kernel.approval import ApprovalPolicy
from agent_kernel.audit import AuditLogger
from agent_kernel.executor import ExecutorBase
from agent_kernel.schemas import (
    ChatRequest,
    ChatResponse,
    IntentTypeKey,
    Plan,
    PlanDecision,
    PlanStep,
    PlanStepStatus,
    RiskLevel,
    RouteKey,
    ToolCallEvent,
    ToolCallStatus,
    UserRole,
)
from agent_kernel.session import SessionStore

logger = structlog.get_logger()


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    session_id: str
    user_id: str
    user_role: UserRole
    context: dict[str, Any]
    intent: IntentTypeKey | None
    route: RouteKey | None
    risk_level: RiskLevel
    needs_approval: bool
    sources: list[str]
    tool_calls: list[ToolCallEvent]
    final_message: str
    plan: Plan | None
    plan_decision: PlanDecision | None


class BaseAgent:
    """Shared planner/graph/session orchestration for vertical agents."""

    def __init__(
        self,
        *,
        planner: Any,
        session_store: SessionStore,
        executors: list[ExecutorBase],
        audit_logger: AuditLogger,
        approval_policy: ApprovalPolicy | None = None,
    ):
        self.planner = planner
        self.session_store = session_store
        self.executors = executors
        self.audit_logger = audit_logger
        self.approval_policy = approval_policy
        self.executor_nodes = {executor.node_name: executor for executor in executors}
        self.graph = self._build_graph()

    def _build_graph(self) -> StateGraph:
        graph = StateGraph(AgentState)
        graph.add_node("planner", self._planner_node)
        for node_name, executor in self.executor_nodes.items():
            graph.add_node(node_name, self._executor_node(executor))
            graph.add_edge(node_name, "planner")
        graph.set_entry_point("planner")
        edges = {name: name for name in self.executor_nodes}
        edges["finish"] = END
        graph.add_conditional_edges("planner", self._dispatcher, edges)
        return graph.compile()

    def _executor_node(self, executor: ExecutorBase) -> Callable[[AgentState], Awaitable[dict[str, Any]]]:
        async def run_executor(state: AgentState) -> dict[str, Any]:
            return await self._run_step(state, executor.execute)

        return run_executor

    async def _planner_node(self, state: AgentState) -> dict[str, Any]:
        plan = state.get("plan")
        if plan is None:
            message = self._get_latest_user_message(state["messages"])
            request = ChatRequest(
                message=message,
                session_id=state["session_id"],
                user_id=state["user_id"],
                user_role=state["user_role"],
                context=state["context"],
            )
            plan = await self.planner.initial_plan(request)
            first_step = plan.current_step()
            if first_step is None:
                plan.done = True
                return {"plan": plan, "plan_decision": PlanDecision.FINISH}
            self.session_store.update_route_state(
                state["session_id"],
                intent=first_step.intent,
                route=first_step.route,
                risk_level=first_step.risk_level,
                metadata={
                    "requires_approval": first_step.requires_approval,
                    "plan_id": plan.plan_id,
                    "step_id": first_step.step_id,
                    "execution_target": first_step.execution_target,
                },
            )
            return {
                "plan": plan,
                "plan_decision": PlanDecision.CONTINUE,
                "intent": first_step.intent,
                "route": first_step.route,
                "risk_level": first_step.risk_level,
                "needs_approval": first_step.requires_approval,
            }

        last_step = self._last_completed_step(plan)
        decision = self.planner.advance(plan, last_step=last_step)
        if decision == PlanDecision.FINISH:
            plan.final_message = self._assemble_final_message(plan, state.get("final_message", ""))
            return {"plan": plan, "plan_decision": decision, "final_message": plan.final_message}

        step = plan.current_step()
        if step is None:
            plan.done = True
            plan.final_message = self._assemble_final_message(plan, state.get("final_message", ""))
            return {"plan": plan, "plan_decision": PlanDecision.FINISH, "final_message": plan.final_message}

        self.session_store.update_route_state(
            state["session_id"],
            intent=step.intent,
            route=step.route,
            risk_level=step.risk_level,
            metadata={
                "requires_approval": step.requires_approval,
                "plan_id": plan.plan_id,
                "step_id": step.step_id,
                "execution_target": step.execution_target,
            },
        )
        return {
            "plan": plan,
            "plan_decision": decision,
            "intent": step.intent,
            "route": step.route,
            "risk_level": step.risk_level,
            "needs_approval": step.requires_approval,
        }

    def _dispatcher(self, state: AgentState) -> str:
        plan = state.get("plan")
        decision = state.get("plan_decision")
        if plan is None or plan.done or decision == PlanDecision.FINISH:
            return "finish"
        step = plan.current_step()
        if step is None:
            return "finish"
        node_name = self._execution_node_name(step)
        return node_name if node_name in self.executor_nodes else "finish"

    def _execution_node_name(self, step: PlanStep) -> str:
        target = step.execution_target or f"executor:{step.route}"
        if target.startswith("executor:"):
            return target.split(":", 1)[1]
        return target

    def _last_completed_step(self, plan: Plan) -> Optional[PlanStep]:
        for step in reversed(plan.steps):
            if step.status in (PlanStepStatus.SUCCEEDED, PlanStepStatus.FAILED, PlanStepStatus.SKIPPED):
                return step
        return None

    def _assemble_final_message(self, plan: Plan, current_message: str) -> str:
        summaries = [step.result_summary for step in plan.steps if step.result_summary]
        if len(summaries) <= 1:
            return summaries[0] if summaries else (current_message or "")
        return "\n\n---\n\n".join(summaries)

    def _current_step(self, state: AgentState) -> PlanStep | None:
        plan = state.get("plan")
        return plan.current_step() if plan else None

    async def _run_step(
        self,
        state: AgentState,
        executor: Callable[..., Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        plan = state["plan"]
        step = plan.current_step() if plan else None
        if step is None:
            return {}
        step.status = PlanStepStatus.RUNNING
        try:
            result = await executor(state)
            step.status = PlanStepStatus.SUCCEEDED
        except Exception as exc:
            logger.error("plan_step_failed", step=step.step_id, error=str(exc))
            step.status = PlanStepStatus.FAILED
            result = {
                "final_message": f"步骤 {step.step_id} 执行失败：{exc}",
                "tool_calls": [],
                "sources": [],
            }

        step.result_summary = result.get("final_message", "")
        new_events: list[ToolCallEvent] = result.get("tool_calls", []) or []
        step.tool_calls = [event.tool_name for event in new_events]
        merged_tool_calls = (state.get("tool_calls") or []) + new_events
        merged_sources = sorted(set((state.get("sources") or [])) | set(result.get("sources") or []))
        plan.cursor += 1
        return {
            "plan": plan,
            "tool_calls": merged_tool_calls,
            "sources": merged_sources,
            "final_message": step.result_summary,
        }

    async def chat(self, request: ChatRequest) -> ChatResponse:
        start_time = time.time()
        session_id = request.session_id or str(uuid.uuid4())
        logger.info("chat_request", user=request.user_id, session=session_id, message=request.message[:100])

        initial_state = self._build_initial_state(request, session_id)

        try:
            result = await self.graph.ainvoke(initial_state)
            final_message = result.get("final_message", "") or "抱歉，我暂时无法处理这个请求。"
            tool_calls = result.get("tool_calls", [])
            intent = result.get("intent")
            route = result.get("route")
            risk_level = result.get("risk_level", RiskLevel.LOW)
            sources = result.get("sources", [])
            needs_approval = result.get("needs_approval", False)
        except Exception as exc:
            logger.error("agent_execution_failed", error=str(exc), session=session_id)
            final_message = f"抱歉，处理请求时遇到了错误：{exc}\n请检查相关服务是否可用，或联系管理员。"
            tool_calls = []
            intent = None
            route = None
            risk_level = RiskLevel.MEDIUM
            sources = []
            needs_approval = False

        self._persist_session_turn(session_id, request.message, final_message)
        duration_ms = int((time.time() - start_time) * 1000)
        self._audit_request(
            request=request,
            session_id=session_id,
            intent=intent,
            route=route,
            risk_level=risk_level,
            needs_approval=needs_approval,
            tool_calls=tool_calls,
            final_message=final_message,
            duration_ms=duration_ms,
        )

        return ChatResponse(
            session_id=session_id,
            message=final_message,
            intent=intent,
            route=route,
            risk_level=risk_level,
            needs_approval=needs_approval,
            tool_calls=tool_calls,
            sources=sources,
            tokens_used=0,
        )

    def _build_initial_state(self, request: ChatRequest, session_id: str) -> AgentState:
        history = self.session_store.get_recent_messages(session_id, limit=6)
        messages = history + [HumanMessage(content=request.message)]
        return {
            "messages": messages,
            "session_id": session_id,
            "user_id": request.user_id,
            "user_role": request.user_role,
            "context": request.context,
            "intent": None,
            "route": None,
            "risk_level": RiskLevel.LOW,
            "needs_approval": False,
            "sources": [],
            "tool_calls": [],
            "final_message": "",
            "plan": None,
            "plan_decision": None,
        }

    def _persist_session_turn(self, session_id: str, user_message: str, assistant_message: str) -> None:
        self.session_store.append_messages(
            session_id,
            [HumanMessage(content=user_message), AIMessage(content=assistant_message)],
        )

    def _audit_request(
        self,
        *,
        request: ChatRequest,
        session_id: str,
        intent: IntentTypeKey | None,
        route: RouteKey | None,
        risk_level: RiskLevel,
        needs_approval: bool,
        tool_calls: list[ToolCallEvent],
        final_message: str,
        duration_ms: int,
    ) -> None:
        self.audit_logger.log(
            user_id=request.user_id,
            session_id=session_id,
            intent=intent,
            route=route,
            risk_level=risk_level,
            needs_approval=needs_approval,
            tool_name=tool_calls[0].tool_name if tool_calls else None,
            tool_calls=[tool.tool_name for tool in tool_calls],
            result_summary=final_message[:200],
            success=not any(tool.status == ToolCallStatus.FAILED for tool in tool_calls),
            duration_ms=duration_ms,
        )

    def _get_latest_user_message(self, messages: list[BaseMessage]) -> str:
        for message in reversed(messages):
            if isinstance(message, HumanMessage):
                return self._normalize_message_content(message.content)
        return ""

    def _normalize_message_content(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(item.get("text", ""))
                else:
                    parts.append(str(item))
            return "\n".join(part for part in parts if part)
        return str(content)
