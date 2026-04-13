"""
OpsAgent Core - route-first orchestration for DevOps workflows.

Architecture:
- Router layer: classify request into knowledge / read-only ops / diagnosis / mutation
- Session layer: keep short conversation history and route metadata
- Subgraphs: each route has its own bounded execution policy and tool allowlist
- Approval gate: mutation requests require approval before tool execution
"""
from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, Annotated, Awaitable, Callable, TypedDict

import structlog
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

from agent_core.audit import audit_logger
from agent_core.router import IntentRouter
from agent_core.schemas import (
    AgentIdentity,
    AgentRoute,
    ChatRequest,
    ChatResponse,
    IntentType,
    MemoryLayer,
    RiskLevel,
    ToolCallEvent,
    ToolCallStatus,
    UserRole,
)
from agent_core.session import session_store
from config import settings
from llm_gateway import llm_gateway
from tools import ALL_TOOLS

logger = structlog.get_logger()


BASE_SYSTEM_PROMPT = """你是 OpsAgent，一个企业级 DevOps 智能运维助手。

必须遵守这些规则：
- 只基于工具返回的真实数据回答，不编造事实
- 优先用中文，先给结论，再给证据和建议
- 如果需要进一步人工确认，要明确说明原因
- 如果工具返回错误，要直接说明受阻点和下一步建议
"""

ROUTE_PROMPTS = {
    AgentRoute.KNOWLEDGE: """你负责知识问答子图。
- 优先使用 knowledge_tool 获取依据
- 回答中引用来源路径
- 不确定时明确说不知道""",
    AgentRoute.READ_ONLY_OPS: """你负责只读运维查询子图。
- 只允许做只读查询
- 优先给状态结论，再列关键字段
- 不要要求审批""",
    AgentRoute.DIAGNOSIS: """你负责故障诊断子图。
- 目标是收集证据，不是猜测
- 优先查询状态，再看日志或构建输出
- 最终输出结论、证据、可能原因、建议动作""",
    AgentRoute.MUTATION: """你负责变更操作子图。
- 任何有副作用的动作都必须经过审批
- 审批前只输出执行计划，不执行工具
- 审批后才可调用允许的变更工具""",
}

ROUTE_STEP_LIMITS = {
    AgentRoute.KNOWLEDGE: 2,
    AgentRoute.READ_ONLY_OPS: 3,
    AgentRoute.DIAGNOSIS: 5,
    AgentRoute.MUTATION: 3,
}

ROUTE_TOOL_NAMES = {
    AgentRoute.KNOWLEDGE: {"query_knowledge", "index_documents"},
    AgentRoute.READ_ONLY_OPS: {
        "query_jenkins_build",
        "get_jenkins_build_log",
        "get_pod_status",
        "get_deployment_status",
        "get_service_info",
        "get_pod_logs",
        "search_logs",
        "get_error_statistics",
    },
    AgentRoute.DIAGNOSIS: {
        "query_jenkins_build",
        "get_jenkins_build_log",
        "get_pod_status",
        "get_deployment_status",
        "get_service_info",
        "get_pod_logs",
        "diagnose_pod",
        "search_logs",
        "get_error_statistics",
        "query_knowledge",
    },
    AgentRoute.MUTATION: {
        "generate_jenkinsfile",
        "index_documents",
    },
}


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    session_id: str
    user_id: str
    user_role: UserRole
    context: dict[str, Any]
    intent: IntentType | None
    route: AgentRoute | None
    risk_level: RiskLevel
    needs_approval: bool
    sources: list[str]
    tool_calls: list[ToolCallEvent]
    final_message: str


class OpsAgent:
    """Route-first OpsAgent orchestrator."""

    def __init__(self):
        self.router = IntentRouter()
        self.tool_registry = {tool.name: tool for tool in ALL_TOOLS}
        self.route_tools = {
            route: [self.tool_registry[name] for name in sorted(tool_names) if name in self.tool_registry]
            for route, tool_names in ROUTE_TOOL_NAMES.items()
        }
        self.graph = self._build_graph()
        logger.info(
            "ops_agent_initialized",
            tools=[tool.name for tool in ALL_TOOLS],
            routes=[route.value for route in self.route_tools],
        )

    def _build_graph(self) -> StateGraph:
        graph = StateGraph(AgentState)
        graph.add_node("router", self._route_node)
        graph.add_node("knowledge", self._knowledge_node)
        graph.add_node("read_only_ops", self._read_only_node)
        graph.add_node("diagnosis", self._diagnosis_node)
        graph.add_node("mutation", self._mutation_node)

        graph.set_entry_point("router")
        graph.add_conditional_edges(
            "router",
            self._select_route_node,
            {
                "knowledge": "knowledge",
                "read_only_ops": "read_only_ops",
                "diagnosis": "diagnosis",
                "mutation": "mutation",
            },
        )
        graph.add_edge("knowledge", END)
        graph.add_edge("read_only_ops", END)
        graph.add_edge("diagnosis", END)
        graph.add_edge("mutation", END)
        return graph.compile()

    async def _route_node(self, state: AgentState) -> dict[str, Any]:
        current_message = self._get_latest_user_message(state["messages"])
        request = ChatRequest(
            message=current_message,
            session_id=state["session_id"],
            user_id=state["user_id"],
            user_role=state["user_role"],
            context=state["context"],
        )
        decision = await self.router.route(request)
        session_store.update_route_state(
            state["session_id"],
            intent=decision.intent,
            route=decision.route,
            risk_level=decision.risk_level,
            metadata={"requires_approval": decision.requires_approval},
        )
        logger.info(
            "route_selected",
            session=state["session_id"],
            route=decision.route.value,
            intent=decision.intent.value,
            risk=decision.risk_level.value,
            requires_approval=decision.requires_approval,
        )
        return {
            "intent": decision.intent,
            "route": decision.route,
            "risk_level": decision.risk_level,
            "needs_approval": decision.requires_approval,
        }

    def _select_route_node(self, state: AgentState) -> str:
        route = state.get("route") or AgentRoute.KNOWLEDGE
        return route.value

    async def _knowledge_node(self, state: AgentState) -> dict[str, Any]:
        return await self._execute_knowledge(state)

    async def _read_only_node(self, state: AgentState) -> dict[str, Any]:
        return await self._execute_read_only_ops(state)

    async def _diagnosis_node(self, state: AgentState) -> dict[str, Any]:
        return await self._run_route_subgraph(state, AgentRoute.DIAGNOSIS)

    async def _mutation_node(self, state: AgentState) -> dict[str, Any]:
        if state["user_role"] == UserRole.VIEWER:
            return {
                "final_message": "当前请求属于变更操作，但你的角色是 Viewer，只允许只读查询。请使用 Operator 或 Admin 身份重试。",
                "tool_calls": [],
                "sources": [],
            }

        return await self._execute_mutation(state)

    async def _execute_knowledge(
        self,
        state: AgentState,
        event_callback: EventCallback | None = None,
    ) -> dict[str, Any]:
        message = self._get_latest_user_message(state["messages"])
        if self._is_index_request(message):
            if state["user_role"] != UserRole.ADMIN:
                return {
                    "final_message": "索引文档属于管理员操作。请使用 Admin 身份执行，或改为普通知识查询。",
                    "tool_calls": [],
                    "sources": [],
                }
            docs_directory = self._extract_docs_directory(message, state["context"])
            event, output = await self._invoke_tool(
                "index_documents",
                {"docs_directory": docs_directory},
                event_callback,
                session_id=state["session_id"],
                route=AgentRoute.MUTATION,
            )
            final_message = self._format_index_result(output, docs_directory)
            self._write_execution_memory(
                state["session_id"],
                action="index_documents",
                target=docs_directory,
                status="completed" if event.status == ToolCallStatus.SUCCESS else "failed",
            )
            return {"final_message": final_message, "tool_calls": [event], "sources": []}

        top_k = self._extract_top_k(message, state["context"])
        event, output = await self._invoke_tool(
            "query_knowledge",
            {"question": message, "top_k": top_k},
            event_callback,
            session_id=state["session_id"],
            route=AgentRoute.KNOWLEDGE,
        )
        sources = self._extract_sources(output)
        final_message = self._format_knowledge_result(output)
        self._update_memory_from_knowledge(state["session_id"], message, output, sources)
        return {"final_message": final_message, "tool_calls": [event], "sources": sources}

    async def _execute_read_only_ops(
        self,
        state: AgentState,
        event_callback: EventCallback | None = None,
    ) -> dict[str, Any]:
        message = self._get_latest_user_message(state["messages"])
        plan = self._plan_read_only_tool(message, state["context"], state["session_id"])
        if not plan:
            return {
                "final_message": "我没有从请求里识别出明确的只读查询目标。请补充服务名、namespace、构建编号或日志范围。",
                "tool_calls": [],
                "sources": [],
            }

        tool_calls: list[ToolCallEvent] = []
        outputs: list[tuple[str, str]] = []
        for tool_name, args in plan:
            event, output = await self._invoke_tool(
                tool_name,
                args,
                event_callback,
                session_id=state["session_id"],
                route=AgentRoute.READ_ONLY_OPS,
            )
            tool_calls.append(event)
            outputs.append((tool_name, output))
            self._update_memory_from_tool_output(state["session_id"], tool_name, output)

        final_message = self._format_read_only_summary(outputs)
        sources = []
        for _, output in outputs:
            sources.extend(self._extract_sources(output))
        return {"final_message": final_message, "tool_calls": tool_calls, "sources": sorted(set(sources))}

    async def _execute_mutation(
        self,
        state: AgentState,
        event_callback: EventCallback | None = None,
    ) -> dict[str, Any]:
        message = self._get_latest_user_message(state["messages"])
        if self._is_index_request(message):
            docs_directory = self._extract_docs_directory(message, state["context"])
            self._write_plan_memory(
                state["session_id"],
                action="index_documents",
                target=docs_directory,
                namespace=self._extract_namespace(message, state["context"], state["session_id"]),
            )
            if state["needs_approval"] and not self._approval_granted(state):
                return {
                    "final_message": (
                        "当前请求会修改知识库索引，执行前需要审批。\n"
                        f"目标目录: {docs_directory}\n"
                        "如果确认执行，请在下一次请求中携带 `context.approved=true`。"
                    ),
                    "tool_calls": [],
                    "sources": [],
                }
            event, output = await self._invoke_tool(
                "index_documents",
                {"docs_directory": docs_directory},
                event_callback,
                session_id=state["session_id"],
                route=AgentRoute.MUTATION,
            )
            self._write_execution_memory(
                state["session_id"],
                action="index_documents",
                target=docs_directory,
                status="completed" if event.status == ToolCallStatus.SUCCESS else "failed",
            )
            return {
                "final_message": self._format_index_result(output, docs_directory),
                "tool_calls": [event],
                "sources": [],
            }

        if state["intent"] != IntentType.PIPELINE_CREATE:
            return {
                "final_message": (
                    "当前 mutation 路由只支持 Jenkinsfile 生成。"
                    "文档索引、重启、扩缩容、回滚等真实变更工具还未完全接入，请先补 mutation tools。"
                ),
                "tool_calls": [],
                "sources": [],
            }

        plan = self._build_pipeline_plan(message, state["context"], state["session_id"])
        self._write_plan_memory(
            state["session_id"],
            action="generate_jenkinsfile",
            target=plan["project_name"],
            namespace=plan["namespace"],
        )
        if state["needs_approval"] and not self._approval_granted(state):
            return {
                "final_message": self._format_mutation_plan(plan),
                "tool_calls": [],
                "sources": [],
            }

        event, output = await self._invoke_tool(
            "generate_jenkinsfile",
            plan,
            event_callback,
            session_id=state["session_id"],
            route=AgentRoute.MUTATION,
        )
        self._write_execution_memory(
            state["session_id"],
            action="generate_jenkinsfile",
            target=plan["project_name"],
            status="completed" if event.status == ToolCallStatus.SUCCESS else "failed",
        )
        final_message = self._format_mutation_execution(plan, output)
        return {"final_message": final_message, "tool_calls": [event], "sources": []}

    async def _run_route_subgraph(
        self,
        state: AgentState,
        route: AgentRoute,
        event_callback: EventCallback | None = None,
    ) -> dict[str, Any]:
        tools = self.route_tools.get(route, [])
        prompt = self._build_route_prompt(route, state)
        return await self._execute_bounded_tool_loop(
            state=state,
            route=route,
            prompt=prompt,
            tools=tools,
            max_steps=ROUTE_STEP_LIMITS[route],
            event_callback=event_callback,
        )

    async def _invoke_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
        event_callback: EventCallback | None = None,
        *,
        session_id: str = "",
        route: AgentRoute | None = None,
    ) -> tuple[ToolCallEvent, str]:
        event = ToolCallEvent(
            tool_name=tool_name,
            action=tool_name,
            params=args,
            status=ToolCallStatus.RUNNING,
        )
        if event_callback:
            await event_callback("tool_call", {"tool": tool_name, "input": args})

        started_at = time.time()
        try:
            tool = self.tool_registry[tool_name]
            output = await tool.ainvoke(args)
            event.status = ToolCallStatus.SUCCESS
            event.result = self._truncate_text(str(output), 500)
        except Exception as exc:
            output = f'{{"error": "{str(exc)}"}}'
            event.status = ToolCallStatus.FAILED
            event.error = str(exc)
            event.result = self._truncate_text(output, 500)
        event.duration_ms = int((time.time() - started_at) * 1000)

        if event_callback:
            await event_callback("tool_result", {"tool": tool_name, "output": self._truncate_text(str(output), 500)})

        if session_id and route:
            self._append_execution_artifact(session_id, route, tool_name, output)

        return event, str(output)

    async def _execute_bounded_tool_loop(
        self,
        *,
        state: AgentState,
        route: AgentRoute,
        prompt: str,
        tools: list[Any],
        max_steps: int,
        event_callback: EventCallback | None = None,
    ) -> dict[str, Any]:
        llm = llm_gateway.get_main_model()
        llm_with_tools = llm.bind_tools(tools) if tools else llm
        conversation: list[BaseMessage] = [SystemMessage(content=prompt)] + list(state["messages"])
        tool_events: list[ToolCallEvent] = []
        sources: list[str] = []
        final_message = ""

        for _ in range(max_steps):
            response = await llm_with_tools.ainvoke(conversation)
            conversation.append(response)

            if not getattr(response, "tool_calls", None):
                final_message = self._normalize_message_content(response.content)
                if event_callback and final_message:
                    await event_callback("message", {"content": final_message, "session_id": state["session_id"]})
                break

            for tool_call in response.tool_calls:
                tool_name = tool_call["name"]
                args = tool_call.get("args", {})
                event = ToolCallEvent(
                    tool_name=tool_name,
                    action=tool_name,
                    params=args,
                    status=ToolCallStatus.RUNNING,
                )

                if event_callback:
                    await event_callback("tool_call", {"tool": tool_name, "input": args})

                started_at = time.time()
                try:
                    tool = self.tool_registry[tool_name]
                    output = await tool.ainvoke(args)
                    event.status = ToolCallStatus.SUCCESS
                    event.result = self._truncate_text(str(output), 500)
                    event.duration_ms = int((time.time() - started_at) * 1000)
                    sources.extend(self._extract_sources(output))
                    conversation.append(ToolMessage(content=str(output), tool_call_id=tool_call["id"]))
                    self._append_execution_artifact(state["session_id"], route, tool_name, output)
                    self._update_memory_from_tool_output(state["session_id"], tool_name, str(output))
                    if event_callback:
                        await event_callback(
                            "tool_result",
                            {"tool": tool_name, "output": self._truncate_text(str(output), 500)},
                        )
                except Exception as exc:
                    event.status = ToolCallStatus.FAILED
                    event.error = str(exc)
                    event.duration_ms = int((time.time() - started_at) * 1000)
                    conversation.append(ToolMessage(content=f"工具执行失败: {exc}", tool_call_id=tool_call["id"]))
                    if event_callback:
                        await event_callback("tool_result", {"tool": tool_name, "output": f"ERROR: {exc}"})
                tool_events.append(event)

        if not final_message:
            final_message = (
                "我已经完成本轮子图执行，但没有得到稳定的最终结论。"
                "建议缩小查询范围，或提供更明确的服务名、namespace、构建编号等信息。"
            )
            if event_callback:
                await event_callback("message", {"content": final_message, "session_id": state["session_id"]})

        if route == AgentRoute.DIAGNOSIS:
            session_store.write_memory_item(
                state["session_id"],
                writer=AgentIdentity.DIAGNOSIS,
                layer=MemoryLayer.HYPOTHESES,
                key="diagnosis_summary",
                value=self._truncate_text(final_message.replace("\n", " "), 240),
                source="diagnosis_executor",
                confidence=0.7,
            )

        return {
            "final_message": final_message,
            "tool_calls": tool_events,
            "sources": sorted(set(filter(None, sources))),
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
        history = session_store.get_recent_messages(session_id, limit=6)
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
        }

    def _persist_session_turn(self, session_id: str, user_message: str, assistant_message: str) -> None:
        session_store.append_messages(
            session_id,
            [HumanMessage(content=user_message), AIMessage(content=assistant_message)],
        )

    def _audit_request(
        self,
        *,
        request: ChatRequest,
        session_id: str,
        intent: IntentType | None,
        route: AgentRoute | None,
        risk_level: RiskLevel,
        needs_approval: bool,
        tool_calls: list[ToolCallEvent],
        final_message: str,
        duration_ms: int,
    ) -> None:
        audit_logger.log(
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

    def _build_route_prompt(self, route: AgentRoute, state: AgentState) -> str:
        history_note = ""
        snapshot = session_store.get(state["session_id"])
        if snapshot.last_intent or snapshot.last_route:
            history_note = (
                f"\n最近一次会话路由: {snapshot.last_route.value if snapshot.last_route else 'unknown'};"
                f" 最近一次意图: {snapshot.last_intent.value if snapshot.last_intent else 'unknown'}。"
            )
        context_note = ""
        if state["context"]:
            context_note = f"\n请求上下文: {json.dumps(state['context'], ensure_ascii=False)}"
        memory_note = self._build_memory_context(state["session_id"])
        return f"{BASE_SYSTEM_PROMPT}\n\n{ROUTE_PROMPTS[route]}{history_note}{context_note}{memory_note}"

    def _build_approval_message(self, state: AgentState) -> str:
        return (
            "当前请求被识别为需要审批的变更操作，已被审批门拦截。\n"
            "如果你确认执行，请在下一次请求中携带 `context.approved=true`，"
            "并明确说明目标环境、服务名和预期动作。"
        )

    def _format_mutation_plan(self, plan: dict[str, Any]) -> str:
        return (
            "当前请求被识别为变更操作，执行前需要审批。\n"
            f"计划动作: 生成 {plan['project_name']} 的 Jenkinsfile\n"
            f"语言类型: {plan['language']}\n"
            f"目标环境: {plan['deploy_env']} / namespace={plan['namespace']}\n"
            f"分支: {plan['branch']}\n"
            "如果确认执行，请在下一次请求中携带 `context.approved=true`。"
        )

    def _format_mutation_execution(self, plan: dict[str, Any], output: str) -> str:
        payload = self._load_json(output)
        if payload.get("error"):
            return f"变更执行失败：{payload['error']}"
        return (
            "变更执行完成。\n"
            f"动作: 为 {plan['project_name']} 生成 Jenkinsfile\n"
            f"语言: {payload.get('language', plan['language'])}\n"
            f"环境: {plan['deploy_env']} / namespace={plan['namespace']}\n"
            "已返回 Jenkinsfile 内容，可继续进入人工评审或后续创建 Job。"
        )

    def _approval_granted(self, state: AgentState) -> bool:
        approved = state["context"].get("approved")
        return approved is True

    def _build_memory_context(self, session_id: str) -> str:
        memory = session_store.get_shared_memory(session_id)
        lines = []
        for layer in (MemoryLayer.FACTS, MemoryLayer.OBSERVATIONS, MemoryLayer.HYPOTHESES):
            layer_items = memory.get_layer(layer)
            if not layer_items:
                continue
            preview = ", ".join(f"{key}={item.value}" for key, item in list(layer_items.items())[:6])
            lines.append(f"{layer.value}: {preview}")
        artifacts = session_store.get_recent_artifacts(session_id, limit=3)
        if artifacts:
            artifact_preview = " | ".join(f"{artifact.tool_name}:{artifact.summary}" for artifact in artifacts)
            lines.append(f"artifacts: {artifact_preview}")
        if not lines:
            return ""
        return "\n共享工作记忆:\n" + "\n".join(f"- {line}" for line in lines)

    def _append_execution_artifact(self, session_id: str, route: AgentRoute, tool_name: str, output: str) -> None:
        payload = self._load_json(output)
        session_store.append_artifact(
            session_id,
            route=route,
            tool_name=tool_name,
            summary=self._summarize_tool_output(tool_name, payload),
            payload=payload,
        )

    def _summarize_tool_output(self, tool_name: str, payload: dict[str, Any]) -> str:
        if payload.get("error"):
            return f"error={payload['error']}"
        if tool_name == "query_knowledge":
            return f"results={len(payload.get('results', []))}"
        if tool_name == "get_pod_status":
            return f"namespace={payload.get('namespace')} pods={payload.get('total_pods', 0)}"
        if tool_name == "get_pod_logs":
            return f"pod={payload.get('pod_name')} lines={payload.get('lines', 0)}"
        if tool_name == "search_logs":
            return f"service={payload.get('service')} count={payload.get('count', 0)}"
        if tool_name == "query_jenkins_build":
            return f"job={payload.get('job_name')} result={payload.get('result')}"
        return self._truncate_text(json.dumps(payload, ensure_ascii=False), 120)

    def _update_memory_from_knowledge(self, session_id: str, message: str, output: str, sources: list[str]) -> None:
        payload = self._load_json(output)
        service = self._extract_service_name(message, {}, session_id)
        if service:
            session_store.write_memory_item(
                session_id,
                writer=AgentIdentity.KNOWLEDGE,
                layer=MemoryLayer.FACTS,
                key="service",
                value=service,
                source="query_knowledge",
                confidence=0.9,
            )

        text_blob = " ".join(str(result.get("content", "")) for result in payload.get("results", []))
        namespace = self._extract_namespace(text_blob, {}, session_id)
        if namespace and namespace != "default":
            session_store.write_memory_item(
                session_id,
                writer=AgentIdentity.KNOWLEDGE,
                layer=MemoryLayer.FACTS,
                key="namespace",
                value=namespace,
                source="query_knowledge",
                confidence=0.9,
            )
            session_store.write_memory_item(
                session_id,
                writer=AgentIdentity.KNOWLEDGE,
                layer=MemoryLayer.FACTS,
                key="env",
                value=namespace,
                source="query_knowledge",
                confidence=0.85,
            )

        if sources:
            session_store.write_memory_item(
                session_id,
                writer=AgentIdentity.KNOWLEDGE,
                layer=MemoryLayer.FACTS,
                key="source_refs",
                value=sources,
                source="query_knowledge",
                confidence=1.0,
            )

    def _update_memory_from_tool_output(self, session_id: str, tool_name: str, output: str) -> None:
        payload = self._load_json(output)
        if payload.get("error"):
            return
        writer = AgentIdentity.READ_OPS
        if tool_name == "diagnose_pod":
            writer = AgentIdentity.DIAGNOSIS

        if tool_name == "get_pod_status":
            namespace = payload.get("namespace")
            if namespace:
                session_store.write_memory_item(
                    session_id,
                    writer=AgentIdentity.READ_OPS,
                    layer=MemoryLayer.OBSERVATIONS,
                    key="namespace",
                    value=namespace,
                    source=tool_name,
                    confidence=0.95,
                )
            pods = payload.get("pods", [])
            if pods:
                first = pods[0]
                session_store.write_memory_item(
                    session_id,
                    writer=AgentIdentity.READ_OPS,
                    layer=MemoryLayer.OBSERVATIONS,
                    key="pod_name",
                    value=first.get("name"),
                    source=tool_name,
                    confidence=0.95,
                )
                session_store.write_memory_item(
                    session_id,
                    writer=AgentIdentity.READ_OPS,
                    layer=MemoryLayer.OBSERVATIONS,
                    key="last_pod_status",
                    value=first.get("phase"),
                    source=tool_name,
                    confidence=0.9,
                )
        elif tool_name == "query_jenkins_build":
            if payload.get("job_name"):
                session_store.write_memory_item(
                    session_id,
                    writer=AgentIdentity.READ_OPS,
                    layer=MemoryLayer.OBSERVATIONS,
                    key="job_name",
                    value=payload["job_name"],
                    source=tool_name,
                    confidence=0.95,
                )
            if payload.get("result"):
                session_store.write_memory_item(
                    session_id,
                    writer=AgentIdentity.READ_OPS,
                    layer=MemoryLayer.OBSERVATIONS,
                    key="last_build_result",
                    value=payload["result"],
                    source=tool_name,
                    confidence=0.9,
                )
        elif tool_name == "search_logs":
            if payload.get("service"):
                session_store.write_memory_item(
                    session_id,
                    writer=AgentIdentity.READ_OPS,
                    layer=MemoryLayer.OBSERVATIONS,
                    key="last_log_service",
                    value=payload["service"],
                    source=tool_name,
                    confidence=0.9,
                )
            logs = payload.get("logs", [])
            if logs:
                first_message = logs[0].get("message", "")
                session_store.write_memory_item(
                    session_id,
                    writer=AgentIdentity.READ_OPS,
                    layer=MemoryLayer.OBSERVATIONS,
                    key="last_error_summary",
                    value=self._truncate_text(first_message.replace("\n", " "), 160),
                    source=tool_name,
                    confidence=0.7,
                )
        elif tool_name == "diagnose_pod":
            issues = payload.get("issues", [])
            if issues:
                issue = issues[0]
                session_store.write_memory_item(
                    session_id,
                    writer=writer,
                    layer=MemoryLayer.HYPOTHESES,
                    key="likely_root_cause",
                    value=issue.get("type") or issue.get("message") or "unknown",
                    source=tool_name,
                    confidence=0.75,
                )
            session_store.write_memory_item(
                session_id,
                writer=writer,
                layer=MemoryLayer.HYPOTHESES,
                key="diagnosis_summary",
                value=self._truncate_text(json.dumps(payload, ensure_ascii=False), 240),
                source=tool_name,
                confidence=0.7,
            )

    def _write_plan_memory(self, session_id: str, action: str, target: str, namespace: str) -> None:
        session_store.write_memory_item(
            session_id,
            writer=AgentIdentity.CHANGE_PLANNER,
            layer=MemoryLayer.PLANS,
            key="planned_action",
            value=action,
            source="mutation_plan",
            confidence=1.0,
        )
        session_store.write_memory_item(
            session_id,
            writer=AgentIdentity.CHANGE_PLANNER,
            layer=MemoryLayer.PLANS,
            key="planned_target",
            value=target,
            source="mutation_plan",
            confidence=1.0,
        )
        session_store.write_memory_item(
            session_id,
            writer=AgentIdentity.CHANGE_PLANNER,
            layer=MemoryLayer.PLANS,
            key="planned_namespace",
            value=namespace,
            source="mutation_plan",
            confidence=1.0,
        )

    def _write_execution_memory(self, session_id: str, action: str, target: str, status: str) -> None:
        session_store.write_memory_item(
            session_id,
            writer=AgentIdentity.CHANGE_EXECUTOR,
            layer=MemoryLayer.EXECUTION,
            key="executed_action",
            value=action,
            source="mutation_execution",
            confidence=1.0,
        )
        session_store.write_memory_item(
            session_id,
            writer=AgentIdentity.CHANGE_EXECUTOR,
            layer=MemoryLayer.EXECUTION,
            key="executed_target",
            value=target,
            source="mutation_execution",
            confidence=1.0,
        )
        session_store.write_memory_item(
            session_id,
            writer=AgentIdentity.CHANGE_EXECUTOR,
            layer=MemoryLayer.EXECUTION,
            key="execution_status",
            value=status,
            source="mutation_execution",
            confidence=1.0,
        )

    def _is_index_request(self, message: str) -> bool:
        return any(token in message.lower() for token in ("索引", "同步文档", "导入文档", "index"))

    def _extract_docs_directory(self, message: str, context: dict[str, Any]) -> str:
        if isinstance(context.get("docs_directory"), str):
            return context["docs_directory"]
        match = re.search(r"(/[\w./-]+)", message)
        if match:
            return match.group(1)
        return "./docs"

    def _extract_top_k(self, message: str, context: dict[str, Any]) -> int:
        if isinstance(context.get("top_k"), int):
            return max(1, min(context["top_k"], 10))
        match = re.search(r"top[_ -]?k\s*=?\s*(\d+)", message.lower())
        if match:
            return max(1, min(int(match.group(1)), 10))
        return 5

    def _plan_read_only_tool(
        self,
        message: str,
        context: dict[str, Any],
        session_id: str = "",
    ) -> list[tuple[str, dict[str, Any]]] | None:
        lowered = message.lower()
        namespace = self._extract_namespace(message, context, session_id)
        service = self._extract_service_name(message, context, session_id)
        build_number = self._extract_build_number(message, context)
        time_range = self._extract_time_range(message, context)

        if "构建日志" in message or ("jenkins" in lowered and "日志" in message):
            job_name = self._extract_job_name(message, context, service, session_id)
            if job_name:
                return [("get_jenkins_build_log", {"job_name": job_name, "build_number": build_number or 1, "tail_lines": 100})]

        if "构建" in message or "jenkins" in lowered:
            job_name = self._extract_job_name(message, context, service, session_id)
            if job_name:
                args: dict[str, Any] = {"job_name": job_name}
                if build_number:
                    args["build_number"] = build_number
                return [("query_jenkins_build", args)]

        if ("错误统计" in message) or ("统计" in message and "日志" in message):
            if service:
                return [("get_error_statistics", {"service": service, "time_range_minutes": time_range})]

        if ("pod日志" in message) or ("pod 日志" in message) or ("日志" in message and "pod" in lowered):
            pod_name = self._extract_pod_name(message, context, service, session_id)
            if pod_name:
                return [("get_pod_logs", {"namespace": namespace, "pod_name": pod_name, "tail_lines": 100})]

        if "日志" in message:
            if service:
                return [(
                    "search_logs",
                    {
                        "service": service,
                        "time_range_minutes": time_range,
                        "level": self._extract_log_level(message),
                        "keyword": self._extract_keyword(message),
                        "limit": 50,
                    },
                )]

        if "deployment" in lowered:
            return [("get_deployment_status", {"namespace": namespace, "name": service})]

        if "service" in lowered or "svc" in lowered:
            return [("get_service_info", {"namespace": namespace, "name": service})]

        if "pod" in lowered:
            return [("get_pod_status", {"namespace": namespace, "name_filter": service, "show_all": False})]

        if any(token in lowered for token in ("namespace", "命名空间")):
            return [("get_pod_status", {"namespace": namespace, "name_filter": service, "show_all": False})]

        return None

    def _extract_namespace(self, message: str, context: dict[str, Any], session_id: str = "") -> str:
        for candidate in [context.get("namespace"), context.get("env"), context.get("environment")]:
            if isinstance(candidate, str) and candidate in settings.allowed_namespaces + settings.readonly_namespaces:
                return candidate
        for namespace in settings.allowed_namespaces + settings.readonly_namespaces:
            if namespace and namespace in message:
                return namespace
        if session_id:
            memory_namespace = session_store.resolve_memory_value(
                session_id,
                "namespace",
                [MemoryLayer.FACTS, MemoryLayer.OBSERVATIONS],
            )
            if isinstance(memory_namespace, str) and memory_namespace:
                return memory_namespace
            memory_env = session_store.resolve_memory_value(
                session_id,
                "env",
                [MemoryLayer.FACTS, MemoryLayer.OBSERVATIONS],
            )
            if isinstance(memory_env, str) and memory_env:
                return memory_env
        return "default"

    def _extract_service_name(self, message: str, context: dict[str, Any], session_id: str = "") -> str:
        for key in ("service", "project", "job_name", "name"):
            value = context.get(key)
            if isinstance(value, str) and value:
                return value
        match = re.search(r"([a-z0-9-]+-service|[a-z0-9-]+-frontend|gateway)", message.lower())
        if match:
            return match.group(1)
        if session_id:
            memory_service = session_store.resolve_memory_value(
                session_id,
                "service",
                [MemoryLayer.FACTS, MemoryLayer.OBSERVATIONS],
            )
            if isinstance(memory_service, str):
                return memory_service
        return ""

    def _extract_job_name(self, message: str, context: dict[str, Any], fallback: str, session_id: str = "") -> str:
        if isinstance(context.get("job_name"), str) and context["job_name"]:
            return context["job_name"]
        match = re.search(r"job\s+([a-zA-Z0-9._-]+)", message)
        if match:
            return match.group(1)
        if session_id:
            memory_job = session_store.resolve_memory_value(
                session_id,
                "job_name",
                [MemoryLayer.FACTS, MemoryLayer.OBSERVATIONS],
            )
            if isinstance(memory_job, str) and memory_job:
                return memory_job
        return fallback

    def _extract_build_number(self, message: str, context: dict[str, Any]) -> int | None:
        value = context.get("build_number")
        if isinstance(value, int):
            return value
        match = re.search(r"(?:build|构建|#)\s*(\d+)", message.lower())
        if match:
            return int(match.group(1))
        return None

    def _extract_time_range(self, message: str, context: dict[str, Any]) -> int:
        value = context.get("time_range_minutes")
        if isinstance(value, int):
            return max(1, min(value, 24 * 60))
        match = re.search(r"最近\s*(\d+)\s*(分钟|小时)", message)
        if match:
            amount = int(match.group(1))
            return amount * 60 if match.group(2) == "小时" else amount
        return 60

    def _extract_pod_name(self, message: str, context: dict[str, Any], fallback: str, session_id: str = "") -> str:
        for key in ("pod_name", "pod"):
            value = context.get(key)
            if isinstance(value, str) and value:
                return value
        match = re.search(r"([a-z0-9-]+-[a-z0-9]+-[a-z0-9]+)", message.lower())
        if match:
            return match.group(1)
        if session_id:
            memory_pod = session_store.resolve_memory_value(
                session_id,
                "pod_name",
                [MemoryLayer.OBSERVATIONS, MemoryLayer.FACTS],
            )
            if isinstance(memory_pod, str) and memory_pod:
                return memory_pod
        return fallback

    def _extract_log_level(self, message: str) -> str:
        lowered = message.lower()
        if "warn" in lowered:
            return "WARN"
        if "info" in lowered:
            return "INFO"
        if "debug" in lowered:
            return "DEBUG"
        return "ERROR"

    def _extract_keyword(self, message: str) -> str:
        match = re.search(r"关键词[是为:]?\s*([^\s，。]+)", message)
        return match.group(1) if match else ""

    def _build_pipeline_plan(self, message: str, context: dict[str, Any], session_id: str = "") -> dict[str, Any]:
        project_name = self._extract_service_name(message, context, session_id) or "project"
        return {
            "project_name": project_name,
            "language": self._extract_language(message, context),
            "repo_url": str(context.get("repo_url") or f"https://git.example.com/{project_name}.git"),
            "branch": str(context.get("branch") or "main"),
            "registry": str(context.get("registry") or "registry.example.com"),
            "deploy_env": str(context.get("deploy_env") or context.get("env") or "staging"),
            "namespace": self._extract_namespace(message, context, session_id),
        }

    def _extract_language(self, message: str, context: dict[str, Any]) -> str:
        value = context.get("language")
        if isinstance(value, str) and value:
            return value
        lowered = message.lower()
        if any(token in lowered for token in ("java", "maven", "spring")):
            return "java_maven"
        if any(token in lowered for token in ("node", "react", "vue", "next")):
            return "nodejs"
        if any(token in lowered for token in ("python", "django", "flask", "fastapi")):
            return "python"
        if any(token in lowered for token in ("go", "golang")):
            return "go"
        return "java_maven"

    def _format_index_result(self, output: str, docs_directory: str) -> str:
        payload = self._load_json(output)
        if payload.get("error"):
            return f"文档索引失败：{payload['error']}"
        return (
            f"文档索引完成，目录: {docs_directory}\n"
            f"本次写入 chunks: {payload.get('indexed_chunks', 0)}\n"
            f"知识库总文档数: {payload.get('total_documents', 0)}"
        )

    def _format_knowledge_result(self, output: str) -> str:
        payload = self._load_json(output)
        if payload.get("answer_status") == "no_results":
            return payload.get("message", "知识库中未找到相关信息。")
        results = payload.get("results", [])[:3]
        if not results:
            return "知识库中未找到相关信息。"
        lines = ["根据知识库检索结果，结论如下："]
        for index, result in enumerate(results, start=1):
            excerpt = self._truncate_text(str(result.get("content", "")).replace("\n", " "), 140)
            source = result.get("source", "unknown")
            lines.append(f"{index}. {excerpt} 来源: {source}")
        return "\n".join(lines)

    def _format_read_only_summary(self, outputs: list[tuple[str, str]]) -> str:
        lines = []
        for tool_name, output in outputs:
            payload = self._load_json(output)
            lines.append(self._format_single_read_only_result(tool_name, payload))
        return "\n\n".join(filter(None, lines))

    def _format_single_read_only_result(self, tool_name: str, payload: dict[str, Any]) -> str:
        if payload.get("error"):
            return f"{tool_name} 执行失败：{payload['error']}"
        if tool_name == "get_pod_status":
            return (
                f"Pod 状态查询完成：namespace={payload.get('namespace')}，"
                f"共 {payload.get('total_pods', 0)} 个匹配 Pod。"
            )
        if tool_name == "get_deployment_status":
            deployments = payload.get("deployments", [])
            if deployments:
                first = deployments[0]
                return (
                    f"Deployment 查询完成：{first.get('name')} "
                    f"ready={first.get('ready_replicas', 0)}/{first.get('replicas', 0)}，"
                    f"image={first.get('image', 'unknown')}"
                )
            return "Deployment 查询完成，但未找到匹配项。"
        if tool_name == "get_service_info":
            services = payload.get("services", [])
            if services:
                first = services[0]
                return (
                    f"Service 查询完成：{first.get('name')} "
                    f"type={first.get('type')} cluster_ip={first.get('cluster_ip')}"
                )
            return "Service 查询完成，但未找到匹配项。"
        if tool_name == "get_pod_logs":
            return (
                f"Pod 日志已获取：pod={payload.get('pod_name')} "
                f"lines={payload.get('lines', 0)}。"
            )
        if tool_name == "query_jenkins_build":
            return (
                f"Jenkins 构建状态：job={payload.get('job_name')} "
                f"build=#{payload.get('build_number')} result={payload.get('result')}"
            )
        if tool_name == "get_jenkins_build_log":
            return (
                f"Jenkins 构建日志已获取：job={payload.get('job_name')} "
                f"build=#{payload.get('build_number')} returned_lines={payload.get('returned_lines', 0)}"
            )
        if tool_name == "search_logs":
            return (
                f"日志检索完成：service={payload.get('service')} "
                f"count={payload.get('count', 0)} level={payload.get('level')}"
            )
        if tool_name == "get_error_statistics":
            return (
                f"错误统计完成：service={payload.get('service')} "
                f"total_errors={payload.get('total_errors', 0)}"
            )
        return self._truncate_text(json.dumps(payload, ensure_ascii=False), 300)

    def _load_json(self, output: str) -> dict[str, Any]:
        try:
            payload = json.loads(output)
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

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

    def _truncate_text(self, text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[:limit] + "...(truncated)"

    def _extract_sources(self, output: Any) -> list[str]:
        try:
            payload = json.loads(output) if isinstance(output, str) else output
        except Exception:
            return []

        if not isinstance(payload, dict):
            return []

        results = payload.get("results", [])
        if not isinstance(results, list):
            return []

        sources = []
        for result in results:
            if isinstance(result, dict) and result.get("source"):
                sources.append(result["source"])
        return sources


EventCallback = Callable[[str, dict[str, Any]], Awaitable[None]]


class OpsAgentStreaming(OpsAgent):
    """Streaming wrapper over the route-first agent."""

    async def chat_stream(self, request: ChatRequest):
        session_id = request.session_id or str(uuid.uuid4())
        start_time = time.time()
        initial_state = self._build_initial_state(request, session_id)

        events: list[dict[str, Any]] = []

        async def emit(event_name: str, data: dict[str, Any]) -> None:
            events.append({"event": event_name, "data": data})

        try:
            route_update = await self._route_node(initial_state)
            state = {**initial_state, **route_update}
            await emit(
                "route",
                {
                    "route": state["route"].value if state["route"] else "",
                    "intent": state["intent"].value if state["intent"] else "",
                    "risk_level": state["risk_level"].value,
                    "needs_approval": state["needs_approval"],
                },
            )

            if state["route"] == AgentRoute.KNOWLEDGE:
                result = await self._execute_knowledge(state, emit)
            elif state["route"] == AgentRoute.READ_ONLY_OPS:
                result = await self._execute_read_only_ops(state, emit)
            elif state["route"] == AgentRoute.DIAGNOSIS:
                result = await self._run_route_subgraph(state, AgentRoute.DIAGNOSIS, emit)
            else:
                if state["user_role"] == UserRole.VIEWER:
                    result = await self._mutation_node(state)
                    await emit("message", {"content": result["final_message"], "session_id": session_id})
                elif state["needs_approval"] and not self._approval_granted(state):
                    result = await self._mutation_node(state)
                    await emit("message", {"content": result["final_message"], "session_id": session_id})
                else:
                    result = await self._execute_mutation(state, emit)

            final_message = result.get("final_message", "")
            tool_calls = result.get("tool_calls", [])
            sources = result.get("sources", [])

            self._persist_session_turn(session_id, request.message, final_message)
            self._audit_request(
                request=request,
                session_id=session_id,
                intent=state.get("intent"),
                route=state.get("route"),
                risk_level=state.get("risk_level", RiskLevel.LOW),
                needs_approval=state.get("needs_approval", False),
                tool_calls=tool_calls,
                final_message=final_message,
                duration_ms=int((time.time() - start_time) * 1000),
            )

            for event in events:
                yield event

            if sources:
                yield {"event": "sources", "data": {"sources": sources}}
        except Exception as exc:
            yield {"event": "error", "data": {"error": str(exc)}}

        duration_ms = int((time.time() - start_time) * 1000)
        yield {"event": "done", "data": {"session_id": session_id, "duration_ms": duration_ms}}
