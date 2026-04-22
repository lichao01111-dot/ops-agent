"""
InvestigatorExecutor — incident triage stage that runs BEFORE hypothesis generation.

Role in the two-role model
--------------------------
Current system: one DiagnosisExecutor does everything (symptom → hypothesis → evidence → score).
Target architecture (architecture-v2 §10 "有限多 Agent"):

    User request
        │
        ▼
    [InvestigatorExecutor]  ← THIS FILE
        │  Collects ALL available facts in parallel:
        │    • K8s status + events
        │    • Recent logs (ERROR level)
        │    • Recent build (was there a deploy in the last hour?)
        │    • Historical incident from session memory
        │    • Topology neighbours
        │  Writes enriched context to OBSERVATIONS memory layer.
        │  Returns a triage summary and recommended next action.
        ▼
    [DiagnosisExecutor]     ← existing, unchanged
        │  Reads enriched OBSERVATIONS from memory.
        │  Generates hypotheses with full incident context already loaded.
        ▼
    [MutationExecutor + VerificationExecutor]  ← existing

When to route here vs DiagnosisExecutor
-----------------------------------------
Route to INVESTIGATION when:
  - The user request is ambiguous ("看看 payment-service 的情况")
  - Context signals an active incident (ctx.incident_active = True)
  - The diagnosis is being re-run (prior_root_cause in memory) → avoid repetition

The planner can chain INVESTIGATION → DIAGNOSIS → MUTATION automatically
by emitting a multi-step plan.  Currently the router does not yet emit
"investigation" — this is a planned upgrade for phase 2 of route heuristics降权.
For now InvestigatorExecutor is wired and ready; the route can be activated
by setting ChatRequest.context["force_investigate"] = True.

Why NOT a full Supervisor pattern?
-----------------------------------
A generic meta-agent that spawns arbitrary sub-agents is powerful but hard to
reason about, audit, or constrain.  The two-role pattern (investigator +
executor/verifier) covers 90% of real on-call scenarios:
  - Simple read queries: skip investigator, go straight to READ_ONLY_OPS.
  - Ambiguous/incident queries: investigator first, then diagnosis + mutation.
  - Known mutations: skip investigator entirely.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Awaitable

import structlog
from langchain_core.messages import HumanMessage

from agent_kernel.executor import ExecutorBase
from agent_kernel.schemas import ToolCallEvent
from agent_kernel.session import SessionStore
from agent_ops.extractors import extract_namespace, extract_service_name
from agent_ops.schemas import AgentIdentity, AgentRoute, MemoryLayer

logger = structlog.get_logger()

EventCallback = Callable[[str, dict[str, Any]], Awaitable[None]]


class InvestigatorExecutor(ExecutorBase):
    """Stage-0 incident triage: collect ALL facts in parallel before hypothesis generation.

    Reads: user message, context
    Writes: OBSERVATIONS memory layer (enriched facts for DiagnosisExecutor)
    Returns: triage summary + recommended_action hint

    The triage summary is the final_message for this step.  When a
    verification / DIAGNOSIS step follows in the plan, it reads the OBSERVATIONS
    layer from the session store and skips re-collecting facts.
    """

    def __init__(
        self,
        invoke_tool: Callable[..., Awaitable[tuple[ToolCallEvent, str]]],
        session_store: SessionStore,
    ):
        super().__init__(node_name="investigation", route_name="investigation")
        self.invoke_tool = invoke_tool
        self.session_store = session_store

    async def execute(
        self,
        state: dict[str, Any],
        event_callback: EventCallback | None = None,
    ) -> dict[str, Any]:
        message = self._latest_user_message(state["messages"])
        context = state.get("context", {})
        session_id = state["session_id"]

        namespace = extract_namespace(message, context, self.session_store, session_id)
        service = extract_service_name(message, context, self.session_store, session_id)

        if not service:
            return {
                "final_message": (
                    "调查阶段无法识别服务名，请在消息中明确指定，"
                    "例如「调查 payment-service 的问题」。"
                ),
                "tool_calls": [],
                "sources": [],
            }

        # ---- Parallel fact collection ----
        tasks = self._build_collection_tasks(
            service=service,
            namespace=namespace,
            state=state,
            event_callback=event_callback,
        )
        gathered = await asyncio.gather(*tasks, return_exceptions=True)

        all_events: list[ToolCallEvent] = []
        facts: dict[str, Any] = {"service": service, "namespace": namespace}
        for label, result in gathered:
            if isinstance(result, Exception):
                logger.warning("investigator_tool_failed", label=label, error=str(result))
                continue
            event, payload = result
            all_events.append(event)
            facts[label] = payload

        # ---- Write enriched facts to OBSERVATIONS layer ----
        self._persist_observations(session_id, service, namespace, facts)

        # ---- Build triage summary ----
        summary = self._build_triage_summary(service, namespace, facts)

        logger.info(
            "investigation_complete",
            session_id=session_id,
            service=service,
            namespace=namespace,
            facts_collected=list(facts.keys()),
        )
        return {
            "final_message": summary,
            "tool_calls": all_events,
            "sources": [],
        }

    # ------------------------------------------------------------------
    # Task builders
    # ------------------------------------------------------------------

    def _build_collection_tasks(
        self,
        *,
        service: str,
        namespace: str,
        state: dict[str, Any],
        event_callback: EventCallback | None,
    ) -> list[Any]:
        """Return coroutine-wrapped tool calls for parallel execution."""
        user_id = state.get("user_id", "")
        session_id = state["session_id"]

        async def call(label: str, tool_name: str, args: dict) -> tuple[str, Any]:
            try:
                event, output = await self.invoke_tool(
                    tool_name, args, event_callback,
                    user_id=user_id, session_id=session_id,
                    route=AgentRoute.DIAGNOSIS,
                )
                return label, (event, self._safe_json(output))
            except Exception as exc:
                return label, exc

        return [
            call("pod_status", "get_pod_status", {
                "namespace": namespace, "name_filter": service, "show_all": False,
            }),
            call("deployment_status", "get_deployment_status", {
                "namespace": namespace, "name": service,
            }),
            call("k8s_events", "get_k8s_events", {
                "namespace": namespace, "name": service,
                "resource_type": "Deployment", "limit": 20,
            }),
            call("error_logs", "search_logs", {
                "service": service, "time_range_minutes": 60,
                "level": "ERROR", "limit": 30,
            }),
            call("recent_build", "query_jenkins_build", {
                "job_name": service, "build_number": None,
            }),
        ]

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist_observations(
        self,
        session_id: str,
        service: str,
        namespace: str,
        facts: dict[str, Any],
    ) -> None:
        """Write key observations into session memory OBSERVATIONS layer."""
        def _write(key: str, value: Any, confidence: float = 0.9) -> None:
            try:
                self.session_store.write_memory_item(
                    session_id,
                    writer=AgentIdentity.READ_OPS,
                    layer=MemoryLayer.OBSERVATIONS,
                    key=key,
                    value=value,
                    source="investigator_executor",
                    confidence=confidence,
                )
            except Exception as exc:
                logger.warning("investigator_persist_failed", key=key, error=str(exc))

        _write("service", service)
        _write("namespace", namespace)

        pod_data = facts.get("pod_status", {})
        pods = pod_data.get("pods", [])
        if pods:
            _write("pod_name", pods[0].get("name", ""))
            _write("last_pod_status", pods[0].get("phase", ""))

        events_data = facts.get("k8s_events", {})
        k8s_events = events_data.get("events", [])
        warning_events = [e for e in k8s_events if e.get("type") == "Warning"]
        if warning_events:
            _write("k8s_warning_events", warning_events[:5], confidence=0.95)

        logs_data = facts.get("error_logs", {})
        if logs_data.get("count", 0) > 0:
            _write("error_log_count", logs_data["count"])
            first_msg = (logs_data.get("logs") or [{}])[0].get("message", "")
            if first_msg:
                _write("last_error_message", first_msg[:200])

        build_data = facts.get("recent_build", {})
        if build_data and not build_data.get("error"):
            _write("last_build_result", build_data.get("result"))
            _write("last_build_number", build_data.get("build_number"))

    # ------------------------------------------------------------------
    # Triage summary
    # ------------------------------------------------------------------

    def _build_triage_summary(
        self, service: str, namespace: str, facts: dict[str, Any]
    ) -> str:
        lines = [f"📋 调查报告: {service} ({namespace})"]

        # Pod status
        pods = facts.get("pod_status", {}).get("pods", [])
        if pods:
            not_running = [p for p in pods if p.get("phase") not in ("Running", "Succeeded")]
            lines.append(
                f"Pod 状态: {len(pods)} 个，"
                + (f"⚠️ {len(not_running)} 个异常" if not_running else "✅ 全部正常")
            )

        # K8s Warning events
        k8s_events = facts.get("k8s_events", {}).get("events", [])
        warnings = [e for e in k8s_events if e.get("type") == "Warning"]
        if warnings:
            last_warn = warnings[-1]
            lines.append(
                f"K8s 告警: {len(warnings)} 条，最近: "
                f"{last_warn.get('reason')} — {(last_warn.get('message') or '')[:80]}"
            )
        else:
            lines.append("K8s 告警: 无")

        # Error logs
        log_data = facts.get("error_logs", {})
        if log_data.get("count", 0):
            first_err = (log_data.get("logs") or [{}])[0].get("message", "")
            lines.append(
                f"错误日志: 最近1小时 {log_data['count']} 条，"
                f"样例: {first_err[:80] if first_err else '(无内容)'}"
            )
        else:
            lines.append("错误日志: 最近1小时无 ERROR")

        # Recent build
        build = facts.get("recent_build", {})
        if build and not build.get("error"):
            lines.append(
                f"最近构建: #{build.get('build_number')} "
                f"result={build.get('result')} "
                f"(可能触发了当前问题)"
            )

        # Recommended action
        has_pod_issue = any(p.get("phase") not in ("Running", "Succeeded") for p in pods)
        has_log_errors = log_data.get("count", 0) > 10
        has_warnings = bool(warnings)
        if has_pod_issue or has_log_errors or has_warnings:
            lines.append("\n→ 建议进入诊断阶段（hypothesis generation）。")
        else:
            lines.append("\n→ 初步无明显异常，可进一步明确问题范围。")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _latest_user_message(self, messages: list[Any]) -> str:
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage) and isinstance(msg.content, str):
                return msg.content
        return ""

    @staticmethod
    def _safe_json(output: str | dict) -> dict[str, Any]:
        if isinstance(output, dict):
            return output
        import json
        try:
            data = json.loads(output)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
