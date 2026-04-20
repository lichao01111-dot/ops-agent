from typing import Any, Callable, Awaitable

from langchain_core.messages import HumanMessage

from agent_kernel.executor import ExecutorBase
from agent_kernel.session import SessionStore
from agent_ops.extractors import extract_namespace, extract_service_name, extract_build_number, extract_time_range, extract_job_name, extract_pod_name, extract_log_level, extract_keyword, extract_sources
from agent_ops.formatters import format_read_only_summary
from agent_ops.memory_hooks import update_memory_from_tool_output
from agent_ops.schemas import AgentRoute


class ReadOnlyOpsExecutor(ExecutorBase):
    def __init__(self, invoke_tool: Callable[..., Awaitable[tuple[Any, str]]], session_store: SessionStore):
        super().__init__(node_name="read_only_ops", route_name="read_only_ops")
        self.invoke_tool = invoke_tool
        self.session_store = session_store

    def _get_latest_user_message(self, messages: list[Any]) -> str:
        for message in reversed(messages):
            if isinstance(message, HumanMessage):
                if isinstance(message.content, str):
                    return message.content
        return ""

    def _plan_read_only_tool(self, message: str, context: dict[str, Any], session_id: str = "") -> list[tuple[str, dict[str, Any]]] | None:
        lowered = message.lower()
        namespace = extract_namespace(message, context, self.session_store, session_id)
        service = extract_service_name(message, context, self.session_store, session_id)
        build_number = extract_build_number(message, context)
        time_range = extract_time_range(message, context)

        if "构建日志" in message or ("jenkins" in lowered and "日志" in message):
            job_name = extract_job_name(message, context, service, self.session_store, session_id)
            if job_name:
                return [("get_jenkins_build_log", {"job_name": job_name, "build_number": build_number or 1, "tail_lines": 100})]

        if "构建" in message or "jenkins" in lowered:
            job_name = extract_job_name(message, context, service, self.session_store, session_id)
            if job_name:
                args: dict[str, Any] = {"job_name": job_name}
                if build_number:
                    args["build_number"] = build_number
                return [("query_jenkins_build", args)]

        if ("错误统计" in message) or ("统计" in message and "日志" in message):
            if service:
                return [("get_error_statistics", {"service": service, "time_range_minutes": time_range})]

        if ("pod日志" in message) or ("pod 日志" in message) or ("日志" in message and "pod" in lowered):
            pod_name = extract_pod_name(message, context, service, self.session_store, session_id)
            if pod_name:
                return [("get_pod_logs", {"namespace": namespace, "pod_name": pod_name, "tail_lines": 100})]

        if "日志" in message:
            if service:
                return [(
                    "search_logs",
                    {
                        "service": service,
                        "time_range_minutes": time_range,
                        "level": extract_log_level(message),
                        "keyword": extract_keyword(message),
                        "limit": 50,
                    },
                )]

        if "deployment" in lowered:
            return [("get_deployment_status", {"namespace": namespace, "name": service})]

        if "service" in lowered or "svc" in lowered:
            return [("get_service_info", {"namespace": namespace, "name": service})]

        if "pod" in lowered:
            return [("get_pod_status", {"namespace": namespace, "name_filter": service, "show_all": False})]

        if any(token in lowered for token in ("namespace", "命名空间")):
            return [("get_pod_status", {"namespace": namespace, "name_filter": service, "show_all": False})]

        return None


    async def execute(self, state: dict[str, Any], event_callback: Callable | None = None) -> dict[str, Any]:
        message = self._get_latest_user_message(state["messages"])
        plan = self._plan_read_only_tool(message, state["context"], state["session_id"])

        if not plan:
            return {
                "final_message": "我没有从请求里识别出明确的只读查询目标。请补充服务名、namespace、构建编号或日志范围。",
                "tool_calls": [],
                "sources": [],
            }

        tool_calls = []
        outputs: list[tuple[str, str]] = []
        for tool_name, args in plan:
            event, output = await self.invoke_tool(
                tool_name,
                args,
                event_callback,
                user_id=state["user_id"],
                session_id=state["session_id"],
                route=AgentRoute.READ_ONLY_OPS,
            )
            tool_calls.append(event)
            outputs.append((tool_name, output))
            update_memory_from_tool_output(self.session_store, state["session_id"], tool_name, output)

        final_message = format_read_only_summary(outputs)
        sources = []
        for _, output in outputs:
            sources.extend(extract_sources(output))
        return {"final_message": final_message, "tool_calls": tool_calls, "sources": sorted(set(sources))}
