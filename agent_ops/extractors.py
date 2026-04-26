import re
from typing import Any

from config import settings
from agent_ops.schemas import MemoryLayer


def _normalize_cluster_token(value: str) -> str:
    compact = re.sub(r"[\s_]+", "-", value.strip().lower())
    return re.sub(r"-{2,}", "-", compact).strip("-")

def extract_docs_directory(message: str, context: dict[str, Any]) -> str:
    if isinstance(context.get("docs_directory"), str):
        return context["docs_directory"]
    match = re.search(r"(/[\w./-]+)", message)
    if match:
        return match.group(1)
    return "./docs"

def extract_top_k(message: str, context: dict[str, Any]) -> int:
    if isinstance(context.get("top_k"), int):
        return max(1, min(context["top_k"], 10))
    match = re.search(r"top[_ -]?k\s*=?\s*(\d+)", message.lower())
    if match:
        return max(1, min(int(match.group(1)), 10))
    return 5


def extract_cluster_name(message: str, context: dict[str, Any], session_store: Any, session_id: str = "") -> str:
    for key in ("cluster", "cluster_name", "kube_context", "k8s_cluster"):
        value = context.get(key)
        if isinstance(value, str) and value.strip():
            return _normalize_cluster_token(value)

    explicit = [
        re.search(r"([a-zA-Z0-9._-]+\s+[a-zA-Z0-9._-]+)\s*(?:的)?\s*k8s\s*集群", message, re.IGNORECASE),
        re.search(r"([a-zA-Z0-9._-]+)\s*(?:的)?\s*k8s\s*集群", message, re.IGNORECASE),
        re.search(r"cluster\s*[:=]?\s*([a-zA-Z0-9._-]+)", message, re.IGNORECASE),
        re.search(r"context\s*[:=]?\s*([a-zA-Z0-9._-]+)", message, re.IGNORECASE),
    ]
    for match in explicit:
        if match:
            return _normalize_cluster_token(match.group(1))

    if session_id and session_store:
        memory_cluster = session_store.resolve_memory_value(
            session_id,
            "cluster",
            [MemoryLayer.FACTS, MemoryLayer.OBSERVATIONS],
        )
        if isinstance(memory_cluster, str) and memory_cluster.strip():
            return _normalize_cluster_token(memory_cluster)
    return ""

def extract_namespace(message: str, context: dict[str, Any], session_store: Any, session_id: str = "") -> str:
    for candidate in [context.get("namespace"), context.get("env"), context.get("environment")]:
        if isinstance(candidate, str) and candidate in settings.allowed_namespaces + settings.readonly_namespaces:
            return candidate
    for namespace in settings.allowed_namespaces + settings.readonly_namespaces:
        if namespace and namespace in message:
            return namespace
    if session_id and session_store:
        memory_namespace = session_store.resolve_memory_value(
            session_id,
            "namespace",
            [MemoryLayer.FACTS, MemoryLayer.OBSERVATIONS],
        )
        if isinstance(memory_namespace, str) and memory_namespace:
            return memory_namespace
        memory_env = session_store.resolve_memory_value(
            session_id,
            "env",
            [MemoryLayer.FACTS, MemoryLayer.OBSERVATIONS],
        )
        if isinstance(memory_env, str) and memory_env:
            return memory_env
    return "default"


def extract_configmap_name(
    message: str,
    context: dict[str, Any],
    fallback: str,
    session_store: Any,
    session_id: str = "",
) -> str:
    for key in ("configmap", "configmap_name"):
        value = context.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    explicit = [
        re.search(r"configmap\s*[:=]?\s*([a-zA-Z0-9][\w.-]*)", message, re.IGNORECASE),
        re.search(r"([a-zA-Z0-9][\w.-]*)\s*(?:的)?\s*configmap", message, re.IGNORECASE),
    ]
    for match in explicit:
        if match:
            return match.group(1)

    if session_id and session_store:
        memory_configmap = session_store.resolve_memory_value(
            session_id,
            "configmap_name",
            [MemoryLayer.FACTS, MemoryLayer.OBSERVATIONS],
        )
        if isinstance(memory_configmap, str) and memory_configmap.strip():
            return memory_configmap.strip()
    return fallback

def extract_service_name(message: str, context: dict[str, Any], session_store: Any, session_id: str = "") -> str:
    for key in ("service", "project", "job_name", "name"):
        value = context.get(key)
        if isinstance(value, str) and value:
            return value
    match = re.search(r"([a-z0-9-]+-service|[a-z0-9-]+-frontend|gateway)", message.lower())
    if match:
        return match.group(1)
    if session_id and session_store:
        memory_service = session_store.resolve_memory_value(
            session_id,
            "service",
            [MemoryLayer.FACTS, MemoryLayer.OBSERVATIONS],
        )
        if isinstance(memory_service, str):
            return memory_service
    match = re.search(r"([a-z0-9][\w-]*)\s*服务", message.lower())
    if match:
        return match.group(1)
    return ""


def extract_config_query_filters(message: str, context: dict[str, Any]) -> list[str]:
    raw = context.get("config_keys") or context.get("key_filter")
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    if isinstance(raw, str) and raw.strip():
        return [item.strip() for item in re.split(r"[,，\s]+", raw) if item.strip()]

    lowered = message.lower()
    filters: list[str] = []
    if any(token in message for token in ("数据库", "链接串", "连接串")) or any(
        token in lowered for token in ("jdbc", "datasource", "db url", "database url")
    ):
        filters.extend(
            [
                "spring.datasource.url",
                "spring.datasource.jdbc-url",
                "datasource",
                "jdbc",
                "db",
                "database",
                "mysql",
                "postgres",
                "url",
            ]
        )
    return filters

def extract_job_name(message: str, context: dict[str, Any], fallback: str, session_store: Any, session_id: str = "") -> str:
    if isinstance(context.get("job_name"), str) and context["job_name"]:
        return context["job_name"]
    match = re.search(r"job\s+([a-zA-Z0-9._-]+)", message)
    if match:
        return match.group(1)
    if session_id and session_store:
        memory_job = session_store.resolve_memory_value(
            session_id,
            "job_name",
            [MemoryLayer.FACTS, MemoryLayer.OBSERVATIONS],
        )
        if isinstance(memory_job, str) and memory_job:
            return memory_job
    return fallback

def extract_build_number(message: str, context: dict[str, Any]) -> int | None:
    value = context.get("build_number")
    if isinstance(value, int):
        return value
    match = re.search(r"(?:build|构建|#)\s*(\d+)", message.lower())
    if match:
        return int(match.group(1))
    return None

def extract_time_range(message: str, context: dict[str, Any]) -> int:
    value = context.get("time_range_minutes")
    if isinstance(value, int):
        return max(1, min(value, 24 * 60))
    match = re.search(r"最近\s*(\d+)\s*(分钟|小时)", message)
    if match:
        amount = int(match.group(1))
        return amount * 60 if match.group(2) == "小时" else amount
    return 60

def extract_pod_name(message: str, context: dict[str, Any], fallback: str, session_store: Any, session_id: str = "") -> str:
    for key in ("pod_name", "pod"):
        value = context.get(key)
        if isinstance(value, str) and value:
            return value
    match = re.search(r"([a-z0-9-]+-[a-z0-9]+-[a-z0-9]+)", message.lower())
    if match:
        return match.group(1)
    if session_id and session_store:
        memory_pod = session_store.resolve_memory_value(
            session_id,
            "pod_name",
            [MemoryLayer.OBSERVATIONS, MemoryLayer.FACTS],
        )
        if isinstance(memory_pod, str) and memory_pod:
            return memory_pod
    return fallback

def extract_log_level(message: str) -> str:
    lowered = message.lower()
    if "warn" in lowered:
        return "WARN"
    if "info" in lowered:
        return "INFO"
    if "debug" in lowered:
        return "DEBUG"
    return "ERROR"

def extract_keyword(message: str) -> str:
    match = re.search(r"关键词[是为:]?\s*([^\s，。]+)", message)
    return match.group(1) if match else ""

def extract_language(message: str, context: dict[str, Any]) -> str:
    value = context.get("language")
    if isinstance(value, str) and value:
        return value
    lowered = message.lower()
    if any(token in lowered for token in ("java", "maven", "spring")):
        return "java_maven"
    if any(token in lowered for token in ("node", "react", "vue", "next")):
        return "nodejs"
    if any(token in lowered for token in ("python", "django", "flask", "fastapi")):
        return "python"
    if any(token in lowered for token in ("go", "golang")):
        return "go"
    return "java_maven"

def build_pipeline_plan(message: str, context: dict[str, Any], session_store: Any, session_id: str = "") -> dict[str, Any]:
    project_name = extract_service_name(message, context, session_store, session_id) or "project"
    return {
        "project_name": project_name,
        "language": extract_language(message, context),
        "repo_url": str(context.get("repo_url") or f"https://git.example.com/{project_name}.git"),
        "branch": str(context.get("branch") or "main"),
        "registry": str(context.get("registry") or "registry.example.com"),
        "deploy_env": str(context.get("deploy_env") or context.get("env") or "staging"),
        "namespace": extract_namespace(message, context, session_store, session_id),
    }

def extract_sources(output: Any) -> list[str]:
    try:
        if isinstance(output, str):
            import json
            output = json.loads(output)
        if not isinstance(output, dict):
            return []
        sources = []
        for result in output.get("results", []):
            source = result.get("source")
            if source:
                sources.append(source)
        return list(set(sources))
    except Exception:
        return []
