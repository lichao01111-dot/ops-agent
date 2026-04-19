"""
审计日志模块
记录所有 Agent 操作：谁、什么时候、做了什么、结果如何

Extension points (architecture-v2 §6 #8):
    - sanitizers: Vertical-provided hooks to mask extra sensitive fields
      before an entry is persisted. Default hook masks common secret names.
    - sinks: additional callables receiving each AuditEntry (e.g. SIEM push,
      DB write, Kafka publish). Kernel only guarantees "every call audited";
      where it lands is Vertical / deployment concern.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Optional

import structlog

from agent_kernel.schemas import AuditEntry, IntentTypeKey, RiskLevel, RouteKey

logger = structlog.get_logger()


Sanitizer = Callable[[dict[str, Any]], dict[str, Any]]
AuditSink = Callable[[AuditEntry], None]


_DEFAULT_SENSITIVE_KEYS = frozenset({
    "password", "token", "secret", "api_key", "credential", "connection_string",
})
_DEFAULT_LONG_STRING_LIMIT = 500


def default_sanitize_params(params: dict[str, Any]) -> dict[str, Any]:
    """Kernel-provided default sanitizer: mask common secret keys and
    truncate very long string values. Always applied first; Vertical
    sanitizers run after and can refine further."""
    sanitized: dict[str, Any] = {}
    for k, v in params.items():
        if any(s in k.lower() for s in _DEFAULT_SENSITIVE_KEYS):
            sanitized[k] = "***REDACTED***"
        elif isinstance(v, str) and len(v) > _DEFAULT_LONG_STRING_LIMIT:
            sanitized[k] = v[:_DEFAULT_LONG_STRING_LIMIT] + "...(truncated)"
        else:
            sanitized[k] = v
    return sanitized


class AuditLogger:
    """审计日志记录器。

    Vertical 通过注入 sanitizers / sinks 扩展：
        audit = AuditLogger(
            sanitizers=[mask_k8s_secrets],
            sinks=[siem_writer, kafka_writer],
        )
    Kernel 只强制 "每次 tool 调用 / 每次 chat 必须落一条 entry"；
    落在哪里由 Vertical 决定。
    """

    def __init__(
        self,
        *,
        sanitizers: Iterable[Sanitizer] | None = None,
        sinks: Iterable[AuditSink] | None = None,
    ):
        self._entries: list[AuditEntry] = []
        # Default sanitizer always runs first, then Vertical-provided hooks.
        self._sanitizers: list[Sanitizer] = [default_sanitize_params]
        if sanitizers:
            self._sanitizers.extend(sanitizers)
        self._sinks: list[AuditSink] = list(sinks or [])

    # ----- extension API -----

    def add_sanitizer(self, fn: Sanitizer) -> None:
        """Append a Vertical-specific sanitizer. Runs after the default."""
        self._sanitizers.append(fn)

    def add_sink(self, fn: AuditSink) -> None:
        """Register an additional audit sink (SIEM / DB / Kafka)."""
        self._sinks.append(fn)

    # ----- core -----

    def log(
        self,
        user_id: str,
        session_id: str,
        intent: Optional[IntentTypeKey] = None,
        route: Optional[RouteKey] = None,
        risk_level: Optional[RiskLevel] = None,
        needs_approval: bool = False,
        tool_name: Optional[str] = None,
        action: Optional[str] = None,
        tool_calls: list[str] | None = None,
        params: dict[str, Any] | None = None,
        result_summary: str = "",
        success: bool = True,
        duration_ms: int = 0,
    ) -> AuditEntry:
        entry = AuditEntry(
            user_id=user_id,
            session_id=session_id,
            intent=intent,
            route=route,
            risk_level=risk_level,
            needs_approval=needs_approval,
            tool_name=tool_name,
            action=action,
            tool_calls=tool_calls or [],
            params=self._sanitize_params(params or {}),
            result_summary=result_summary,
            success=success,
            duration_ms=duration_ms,
        )
        self._entries.append(entry)

        # Kernel-provided structured log (always on).
        logger.info(
            "audit_log",
            user=user_id,
            intent=intent if intent else None,
            route=route if route else None,
            risk_level=risk_level.value if risk_level else None,
            needs_approval=needs_approval,
            tool=tool_name,
            action=action,
            tool_calls=tool_calls or [],
            success=success,
            duration_ms=duration_ms,
        )

        # Vertical / deployment sinks — isolated so one bad sink can't
        # break auditing.
        for sink in self._sinks:
            try:
                sink(entry)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("audit_sink_failed", sink=getattr(sink, "__name__", repr(sink)), error=str(exc))

        return entry

    def _sanitize_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """Run default + Vertical sanitizers in registration order.
        Each sanitizer receives the output of the previous one."""
        current = dict(params)
        for fn in self._sanitizers:
            try:
                current = fn(current)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("sanitizer_failed", sanitizer=getattr(fn, "__name__", repr(fn)), error=str(exc))
        return current

    def get_recent(self, limit: int = 50) -> list[AuditEntry]:
        return self._entries[-limit:]

    def get_by_user(self, user_id: str, limit: int = 50) -> list[AuditEntry]:
        return [e for e in self._entries if e.user_id == user_id][-limit:]


def create_audit_logger(
    *,
    sanitizers: Iterable[Sanitizer] | None = None,
    sinks: Iterable[AuditSink] | None = None,
) -> AuditLogger:
    return AuditLogger(sanitizers=sanitizers, sinks=sinks)
