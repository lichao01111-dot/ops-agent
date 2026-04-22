"""
Kubernetes 运维 Tool
- 查询 Pod/Deployment/Service 状态
- 异常诊断（CrashLoopBackOff, OOMKilled 等）
- 资源用量查询
- 多集群 / 多 Namespace 支持
"""
from __future__ import annotations

import json
import structlog
from langchain_core.tools import tool

from config import settings

logger = structlog.get_logger()


def _get_k8s_client():
    """延迟加载 K8s 客户端，避免没有 kubeconfig 时报错"""
    from kubernetes import client, config as k8s_config

    try:
        if settings.kubeconfig_path:
            k8s_config.load_kube_config(config_file=settings.kubeconfig_path)
        else:
            try:
                k8s_config.load_incluster_config()
            except k8s_config.ConfigException:
                k8s_config.load_kube_config()
    except Exception as e:
        logger.warning("k8s_config_load_failed", error=str(e))
        return None, None

    v1 = client.CoreV1Api()
    apps_v1 = client.AppsV1Api()
    return v1, apps_v1


def _check_namespace_access(namespace: str, write: bool = False) -> str | None:
    """检查 namespace 访问权限"""
    all_allowed = settings.allowed_namespaces + settings.readonly_namespaces
    if namespace not in all_allowed:
        return f"无权访问 namespace: {namespace}。允许的 namespace: {', '.join(all_allowed)}"
    if write and namespace in settings.readonly_namespaces:
        return f"namespace {namespace} 为只读（生产环境），不允许写操作"
    return None


@tool
async def get_pod_status(
    namespace: str = "default",
    name_filter: str = "",
    show_all: bool = False,
) -> str:
    """查询 K8s Pod 状态列表。

    Args:
        namespace: K8s namespace
        name_filter: Pod 名称过滤（模糊匹配）
        show_all: 是否显示所有 Pod（包括已完成的）
    """
    if err := _check_namespace_access(namespace):
        return json.dumps({"error": err})

    v1, _ = _get_k8s_client()
    if not v1:
        return json.dumps({"error": "K8s 客户端未配置，请检查 KUBECONFIG_PATH"})

    try:
        pods = v1.list_namespaced_pod(namespace=namespace)
        results = []

        for pod in pods.items:
            name = pod.metadata.name
            if name_filter and name_filter.lower() not in name.lower():
                continue

            phase = pod.status.phase
            if not show_all and phase in ("Succeeded", "Failed"):
                continue

            # 获取容器状态
            container_statuses = []
            if pod.status.container_statuses:
                for cs in pod.status.container_statuses:
                    status_detail = "Running"
                    restart_count = cs.restart_count

                    if cs.state.waiting:
                        status_detail = f"Waiting: {cs.state.waiting.reason or 'Unknown'}"
                    elif cs.state.terminated:
                        status_detail = f"Terminated: {cs.state.terminated.reason or 'Unknown'}"

                    container_statuses.append({
                        "name": cs.name,
                        "ready": cs.ready,
                        "status": status_detail,
                        "restart_count": restart_count,
                        "image": cs.image,
                    })

            results.append({
                "name": name,
                "phase": phase,
                "node": pod.spec.node_name,
                "ip": pod.status.pod_ip,
                "start_time": pod.status.start_time.isoformat() if pod.status.start_time else None,
                "containers": container_statuses,
            })

        return json.dumps({
            "namespace": namespace,
            "total_pods": len(results),
            "pods": results,
        }, ensure_ascii=False, default=str)

    except Exception as e:
        return json.dumps({"error": f"查询 Pod 失败: {str(e)}"})


@tool
async def get_deployment_status(
    namespace: str = "default",
    name: str = "",
) -> str:
    """查询 K8s Deployment 状态。

    Args:
        namespace: K8s namespace
        name: Deployment 名称，为空则列出所有
    """
    if err := _check_namespace_access(namespace):
        return json.dumps({"error": err})

    _, apps_v1 = _get_k8s_client()
    if not apps_v1:
        return json.dumps({"error": "K8s 客户端未配置"})

    try:
        if name:
            dep = apps_v1.read_namespaced_deployment(name=name, namespace=namespace)
            deployments = [dep]
        else:
            dep_list = apps_v1.list_namespaced_deployment(namespace=namespace)
            deployments = dep_list.items

        results = []
        for dep in deployments:
            results.append({
                "name": dep.metadata.name,
                "replicas": dep.spec.replicas,
                "ready_replicas": dep.status.ready_replicas or 0,
                "available_replicas": dep.status.available_replicas or 0,
                "updated_replicas": dep.status.updated_replicas or 0,
                "strategy": dep.spec.strategy.type if dep.spec.strategy else "Unknown",
                "image": dep.spec.template.spec.containers[0].image if dep.spec.template.spec.containers else "Unknown",
                "created": dep.metadata.creation_timestamp.isoformat() if dep.metadata.creation_timestamp else None,
            })

        return json.dumps({
            "namespace": namespace,
            "deployments": results,
        }, ensure_ascii=False, default=str)

    except Exception as e:
        return json.dumps({"error": f"查询 Deployment 失败: {str(e)}"})


@tool
async def get_service_info(
    namespace: str = "default",
    name: str = "",
) -> str:
    """查询 K8s Service 信息（端口、类型、Endpoint）。

    Args:
        namespace: K8s namespace
        name: Service 名称，为空则列出所有
    """
    if err := _check_namespace_access(namespace):
        return json.dumps({"error": err})

    v1, _ = _get_k8s_client()
    if not v1:
        return json.dumps({"error": "K8s 客户端未配置"})

    try:
        if name:
            svc = v1.read_namespaced_service(name=name, namespace=namespace)
            services = [svc]
        else:
            svc_list = v1.list_namespaced_service(namespace=namespace)
            services = svc_list.items

        results = []
        for svc in services:
            ports = []
            if svc.spec.ports:
                for p in svc.spec.ports:
                    ports.append({
                        "name": p.name,
                        "port": p.port,
                        "target_port": str(p.target_port),
                        "protocol": p.protocol,
                        "node_port": p.node_port,
                    })

            results.append({
                "name": svc.metadata.name,
                "type": svc.spec.type,
                "cluster_ip": svc.spec.cluster_ip,
                "external_ip": svc.status.load_balancer.ingress[0].ip if svc.status.load_balancer and svc.status.load_balancer.ingress else None,
                "ports": ports,
                "selector": svc.spec.selector,
            })

        return json.dumps({
            "namespace": namespace,
            "services": results,
        }, ensure_ascii=False, default=str)

    except Exception as e:
        return json.dumps({"error": f"查询 Service 失败: {str(e)}"})


@tool
async def get_pod_logs(
    namespace: str,
    pod_name: str,
    container: str = "",
    tail_lines: int = 100,
    previous: bool = False,
) -> str:
    """获取 Pod 容器日志，用于排查问题。

    Args:
        namespace: K8s namespace
        pod_name: Pod 名称
        container: 容器名称（多容器 Pod 时需要指定）
        tail_lines: 返回最后多少行日志
        previous: 是否获取上一次容器实例的日志（用于排查 CrashLoopBackOff）
    """
    if err := _check_namespace_access(namespace):
        return json.dumps({"error": err})

    v1, _ = _get_k8s_client()
    if not v1:
        return json.dumps({"error": "K8s 客户端未配置"})

    try:
        kwargs = {
            "name": pod_name,
            "namespace": namespace,
            "tail_lines": tail_lines,
            "previous": previous,
        }
        if container:
            kwargs["container"] = container

        logs = v1.read_namespaced_pod_log(**kwargs)

        return json.dumps({
            "namespace": namespace,
            "pod_name": pod_name,
            "container": container or "default",
            "previous": previous,
            "lines": len(logs.split("\n")),
            "log": logs,
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": f"获取 Pod 日志失败: {str(e)}"})


@tool
async def diagnose_pod(
    namespace: str,
    pod_name: str,
) -> str:
    """诊断 Pod 异常状态，分析 CrashLoopBackOff / OOMKilled / ImagePullBackOff 等问题。

    Args:
        namespace: K8s namespace
        pod_name: Pod 名称
    """
    if err := _check_namespace_access(namespace):
        return json.dumps({"error": err})

    v1, _ = _get_k8s_client()
    if not v1:
        return json.dumps({"error": "K8s 客户端未配置"})

    try:
        pod = v1.read_namespaced_pod(name=pod_name, namespace=namespace)
        diagnosis = {
            "pod_name": pod_name,
            "namespace": namespace,
            "phase": pod.status.phase,
            "issues": [],
            "events": [],
        }

        # 分析容器状态
        if pod.status.container_statuses:
            for cs in pod.status.container_statuses:
                if cs.state.waiting:
                    reason = cs.state.waiting.reason or "Unknown"
                    message = cs.state.waiting.message or ""
                    diagnosis["issues"].append({
                        "container": cs.name,
                        "type": reason,
                        "message": message,
                        "restart_count": cs.restart_count,
                    })
                elif cs.state.terminated:
                    reason = cs.state.terminated.reason or "Unknown"
                    exit_code = cs.state.terminated.exit_code
                    diagnosis["issues"].append({
                        "container": cs.name,
                        "type": reason,
                        "exit_code": exit_code,
                        "restart_count": cs.restart_count,
                    })

        # 获取相关 Events
        events = v1.list_namespaced_event(
            namespace=namespace,
            field_selector=f"involvedObject.name={pod_name}",
        )
        for event in events.items[-10:]:  # 最近10条
            diagnosis["events"].append({
                "type": event.type,
                "reason": event.reason,
                "message": event.message,
                "count": event.count,
                "last_seen": event.last_timestamp.isoformat() if event.last_timestamp else None,
            })

        # 获取资源限制
        if pod.spec.containers:
            container = pod.spec.containers[0]
            if container.resources:
                diagnosis["resources"] = {
                    "requests": {
                        "cpu": str(container.resources.requests.get("cpu", "N/A")) if container.resources.requests else "N/A",
                        "memory": str(container.resources.requests.get("memory", "N/A")) if container.resources.requests else "N/A",
                    },
                    "limits": {
                        "cpu": str(container.resources.limits.get("cpu", "N/A")) if container.resources.limits else "N/A",
                        "memory": str(container.resources.limits.get("memory", "N/A")) if container.resources.limits else "N/A",
                    },
                }

        return json.dumps(diagnosis, ensure_ascii=False, default=str)

    except Exception as e:
        return json.dumps({"error": f"诊断 Pod 失败: {str(e)}"})


@tool
async def restart_deployment(namespace: str, name: str) -> str:
    """触发 Deployment 滚动重启（等效于 kubectl rollout restart）。

    Args:
        namespace: K8s namespace
        name: Deployment 名称
    """
    if err := _check_namespace_access(namespace, write=True):
        return json.dumps({"error": err})

    _, apps_v1 = _get_k8s_client()
    if not apps_v1:
        return json.dumps({"error": "K8s 客户端未配置"})

    try:
        import datetime as _dt
        patch_body = {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            "kubectl.kubernetes.io/restartedAt": (
                                _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
                            )
                        }
                    }
                }
            }
        }
        result = apps_v1.patch_namespaced_deployment(
            name=name, namespace=namespace, body=patch_body
        )
        return json.dumps({
            "namespace": namespace,
            "name": name,
            "action": "restart_deployment",
            "generation": result.metadata.generation,
            "message": f"Deployment {name} 滚动重启已触发。",
        }, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": f"重启 Deployment 失败: {str(e)}"})


@tool
async def scale_deployment(namespace: str, name: str, replicas: int) -> str:
    """调整 Deployment 副本数（扩容 / 缩容）。

    Args:
        namespace: K8s namespace
        name: Deployment 名称
        replicas: 目标副本数（0-50）
    """
    if err := _check_namespace_access(namespace, write=True):
        return json.dumps({"error": err})

    if not (0 <= replicas <= 50):
        return json.dumps({"error": f"replicas={replicas} 不合法，允许范围 0-50"})

    _, apps_v1 = _get_k8s_client()
    if not apps_v1:
        return json.dumps({"error": "K8s 客户端未配置"})

    try:
        patch_body = {"spec": {"replicas": replicas}}
        apps_v1.patch_namespaced_deployment_scale(
            name=name, namespace=namespace, body=patch_body
        )
        return json.dumps({
            "namespace": namespace,
            "name": name,
            "action": "scale_deployment",
            "target_replicas": replicas,
            "message": f"Deployment {name} 目标副本数已设置为 {replicas}。",
        }, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": f"扩缩容失败: {str(e)}"})


@tool
async def rollback_deployment(namespace: str, name: str, revision: int = 0) -> str:
    """回滚 Deployment 到指定版本（revision=0 表示回滚到上一个版本）。

    Args:
        namespace: K8s namespace
        name: Deployment 名称
        revision: 目标 revision，0 表示上一版本
    """
    if err := _check_namespace_access(namespace, write=True):
        return json.dumps({"error": err})

    _, apps_v1 = _get_k8s_client()
    if not apps_v1:
        return json.dumps({"error": "K8s 客户端未配置"})

    try:
        # kubectl rollout undo 等效于 patch DeprecatedRollbackTo (v1beta1).
        # 对于 apps/v1，我们通过读取 rollout history 找到目标 revision 的
        # template hash，然后 patch spec.template 到该快照。
        # 简化实现：直接通过 deployment rollout 的 annotation 触发 undo。
        dep = apps_v1.read_namespaced_deployment(name=name, namespace=namespace)

        # 获取 revision history
        rs_list = apps_v1.list_namespaced_replica_set(
            namespace=namespace,
            label_selector=",".join(
                f"{k}={v}" for k, v in (dep.spec.selector.match_labels or {}).items()
            ),
        )

        # 按 revision annotation 排序，找目标 ReplicaSet
        annotated = []
        for rs in rs_list.items:
            rev_str = (rs.metadata.annotations or {}).get(
                "deployment.kubernetes.io/revision", "0"
            )
            try:
                rev = int(rev_str)
            except ValueError:
                rev = 0
            annotated.append((rev, rs))
        annotated.sort(key=lambda x: x[0])

        if len(annotated) < 2:
            return json.dumps({"error": "没有找到可回滚的历史版本"})

        if revision == 0:
            # 回滚到上一个 revision（当前最大 - 1）
            target_rs = annotated[-2][1]
            target_rev = annotated[-2][0]
        else:
            matches = [(r, rs) for r, rs in annotated if r == revision]
            if not matches:
                return json.dumps({"error": f"找不到 revision={revision} 的历史版本"})
            target_rev, target_rs = matches[0]

        # patch deployment template to target RS template
        patch_body = {
            "spec": {"template": target_rs.spec.template.to_dict()},
            "metadata": {
                "annotations": {
                    "deployment.kubernetes.io/revision": str(target_rev),
                }
            },
        }
        apps_v1.patch_namespaced_deployment(
            name=name, namespace=namespace, body=patch_body
        )
        return json.dumps({
            "namespace": namespace,
            "name": name,
            "action": "rollback_deployment",
            "target_revision": target_rev,
            "message": f"Deployment {name} 已触发回滚到 revision {target_rev}。",
        }, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": f"回滚失败: {str(e)}"})


k8s_tools = [
    get_pod_status,
    get_deployment_status,
    get_service_info,
    get_pod_logs,
    diagnose_pod,
    restart_deployment,
    scale_deployment,
    rollback_deployment,
]
