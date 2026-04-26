import re
from typing import Any, Callable, Awaitable

from langchain_core.messages import HumanMessage

from agent_kernel.executor import ExecutorBase
from agent_kernel.session import SessionStore
from agent_ops.extractors import (
    extract_build_number,
    extract_cluster_name,
    extract_config_query_filters,
    extract_configmap_name,
    extract_job_name,
    extract_keyword,
    extract_log_level,
    extract_namespace,
    extract_pod_name,
    extract_service_name,
    extract_sources,
    extract_time_range,
)
from agent_ops.formatters import format_read_only_summary
from agent_ops.memory_hooks import update_memory_from_knowledge, update_memory_from_tool_output
from agent_ops.schemas import AgentRoute
from agent_ops.formatters import load_json


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

    def _is_config_lookup_request(self, message: str) -> bool:
        lowered = message.lower()
        return any(
            token in lowered
            for token in ("configmap", "jdbc", "datasource", "链接串", "连接串", "secret")
        ) or "配置" in message

    def _merge_lookup_hints(self, target: dict[str, str], candidate: dict[str, str]) -> dict[str, str]:
        merged = dict(target)
        for key, value in candidate.items():
            if value and (not merged.get(key) or merged[key] in ("", "default")):
                merged[key] = value
        return merged

    def _extract_lookup_hints_from_knowledge(self, output: str) -> dict[str, str]:
        payload = load_json(output)
        texts = [
            " ".join(
                str(part)
                for part in (
                    result.get("content", ""),
                    result.get("source", ""),
                    result.get("metadata", {}),
                )
            )
            for result in payload.get("results", [])
        ]
        blob = "\n".join(texts)
        hints = {
            "cluster": "",
            "namespace": "",
            "configmap_name": "",
            "service": "",
        }

        cluster = extract_cluster_name(blob, {}, self.session_store, "")
        if cluster:
            hints["cluster"] = cluster

        namespace_match = None
        for pattern in (
            r"namespace\s*[:：=]\s*([a-z0-9._-]+)",
            r"命名空间\s*[:：]\s*([a-z0-9._-]+)",
        ):
            namespace_match = re.search(pattern, blob, re.IGNORECASE)
            if namespace_match:
                break
        if namespace_match:
            hints["namespace"] = namespace_match.group(1)

        configmap_match = None
        for pattern in (
            r"configmap\s*[:：=]\s*([a-z0-9][\w.-]*)",
            r"([a-z0-9][\w.-]*)\s*(?:的)?\s*configmap",
        ):
            configmap_match = re.search(pattern, blob, re.IGNORECASE)
            if configmap_match:
                break
        if configmap_match:
            hints["configmap_name"] = configmap_match.group(1)

        service_match = re.search(r"service\s*[:：=]\s*([a-z0-9][\w.-]*)", blob, re.IGNORECASE)
        if service_match:
            hints["service"] = service_match.group(1)

        return hints

    @staticmethod
    def _append_matched_detail(final_message: str, label: str, entries: dict[str, Any]) -> str:
        if not entries:
            return final_message
        detail_lines = [f"{key}={value}" for key, value in entries.items()]
        return final_message + f"\n\n{label}：\n" + "\n".join(detail_lines)

    async def _get_configmap_with_fallbacks(
        self,
        *,
        state: dict[str, Any],
        hints: dict[str, str],
        config_filters: list[str],
        event_callback: Callable | None,
        tool_calls: list[Any],
    ) -> tuple[str, list[str]]:
        session_id = state["session_id"]
        sources: list[str] = []
        explicit_configmap_name = bool(hints["configmap_name"])
        configmap_name = hints["configmap_name"]
        if not configmap_name and hints["service"]:
            configmap_name = hints["service"]

        event, output = await self.invoke_tool(
            "get_configmap",
            {
                "namespace": hints["namespace"],
                "name": configmap_name if explicit_configmap_name else "",
                "name_filter": "" if explicit_configmap_name else configmap_name,
                "cluster": hints["cluster"],
                "key_filter": ",".join(config_filters),
            },
            event_callback,
            user_id=state["user_id"],
            session_id=session_id,
            route=AgentRoute.READ_ONLY_OPS,
        )
        tool_calls.append(event)
        update_memory_from_tool_output(self.session_store, session_id, "get_configmap", output)
        final_message = format_read_only_summary([("get_configmap", output)])
        payload = load_json(output)
        configmaps = payload.get("configmaps") or []
        if configmaps:
            first = configmaps[0]
            final_message = self._append_matched_detail(
                final_message,
                "匹配配置项",
                first.get("matched_entries") or {},
            )
            if first.get("matched_entries"):
                return final_message, sources

        if not hints["service"]:
            return final_message, sources

        # Continue tracing through deployment refs and explicit env vars.
        dep_event, dep_output = await self.invoke_tool(
            "get_deployment_config_refs",
            {
                "namespace": hints["namespace"],
                "name": hints["service"],
                "cluster": hints["cluster"],
            },
            event_callback,
            user_id=state["user_id"],
            session_id=session_id,
            route=AgentRoute.READ_ONLY_OPS,
        )
        tool_calls.append(dep_event)
        update_memory_from_tool_output(self.session_store, session_id, "get_deployment_config_refs", dep_output)
        dep_payload = load_json(dep_output)
        final_message += "\n\n" + format_read_only_summary([("get_deployment_config_refs", dep_output)])

        # First check explicit env vars on the deployment.
        env_event, env_output = await self.invoke_tool(
            "get_deployment_env",
            {
                "namespace": hints["namespace"],
                "name": hints["service"],
                "cluster": hints["cluster"],
                "key_filter": ",".join(config_filters),
            },
            event_callback,
            user_id=state["user_id"],
            session_id=session_id,
            route=AgentRoute.READ_ONLY_OPS,
        )
        tool_calls.append(env_event)
        update_memory_from_tool_output(self.session_store, session_id, "get_deployment_env", env_output)
        env_payload = load_json(env_output)
        entries = env_payload.get("entries") or []
        if entries:
            final_message += "\n\n" + format_read_only_summary([("get_deployment_env", env_output)])
            final_message += "\n\n显式环境变量：\n" + "\n".join(
                f"{item.get('name')}={item.get('value')}" for item in entries
            )
            return final_message, sources

        refs = dep_payload.get("refs", {})
        configmap_refs = refs.get("configmaps") or []
        secret_refs = refs.get("secrets") or []

        seen_configmaps = {item.get("name") for item in configmaps}
        for ref in configmap_refs:
            ref_name = ref.get("name", "")
            if not ref_name or ref_name in seen_configmaps:
                continue
            ref_event, ref_output = await self.invoke_tool(
                "get_configmap",
                {
                    "namespace": hints["namespace"],
                    "name": ref_name,
                    "name_filter": "",
                    "cluster": hints["cluster"],
                    "key_filter": ",".join(config_filters),
                },
                event_callback,
                user_id=state["user_id"],
                session_id=session_id,
                route=AgentRoute.READ_ONLY_OPS,
            )
            tool_calls.append(ref_event)
            update_memory_from_tool_output(self.session_store, session_id, "get_configmap", ref_output)
            ref_payload = load_json(ref_output)
            ref_configmaps = ref_payload.get("configmaps") or []
            if ref_configmaps and ref_configmaps[0].get("matched_entries"):
                final_message += "\n\n" + format_read_only_summary([("get_configmap", ref_output)])
                final_message += "\n\nDeployment 关联 ConfigMap 命中：\n" + "\n".join(
                    f"{key}={value}" for key, value in ref_configmaps[0]["matched_entries"].items()
                )
                return final_message, sources

        seen_secrets = set()
        for ref in secret_refs:
            ref_name = ref.get("name", "")
            if not ref_name or ref_name in seen_secrets:
                continue
            seen_secrets.add(ref_name)
            sec_event, sec_output = await self.invoke_tool(
                "get_secret",
                {
                    "namespace": hints["namespace"],
                    "name": ref_name,
                    "name_filter": "",
                    "cluster": hints["cluster"],
                    "key_filter": ",".join(config_filters),
                },
                event_callback,
                user_id=state["user_id"],
                session_id=session_id,
                route=AgentRoute.READ_ONLY_OPS,
            )
            tool_calls.append(sec_event)
            update_memory_from_tool_output(self.session_store, session_id, "get_secret", sec_output)
            sec_payload = load_json(sec_output)
            secrets = sec_payload.get("secrets") or []
            if secrets and secrets[0].get("matched_entries"):
                final_message += "\n\n" + format_read_only_summary([("get_secret", sec_output)])
                final_message += "\n\nDeployment 关联 Secret 命中：\n" + "\n".join(
                    f"{key}={value}" for key, value in secrets[0]["matched_entries"].items()
                )
                return final_message, sources

        final_message += "\n\n未在 ConfigMap、Deployment 显式 env 或关联 Secret 中命中目标配置项。"
        return final_message, sources

    async def _execute_config_lookup(
        self,
        state: dict[str, Any],
        message: str,
        event_callback: Callable | None,
    ) -> dict[str, Any]:
        session_id = state["session_id"]
        context = state["context"]

        hints = {
            "cluster": extract_cluster_name(message, context, self.session_store, session_id),
            "namespace": extract_namespace(message, context, self.session_store, session_id),
            "service": extract_service_name(message, context, self.session_store, session_id),
            "configmap_name": extract_configmap_name(
                message,
                context,
                "",
                self.session_store,
                session_id,
            ),
        }
        config_filters = extract_config_query_filters(message, context)
        tool_calls = []
        sources: list[str] = []

        # Knowledge-first augmentation for missing cluster / namespace / configmap hints.
        if not hints["cluster"] or hints["namespace"] == "default" or not hints["configmap_name"]:
            knowledge_event, knowledge_output = await self.invoke_tool(
                "query_knowledge",
                {
                    "question": (
                        "请提取以下运维查询所需的定位信息：集群、namespace、service 对应的 configmap 名称，"
                        f"以及数据库连接串配置 key。原始请求：{message}"
                    ),
                    "top_k": 5,
                },
                event_callback,
                user_id=state["user_id"],
                session_id=session_id,
                route=AgentRoute.READ_ONLY_OPS,
            )
            tool_calls.append(knowledge_event)
            sources.extend(extract_sources(knowledge_output))
            update_memory_from_knowledge(self.session_store, session_id, message, knowledge_output, sources)
            hints = self._merge_lookup_hints(hints, self._extract_lookup_hints_from_knowledge(knowledge_output))

        final_message, more_sources = await self._get_configmap_with_fallbacks(
            state=state,
            hints=hints,
            config_filters=config_filters,
            event_callback=event_callback,
            tool_calls=tool_calls,
        )
        sources.extend(more_sources)
        return {"final_message": final_message, "tool_calls": tool_calls, "sources": sorted(set(sources))}


    async def execute(self, state: dict[str, Any], event_callback: Callable | None = None) -> dict[str, Any]:
        message = self._get_latest_user_message(state["messages"])
        if self._is_config_lookup_request(message):
            return await self._execute_config_lookup(state, message, event_callback)

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
