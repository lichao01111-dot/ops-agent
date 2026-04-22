"""
MutationExecutor — side-effecting change operations for the Ops vertical.

Handles:
  - K8s Deployment restart / scale / rollback
  - Knowledge-base document indexing
  - Jenkinsfile generation

Every K8s mutation:
  1. Builds a typed MutationPlan (including VerificationCriteria + RollbackSpec)
  2. Checks approval receipt when required
  3. Invokes the side-effecting tool
  4. Stores the MutationPlan in session memory so the auto-appended
     VerificationExecutor step can consume it

Architecture ref: §9 "Mutation execution loop"
"""
from __future__ import annotations

import re
from typing import Any, Callable, Awaitable

from langchain_core.messages import HumanMessage

from agent_kernel.executor import ExecutorBase
from agent_kernel.schemas import ApprovalReceipt, PlanStep, ToolCallStatus, UserRole
from agent_kernel.session import SessionStore
from agent_ops.extractors import (
    extract_docs_directory,
    extract_namespace,
    extract_service_name,
    build_pipeline_plan,
)
from agent_ops.formatters import (
    format_index_result,
    format_mutation_plan,
    format_mutation_execution,
    format_k8s_mutation_pending,
    format_k8s_mutation_result,
)
from agent_ops.memory_hooks import write_plan_memory, write_execution_memory, store_mutation_plan
from agent_ops.mutation_plan import (
    build_restart_plan,
    build_scale_plan,
    build_rollback_plan,
)
from agent_ops.schemas import AgentRoute, IntentType


# ---------------------------------------------------------------------------
# Keyword extraction helpers
# ---------------------------------------------------------------------------

def _extract_deployment_name(message: str, context: dict[str, Any], session_store: Any, session_id: str) -> str:
    """Extract deployment name from message or context."""
    for key in ("deployment", "name", "service", "app"):
        value = context.get(key)
        if isinstance(value, str) and value:
            return value
    # match patterns: "重启 foo-service", "回滚 payment-gateway", "foo-deployment"
    match = re.search(
        r"(?:重启|回滚|扩容|缩容|scale|restart|rollback)\s+([a-zA-Z0-9][\w.-]*)",
        message,
    )
    if match:
        return match.group(1)
    match = re.search(r"([a-z0-9][\w-]*(?:-service|-gateway|-frontend|-backend|-worker|-deployment))", message.lower())
    if match:
        return match.group(1)
    return extract_service_name(message, context, session_store, session_id) or "unknown"


def _extract_replicas(message: str, context: dict[str, Any]) -> int:
    """Extract desired replica count from message or context."""
    if isinstance(context.get("replicas"), int):
        return max(0, min(context["replicas"], 50))
    match = re.search(r"(\d+)\s*(?:个|副本|replicas?|实例)", message)
    if match:
        return max(0, min(int(match.group(1)), 50))
    return 2  # safe default


def _extract_revision(message: str, context: dict[str, Any]) -> int:
    """Extract rollback revision from message or context (0 = previous)."""
    if isinstance(context.get("revision"), int):
        return max(0, context["revision"])
    match = re.search(r"revision[=\s]*(\d+)", message.lower())
    if match:
        return int(match.group(1))
    return 0


def _is_index_request(message: str) -> bool:
    return any(token in message.lower() for token in ("索引", "同步文档", "导入文档", "index"))


def _is_restart_request(message: str) -> bool:
    return any(token in message.lower() for token in ("重启", "restart", "rolling restart", "滚动重启"))


def _is_scale_request(message: str) -> bool:
    return any(token in message.lower() for token in ("扩容", "缩容", "扩缩容", "副本", "replicas", "scale"))


def _is_rollback_request(message: str) -> bool:
    return any(token in message.lower() for token in ("回滚", "rollback", "undo", "恢复版本"))


class MutationExecutor(ExecutorBase):
    """Executes side-effecting change operations and wires up verification/rollback metadata."""

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

    # ------------------------------------------------------------------
    # Main dispatch
    # ------------------------------------------------------------------

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

        # ----- Route to sub-handler -----
        if _is_index_request(message):
            return await self._handle_index(state, message, step, receipt, event_callback)

        if state.get("intent") == IntentType.PIPELINE_CREATE or _is_jenkinsfile_request(message):
            return await self._handle_jenkinsfile(state, message, step, receipt, event_callback)

        if _is_restart_request(message):
            return await self._handle_restart(state, message, step, receipt, event_callback)

        if _is_scale_request(message):
            return await self._handle_scale(state, message, step, receipt, event_callback)

        if _is_rollback_request(message):
            return await self._handle_rollback(state, message, step, receipt, event_callback)

        return {
            "final_message": (
                "当前 mutation 路由收到了变更请求，但未能识别具体操作类型。\n"
                "支持的操作：重启 Deployment、扩缩容 Deployment、回滚 Deployment、"
                "文档索引、生成 Jenkinsfile。\n"
                "请明确指定操作目标，例如：「重启 payment-service」、「将 gateway 副本数调整为 3」。"
            ),
            "tool_calls": [],
            "sources": [],
        }

    # ------------------------------------------------------------------
    # Sub-handlers: K8s mutations
    # ------------------------------------------------------------------

    async def _handle_restart(
        self,
        state: dict[str, Any],
        message: str,
        step: PlanStep | None,
        receipt: ApprovalReceipt | None,
        event_callback: Callable | None,
    ) -> dict[str, Any]:
        namespace = extract_namespace(message, state["context"], self.session_store, state["session_id"])
        name = _extract_deployment_name(message, state["context"], self.session_store, state["session_id"])
        mutation_plan = build_restart_plan(
            namespace=namespace,
            name=name,
            approval_receipt_id=receipt.receipt_id if receipt else "",
            step_id=step.step_id if step else "",
        )
        write_plan_memory(
            self.session_store, state["session_id"],
            action="restart_deployment", target=name, namespace=namespace,
            step_id=step.step_id if step else "",
        )
        if state.get("needs_approval") and receipt is None:
            store_mutation_plan(self.session_store, state["session_id"], mutation_plan)
            return {
                "final_message": format_k8s_mutation_pending(
                    "restart_deployment", name, namespace, step.step_id if step else None
                ),
                "tool_calls": [],
                "sources": [],
            }

        event, output = await self.invoke_tool(
            "restart_deployment",
            {"namespace": namespace, "name": name},
            event_callback,
            user_id=state["user_id"],
            session_id=state["session_id"],
            route=AgentRoute.MUTATION,
            step=step,
            approval_receipt=receipt,
            execution_target=step.execution_target if step else "executor:mutation",
        )
        mutation_plan.execution_status = "completed" if event.status == ToolCallStatus.SUCCESS else "failed"
        store_mutation_plan(self.session_store, state["session_id"], mutation_plan)
        write_execution_memory(
            self.session_store, state["session_id"],
            action="restart_deployment", target=name,
            status=mutation_plan.execution_status,
            step_id=step.step_id if step else "",
            approval_receipt_id=receipt.receipt_id if receipt else "",
        )
        return {
            "final_message": format_k8s_mutation_result("restart_deployment", output, name, namespace),
            "tool_calls": [event],
            "sources": [],
        }

    async def _handle_scale(
        self,
        state: dict[str, Any],
        message: str,
        step: PlanStep | None,
        receipt: ApprovalReceipt | None,
        event_callback: Callable | None,
    ) -> dict[str, Any]:
        namespace = extract_namespace(message, state["context"], self.session_store, state["session_id"])
        name = _extract_deployment_name(message, state["context"], self.session_store, state["session_id"])
        replicas = _extract_replicas(message, state["context"])
        mutation_plan = build_scale_plan(
            namespace=namespace,
            name=name,
            replicas=replicas,
            approval_receipt_id=receipt.receipt_id if receipt else "",
            step_id=step.step_id if step else "",
        )
        write_plan_memory(
            self.session_store, state["session_id"],
            action="scale_deployment", target=name, namespace=namespace,
            step_id=step.step_id if step else "",
        )
        if state.get("needs_approval") and receipt is None:
            store_mutation_plan(self.session_store, state["session_id"], mutation_plan)
            return {
                "final_message": format_k8s_mutation_pending(
                    "scale_deployment", f"{name} → {replicas} replicas", namespace,
                    step.step_id if step else None,
                ),
                "tool_calls": [],
                "sources": [],
            }

        event, output = await self.invoke_tool(
            "scale_deployment",
            {"namespace": namespace, "name": name, "replicas": replicas},
            event_callback,
            user_id=state["user_id"],
            session_id=state["session_id"],
            route=AgentRoute.MUTATION,
            step=step,
            approval_receipt=receipt,
            execution_target=step.execution_target if step else "executor:mutation",
        )
        mutation_plan.execution_status = "completed" if event.status == ToolCallStatus.SUCCESS else "failed"
        store_mutation_plan(self.session_store, state["session_id"], mutation_plan)
        write_execution_memory(
            self.session_store, state["session_id"],
            action="scale_deployment", target=name,
            status=mutation_plan.execution_status,
            step_id=step.step_id if step else "",
            approval_receipt_id=receipt.receipt_id if receipt else "",
        )
        return {
            "final_message": format_k8s_mutation_result("scale_deployment", output, name, namespace),
            "tool_calls": [event],
            "sources": [],
        }

    async def _handle_rollback(
        self,
        state: dict[str, Any],
        message: str,
        step: PlanStep | None,
        receipt: ApprovalReceipt | None,
        event_callback: Callable | None,
    ) -> dict[str, Any]:
        namespace = extract_namespace(message, state["context"], self.session_store, state["session_id"])
        name = _extract_deployment_name(message, state["context"], self.session_store, state["session_id"])
        revision = _extract_revision(message, state["context"])
        mutation_plan = build_rollback_plan(
            namespace=namespace,
            name=name,
            revision=revision,
            approval_receipt_id=receipt.receipt_id if receipt else "",
            step_id=step.step_id if step else "",
        )
        write_plan_memory(
            self.session_store, state["session_id"],
            action="rollback_deployment", target=name, namespace=namespace,
            step_id=step.step_id if step else "",
        )
        if state.get("needs_approval") and receipt is None:
            store_mutation_plan(self.session_store, state["session_id"], mutation_plan)
            rev_label = "上一版本" if revision == 0 else f"revision {revision}"
            return {
                "final_message": format_k8s_mutation_pending(
                    "rollback_deployment", f"{name} → {rev_label}", namespace,
                    step.step_id if step else None,
                ),
                "tool_calls": [],
                "sources": [],
            }

        event, output = await self.invoke_tool(
            "rollback_deployment",
            {"namespace": namespace, "name": name, "revision": revision},
            event_callback,
            user_id=state["user_id"],
            session_id=state["session_id"],
            route=AgentRoute.MUTATION,
            step=step,
            approval_receipt=receipt,
            execution_target=step.execution_target if step else "executor:mutation",
        )
        mutation_plan.execution_status = "completed" if event.status == ToolCallStatus.SUCCESS else "failed"
        store_mutation_plan(self.session_store, state["session_id"], mutation_plan)
        write_execution_memory(
            self.session_store, state["session_id"],
            action="rollback_deployment", target=name,
            status=mutation_plan.execution_status,
            step_id=step.step_id if step else "",
            approval_receipt_id=receipt.receipt_id if receipt else "",
        )
        return {
            "final_message": format_k8s_mutation_result("rollback_deployment", output, name, namespace),
            "tool_calls": [event],
            "sources": [],
        }

    # ------------------------------------------------------------------
    # Sub-handlers: index documents & Jenkinsfile (carried over)
    # ------------------------------------------------------------------

    async def _handle_index(
        self,
        state: dict[str, Any],
        message: str,
        step: PlanStep | None,
        receipt: ApprovalReceipt | None,
        event_callback: Callable | None,
    ) -> dict[str, Any]:
        docs_directory = extract_docs_directory(message, state["context"])
        namespace = extract_namespace(message, state["context"], self.session_store, state["session_id"])
        write_plan_memory(
            self.session_store, state["session_id"],
            action="index_documents", target=docs_directory, namespace=namespace,
            step_id=step.step_id if step else "",
        )
        if state.get("needs_approval") and receipt is None:
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
            self.session_store, state["session_id"],
            action="index_documents", target=docs_directory,
            status="completed" if event.status == ToolCallStatus.SUCCESS else "failed",
            step_id=step.step_id if step else "",
            approval_receipt_id=receipt.receipt_id if receipt else "",
        )
        return {
            "final_message": format_index_result(output, docs_directory),
            "tool_calls": [event],
            "sources": [],
        }

    async def _handle_jenkinsfile(
        self,
        state: dict[str, Any],
        message: str,
        step: PlanStep | None,
        receipt: ApprovalReceipt | None,
        event_callback: Callable | None,
    ) -> dict[str, Any]:
        pipeline_plan = build_pipeline_plan(message, state["context"], self.session_store, state["session_id"])
        write_plan_memory(
            self.session_store, state["session_id"],
            action="generate_jenkinsfile", target=pipeline_plan["project_name"],
            namespace=pipeline_plan["namespace"],
            step_id=step.step_id if step else "",
        )
        if state.get("needs_approval") and receipt is None:
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
            self.session_store, state["session_id"],
            action="generate_jenkinsfile", target=pipeline_plan["project_name"],
            status="completed" if event.status == ToolCallStatus.SUCCESS else "failed",
            step_id=step.step_id if step else "",
            approval_receipt_id=receipt.receipt_id if receipt else "",
        )
        return {
            "final_message": format_mutation_execution(
                pipeline_plan, output, receipt.receipt_id if receipt else ""
            ),
            "tool_calls": [event],
            "sources": [],
        }


def _is_jenkinsfile_request(message: str) -> bool:
    return any(token in message.lower() for token in ("jenkinsfile", "pipeline", "流水线", "生成pipeline"))
