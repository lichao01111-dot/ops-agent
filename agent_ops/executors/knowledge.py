from typing import Any, Callable, Awaitable

from langchain_core.messages import HumanMessage

from agent_kernel.executor import ExecutorBase
from agent_kernel.schemas import ApprovalReceipt, PlanStep, ToolCallStatus, UserRole
from agent_kernel.session import SessionStore
from agent_ops.extractors import extract_docs_directory, extract_sources, extract_top_k
from agent_ops.formatters import format_index_result, format_knowledge_result
from agent_ops.memory_hooks import update_memory_from_knowledge, write_execution_memory
from agent_ops.schemas import AgentRoute


class KnowledgeExecutor(ExecutorBase):
    def __init__(self, invoke_tool: Callable[..., Awaitable[tuple[Any, str]]], session_store: SessionStore):
        super().__init__(node_name="knowledge", route_name="knowledge")
        self.invoke_tool = invoke_tool
        self.session_store = session_store

    def _get_latest_user_message(self, messages: list[Any]) -> str:
        for message in reversed(messages):
            if isinstance(message, HumanMessage):
                if isinstance(message.content, str):
                    return message.content
        return ""

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
        message = self._get_latest_user_message(state["messages"])
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
        final_message = format_knowledge_result(output)
        update_memory_from_knowledge(self.session_store, state["session_id"], message, output, sources)
        return {"final_message": final_message, "tool_calls": [event], "sources": sources}
