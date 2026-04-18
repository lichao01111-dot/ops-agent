# OpsAgent Shared Memory 设计

> **定位说明**
>
> 本文档描述的是 Ops 垂直下的 memory 语义示例。
> Kernel 层只定义 `MemorySchema` / `MemoryBackend` / `SessionStore` 的接口和默认实现；
> 当前推荐契约以 [`architecture-v2.md`](./architecture-v2.md) 为准。

本文档定义 OpsAgent 的 shared memory schema 和 agent 权限矩阵。目标是让多个 agent / executor 可以共享高价值上下文，同时避免“推断污染事实”。

## 设计原则

1. 所有 agent 都可以读 shared memory。
2. 不是所有 agent 都能写所有 layer。
3. `facts`、`observations`、`hypotheses`、`plans`、`execution`、`verification` 必须分层。
4. 每次写入都必须带 `writer`、`source`、`confidence`。
5. `MemorySchema` 属于 Vertical，不同 Vertical 不共享同一套 layer 语义。
6. `SessionStore` / `MemoryBackend` 的实例生命周期由 Vertical 装配层管理，不能默认做成全局共享单例。

## Shared Memory Schema

```python
# 不建议依赖“可运行时扩展的 Python Enum”。
# 这里用可注册字符串 key 表达 layer 名称。
MemoryLayerKey = NewType("MemoryLayerKey", str)

OPS_MEMORY_LAYERS = {
    "facts",
    "observations",
    "hypotheses",
    "plans",
    "execution",
    "verification",
}
```

```python
@dataclass
class MemoryItem:
    key: str
    value: Any
    layer: MemoryLayerKey
    writer: str
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

这里的 writer identity 是 **Ops 垂直内部** 的身份命名，不是 Kernel 级固定枚举。

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

在 `architecture-v2.md` 的契约里，这些更准确地说是 `AgentIdentityKey` 的一组 Ops 实例值。

## 当前代码映射

当前实现尚未完全拆成独立 executor 文件，但已经开始映射：

- `knowledge` 路由 -> `knowledge_agent`
- `read_only_ops` 路由 -> `read_ops_agent`
- `diagnosis` 路由 -> `diagnosis_agent`
- `mutation` 的计划环节 -> `change_planner`
- `mutation` 的执行环节 -> `change_executor`

## Kernel / Vertical 边界

必须明确这几个职责边界：

- Kernel 定义 `MemorySchema`、`MemoryBackend`、`SessionStore` 的接口
- Kernel 可以提供 `InMemorySessionStore` / `RedisSessionStore` 这样的默认实现
- Ops 只定义自己的 layer 语义、writer 身份和写入规则
- `SessionStore` / `MemoryBackend` 的**实例**由 `create_ops_agent()` 这类装配函数创建

示意：

```python
OPS_MEMORY_SCHEMA = MemorySchema(layers={
    "facts": {"knowledge_agent"},
    "observations": {"read_ops_agent"},
    "hypotheses": {"diagnosis_agent"},
    "plans": {"change_planner"},
    "execution": {"change_executor"},
    "verification": {"verification_agent"},
})

def create_ops_agent() -> BaseAgent:
    session_store = RedisSessionStore(prefix="ops")
    memory_backend = RedisMemoryBackend(prefix="ops")
    return BaseAgent(
        ...,
        memory_schema=OPS_MEMORY_SCHEMA,
        session_store=session_store,
        memory_backend=memory_backend,
    )
```

这样可以避免多个 Vertical 误共享同一个 store 实例，导致 memory 串味。

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

传统键（单假设兼容）：

- `likely_root_cause`
- `diagnosis_summary`

多假设扩展（见 architecture-deep-dive §9.3）：

- `hypothesis:<id>` —— 每条 hypothesis 一条记录，value 为 `Hypothesis` 模型的 JSON / dict
- `top_hypothesis_id` —— 最终被选中的 hypothesis 的 id

多假设场景下 diagnosis executor 会：
1. 写 `hypothesis:<id>` 多条
2. 写 `top_hypothesis_id`
3. 写 `diagnosis_summary` 作为对外总结（保持向后兼容）

### plans

- `planned_action`
- `planned_target`
- `planned_namespace`
- `planned_step_id`
- `rollback_plan`

说明：

- `plans` layer 记录的是“将要执行的业务变更”
- 不是 Planner 在 LangGraph 里的内部 `Plan / PlanStep` 控制状态
- 如果需要把审批和计划绑定，建议记录 `planned_step_id`

### execution

- `execution_status`
- `executed_action`
- `executed_target`
- `executed_step_id`
- `approval_receipt_id`

说明：

- 对 side-effect tool，建议把 `approval_receipt_id` 一并落入 execution layer 或 artifact payload
- 这样 audit / replay / verification 才能知道“这次执行是基于哪张审批票据发生的”

### verification

- `verification_status`
- `verification_summary`

## 为什么需要 artifacts

除了 shared memory，还需要保留最近工具执行 artifacts：

```python
@dataclass
class ExecutionArtifact:
    route: str
    tool_name: str
    summary: str
    payload: dict[str, Any]
    timestamp: datetime
```

用途：

- 给 diagnosis 作为最近证据
- 给 verification 作为执行前后对比
- 给 postmortem 作为摘要原料

建议 artifact payload 至少保留：

- `tool_source` (`local` / `mcp`)
- `step_id`
- `execution_target`
- `approval_receipt_id`（如果是 side-effect tool）

## 实现顺序

1. `session.py` 增加分层 shared memory 和 artifacts
2. `knowledge` 写 `facts`
3. `read_only_ops` 读 `facts`，写 `observations`
4. `diagnosis` 读 `facts + observations + artifacts + topology`，写多条 `hypothesis:<id>` + `top_hypothesis_id` + `diagnosis_summary`
5. `mutation` 写 `plans / execution`
6. `verification` 写 `verification`
7. 增加 kernel contract tests，验证不同 Vertical 的 store 实例不会串数据

## 与 Planner 的配合

- Planner 不直接写任何业务 layer；它只读所有 layer，决定下一个 step。
- `plans` layer 只由 `change_planner` 写，记录"将要执行什么变更"，不是 "planner 规划了哪些 step"。
- Planner 的内部 plan 状态保存在 LangGraph 的 `AgentState.plan` 字段里，不进入 shared memory；原因：plan 是流程控制状态，不是业务事实。
- 如果进入 Supervisor 场景，shared memory 仍应保持 per-agent 隔离；跨 Agent 协同记忆应放在 Supervisor 自己的 memory schema 中。

## 与 Tool Registry 的配合

- 任何 tool 的执行（不论本地 / MCP）都通过 `_invoke_tool()` 写 artifacts。
- Tool 来源 (`local` / `mcp`) 作为 artifact payload 的一个字段保存，用于后续审计。
- side-effect tool 在写 artifact / execution layer 时，应该同时带上与 step 绑定的 `approval_receipt_id`。
