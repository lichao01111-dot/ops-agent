import json
from typing import TYPE_CHECKING

from agent_kernel.session import SessionStore
from agent_ops.extractors import extract_namespace, extract_service_name
from agent_ops.formatters import load_json
from agent_ops.schemas import AgentIdentity, MemoryLayer

if TYPE_CHECKING:
    from agent_ops.mutation_plan import MutationPlan

def update_memory_from_knowledge(
    session_store: SessionStore,
    session_id: str,
    message: str,
    output: str,
    sources: list[str],
) -> None:
    payload = load_json(output)
    service = extract_service_name(message, {}, session_store, session_id)
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
    namespace = extract_namespace(text_blob, {}, session_store, session_id)
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

def update_memory_from_tool_output(
    session_store: SessionStore,
    session_id: str,
    tool_name: str,
    output: str,
) -> None:
    payload = load_json(output)
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
                value=first_message.replace("\n", " ")[:160] + "..." if len(first_message) > 160 else first_message.replace("\n", " "),
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
            value=json.dumps(payload, ensure_ascii=False)[:240] + "..." if len(json.dumps(payload, ensure_ascii=False)) > 240 else json.dumps(payload, ensure_ascii=False),
            source=tool_name,
            confidence=0.7,
        )

def write_plan_memory(
    session_store: SessionStore,
    session_id: str,
    action: str,
    target: str,
    namespace: str,
    step_id: str = "",
) -> None:
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
    if step_id:
        session_store.write_memory_item(
            session_id,
            writer=AgentIdentity.CHANGE_PLANNER,
            layer=MemoryLayer.PLANS,
            key="planned_step_id",
            value=step_id,
            source="mutation_plan",
            confidence=1.0,
        )

def write_execution_memory(
    session_store: SessionStore,
    session_id: str,
    action: str,
    target: str,
    status: str,
    step_id: str = "",
    approval_receipt_id: str = "",
) -> None:
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
    if step_id:
        session_store.write_memory_item(
            session_id,
            writer=AgentIdentity.CHANGE_EXECUTOR,
            layer=MemoryLayer.EXECUTION,
            key="executed_step_id",
            value=step_id,
            source="mutation_execution",
            confidence=1.0,
        )
    if approval_receipt_id:
        session_store.write_memory_item(
            session_id,
            writer=AgentIdentity.CHANGE_EXECUTOR,
            layer=MemoryLayer.EXECUTION,
            key="approval_receipt_id",
            value=approval_receipt_id,
            source="mutation_execution",
            confidence=1.0,
        )


def store_mutation_plan(
    session_store: SessionStore,
    session_id: str,
    mutation_plan: "MutationPlan",
) -> None:
    """Persist the full MutationPlan in the PLANS layer so VerificationExecutor can read it.

    Uses CHANGE_PLANNER as writer because the mutation plan — including verification
    criteria and rollback spec — is a planning artifact, not an execution result.
    """
    session_store.write_memory_item(
        session_id,
        writer=AgentIdentity.CHANGE_PLANNER,
        layer=MemoryLayer.PLANS,
        key="mutation_plan",
        value=mutation_plan.model_dump_json(),
        source="mutation_executor",
        confidence=1.0,
    )


def load_mutation_plan(
    session_store: SessionStore,
    session_id: str,
) -> "MutationPlan | None":
    """Read the MutationPlan stored by the most recent MutationExecutor run."""
    from agent_ops.mutation_plan import MutationPlan  # late import avoids circular
    raw = session_store.resolve_memory_value(
        session_id,
        "mutation_plan",
        [MemoryLayer.PLANS],
    )
    if not isinstance(raw, str):
        return None
    try:
        return MutationPlan.model_validate_json(raw)
    except Exception:
        return None


def write_verification_memory(
    session_store: SessionStore,
    session_id: str,
    *,
    mutation_action: str,
    target: str,
    namespace: str,
    verdict: str,           # "passed" | "failed" | "rolled_back" | "escalated"
    detail: str = "",
    step_id: str = "",
    attempts: int = 0,
) -> None:
    """Write the outcome of a verification step into the VERIFICATION memory layer."""
    session_store.write_memory_item(
        session_id,
        writer=AgentIdentity.VERIFICATION,
        layer=MemoryLayer.VERIFICATION,
        key="verification_verdict",
        value=verdict,
        source="verification_executor",
        confidence=1.0,
    )
    session_store.write_memory_item(
        session_id,
        writer=AgentIdentity.VERIFICATION,
        layer=MemoryLayer.VERIFICATION,
        key="verification_action",
        value=mutation_action,
        source="verification_executor",
        confidence=1.0,
    )
    session_store.write_memory_item(
        session_id,
        writer=AgentIdentity.VERIFICATION,
        layer=MemoryLayer.VERIFICATION,
        key="verification_target",
        value=f"{namespace}/{target}",
        source="verification_executor",
        confidence=1.0,
    )
    if detail:
        session_store.write_memory_item(
            session_id,
            writer=AgentIdentity.VERIFICATION,
            layer=MemoryLayer.VERIFICATION,
            key="verification_detail",
            value=detail[:400],
            source="verification_executor",
            confidence=0.9,
        )
    if step_id:
        session_store.write_memory_item(
            session_id,
            writer=AgentIdentity.VERIFICATION,
            layer=MemoryLayer.VERIFICATION,
            key="verification_step_id",
            value=step_id,
            source="verification_executor",
            confidence=1.0,
        )
    if attempts:
        session_store.write_memory_item(
            session_id,
            writer=AgentIdentity.VERIFICATION,
            layer=MemoryLayer.VERIFICATION,
            key="verification_attempts",
            value=attempts,
            source="verification_executor",
            confidence=1.0,
        )
