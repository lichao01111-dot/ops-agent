import json
from typing import Any

def load_json(output: str) -> dict[str, Any]:
    try:
        payload = json.loads(output)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}

def truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "...(truncated)"

def format_index_result(output: str, docs_directory: str) -> str:
    payload = load_json(output)
    if payload.get("error"):
        return f"文档索引失败：{payload['error']}"
    return (
        f"文档索引完成，目录: {docs_directory}\n"
        f"本次写入 chunks: {payload.get('indexed_chunks', 0)}\n"
        f"知识库总文档数: {payload.get('total_documents', 0)}"
    )

def format_knowledge_result(output: str) -> str:
    payload = load_json(output)
    if payload.get("answer_status") == "no_results":
        return payload.get("message", "知识库中未找到相关信息。")
    results = payload.get("results", [])[:3]
    if not results:
        return "知识库中未找到相关信息。"
    lines = ["根据知识库检索结果，结论如下："]
    for index, result in enumerate(results, start=1):
        excerpt = truncate_text(str(result.get("content", "")).replace("\n", " "), 140)
        source = result.get("source", "unknown")
        lines.append(f"{index}. {excerpt} 来源: {source}")
    return "\n".join(lines)

def format_single_read_only_result(tool_name: str, payload: dict[str, Any]) -> str:
    if payload.get("error"):
        return f"{tool_name} 执行失败：{payload['error']}"
    if tool_name == "get_pod_status":
        return (
            f"Pod 状态查询完成：namespace={payload.get('namespace')}，"
            f"共 {payload.get('total_pods', 0)} 个匹配 Pod。"
        )
    if tool_name == "get_deployment_status":
        deployments = payload.get("deployments", [])
        if deployments:
            first = deployments[0]
            return (
                f"Deployment 查询完成：{first.get('name')} "
                f"ready={first.get('ready_replicas', 0)}/{first.get('replicas', 0)}，"
                f"image={first.get('image', 'unknown')}"
            )
        return "Deployment 查询完成，但未找到匹配项。"
    if tool_name == "get_service_info":
        services = payload.get("services", [])
        if services:
            first = services[0]
            return (
                f"Service 查询完成：{first.get('name')} "
                f"type={first.get('type')} cluster_ip={first.get('cluster_ip')}"
            )
        return "Service 查询完成，但未找到匹配项。"
    if tool_name == "get_configmap":
        configmaps = payload.get("configmaps", [])
        if configmaps:
            first = configmaps[0]
            matched_keys = first.get("matched_keys") or []
            cluster = payload.get("cluster") or "default-context"
            if matched_keys:
                return (
                    f"ConfigMap 查询完成：cluster={cluster} "
                    f"namespace={payload.get('namespace')} "
                    f"name={first.get('name')} "
                    f"matched_keys={', '.join(matched_keys[:5])}"
                )
            return (
                f"ConfigMap 查询完成：cluster={cluster} "
                f"namespace={payload.get('namespace')} "
                f"name={first.get('name')}，但未匹配到目标配置项。"
            )
        return "ConfigMap 查询完成，但未找到匹配项。"
    if tool_name == "get_secret":
        secrets = payload.get("secrets", [])
        if secrets:
            first = secrets[0]
            matched_keys = first.get("matched_keys") or []
            cluster = payload.get("cluster") or "default-context"
            if matched_keys:
                return (
                    f"Secret 查询完成：cluster={cluster} "
                    f"namespace={payload.get('namespace')} "
                    f"name={first.get('name')} "
                    f"matched_keys={', '.join(matched_keys[:5])}"
                )
            return (
                f"Secret 查询完成：cluster={cluster} "
                f"namespace={payload.get('namespace')} "
                f"name={first.get('name')}，但未匹配到目标配置项。"
            )
        return "Secret 查询完成，但未找到匹配项。"
    if tool_name == "get_deployment_config_refs":
        refs = payload.get("refs", {})
        return (
            f"Deployment 引用查询完成：deployment={payload.get('deployment')} "
            f"configmaps={len(refs.get('configmaps', []))} "
            f"secrets={len(refs.get('secrets', []))} "
            f"env={len(refs.get('env', []))}"
        )
    if tool_name == "get_deployment_env":
        entries = payload.get("entries", [])
        return (
            f"Deployment env 查询完成：deployment={payload.get('deployment')} "
            f"matched_entries={len(entries)}"
        )
    if tool_name == "get_pod_logs":
        return (
            f"Pod 日志已获取：pod={payload.get('pod_name')} "
            f"lines={payload.get('lines', 0)}。"
        )
    if tool_name == "query_jenkins_build":
        return (
            f"Jenkins 构建状态：job={payload.get('job_name')} "
            f"build=#{payload.get('build_number')} result={payload.get('result')}"
        )
    if tool_name == "get_jenkins_build_log":
        return (
            f"Jenkins 构建日志已获取：job={payload.get('job_name')} "
            f"build=#{payload.get('build_number')} returned_lines={payload.get('returned_lines', 0)}"
        )
    if tool_name == "search_logs":
        return (
            f"日志检索完成：service={payload.get('service')} "
            f"count={payload.get('count', 0)} level={payload.get('level')}"
        )
    if tool_name == "get_error_statistics":
        return (
            f"错误统计完成：service={payload.get('service')} "
            f"total_errors={payload.get('total_errors', 0)}"
        )
    return truncate_text(json.dumps(payload, ensure_ascii=False), 300)

def format_read_only_summary(outputs: list[tuple[str, str]]) -> str:
    lines = []
    for tool_name, output in outputs:
        payload = load_json(output)
        lines.append(format_single_read_only_result(tool_name, payload))
    return "\n\n".join(filter(None, lines))

def format_k8s_mutation_pending(action: str, target: str, namespace: str, step_id: str | None) -> str:
    """审批等待提示 (k8s 变更操作)."""
    action_labels = {
        "restart_deployment": "滚动重启 Deployment",
        "scale_deployment": "调整 Deployment 副本数",
        "rollback_deployment": "回滚 Deployment",
    }
    label = action_labels.get(action, action)
    return (
        "当前请求属于 K8s 变更操作，执行前需要审批。\n"
        f"操作: {label}\n"
        f"目标: {namespace}/{target}\n"
        f"step_id: {step_id or 'unknown'}\n"
        "如果确认执行，请在下一次请求中携带 `context.approval_receipt`，"
        "例如：`{\"receipt_id\":\"r-123\",\"step_id\":\"当前 step_id\"}`。"
    )


def format_k8s_mutation_result(action: str, output: str, target: str, namespace: str) -> str:
    """变更执行完成提示 (k8s 操作)."""
    payload = load_json(output)
    if payload.get("error"):
        return f"K8s 变更执行失败：{payload['error']}"
    action_labels = {
        "restart_deployment": "滚动重启",
        "scale_deployment": "扩缩容",
        "rollback_deployment": "回滚",
    }
    label = action_labels.get(action, action)
    detail = payload.get("message", "")
    return (
        f"✅ {label}已触发。\n"
        f"目标: {namespace}/{target}\n"
        f"{detail}\n"
        "验证步骤将自动执行，持续监控 Deployment 状态…"
    )


def format_verification_passed(action: str, target: str, namespace: str, attempts: int) -> str:
    return (
        f"✅ 变更验证通过。\n"
        f"操作: {action}  目标: {namespace}/{target}\n"
        f"共轮询 {attempts} 次，Deployment 已达到预期状态。"
    )


def format_verification_failed_with_rollback(
    action: str, target: str, namespace: str, attempts: int, rollback_output: str
) -> str:
    payload = load_json(rollback_output)
    rollback_ok = not bool(payload.get("error"))
    rollback_status = "回滚已触发" if rollback_ok else f"回滚失败: {payload.get('error', '未知错误')}"
    return (
        f"⚠️ 变更验证失败，已触发自动回滚。\n"
        f"操作: {action}  目标: {namespace}/{target}\n"
        f"轮询 {attempts} 次后 Deployment 仍未就绪。\n"
        f"回滚结果: {rollback_status}\n"
        "建议检查镜像是否可拉取、资源配额是否充足、Pod Events 是否有异常。"
    )


def format_verification_escalated(
    action: str, target: str, namespace: str, attempts: int, escalation_message: str
) -> str:
    return (
        f"🚨 变更验证失败，需要人工介入。\n"
        f"操作: {action}  目标: {namespace}/{target}\n"
        f"轮询 {attempts} 次后仍未通过。\n"
        f"{escalation_message}"
    )


def format_mutation_plan(plan: dict[str, Any], step_id: str | None) -> str:
    return (
        "当前请求被识别为变更操作，执行前需要审批。\n"
        f"计划动作: 生成 {plan['project_name']} 的 Jenkinsfile\n"
        f"语言类型: {plan['language']}\n"
        f"目标环境: {plan['deploy_env']} / namespace={plan['namespace']}\n"
        f"分支: {plan['branch']}\n"
        f"step_id: {step_id or 'unknown'}\n"
        "如果确认执行，请在下一次请求中携带 `context.approval_receipt`，"
        "例如：`{\"receipt_id\":\"r-123\",\"step_id\":\"当前 step_id\"}`。"
    )

def format_mutation_execution(plan: dict[str, Any], output: str, approval_receipt_id: str | None) -> str:
    payload = load_json(output)
    if payload.get("error"):
        return f"变更执行失败：{payload['error']}"
    receipt_line = f"\n审批票据: {approval_receipt_id}" if approval_receipt_id else ""
    return (
        "变更执行完成。\n"
        f"动作: 为 {plan['project_name']} 生成 Jenkinsfile\n"
        f"语言: {payload.get('language', plan['language'])}\n"
        f"环境: {plan['deploy_env']} / namespace={plan['namespace']}\n"
        f"已返回 Jenkinsfile 内容，可继续进入人工评审或后续创建 Job。{receipt_line}"
    )
