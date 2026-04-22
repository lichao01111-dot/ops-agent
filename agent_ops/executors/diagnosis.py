"""
Multi-hypothesis diagnosis executor.

Flow:
1. Symptom collection  — deterministic read tools that any diagnosis needs
2. Hypothesis generation — LLM + topology + symptoms -> up to N hypotheses
3. Parallel evidence  — asyncio.gather runs each hypothesis's evidence tools
4. Scoring / synthesis — rank hypotheses, pick top, build final answer
5. Memory write       — one record per hypothesis + top_hypothesis_id + summary

LLM calls are wrapped in try/except so the executor degrades gracefully: if
hypothesis generation or scoring fails, we fall back to the single-chain
bounded ReAct behaviour preserved in :meth:`_fallback_bounded_loop`.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Awaitable, Callable, Optional

import structlog
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from agent_kernel.executor import ExecutorBase
from agent_kernel.schemas import ToolCallEvent
from agent_kernel.session import SessionStore, create_session_store
from agent_ops.extractors import extract_namespace, extract_pod_name, extract_service_name
from agent_ops.memory_schema import OPS_MEMORY_SCHEMA
from agent_ops.schemas import AgentIdentity, AgentRoute, Hypothesis, HypothesisVerdict, MemoryLayer
from agent_ops.topology import ServiceTopology, get_topology

logger = structlog.get_logger()


MAX_HYPOTHESES = 4
EVIDENCE_TOOLS_PER_HYPOTHESIS = 2


EventCallback = Callable[[str, dict[str, Any]], Awaitable[None]]


class HypothesisDraft(BaseModel):
    statement: str = Field(..., description="A concise hypothesis sentence")
    suspected_target: str = Field("", description="Service / pod / component suspected")
    evidence_tools: list[str] = Field(
        default_factory=list,
        description="Names of tools (from the provided candidates) that would confirm or reject this hypothesis",
    )


class HypothesisDraftList(BaseModel):
    hypotheses: list[HypothesisDraft] = Field(default_factory=list)


class DiagnosisExecutor(ExecutorBase):
    """Produces multi-hypothesis RCA by running evidence collection in parallel."""

    def __init__(
        self,
        *,
        invoke_tool: Callable[..., Awaitable[tuple[ToolCallEvent, str]]],
        llm_provider: Callable[[], Any],
        tool_retriever: Callable[..., list[Any]],
        topology: ServiceTopology | None = None,
        session_store_instance: SessionStore | None = None,
        hint_builder: Callable[[dict[str, Any], str], dict[str, Any]] | None = None,
    ):
        """
        Args:
            invoke_tool: ``await invoke_tool(name, args, callback, session_id=..., route=...)``
                returning (ToolCallEvent, output_str). Reuses the agent's existing helper.
            llm_provider: zero-arg callable returning a BaseChatModel (usually ``llm_gateway.get_main_model``).
            tool_retriever: ``tool_retriever(goal, route, top_k) -> list[ToolSpec]``.
            topology: optional ServiceTopology; defaults to global.
        """
        super().__init__(node_name="diagnosis", route_name=AgentRoute.DIAGNOSIS.value)
        self._invoke_tool = invoke_tool
        self._llm_provider = llm_provider
        self._tool_retriever = tool_retriever
        self._topology = topology or get_topology()
        self._session_store = session_store_instance or create_session_store(memory_schema=OPS_MEMORY_SCHEMA)
        self._hint_builder = hint_builder

    async def execute(
        self,
        state: dict[str, Any],
        event_callback: EventCallback | None = None,
    ) -> dict[str, Any]:
        plan = state.get("plan")
        step = plan.current_step() if plan else None
        goal = step.goal if step else self._latest_user_message(state.get("messages", []))
        hints = self._build_symptom_hints(state, goal)
        return await self.run(
            state=state,
            goal=goal,
            event_callback=event_callback,
            symptom_hints=hints,
        )

    async def run(
        self,
        *,
        state: dict[str, Any],
        goal: str,
        event_callback: EventCallback | None = None,
        symptom_hints: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        session_id = state["session_id"]
        symptom_hints = symptom_hints or {}

        symptom_events, symptom_payloads = await self._collect_symptoms(
            state=state, goal=goal, hints=symptom_hints, event_callback=event_callback
        )

        candidate_specs = self._tool_retriever(
            goal=goal, route=AgentRoute.DIAGNOSIS, top_k=8
        )
        candidate_names = [spec.name for spec in candidate_specs]

        hypotheses = await self._generate_hypotheses(
            goal=goal,
            symptoms=symptom_payloads,
            candidate_tools=candidate_names,
            topology_hint=self._topology_hint(symptom_hints, symptom_payloads),
        )

        if not hypotheses:
            logger.info("diagnosis_no_hypotheses", session=session_id)
            return await self._fallback_single_chain(
                state=state,
                goal=goal,
                symptom_events=symptom_events,
                event_callback=event_callback,
            )

        evidence_events, enriched = await self._collect_evidence_parallel(
            state=state,
            hypotheses=hypotheses,
            event_callback=event_callback,
            symptoms=symptom_payloads,
        )

        top, final_message = self._score_and_synthesize(goal, enriched, symptom_payloads)
        self._write_memory(session_id=session_id, hypotheses=enriched, top=top, summary=final_message)

        return {
            "final_message": final_message,
            "tool_calls": symptom_events + evidence_events,
            "sources": [],
            "hypotheses": [h.model_dump() for h in enriched],
        }

    # ---------- Stage 1: Incident-native symptom collection ----------

    async def _collect_symptoms(
        self,
        *,
        state: dict[str, Any],
        goal: str,
        hints: dict[str, Any],
        event_callback: EventCallback | None,
    ) -> tuple[list[ToolCallEvent], dict[str, Any]]:
        """Aggregate incident context BEFORE hypothesis generation.

        Collection order (fact-layer first, inference-layer second):
          1. Pod / Deployment status (current state)
          2. K8s Events for the deployment (what changed recently)
          3. Recent error logs (what's been failing)
          4. Recent Jenkins build (did a deploy just happen?)
          5. Historical incidents from session memory (recurring?)
        """
        events: list[ToolCallEvent] = []
        payloads: dict[str, Any] = {}

        pod_name = hints.get("pod_name")
        namespace = hints.get("namespace") or "default"
        service = hints.get("service")

        # ---- 1. Pod / Deployment status ----
        if pod_name:
            event, output = await self._invoke_tool(
                "diagnose_pod",
                {"namespace": namespace, "pod_name": pod_name},
                event_callback,
                user_id=state["user_id"],
                session_id=state["session_id"],
                route=AgentRoute.DIAGNOSIS,
            )
            events.append(event)
            payloads["diagnose_pod"] = self._safe_json(output)
        elif service or hints.get("name_filter"):
            event, output = await self._invoke_tool(
                "get_pod_status",
                {"namespace": namespace, "name_filter": service or hints.get("name_filter") or "", "show_all": False},
                event_callback,
                user_id=state["user_id"],
                session_id=state["session_id"],
                route=AgentRoute.DIAGNOSIS,
            )
            events.append(event)
            payloads["get_pod_status"] = self._safe_json(output)

        # ---- 2. K8s Events — "what changed" signal ----
        if service:
            event, output = await self._invoke_tool(
                "get_k8s_events",
                {"namespace": namespace, "name": service, "resource_type": "Deployment", "limit": 15},
                event_callback,
                user_id=state["user_id"],
                session_id=state["session_id"],
                route=AgentRoute.DIAGNOSIS,
            )
            events.append(event)
            payloads["k8s_events"] = self._safe_json(output)

        # ---- 3. Recent error logs ----
        if service:
            event, output = await self._invoke_tool(
                "search_logs",
                {"service": service, "time_range_minutes": 30, "level": "ERROR", "limit": 20},
                event_callback,
                user_id=state["user_id"],
                session_id=state["session_id"],
                route=AgentRoute.DIAGNOSIS,
            )
            events.append(event)
            payloads["search_logs"] = self._safe_json(output)

        # ---- 4. Recent Jenkins build — "was there a recent deploy?" ----
        if service:
            try:
                event, output = await self._invoke_tool(
                    "query_jenkins_build",
                    {"job_name": service, "build_number": None},
                    event_callback,
                    user_id=state["user_id"],
                    session_id=state["session_id"],
                    route=AgentRoute.DIAGNOSIS,
                )
                events.append(event)
                bld = self._safe_json(output)
                if not bld.get("error"):
                    payloads["recent_build"] = bld
            except Exception:
                pass  # Jenkins may not be configured — silently skip

        # ---- 5. Historical incidents from session memory ----
        prior = self._load_prior_incident_context(state["session_id"])
        if prior:
            payloads["prior_incident"] = prior

        return events, payloads

    def _load_prior_incident_context(self, session_id: str) -> dict[str, Any] | None:
        """Read the most recent diagnosis summary from HYPOTHESES memory layer.

        This gives the LLM context about recurring patterns — if the same
        service has crashed twice in the same session, that's a strong signal.
        """
        try:
            summary = self._session_store.resolve_memory_value(
                session_id,
                "diagnosis_summary",
                ["hypotheses"],
            )
            cause = self._session_store.resolve_memory_value(
                session_id,
                "likely_root_cause",
                ["hypotheses"],
            )
            if summary or cause:
                return {
                    "prior_diagnosis_summary": summary or "",
                    "prior_root_cause": cause or "",
                }
        except Exception:
            pass
        return None

    # ---------- Stage 2: Hypothesis generation ----------

    async def _generate_hypotheses(
        self,
        *,
        goal: str,
        symptoms: dict[str, Any],
        candidate_tools: list[str],
        topology_hint: str,
    ) -> list[Hypothesis]:
        try:
            llm = self._llm_provider()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("diagnosis_llm_unavailable", error=str(exc))
            return []

        # Build structured context sections for the hypothesis LLM
        k8s_ev = symptoms.get("k8s_events", {})
        recent_build = symptoms.get("recent_build", {})
        prior = symptoms.get("prior_incident", {})

        context_sections = []
        if k8s_ev.get("events"):
            ev_text = "; ".join(
                f"{e.get('reason')}({e.get('type')}): {(e.get('message') or '')[:60]}"
                for e in k8s_ev["events"][-5:]
            )
            context_sections.append(f"K8s Events(最近5条): {ev_text}")
        if recent_build and not recent_build.get("error"):
            context_sections.append(
                f"最近构建: job={recent_build.get('job_name')} "
                f"#{recent_build.get('build_number')} result={recent_build.get('result')}"
            )
        if prior:
            context_sections.append(
                f"历史 incident: {prior.get('prior_root_cause') or prior.get('prior_diagnosis_summary')}"
            )
        incident_ctx = "\n".join(context_sections) if context_sections else "无"

        prompt = (
            "你是 OpsAgent 的诊断假设生成器。\n"
            "基于用户诉求、事实层信息（K8s状态/Events/最近构建/历史incident）和拓扑提示，"
            f"产出最多 {MAX_HYPOTHESES} 条互不重复的诊断假设。\n"
            "要求：\n"
            "- 每条 hypothesis 包含 statement、suspected_target、evidence_tools\n"
            "- evidence_tools 只能从以下候选工具中选：" + ", ".join(candidate_tools) + "\n"
            "- evidence_tools 每条最多 " + str(EVIDENCE_TOOLS_PER_HYPOTHESIS) + " 个\n"
            "- 优先引用已采集到的异常事件作为假设来源，避免无依据猜测\n"
            "- 不要解释，直接输出结构化结果\n\n"
            f"用户诉求：{goal}\n"
            f"事实层上下文（incident context）：\n{incident_ctx}\n"
            f"详细症状数据：{self._truncate(json.dumps(symptoms, ensure_ascii=False), 1200)}\n"
            f"拓扑提示：{topology_hint or '无'}"
        )

        try:
            structured = llm.with_structured_output(HypothesisDraftList)
            draft_list = await structured.ainvoke(prompt)
        except Exception as exc:
            logger.warning("hypothesis_generation_failed", error=str(exc))
            return []

        if not isinstance(draft_list, HypothesisDraftList):
            return []

        hypotheses: list[Hypothesis] = []
        for draft in draft_list.hypotheses[:MAX_HYPOTHESES]:
            if not draft.statement:
                continue
            evidence_tools = [t for t in draft.evidence_tools if t in candidate_tools][:EVIDENCE_TOOLS_PER_HYPOTHESIS]
            hypotheses.append(
                Hypothesis(
                    hypothesis_id=f"h-{uuid.uuid4().hex[:8]}",
                    statement=draft.statement.strip(),
                    suspected_target=draft.suspected_target.strip(),
                    evidence_tools=evidence_tools,
                )
            )
        return hypotheses

    def _topology_hint(self, hints: dict[str, Any], symptoms: dict[str, Any]) -> str:
        target = hints.get("service") or hints.get("pod_name") or ""
        if not target and symptoms.get("get_pod_status", {}).get("pods"):
            first = symptoms["get_pod_status"]["pods"][0]
            target = first.get("name", "")
        if not target:
            return ""
        desc = self._topology.describe(target)
        if not desc:
            neighbors = self._topology.neighbors(target, depth=1)
            if neighbors:
                return "neighbors: " + ", ".join(n.name for n in neighbors[:5])
            return ""
        return desc

    # ---------- Stage 3: Parallel evidence ----------

    async def _collect_evidence_parallel(
        self,
        *,
        state: dict[str, Any],
        hypotheses: list[Hypothesis],
        event_callback: EventCallback | None,
        symptoms: dict[str, Any],
    ) -> tuple[list[ToolCallEvent], list[Hypothesis]]:
        tasks = [
            self._collect_for_hypothesis(
                state=state,
                hypothesis=h,
                event_callback=event_callback,
                symptoms=symptoms,
            )
            for h in hypotheses
        ]
        gathered = await asyncio.gather(*tasks, return_exceptions=False)
        events: list[ToolCallEvent] = []
        enriched: list[Hypothesis] = []
        for hypothesis, (h_events, h_payloads) in zip(hypotheses, gathered):
            hypothesis.evidence_summary = self._summarize_evidence(h_payloads)
            enriched.append(hypothesis)
            events.extend(h_events)
        return events, enriched

    async def _collect_for_hypothesis(
        self,
        *,
        state: dict[str, Any],
        hypothesis: Hypothesis,
        event_callback: EventCallback | None,
        symptoms: dict[str, Any],
    ) -> tuple[list[ToolCallEvent], dict[str, Any]]:
        events: list[ToolCallEvent] = []
        payloads: dict[str, Any] = {}
        for tool_name in hypothesis.evidence_tools:
            args = self._default_args_for_tool(tool_name, hypothesis, symptoms, state)
            if args is None:
                continue
            try:
                event, output = await self._invoke_tool(
                    tool_name,
                    args,
                    event_callback,
                    user_id=state["user_id"],
                    session_id=state["session_id"],
                    route=AgentRoute.DIAGNOSIS,
                )
                events.append(event)
                payloads[tool_name] = self._safe_json(output)
            except Exception as exc:
                logger.warning(
                    "evidence_tool_failed",
                    hypothesis=hypothesis.hypothesis_id,
                    tool=tool_name,
                    error=str(exc),
                )
        return events, payloads

    def _default_args_for_tool(
        self,
        tool_name: str,
        hypothesis: Hypothesis,
        symptoms: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Best-effort parameter synthesis for evidence tools.

        The planner would normally extract these from the user message. In the
        evidence phase we derive them from already-known symptoms and the
        hypothesis's suspected target.
        """
        target = hypothesis.suspected_target
        namespace = (
            symptoms.get("diagnose_pod", {}).get("namespace")
            or symptoms.get("get_pod_status", {}).get("namespace")
            or "default"
        )
        service = target or symptoms.get("search_logs", {}).get("service") or ""

        if tool_name == "diagnose_pod":
            pod_name = target or self._first_pod_name(symptoms)
            if not pod_name:
                return None
            return {"namespace": namespace, "pod_name": pod_name}
        if tool_name == "get_pod_status":
            return {"namespace": namespace, "name_filter": service, "show_all": False}
        if tool_name == "get_deployment_status":
            return {"namespace": namespace, "name": service}
        if tool_name == "get_service_info":
            return {"namespace": namespace, "name": service}
        if tool_name == "get_pod_logs":
            pod_name = target or self._first_pod_name(symptoms)
            if not pod_name:
                return None
            return {"namespace": namespace, "pod_name": pod_name, "tail_lines": 80}
        if tool_name == "search_logs":
            if not service:
                return None
            return {"service": service, "time_range_minutes": 30, "level": "ERROR", "limit": 20}
        if tool_name == "get_error_statistics":
            if not service:
                return None
            return {"service": service, "time_range_minutes": 60}
        if tool_name == "query_jenkins_build":
            if not target:
                return None
            return {"job_name": target}
        if tool_name == "query_knowledge":
            return {"question": hypothesis.statement, "top_k": 3}
        return None

    def _first_pod_name(self, symptoms: dict[str, Any]) -> str:
        pods = symptoms.get("get_pod_status", {}).get("pods") or []
        if pods and isinstance(pods, list):
            return pods[0].get("name", "")
        diag = symptoms.get("diagnose_pod", {})
        return diag.get("pod_name", "")

    # ---------- Stage 4: Scoring ----------

    def _score_and_synthesize(
        self,
        goal: str,
        hypotheses: list[Hypothesis],
        symptoms: dict[str, Any],
    ) -> tuple[Hypothesis | None, str]:
        if not hypotheses:
            return None, "诊断未产生明确假设，建议补充更多环境 / 服务上下文后重试。"

        for hypothesis in hypotheses:
            hypothesis.score = self._heuristic_score(hypothesis, symptoms)
            if hypothesis.score >= 2.0:
                hypothesis.verdict = HypothesisVerdict.SUPPORTED
            elif hypothesis.score <= 0.5:
                hypothesis.verdict = HypothesisVerdict.REJECTED
            else:
                hypothesis.verdict = HypothesisVerdict.INCONCLUSIVE

        ranked = sorted(hypotheses, key=lambda h: h.score, reverse=True)
        top = ranked[0]

        lines = [
            f"诊断结论（基于 {len(ranked)} 条候选假设并行取证）：",
            f"Top 假设：{top.statement}",
            f"疑点对象：{top.suspected_target or '未明确'}",
            f"证据摘要：{top.evidence_summary or '无额外证据'}",
            "",
            "其他候选：",
        ]
        for other in ranked[1:]:
            lines.append(
                f"- [{other.verdict.value} score={other.score:.1f}] {other.statement}"
            )
        return top, "\n".join(lines)

    def _heuristic_score(self, hypothesis: Hypothesis, symptoms: dict[str, Any]) -> float:
        score = 0.5  # prior
        summary = (hypothesis.evidence_summary or "").lower()
        if any(signal in summary for signal in ("error", "failed", "oom", "crashloop", "imagepullbackoff", "exception")):
            score += 1.8
        if hypothesis.suspected_target and hypothesis.suspected_target in summary:
            score += 0.5
        logs = symptoms.get("search_logs", {}).get("logs") or []
        if logs:
            score += 0.3
        return round(score, 2)

    def _summarize_evidence(self, payloads: dict[str, Any]) -> str:
        if not payloads:
            return ""
        fragments: list[str] = []
        for tool_name, payload in payloads.items():
            if not isinstance(payload, dict):
                continue
            if payload.get("error"):
                fragments.append(f"{tool_name}: error={payload['error']}")
                continue
            if tool_name == "diagnose_pod":
                issues = payload.get("issues") or []
                if issues:
                    fragments.append(
                        f"diagnose_pod: {issues[0].get('type', 'unknown')} "
                        f"({issues[0].get('message', '')[:80]})"
                    )
            elif tool_name == "get_pod_logs":
                fragments.append(f"get_pod_logs: lines={payload.get('lines', 0)}")
            elif tool_name == "search_logs":
                fragments.append(
                    f"search_logs: count={payload.get('count', 0)} level={payload.get('level')}"
                )
            elif tool_name == "get_error_statistics":
                fragments.append(
                    f"get_error_statistics: total_errors={payload.get('total_errors', 0)}"
                )
            elif tool_name == "query_knowledge":
                fragments.append(f"query_knowledge: results={len(payload.get('results') or [])}")
            else:
                fragments.append(f"{tool_name}: {self._truncate(json.dumps(payload, ensure_ascii=False), 100)}")
        return "; ".join(fragments)

    # ---------- Stage 5: Memory ----------

    def _write_memory(
        self,
        *,
        session_id: str,
        hypotheses: list[Hypothesis],
        top: Hypothesis | None,
        summary: str,
    ) -> None:
        for hypothesis in hypotheses:
            self._session_store.write_memory_item(
                session_id,
                writer=AgentIdentity.DIAGNOSIS,
                layer=MemoryLayer.HYPOTHESES,
                key=f"hypothesis:{hypothesis.hypothesis_id}",
                value=hypothesis.model_dump(),
                source="diagnosis_executor",
                confidence=min(1.0, hypothesis.score / 3.0),
            )
        if top:
            self._session_store.write_memory_item(
                session_id,
                writer=AgentIdentity.DIAGNOSIS,
                layer=MemoryLayer.HYPOTHESES,
                key="top_hypothesis_id",
                value=top.hypothesis_id,
                source="diagnosis_executor",
                confidence=0.9,
            )
            self._session_store.write_memory_item(
                session_id,
                writer=AgentIdentity.DIAGNOSIS,
                layer=MemoryLayer.HYPOTHESES,
                key="likely_root_cause",
                value=top.statement,
                source="diagnosis_executor",
                confidence=min(1.0, top.score / 3.0),
            )
        self._session_store.write_memory_item(
            session_id,
            writer=AgentIdentity.DIAGNOSIS,
            layer=MemoryLayer.HYPOTHESES,
            key="diagnosis_summary",
            value=self._truncate(summary.replace("\n", " "), 320),
            source="diagnosis_executor",
            confidence=0.7,
        )

    # ---------- Fallback ----------

    async def _fallback_single_chain(
        self,
        *,
        state: dict[str, Any],
        goal: str,
        symptom_events: list[ToolCallEvent],
        event_callback: EventCallback | None,
    ) -> dict[str, Any]:
        """If hypothesis generation fails, surface a best-effort summary from
        what we already collected instead of going silent."""
        final_message = (
            "未能生成多条假设，已基于初步症状返回结论。\n"
            "建议：补充服务名 / namespace / 具体 pod 名后重试，或开启 LLM 访问以启用多假设诊断。"
        )
        self._session_store.write_memory_item(
            state["session_id"],
            writer=AgentIdentity.DIAGNOSIS,
            layer=MemoryLayer.HYPOTHESES,
            key="diagnosis_summary",
            value=self._truncate(final_message.replace("\n", " "), 240),
            source="diagnosis_fallback",
            confidence=0.4,
        )
        return {
            "final_message": final_message,
            "tool_calls": symptom_events,
            "sources": [],
            "hypotheses": [],
        }

    # ---------- Utilities ----------

    def _safe_json(self, output: str | dict[str, Any]) -> dict[str, Any]:
        if isinstance(output, dict):
            return output
        try:
            data = json.loads(output)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _build_symptom_hints(self, state: dict[str, Any], goal: str) -> dict[str, Any]:
        if self._hint_builder is not None:
            return self._hint_builder(state, goal)
        session_id = state.get("session_id", "")
        context = state.get("context", {})
        return {
            "service": extract_service_name(goal, context, self._session_store, session_id),
            "namespace": extract_namespace(goal, context, self._session_store, session_id),
            "pod_name": extract_pod_name(goal, context, "", self._session_store, session_id) or None,
        }

    def _latest_user_message(self, messages: list[Any]) -> str:
        for message in reversed(messages):
            if isinstance(message, HumanMessage) and isinstance(message.content, str):
                return message.content
        return ""

    def _truncate(self, text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[:limit] + "...(truncated)"
