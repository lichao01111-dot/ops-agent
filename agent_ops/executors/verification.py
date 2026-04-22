"""
VerificationExecutor — polls mutation outcome and triggers rollback / escalation.

This executor is auto-appended by OpsPlanner._maybe_replan after every
successful mutation step.  It reads the MutationPlan that MutationExecutor
stored in the session PLANS memory layer and drives the verify → rollback →
escalate lifecycle.

Lifecycle
---------
1. Read MutationPlan from session memory.
2. If no plan (e.g. Jenkinsfile / index path) → skip gracefully.
3. Poll the verification tool up to max_attempts × poll_interval_s seconds.
4. On success → write VERIFICATION memory, return pass message.
5. On failure after all retries:
   a. If rollback spec has a tool → invoke rollback, write VERIFICATION memory.
   b. Otherwise → escalate (write VERIFICATION memory, return human-alert message).

Architecture ref: §9 "Mutation execution loop — verify / rollback / escalate"
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Awaitable

import structlog

from agent_kernel.executor import ExecutorBase
from agent_kernel.schemas import PlanStep, ToolCallStatus
from agent_kernel.session import SessionStore
from agent_ops.formatters import (
    format_verification_passed,
    format_verification_failed_with_rollback,
    format_verification_escalated,
)
from agent_ops.memory_hooks import load_mutation_plan, write_verification_memory
from agent_ops.mutation_plan import MutationPlan
from agent_ops.schemas import AgentRoute

logger = structlog.get_logger()


class VerificationExecutor(ExecutorBase):
    """Poll mutation outcome; auto-rollback or escalate on failure."""

    def __init__(
        self,
        invoke_tool: Callable[..., Awaitable[tuple[Any, str]]],
        session_store: SessionStore,
    ):
        super().__init__(node_name="verification", route_name="verification")
        self.invoke_tool = invoke_tool
        self.session_store = session_store

    async def execute(self, state: dict[str, Any], event_callback: Callable | None = None) -> dict[str, Any]:
        session_id: str = state["session_id"]
        plan = state.get("plan")
        step: PlanStep | None = plan.current_step() if plan else None

        mutation_plan = load_mutation_plan(self.session_store, session_id)
        if mutation_plan is None:
            logger.warning("verification_no_mutation_plan", session_id=session_id)
            return {
                "final_message": "验证步骤跳过：未找到关联的变更计划（可能是 Jenkinsfile 生成或文档索引操作，无需 K8s 状态验证）。",
                "tool_calls": [],
                "sources": [],
            }

        if mutation_plan.execution_status == "failed":
            return {
                "final_message": (
                    f"变更执行阶段已报告失败（{mutation_plan.action} / {mutation_plan.target}），"
                    "跳过验证，请检查上一步错误信息。"
                ),
                "tool_calls": [],
                "sources": [],
            }

        if mutation_plan.verification is None:
            # Mutations without a verification spec (e.g. generate_jenkinsfile)
            return {
                "final_message": "变更已完成，该操作类型无自动验证步骤。",
                "tool_calls": [],
                "sources": [],
            }

        return await self._poll_and_decide(state, step, mutation_plan, event_callback)

    # ------------------------------------------------------------------
    # Polling loop
    # ------------------------------------------------------------------

    async def _poll_and_decide(
        self,
        state: dict[str, Any],
        step: PlanStep | None,
        mutation_plan: MutationPlan,
        event_callback: Callable | None,
    ) -> dict[str, Any]:
        criteria = mutation_plan.verification
        assert criteria is not None  # guarded by caller

        session_id = state["session_id"]
        all_tool_calls = []
        attempt = 0

        logger.info(
            "verification_polling_start",
            action=mutation_plan.action,
            target=mutation_plan.target,
            namespace=mutation_plan.namespace,
            max_attempts=criteria.max_attempts,
            poll_interval_s=criteria.poll_interval_s,
        )

        while attempt < criteria.max_attempts:
            attempt += 1
            if attempt > 1:
                await asyncio.sleep(criteria.poll_interval_s)

            event, output = await self.invoke_tool(
                criteria.tool,
                criteria.args,
                event_callback,
                user_id=state.get("user_id", ""),
                session_id=session_id,
                route=AgentRoute.VERIFICATION,
                step=step,
            )
            all_tool_calls.append(event)

            if event.status != ToolCallStatus.SUCCESS:
                logger.warning(
                    "verification_tool_error",
                    attempt=attempt,
                    error=event.error,
                )
                continue  # tool error — retry

            passed, detail = self._check_success(output, mutation_plan, criteria)
            logger.info(
                "verification_poll_result",
                attempt=attempt,
                passed=passed,
                detail=detail,
            )
            if passed:
                write_verification_memory(
                    self.session_store, session_id,
                    mutation_action=str(mutation_plan.action),
                    target=mutation_plan.target,
                    namespace=mutation_plan.namespace,
                    verdict="passed",
                    detail=detail,
                    step_id=step.step_id if step else "",
                    attempts=attempt,
                )
                return {
                    "final_message": format_verification_passed(
                        str(mutation_plan.action),
                        mutation_plan.target,
                        mutation_plan.namespace,
                        attempt,
                    ),
                    "tool_calls": all_tool_calls,
                    "sources": [],
                }

        # All retries exhausted
        return await self._handle_failure(state, step, mutation_plan, attempt, all_tool_calls, event_callback)

    # ------------------------------------------------------------------
    # Success condition evaluation
    # ------------------------------------------------------------------

    @staticmethod
    def _check_success(output: str, plan: MutationPlan, criteria: Any) -> tuple[bool, str]:
        """Return (passed, detail_string) for a deployment status poll response."""
        try:
            payload = json.loads(output)
        except Exception:
            return False, "无法解析工具返回值"

        if payload.get("error"):
            return False, f"工具报错: {payload['error']}"

        # Deployment-based verification
        deployments = payload.get("deployments", [])
        if not deployments:
            return False, "未找到 Deployment 信息"

        dep = deployments[0]
        total = dep.get("replicas") or 0
        ready = dep.get("ready_replicas") or 0
        available = dep.get("available_replicas") or 0

        # For scale operations, check against the target replica count
        if criteria.expected_replicas is not None:
            target = criteria.expected_replicas
        else:
            target = total

        if target == 0:
            # Scaled to zero is always "success"
            return True, "Deployment 已缩容至 0 副本"

        if ready >= target and available >= target:
            return True, f"ready={ready}/{target}  available={available}"

        return False, f"等待就绪: ready={ready}/{target}  available={available}"

    # ------------------------------------------------------------------
    # Failure path: rollback or escalate
    # ------------------------------------------------------------------

    async def _handle_failure(
        self,
        state: dict[str, Any],
        step: PlanStep | None,
        mutation_plan: MutationPlan,
        attempts: int,
        all_tool_calls: list,
        event_callback: Callable | None,
    ) -> dict[str, Any]:
        session_id = state["session_id"]
        rollback_spec = mutation_plan.rollback

        if rollback_spec and rollback_spec.tool:
            logger.warning(
                "verification_failed_triggering_rollback",
                action=mutation_plan.action,
                target=mutation_plan.target,
                rollback_tool=rollback_spec.tool,
            )
            rb_event, rb_output = await self.invoke_tool(
                rollback_spec.tool,
                rollback_spec.args,
                event_callback,
                user_id=state.get("user_id", ""),
                session_id=session_id,
                route=AgentRoute.VERIFICATION,
                step=step,
            )
            all_tool_calls.append(rb_event)
            write_verification_memory(
                self.session_store, session_id,
                mutation_action=str(mutation_plan.action),
                target=mutation_plan.target,
                namespace=mutation_plan.namespace,
                verdict="rolled_back",
                detail=rollback_spec.escalation_message,
                step_id=step.step_id if step else "",
                attempts=attempts,
            )
            return {
                "final_message": format_verification_failed_with_rollback(
                    str(mutation_plan.action),
                    mutation_plan.target,
                    mutation_plan.namespace,
                    attempts,
                    rb_output,
                ),
                "tool_calls": all_tool_calls,
                "sources": [],
            }

        # No rollback possible → escalate
        escalation_msg = (
            rollback_spec.escalation_message
            if rollback_spec
            else f"Deployment {mutation_plan.target} 变更后验证失败，请立即人工检查。"
        )
        write_verification_memory(
            self.session_store, session_id,
            mutation_action=str(mutation_plan.action),
            target=mutation_plan.target,
            namespace=mutation_plan.namespace,
            verdict="escalated",
            detail=escalation_msg,
            step_id=step.step_id if step else "",
            attempts=attempts,
        )
        return {
            "final_message": format_verification_escalated(
                str(mutation_plan.action),
                mutation_plan.target,
                mutation_plan.namespace,
                attempts,
                escalation_msg,
            ),
            "tool_calls": all_tool_calls,
            "sources": [],
        }
