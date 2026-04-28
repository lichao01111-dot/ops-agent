"""
OpsAgent Core - planner-driven orchestration for DevOps workflows.

Architecture (v2, see docs/architecture-deep-dive.md §9):
- Planner: generates a Plan of PlanSteps, can replan mid-flight
- Dispatcher: routes the current step to one of four executor nodes
- Executors: knowledge / read_only_ops / diagnosis (multi-hypothesis) / mutation
- Tool Registry: uniform lookup for local + MCP tools, replaces hardcoded
  per-route allowlists
- Approval gate: mutation requests require approval before side-effect tools
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, AsyncIterator, Awaitable, Callable

import structlog

from agent_kernel.approval import ApprovalDecision
from agent_kernel.audit import AuditLogger
from agent_kernel.base_agent import BaseAgent
from agent_kernel.observability.bootstrap import build_stage_observability_sink
from agent_kernel.schemas import (
    ChatRequest,
    ApprovalReceipt,
    PlanStep,
    RouteKey,
    ToolCallEvent,
    ToolCallStatus,
)
from agent_kernel.session import SessionStore
from agent_kernel.tools.mcp_gateway import MCPClient
from agent_kernel.tools.middleware import (
    InvocationContext,
    MetricsMiddleware,
    Middleware,
    build_default_chain,
    run_chain,
)
from agent_kernel.tools.observability import MultiSink, StructlogSink
from agent_kernel.tools.invoker import ToolInvoker
from agent_kernel.tools.registry import ToolRegistry
from agent_ops.risk_policy import OpsApprovalPolicy
from agent_ops.executors import (
    DiagnosisExecutor,
    InvestigatorExecutor,
    KnowledgeExecutor,
    MutationExecutor,
    ReadOnlyOpsExecutor,
    VerificationExecutor,
)
from agent_ops.extractors import extract_namespace, extract_pod_name, extract_service_name
from agent_ops.formatters import load_json, truncate_text
from agent_ops.planner import OpsPlanner
from agent_ops.router import IntentRouter
from agent_ops.topology import get_topology
from llm_gateway import llm_gateway
from tools import ALL_TOOLS

logger = structlog.get_logger()

EventCallback = Callable[[str, dict[str, Any]], Awaitable[None]]

SECRET_VALUE_TOKENS = (
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "apikey",
    "api_key",
    "access_key",
    "private_key",
)

class OpsAgent(BaseAgent):
    """Planner-driven OpsAgent orchestrator."""

    def __init__(
        self,
        *,
        session_store: SessionStore,
        tool_registry: ToolRegistry,
        audit_logger: AuditLogger,
        mcp_client: MCPClient | None = None,
        middlewares: list[Middleware] | None = None,
    ):
        local_session_store = session_store
        self.tool_registry = tool_registry
        self.mcp_client = mcp_client
        self.router = IntentRouter()
        self.observability_sink = build_stage_observability_sink("ops")
        llm_gateway.set_observability_sink(self.observability_sink)
        # Non-functional envelope: timeout / retry / circuit / idempotency /
        # cost-budget / schema-version / metrics. Built once and reused for
        # every tool invocation. Callers can inject a custom chain for tests
        # or swap backends for Redis in production.
        if middlewares is not None:
            self.middlewares = middlewares
        else:
            metrics_sink = StructlogSink()
            if self.observability_sink is not None:
                metrics_sink = MultiSink(children=[metrics_sink, self.observability_sink])
            self.middlewares = build_default_chain(metrics_middleware=MetricsMiddleware(sink=metrics_sink))
        self.planner = OpsPlanner(router=self.router, llm_provider=llm_gateway.get_main_model)
        self.approval_policy = OpsApprovalPolicy()
        self.topology = get_topology()

        # Per-Executor ToolInvoker: each Executor holds a minimum-privilege
        # handle instead of the raw bound ``_invoke_tool``. The invoker
        # * rejects unregistered tool names,
        # * enforces ``allowed_routes`` per caller identity,
        # * strips unrecognised kwargs before delegating to ``_invoke_tool``.
        # The invoker is awaitable (``__call__``), so executors keep calling
        # ``self.invoke_tool(name, args, event_callback, ...)`` unchanged.
        def _mk_invoker(caller: str, allowed: tuple[str, ...]) -> ToolInvoker:
            return ToolInvoker.from_bound(
                self._invoke_tool,
                get_spec=self.tool_registry.get_spec,
                caller=caller,
                allowed_routes=allowed,
            )

        # Instantiate executors
        self.knowledge_executor = KnowledgeExecutor(
            # knowledge → both query_knowledge (route=KNOWLEDGE) AND
            # index_documents (route=MUTATION, admin-only, gated by approval)
            invoke_tool=_mk_invoker("knowledge_executor", ("knowledge", "mutation")),
            session_store=local_session_store,
            llm_provider=llm_gateway.get_main_model,  # RAG: chunks -> grounded answer
        )
        self.read_only_executor = ReadOnlyOpsExecutor(
            invoke_tool=_mk_invoker("read_only_executor", ("read_only_ops",)),
            session_store=local_session_store,
        )
        self.mutation_executor = MutationExecutor(
            invoke_tool=_mk_invoker("mutation_executor", ("mutation",)),
            session_store=local_session_store,
        )
        self.diagnosis_executor = DiagnosisExecutor(
            invoke_tool=_mk_invoker("diagnosis_executor", ("diagnosis",)),
            llm_provider=llm_gateway.get_main_model,
            tool_retriever=self.tool_registry.retrieve,
            topology=self.topology,
            session_store_instance=local_session_store,
            hint_builder=self._build_diagnosis_hints,
        )
        self.verification_executor = VerificationExecutor(
            # verification may also invoke rollback (route=MUTATION) on its
            # own auto-approval contract — allow both.
            invoke_tool=_mk_invoker("verification_executor", ("verification", "mutation")),
            session_store=local_session_store,
        )
        self.investigator_executor = InvestigatorExecutor(
            invoke_tool=_mk_invoker("investigator_executor", ("diagnosis",)),
            session_store=local_session_store,
        )

        super().__init__(
            planner=self.planner,
            session_store=local_session_store,
            audit_logger=audit_logger,
            executors=[
                self.knowledge_executor,
                self.read_only_executor,
                self.investigator_executor,
                self.diagnosis_executor,
                self.mutation_executor,
                self.verification_executor,
            ],
            approval_policy=self.approval_policy,
            observability_sink=self.observability_sink,
        )
        logger.info(
            "ops_agent_initialized",
            tools=[tool.name for tool in ALL_TOOLS],
            registry_size=len(self.tool_registry.all_specs()),
            topology_nodes=len(self.topology.all_nodes()),
        )

    # ---------- Tool Invocation & Auditing ----------

    async def _invoke_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
        event_callback: EventCallback | None = None,
        *,
        user_id: str = "",
        session_id: str = "",
        route: RouteKey | None = None,
        step: PlanStep | None = None,
        approval_receipt: ApprovalReceipt | None = None,
        execution_target: str = "",
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
        spec = self.tool_registry.get_spec(tool_name)
        if spec and spec.side_effect:
            approval_decision = self._evaluate_side_effect_tool_call(
                tool_name=tool_name,
                route=route,
                step=step,
                context=self._approval_context(
                    state_step=step,
                    approval_receipt=approval_receipt,
                ),
            )
            if not approval_decision.approved:
                output = json.dumps({"error": approval_decision.reason}, ensure_ascii=False)
                event.status = ToolCallStatus.FAILED
                event.error = approval_decision.reason
                event.result = self._truncate_text(output, 500)
                event.duration_ms = int((time.time() - started_at) * 1000)
                self._audit_tool_invocation(
                    user_id=user_id,
                    session_id=session_id,
                    route=route,
                    step=step,
                    tool_name=tool_name,
                    args=args,
                    event=event,
                )
                if event_callback:
                    await event_callback("tool_result", {"tool": tool_name, "output": event.result})
                if session_id and route:
                    self._append_execution_artifact(
                        session_id,
                        route,
                        tool_name,
                        output,
                        step_id=step.step_id if step else "",
                        execution_target=execution_target,
                        approval_receipt_id=approval_receipt.receipt_id if approval_receipt else "",
                    )
                return event, output

        try:
            handler = self.tool_registry.get_handler(tool_name)
            if spec is None or handler is None:
                raise KeyError(f"tool not registered: {tool_name}")
            if not hasattr(handler, "ainvoke"):
                raise TypeError(f"tool handler for {tool_name} does not implement ainvoke")
            route_value = getattr(route, "value", route) if route is not None else ""
            ctx = InvocationContext(
                tool_name=tool_name,
                spec=spec,
                arguments=args,
                session_id=session_id,
                user_id=user_id,
                route=str(route_value or ""),
                idempotency_key=approval_receipt.receipt_id if approval_receipt else "",
            )

            async def _terminal() -> Any:
                return await handler.ainvoke(args)

            output = await run_chain(getattr(self, "middlewares", []), ctx, _terminal)
            if tool_name == "get_secret":
                output = json.dumps(
                    self._sanitize_secret_payload(self._load_json(str(output))),
                    ensure_ascii=False,
                )
            event.status = ToolCallStatus.SUCCESS
            event.result = self._truncate_text(str(output), 500)
        except Exception as exc:
            output = f'{{"error": "{str(exc)}"}}'
            event.status = ToolCallStatus.FAILED
            event.error = str(exc)
            event.result = self._truncate_text(output, 500)
        event.duration_ms = int((time.time() - started_at) * 1000)
        self._audit_tool_invocation(
            user_id=user_id,
            session_id=session_id,
            route=route,
            step=step,
            tool_name=tool_name,
            args=args,
            event=event,
        )

        if event_callback:
            await event_callback("tool_result", {"tool": tool_name, "output": self._truncate_text(str(output), 500)})

        if session_id and route:
            self._append_execution_artifact(
                session_id,
                route,
                tool_name,
                output,
                step_id=step.step_id if step else "",
                execution_target=execution_target,
                approval_receipt_id=approval_receipt.receipt_id if approval_receipt else "",
            )

        return event, str(output)

    def _audit_tool_invocation(
        self,
        *,
        user_id: str,
        session_id: str,
        route: RouteKey | None,
        step: PlanStep | None,
        tool_name: str,
        args: dict[str, Any],
        event: ToolCallEvent,
    ) -> None:
        if not hasattr(self, "audit_logger") or self.audit_logger is None:
            return
        self.audit_logger.log(
            user_id=user_id,
            session_id=session_id,
            intent=step.intent if step else None,
            route=route,
            risk_level=step.risk_level if step else None,
            needs_approval=step.requires_approval if step else False,
            tool_name=tool_name,
            action=tool_name,
            params=args,
            result_summary=event.error or str(event.result or ""),
            success=event.status == ToolCallStatus.SUCCESS,
            duration_ms=event.duration_ms or 0,
        )

    def _approval_context(
        self,
        *,
        state_step: PlanStep | None,
        approval_receipt: ApprovalReceipt | None,
    ) -> dict[str, Any]:
        if approval_receipt is None:
            return {}
        if state_step is not None and approval_receipt.step_id != state_step.step_id:
            return {}
        return {"approval_receipt": approval_receipt.model_dump()}

    def _evaluate_side_effect_tool_call(
        self,
        *,
        tool_name: str,
        route: RouteKey | None,
        step: PlanStep | None,
        context: dict[str, Any],
    ) -> ApprovalDecision:
        return self.approval_policy.evaluate(
            tool_name=tool_name,
            route=route,
            step=step,
            context=context,
        )

    def _append_execution_artifact(
        self,
        session_id: str,
        route: RouteKey,
        tool_name: str,
        output: str,
        *,
        step_id: str = "",
        execution_target: str = "",
        approval_receipt_id: str = "",
    ) -> None:
        payload = self._load_json(output)
        payload = self._sanitize_artifact_payload(tool_name, payload)
        payload.update(
            {
                "step_id": step_id,
                "execution_target": execution_target,
                "approval_receipt_id": approval_receipt_id,
            }
        )
        self.session_store.append_artifact(
            session_id,
            route=route,
            tool_name=tool_name,
            summary=self._summarize_tool_output(tool_name, payload),
            step_id=step_id,
            execution_target=execution_target,
            approval_receipt_id=approval_receipt_id,
            payload=payload,
        )

    def _sanitize_artifact_payload(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if tool_name != "get_secret":
            return payload
        return self._sanitize_secret_payload(payload, redact_all_entries=True)

    def _sanitize_secret_payload(
        self,
        payload: dict[str, Any],
        *,
        redact_all_entries: bool = False,
    ) -> dict[str, Any]:
        sanitized = dict(payload)
        secrets = []
        for secret in sanitized.get("secrets") or []:
            if not isinstance(secret, dict):
                secrets.append(secret)
                continue
            item = dict(secret)
            item.pop("data", None)
            matched_entries = item.get("matched_entries")
            if isinstance(matched_entries, dict):
                item["matched_entries"] = {
                    str(key): (
                        "***REDACTED***"
                        if redact_all_entries
                        else self._redact_secret_value(str(key), str(value))
                    )
                    for key, value in matched_entries.items()
                }
            secrets.append(item)
        sanitized["secrets"] = secrets
        return sanitized

    @staticmethod
    def _redact_secret_value(key: str, value: str) -> str:
        lowered = key.lower()
        if any(token in lowered for token in SECRET_VALUE_TOKENS):
            return "***REDACTED***"
        return re.sub(r"://([^:/@\s]+):([^@\s]+)@", r"://\1:***@", value)

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

    _load_json = staticmethod(load_json)
    _truncate_text = staticmethod(truncate_text)

    def _build_diagnosis_hints(self, state: dict[str, Any], goal: str) -> dict[str, Any]:
        session_id = state.get("session_id", "")
        context = state.get("context", {})
        return {
            "service": extract_service_name(goal, context, self.session_store, session_id),
            "namespace": extract_namespace(goal, context, self.session_store, session_id),
            "pod_name": extract_pod_name(goal, context, "", self.session_store, session_id) or None,
        }


class OpsAgentStreaming(OpsAgent):
    """Compatibility streaming wrapper for the gateway SSE endpoint."""

    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[dict[str, Any]]:
        yield {"event": "start", "data": {"session_id": request.session_id}}
        response = await self.chat(request)
        for tool_call in response.tool_calls:
            yield {
                "event": "tool_call",
                "data": {
                    "tool": tool_call.tool_name,
                    "status": tool_call.status,
                    "result": tool_call.result,
                    "error": tool_call.error,
                },
            }
        yield {
            "event": "final",
            "data": response.model_dump(),
        }
