import json
from typing import Any, Callable, Awaitable

import structlog
from langchain_core.messages import HumanMessage, SystemMessage

from agent_kernel.executor import ExecutorBase
from agent_kernel.schemas import ApprovalReceipt, PlanStep, ToolCallStatus, UserRole
from agent_kernel.session import SessionStore
from agent_ops.extractors import extract_docs_directory, extract_sources, extract_top_k
from agent_ops.formatters import format_index_result, format_knowledge_result
from agent_ops.memory_hooks import update_memory_from_knowledge, write_execution_memory
from agent_ops.schemas import AgentRoute
from llm_gateway.prompt_registry import prompt_registry

logger = structlog.get_logger()


_KNOWLEDGE_SYS_PROMPT = """你是一名内部系统知识库助手。你的任务是基于"参考资料"回答用户问题。

严格遵守：
1. 只用参考资料中出现的信息作答；不要编造、不要泛化、不要补充常识。
2. 如果参考资料中没有所需信息，直接说「知识库中未找到相关信息」并指出可能缺失的内容，不要瞎猜。
3. 涉及具体数值（地址 / 端口 / 路径 / 人名 / 命令 / URL 等）必须**逐字引用**资料中的原文。
4. 答案用简洁中文，先给结论，再给细节；如果可列点就列点。
5. 末尾用一行「来源：xxx」列出实际用到的文档名（去重）。
"""


def _build_context_block(results: list[dict[str, Any]], max_chars: int = 6000) -> str:
    """Concat full chunk content (not truncated) into a numbered context block."""
    parts: list[str] = []
    used = 0
    for i, r in enumerate(results, start=1):
        content = str(r.get("content", "")).strip()
        source = r.get("source", "unknown")
        block = f"[资料 {i}] 来源={source}\n{content}\n"
        if used + len(block) > max_chars:
            break
        parts.append(block)
        used += len(block)
    return "\n".join(parts)


class KnowledgeExecutor(ExecutorBase):
    def __init__(
        self,
        invoke_tool: Callable[..., Awaitable[tuple[Any, str]]],
        session_store: SessionStore,
        llm_provider: Callable[[], Any] | None = None,
    ):
        super().__init__(node_name="knowledge", route_name="knowledge")
        self.invoke_tool = invoke_tool
        self.session_store = session_store
        # Lazy LLM resolver; allows tests to construct without LLM.
        self._llm_provider = llm_provider

    def _get_latest_user_message(self, messages: list[Any]) -> str:
        for message in reversed(messages):
            if isinstance(message, HumanMessage):
                if isinstance(message.content, str):
                    return message.content
        return ""

    def _get_current_goal(self, state: dict[str, Any]) -> str:
        plan = state.get("plan")
        step = plan.current_step() if plan else None
        return step.goal if step and step.goal else self._get_latest_user_message(state["messages"])

    def _is_index_request(self, message: str) -> bool:
        return any(token in message.lower() for token in ("索引", "同步文档", "导入文档", "index"))

    def _approval_receipt_for_step(
        self,
        state: dict[str, Any],
        step: PlanStep | None,
    ) -> ApprovalReceipt | None:
        if not step or not step.requires_approval:
            return None
        raw = state["context"].get("approval_receipt")
        if isinstance(raw, ApprovalReceipt):
            return raw
        if not isinstance(raw, dict):
            return None
        try:
            return ApprovalReceipt(**raw)
        except Exception:
            return None

    async def execute(self, state: dict[str, Any], event_callback: Callable | None = None) -> dict[str, Any]:
        message = self._get_current_goal(state)
        plan = state.get("plan")
        step = plan.current_step() if plan else None

        if self._is_index_request(message):
            receipt = self._approval_receipt_for_step(state, step)
            if state["user_role"] != UserRole.ADMIN:
                return {
                    "final_message": "索引文档属于管理员操作。请使用 Admin 身份执行，或改为普通知识查询。",
                    "tool_calls": [],
                    "sources": [],
                }
            docs_directory = extract_docs_directory(message, state["context"])
            event, output = await self.invoke_tool(
                "index_documents",
                {"docs_directory": docs_directory},
                event_callback,
                user_id=state["user_id"],
                session_id=state["session_id"],
                route=AgentRoute.MUTATION,
                step=step,
                approval_receipt=receipt,
                execution_target=step.execution_target if step else "executor:mutation",
            )
            final_message = format_index_result(output, docs_directory)
            write_execution_memory(
                self.session_store,
                state["session_id"],
                action="index_documents",
                target=docs_directory,
                status="completed" if event.status == ToolCallStatus.SUCCESS else "failed",
                step_id=step.step_id if step else "",
                approval_receipt_id=receipt.receipt_id if receipt else "",
            )
            return {"final_message": final_message, "tool_calls": [event], "sources": []}

        top_k = extract_top_k(message, state["context"])
        event, output = await self.invoke_tool(
            "query_knowledge",
            {"question": message, "top_k": top_k},
            event_callback,
            user_id=state["user_id"],
            session_id=state["session_id"],
            route=AgentRoute.KNOWLEDGE,
        )
        sources = extract_sources(output)

        # Try LLM-grounded summarisation first; fall back to template if LLM
        # unavailable or fails. The fallback is what we used to ship — it
        # never lies, just looks raw.
        final_message = await self._llm_summarise(message, output) or format_knowledge_result(output)

        update_memory_from_knowledge(self.session_store, state["session_id"], message, output, sources)
        return {"final_message": final_message, "tool_calls": [event], "sources": sources}

    async def _llm_summarise(self, question: str, tool_output: str) -> str | None:
        """RAG step: feed the question + retrieved chunks to the main LLM.

        Returns None on any failure so caller can fall back to template.
        """
        if not self._llm_provider:
            return None
        try:
            payload = json.loads(tool_output) if isinstance(tool_output, str) else tool_output
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            return None
        if payload.get("answer_status") != "found":
            # No results — let formatter produce the canonical "未找到" message.
            return None
        results = payload.get("results") or []
        if not results:
            return None

        context_block = _build_context_block(results)
        user_prompt = (
            f"用户问题:\n{question}\n\n"
            f"参考资料 (按相关度排序):\n{context_block}\n\n"
            "请基于以上参考资料回答用户问题。记住：只引用资料中的原文，不要编造。"
        )

        try:
            llm = self._llm_provider()
            system_prompt = prompt_registry.get_prompt("ops/knowledge/grounded_answer", _KNOWLEDGE_SYS_PROMPT)
            resp = await llm.ainvoke([
                SystemMessage(content=system_prompt.text),
                HumanMessage(content=user_prompt),
            ], prompt_meta=system_prompt.meta)
            text = getattr(resp, "content", None)
            if isinstance(text, list):
                # Some providers return list-of-parts.
                text = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in text)
            text = (text or "").strip()
            if not text:
                return None
            return text
        except Exception as exc:  # pragma: no cover - upstream LLM may flake
            logger.warning("knowledge_llm_summarise_failed", error=str(exc))
            return None
