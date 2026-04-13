# OpsAgent Shared Memory 设计

本文档定义 OpsAgent 的 shared memory schema 和 agent 权限矩阵。目标是让多个 agent / executor 可以共享高价值上下文，同时避免“推断污染事实”。

## 设计原则

1. 所有 agent 都可以读 shared memory。
2. 不是所有 agent 都能写所有 layer。
3. `facts`、`observations`、`hypotheses`、`plans`、`execution`、`verification` 必须分层。
4. 每次写入都必须带 `writer`、`source`、`confidence`。

## Shared Memory Schema

```python
class MemoryLayer(str, Enum):
    FACTS = "facts"
    OBSERVATIONS = "observations"
    HYPOTHESES = "hypotheses"
    PLANS = "plans"
    EXECUTION = "execution"
    VERIFICATION = "verification"
```

```python
@dataclass
class MemoryItem:
    key: str
    value: Any
    layer: MemoryLayer
    writer: AgentIdentity
    source: str
    confidence: float
    timestamp: datetime
    ttl_seconds: int | None
```

```python
@dataclass
class SharedMemory:
    facts: dict[str, MemoryItem]
    observations: dict[str, MemoryItem]
    hypotheses: dict[str, MemoryItem]
    plans: dict[str, MemoryItem]
    execution: dict[str, MemoryItem]
    verification: dict[str, MemoryItem]
```

## Agent 权限矩阵

| Agent | 读取 | 可写 Layer | 说明 |
|------|------|------------|------|
| `router` | 全部 | 无 | 只做分流，不写业务事实 |
| `knowledge_agent` | 全部 | `facts` | 只把带来源的知识事实写入 |
| `read_ops_agent` | 全部 | `observations` | 写工具查询观察结果，不写结论 |
| `diagnosis_agent` | 全部 | `hypotheses` | 写诊断假设和结论草稿 |
| `change_planner` | 全部 | `plans` | 写变更计划和风险评估 |
| `change_executor` | 全部 | `execution` | 写执行结果和后置状态 |
| `verification_agent` | 全部 | `verification` | 写验证结果 |
| `system` | 全部 | 全部 | 保底维护能力，仅框架层使用 |

## 当前代码映射

当前实现尚未完全拆成独立 executor 文件，但已经开始映射：

- `knowledge` 路由 -> `knowledge_agent`
- `read_only_ops` 路由 -> `read_ops_agent`
- `diagnosis` 路由 -> `diagnosis_agent`
- `mutation` 的计划环节 -> `change_planner`
- `mutation` 的执行环节 -> `change_executor`

## 关键字段建议

建议优先维护这些 key：

### facts

- `env`
- `namespace`
- `service`
- `pod_name`
- `deployment_name`
- `job_name`
- `source_refs`

### observations

- `last_pod_status`
- `last_restart_count`
- `last_log_service`
- `last_error_summary`
- `last_build_result`

### hypotheses

- `likely_root_cause`
- `diagnosis_summary`

### plans

- `planned_action`
- `planned_target`
- `planned_namespace`
- `rollback_plan`

### execution

- `execution_status`
- `executed_action`
- `executed_target`

### verification

- `verification_status`
- `verification_summary`

## 为什么需要 artifacts

除了 shared memory，还需要保留最近工具执行 artifacts：

```python
@dataclass
class ExecutionArtifact:
    route: AgentRoute
    tool_name: str
    summary: str
    payload: dict[str, Any]
    timestamp: datetime
```

用途：

- 给 diagnosis 作为最近证据
- 给 verification 作为执行前后对比
- 给 postmortem 作为摘要原料

## 实现顺序

1. `session.py` 增加分层 shared memory 和 artifacts
2. `knowledge` 写 `facts`
3. `read_only_ops` 读 `facts`，写 `observations`
4. `diagnosis` 读 `facts + observations + artifacts`，写 `hypotheses`
5. `mutation` 写 `plans / execution`
6. `verification` 写 `verification`
