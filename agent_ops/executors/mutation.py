from typing import Any, Callable, Awaitable

from langchain_core.messages import HumanMessage

from agent_kernel.executor import ExecutorBase
from agent_kernel.schemas import ApprovalReceipt, PlanStep, ToolCallStatus, UserRole
from agent_kernel.session import SessionStore
from agent_ops.extractors import extract_docs_directory, extract_namespace, build_pipeline_plan
from agent_ops.formatters import format_index_result, format_mutation_plan, format_mutation_execution
from agent_ops.memory_hooks import write_plan_memory, write_execution_memory
from agent_ops.schemas import AgentRoute, IntentType


class MutationExecutor(ExecutorBase):
    def __init__(self, invoke_tool: Callable[..., Awaitable[tuple[Any, str]]], session_store: SessionStore):
        super().__init__(node_name="mutation", route_name="mutation")
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

    def _approval_receipt_for_step(self, state: dict[str, Any], step: PlanStep | None) -> ApprovalReceipt | None:
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
        if state["user_role"] == UserRole.VIEWER:
            return {
                "final_message": "当前请求属于变更操作，但你的角色是 Viewer，只允许只读查询。请使用 Operator 或 Admin 身份重试。",
                "tool_calls": [],
                "sources": [],
            }

        message = self._get_latest_user_message(state["messages"])
        plan = state.get("plan")
        step = plan.current_step() if plan else None
        receipt = self._approval_receipt_for_step(state, step)

        if self._is_index_request(message):
            docs_directory = extract_docs_directory(message, state["context"])
            write_plan_memory(
                self.session_store,
                state["session_id"],
                action="index_documents",
                target=docs_directory,
                namespace=extract_namespace(message, state["context"], self.session_store, state["session_id"]),
                step_id=step.step_id if step else "",
            )
            if state["needs_approval"] and receipt is None:
                return {
                    "final_message": (
                        "当前请求会修改知识库索引，执行前需要审批。\n"
                        f"目标目录: {docs_directory}\n"
                        "如果确认执行，请在下一次请求中携带 `context.approval_receipt`，"
                        "其中至少包含 `receipt_id` 和当前 `step_id`。"
                    ),
                    "tool_calls": [],
                    "sources": [],
                }
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
            write_execution_memory(
                self.session_store,
                state["session_id"],
                action="index_documents",
                target=docs_directory,
                status="completed" if event.status == ToolCallStatus.SUCCESS else "failed",
                step_id=step.step_id if step else "",
                approval_receipt_id=receipt.receipt_id if receipt else "",
            )
            return {
                "final_message": format_index_result(output, docs_directory),
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

        pipeline_plan = build_pipeline_plan(message, state["context"], self.session_store, state["session_id"])
        write_plan_memory(
            self.session_store,
            state["session_id"],
            action="generate_jenkinsfile",
            target=pipeline_plan["project_name"],
            namespace=pipeline_plan["namespace"],
            step_id=step.step_id if step else "",
        )
        if state["needs_approval"] and receipt is None:
            return {
                "final_message": format_mutation_plan(pipeline_plan, step.step_id if step else None),
                "tool_calls": [],
                "sources": [],
            }

        event, output = await self.invoke_tool(
            "generate_jenkinsfile",
            pipeline_plan,
            event_callback,
            user_id=state["user_id"],
            session_id=state["session_id"],
            route=AgentRoute.MUTATION,
            step=step,
            approval_receipt=receipt,
            execution_target=step.execution_target if step else "executor:mutation",
        )
        write_execution_memory(
            self.session_store,
            state["session_id"],
            action="generate_jenkinsfile",
            target=pipeline_plan["project_name"],
            status="completed" if event.status == ToolCallStatus.SUCCESS else "failed",
            step_id=step.step_id if step else "",
            approval_receipt_id=receipt.receipt_id if receipt else "",
        )
        final_message = format_mutation_execution(
            pipeline_plan, 
            output, 
            receipt.receipt_id if receipt else ""
        )
        return {"final_message": final_message, "tool_calls": [event], "sources": []}
