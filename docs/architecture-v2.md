# OpsAgent v2 架构设计：Agent Kernel + 垂直 Agent

> **本文档的定位已经改变**。
>
> 之前的 v2 版本把 OpsAgent 当作一个"带 Planner 的运维 Agent"来设计。现在我们意识到：**真正有价值的是把编排骨架抽成通用框架，再在其上构造不同垂直 Agent**。
>
> 所以本文档描述的不再是"一个更强的 OpsAgent"，而是：
>
> 1. **Agent Kernel**：领域无关的通用编排框架（Planner / ToolRegistry / MCP / StateGraph / Memory / Audit / Approval …）
> 2. **Vertical Agents**：Kernel 之上的若干垂直 Agent（当前第一个是 OpsAgent；未来可能加 CsmAgent / DataAgent / DocAgent …）
> 3. **Supervisor（未来）**：多 Agent 协同的上层调度

---

## 目录

- §1 为什么要分层
- §2 设计原则
- §3 整体分层（Kernel / Vertical / Supervisor）
- §4 Agent Kernel：职责与 API
- §5 Vertical Agent：OpsAgent（第一个垂直示例）
- §6 插件点（Plugin Points）
- §7 Supervisor 多 Agent 模式（演进方向）
- §8 数据契约与安全边界
- §9 从当前代码到新架构的迁移路径
- §10 失败路径与降级
- §11 反模式清单
- §12 参考

---

## 1. 为什么要分层

### 1.1 现状诊断

当前代码里真正和"运维领域"绑定的只占 ~30%：

```
领域无关（可复用）  ~70%                领域相关（Ops 专属）  ~30%
─────────────────────                   ─────────────────────
Planner / PlanStep / advance           tools/*.py (K8s/Jenkins/...)
StateGraph 编排                         config/topology.yaml
ToolRegistry + MCP Gateway              router.py 关键词映射
DiagnosisExecutor 的"多假设"模式         _extract_namespace / _pod / _service
6 层共享记忆 + RBAC                      _plan_read_only_tool
Approval + Audit + 脱敏                 _build_pipeline_plan
双入口 (chat / chat_stream)              _format_single_read_only_result
三级降级                                 _update_memory_from_tool_output
```

### 1.2 把这 70% 抽成框架的价值

- **横向扩展**：新垂直（客服 / 数据 / 文档 / HR）只写 30%
- **质量复用**：安全边界、审计、脱敏、降级这些"做对很难"的部分不再逐个 Agent 重写
- **可测试**：骨架单独测，每个垂直单独测，互不污染
- **可组合**：未来 Supervisor 把多个垂直 Agent 串起来解决跨域问题

### 1.3 为什么不做"一个全能 Agent"

这是最重要的反问：**我们不追求"加 tools 就变万能"**。原因：

| 问题 | 解释 |
|------|------|
| 路由退化 | 多领域关键词互相冲突，准确率断崖下跌 |
| 工具选错 | 单 Agent 工具数 > ~20 后 LLM 检索明显退化 |
| 安全边界失守 | "重启 Pod"、"发起转账"、"修改薪资"的审批流完全不同 |
| 记忆语义串味 | FACTS / OBSERVATIONS / HYPOTHESES 的分层只适合诊断-决策-执行；换到客服、数据场景不合适 |
| 诊断策略不通用 | 多假设打分的信号词（oom/crashloop）是 Ops 特有 |

**窄而深的垂直 Agent 是护城河；一个 Agent 吃天下是陷阱。**

---

## 2. 设计原则

1. **Kernel 零领域知识**：任何关键词、工具名、Schema、格式化函数都不许进 Kernel
2. **插件点显式声明**：Kernel 通过抽象基类（ABC）+ 依赖注入暴露扩展点
3. **契约先于实现**：Pydantic / TypedDict 定义 Plan / PlanStep / ToolSpec / MemoryItem，多个 Vertical 共享
4. **安全边界可配置但不可绕过**：RBAC 身份、Approval 规则、`side_effect` 隔离 Kernel 强制执行，Vertical 只能填配置不能削弱
5. **每个 Vertical 是独立实例**：各自的工具、路由、记忆语义；不共享运行时状态
6. **Supervisor 是"Agent 的 Planner"**：上层调度不关心下层怎么干，只发 sub-plan 给具名的子 Agent

---

## 3. 整体分层

```
┌────────────────────────────────────────────────────────────────────┐
│                                                                     │
│                    Supervisor  (演进阶段，§7)                        │
│                                                                     │
│             ┌────────────────────────────────────┐                 │
│             │  MetaPlanner                        │                 │
│             │   PlanStep.route = <agent_name>    │                 │
│             │   跨 Agent 的联合审批 / 审计         │                 │
│             └────────────────────────────────────┘                 │
│                      │                                              │
└──────────────────────┼──────────────────────────────────────────────┘
                       │  sub-plan
      ┌────────────────┼──────────────┬───────────────┐
      ↓                ↓              ↓               ↓
┌──────────┐    ┌──────────┐    ┌──────────┐   ┌──────────┐
│ OpsAgent │    │ CsmAgent │    │DataAgent │   │ DocAgent │       (各垂直 Agent)
│          │    │  (未来)   │    │  (未来)   │   │  (未来)   │
└────┬─────┘    └────┬─────┘    └────┬─────┘   └────┬─────┘
     └────────────────┴───────────────┴─────────────┘
                          ↓
      每个垂直 Agent = 装配在 Kernel 上的组件集合
                          ↓
┌─────────────────────────────────────────────────────────────────┐
│                                                                  │
│                 Agent Kernel  (通用骨架，§4)                      │
│                                                                  │
│    Planner | StateGraph | ToolRegistry | MCP Gateway            │
│    Memory  | Audit      | Approval     | Stream I/O             │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. Agent Kernel：职责与 API

### 4.1 Kernel 包含什么

| 组件 | 职责 | 源文件（目标） |
|------|------|----------------|
| `BaseAgent` | 装配 Planner + Router + Executors + Memory，暴露 `chat` / `chat_stream` | `agent_kernel/base_agent.py` |
| `Planner` | 生成 Plan、advance / replan、max_iterations 预算 | `agent_kernel/planner.py` |
| `ExecutorBase` | 执行器抽象基类，子类实现 `execute(state)` | `agent_kernel/executor.py` |
| `RouterBase` | 路由抽象基类，子类实现 `route(request) -> RouteDecision` | `agent_kernel/router.py` |
| `ToolRegistry` | 本地 + MCP 工具统一 ToolSpec + retrieve | `agent_kernel/tools/registry.py` |
| `MCPClient` | MCP 网关，register_server / load_tools | `agent_kernel/tools/mcp_gateway.py` |
| `MemoryBackend` | 共享记忆存储接口（内存 / Redis / DB） | `agent_kernel/memory/backend.py` |
| `MemorySchema` | 可配置的层 + writer 权限（不再写死 6 层） | `agent_kernel/memory/schema.py` |
| `AuditLogger` | 审计接口 + 参数脱敏 hook | `agent_kernel/audit.py` |
| `ApprovalPolicy` | 审批策略接口（可按风险等级 / 金额阈值 / 双人复核扩展） | `agent_kernel/approval.py` |
| `SessionStore` | 会话 + 消息历史 + 产物的存储接口与默认实现 | `agent_kernel/session.py` |

### 4.2 Kernel 的不变量（任何 Vertical 不可违反）

1. `side_effect=True` 的工具**只能**被 `requires_approval=True` 的 PlanStep 调用，且必须携带与该 step 绑定、可校验、未过期的 `approval_receipt`
2. 所有工具调用走 `_invoke_tool` 统一入口 → 必审计、必脱敏
3. 记忆层写入必须走 `MemorySchema` 的 RBAC 校验
4. `Plan.max_iterations` 是硬预算，超过立即 FINISH（防死循环）
5. 步骤 FAILED 默认 fail-fast（Vertical 可通过 `_maybe_replan` 覆写，但必须显式）

> `context.approved=true` 只能作为 transport 层的便捷输入，不能作为 Kernel 的最终安全判断条件。
> Kernel 只认 `ApprovalPolicy` 签发并验证通过的 `approval_receipt`，避免“旧审批复用到新动作”。

### 4.3 Kernel 的核心图

```
        ┌──────────────┐
        │  entry_point │
        └──────┬───────┘
               ↓
        ┌──────────────┐
        │   planner    │  ← BaseAgent._planner_node
        └──────┬───────┘  
               ↓ conditional_edges
    ┌─────┬────┼────┬─────┬────────┐
    ↓     ↓    ↓    ↓     ↓        ↓
  <exec1> <exec2> <exec3> ... <finish>   ← 动态的，由 Vertical 注册
    │     │    │    │     │
    └─────┴────┴────┴─────┘
               ↓
         回到 planner
```

**关键改动**：图的子节点**不再写死为** knowledge / read_only_ops / diagnosis / mutation，而是**由 Vertical 注册的 Executor 列表决定**。

```python
class BaseAgent:
    def __init__(self, *, planner, router, executors: list[ExecutorBase],
                 memory, audit, approval, registry):
        self.executors = {e.route: e for e in executors}
        self.graph = self._build_graph()

    def _build_graph(self):
        g = StateGraph(AgentState)
        g.add_node("planner", self._planner_node)
        for route, executor in self.executors.items():
            g.add_node(route.value, executor.as_node())
            g.add_edge(route.value, "planner")
        edges = {r.value: r.value for r in self.executors} | {"finish": END}
        g.add_conditional_edges("planner", self._dispatcher, edges)
        g.set_entry_point("planner")
        return g.compile()
```

### 4.4 Kernel 提供的通用 Schema

```python
# agent_kernel/schemas.py
class Plan(BaseModel): ...
class PlanStep(BaseModel): ...
class PlanDecision(str, Enum): CONTINUE / REPLAN / FINISH
class RouteDecision(BaseModel): ...
class ToolSpec(BaseModel): ...
class ToolCallEvent(BaseModel): ...
class RiskLevel(str, Enum): LOW / MEDIUM / HIGH

# 内建常量可以保留 Enum，但跨 Vertical / Supervisor 的扩展值
# 必须是“可注册字符串”，不能依赖运行时扩展 Python Enum。
RouteKey = NewType("RouteKey", str)         # e.g. "knowledge", "diagnosis", "agent:ops"
MemoryLayerKey = NewType("MemoryLayerKey", str)
AgentIdentityKey = NewType("AgentIdentityKey", str)

class RouteCatalog:
    RESERVED = {"finish"}
    BUILTIN = {"knowledge", "read_only_ops", "diagnosis", "mutation"}
    ...
```

**实现约束**：

- Kernel 可以提供一组内建 route / layer / identity 常量
- Vertical 和 Supervisor 的新增值通过注册表或校验器接入
- 不要写成“可运行时扩展的 Python Enum”，那会把 Pydantic 校验、序列化、插件加载全部搞复杂

---

## 5. Vertical Agent：OpsAgent（第一个示例）

### 5.1 OpsAgent 的组成

当前 OpsAgent 包含 **6 个执行器**，形成完整的调查→诊断→变更→校验闭环：

```
agent_ops/
├─ agent.py                 ← OpsAgent(BaseAgent) 装配入口
├─ planner.py               ← OpsPlanner：_maybe_replan（mutation 后自动追加 verification）
├─ router.py                ← IntentRouter(RouterBase)：关键词 + 上下文信号 + LLM fallback
├─ schemas.py               ← AgentRoute / IntentType / Hypothesis / ServiceNode …
├─ executors/
│   ├─ knowledge.py         ← KnowledgeExecutor(ExecutorBase)       route="knowledge"
│   ├─ read_only.py         ← ReadOnlyOpsExecutor(ExecutorBase)      route="read_only_ops"
│   ├─ investigator.py      ← InvestigatorExecutor(ExecutorBase) ★   route="investigation"
│   ├─ diagnosis.py         ← DiagnosisExecutor(ExecutorBase)    ★   route="diagnosis"
│   ├─ mutation.py          ← MutationExecutor(ExecutorBase)         route="mutation"
│   └─ verification.py      ← VerificationExecutor(ExecutorBase) ★   route="verification"
├─ mutation_plan.py         ← MutationPlan / VerificationCriteria / RollbackSpec + 工厂函数
├─ extractors.py            ← extract_namespace / extract_service_name / extract_pod_name
├─ formatters.py            ← 各路由响应格式化函数
├─ memory_hooks.py          ← store_mutation_plan / load_mutation_plan / write_verification_memory
├─ memory_schema.py         ← OPS_MEMORY_SCHEMA（6 层 + RBAC 写入权限）
├─ risk_policy.py           ← OpsApprovalPolicy（namespace 约束 + 回滚预授权）
├─ tool_setup.py            ← 注册 16 个 Ops 工具
└─ topology.py              ← ServiceTopology（Ops 特化）
```

★ 标注的是新增执行器，相比旧版（4 executors）的核心扩展点。

### 5.1.1 六个执行器职责速查

| 执行器 | route | 触发条件 | 核心行为 |
|--------|-------|----------|----------|
| `KnowledgeExecutor` | `knowledge` | 知识库 / SOP / 文档问答 | RAG-first，非 ReAct |
| `ReadOnlyOpsExecutor` | `read_only_ops` | 查询 Pod / 日志 / Jenkins | 确定性查询，非 ReAct |
| `InvestigatorExecutor` | `investigation` | 活跃告警 + 短消息 / `force_investigate` | asyncio.gather 5 工具并行，写 OBSERVATIONS |
| `DiagnosisExecutor` | `diagnosis` | 故障原因 / 根因分析 | 多假设生成 + 5 层症状采集 + 打分 |
| `MutationExecutor` | `mutation` | 重启 / 扩缩容 / 回滚 / 生成 | 构建 MutationPlan，审批，执行，写 PLANS |
| `VerificationExecutor` | `verification` | OpsPlanner 自动追加 | 轮询校验，失败自动回滚或升级 |

### 5.2 装配入口

```python
# agent_ops/agent.py
class OpsAgent(BaseAgent):
    def __init__(self, *, session_store, tool_registry, audit_logger, mcp_client=None):
        self.router = IntentRouter()
        self.planner = OpsPlanner(router=self.router)      # 含 _maybe_replan
        self.approval_policy = OpsApprovalPolicy()

        # 16 个工具注册到 ToolRegistry
        # tools/k8s_tool/: get_pod_status, get_deployment_status, get_k8s_events,
        #                   restart_deployment, scale_deployment, rollback_deployment,
        #                   get_pod_logs, get_service_status
        # tools/jenkins_tool/: query_jenkins_build, trigger_jenkins_build
        # tools/log_tool/: search_logs
        # tools/knowledge_tool/: query_knowledge, index_documents
        # 以及 diagnose_pod, get_namespace_summary, generate_jenkinsfile

        executors = [
            KnowledgeExecutor(invoke_tool=..., session_store=session_store),
            ReadOnlyOpsExecutor(invoke_tool=..., session_store=session_store),
            InvestigatorExecutor(invoke_tool=..., session_store=session_store),
            DiagnosisExecutor(invoke_tool=..., llm_provider=..., topology=..., ...),
            MutationExecutor(invoke_tool=..., session_store=session_store),
            VerificationExecutor(invoke_tool=..., session_store=session_store),
        ]

        super().__init__(
            planner=self.planner,
            session_store=session_store,
            audit_logger=audit_logger,
            executors=executors,
            approval_policy=self.approval_policy,
        )
```

### 5.2.1 Mutation 执行闭环

`MutationPlan` 是 mutation → verification 传递上下文的核心数据结构：

```python
# agent_ops/mutation_plan.py
@dataclass
class VerificationCriteria:
    tool: str                    # 校验工具（通常是 get_deployment_status）
    args: dict                   # 工具参数
    success_condition: str       # "ready_replicas >= N"
    poll_interval_s: int = 10    # 轮询间隔
    max_attempts: int = 6        # 最大轮询次数
    expected_replicas: int = 0

@dataclass
class RollbackSpec:
    tool: str                    # 回滚工具（rollback_deployment）
    args: dict
    escalation_message: str      # 回滚失败时的升级消息

@dataclass
class MutationPlan:
    action: MutationAction       # RESTART_DEPLOYMENT / SCALE_DEPLOYMENT / …
    target: str                  # Deployment 名称
    namespace: str
    tool_name: str
    tool_args: dict
    verification: VerificationCriteria | None
    rollback: RollbackSpec | None
    step_id: str = ""
    approval_receipt_id: str = ""
```

`OpsPlanner._maybe_replan()` 在 mutation step 成功后自动追加 verification step：

```python
# agent_ops/planner.py
_MUTATION_INTENTS_NEEDING_VERIFY = {"k8s_operate", "k8s_restart", "k8s_scale", "k8s_rollback"}

def _maybe_replan(self, plan, last_step):
    if last_step.route != "mutation": return None
    if last_step.status != PlanStepStatus.SUCCEEDED: return None
    if str(last_step.intent) not in _MUTATION_INTENTS_NEEDING_VERIFY: return None
    if any(s.route == "verification" for s in plan.steps): return None
    return PlanStep(route="verification", intent="verify_mutation", ...)
```

`OpsApprovalPolicy` 对回滚预授权（verification 阶段无需再次审批）：

```python
# agent_ops/risk_policy.py
_VERIFICATION_AUTO_APPROVED_TOOLS = frozenset({"rollback_deployment"})

def evaluate(self, *, tool_name, route, step, context):
    if str(route) == AgentRoute.VERIFICATION and tool_name in _VERIFICATION_AUTO_APPROVED_TOOLS:
        return ApprovalDecision(approved=True, reason="auto_rollback_pre_authorized_by_mutation_approval")
    return super().evaluate(...)
```

### 5.3 有限多 Agent 模式（InvestigatorExecutor）

OpsAgent 实现了"有限多 Agent"而不是全功能 Supervisor，原因见 §7 前的说明：

```
调查路由触发条件（满足任一）：
  ctx.force_investigate = True
  OR（ctx_has_incident AND 消息 ≤ 6 词 AND 没有明确 mutation/restart/rollback 意图）

InvestigatorExecutor（asyncio.gather 并行）：
  pod_status      → get_pod_status
  deployment_status → get_deployment_status
  k8s_events      → get_k8s_events (Warning 事件)
  error_logs      → search_logs (ERROR, 最近 1 小时)
  recent_build    → query_jenkins_build

写 OBSERVATIONS 层（供 DiagnosisExecutor 读取）：
  pod_name / last_pod_status / k8s_warning_events
  error_log_count / last_error_message
  last_build_result / last_build_number
```

这个模式覆盖 90% 的真实 on-call 场景，无需引入通用 Supervisor 的复杂性：
- 简单只读查询：跳过 investigator，直接 READ_ONLY_OPS
- 模糊/告警查询：investigator 先行，然后 diagnosis + mutation
- 已知变更：跳过 investigator

### 5.4 DiagnosisExecutor 仍然保留在 OpsAgent

**重要**：`DiagnosisExecutor` 的多假设并行模式虽然精妙，但它的**启发式打分**（"oom" / "crashloop" / "imagepullbackoff"）是 Ops 特有的，因此它属于 `agent_ops/`，不进 Kernel。

**Kernel 只提供抽象**：

```python
# agent_kernel/patterns/multi_hypothesis.py（可选的模式库）
class MultiHypothesisExecutor(ExecutorBase):
    """通用多假设并行执行器抽象。子类实现：
    - _collect_symptoms
    - _score_hypothesis  ← 领域特化打分
    - _summarize
    """
    ...
```

`agent_ops/executors/diagnosis.py` 继承它，填入 Ops 的症状采集、Ops 的打分规则。  
未来 `agent_csm/executors/complaint_diagnosis.py` 也可以继承它，填入客服的信号词（"退款"、"延迟"、"漏发"）。

### 5.5 Ops 的 MemorySchema

```python
# agent_ops/memory_schema.py
OPS_MEMORY_SCHEMA = MemorySchema(layers={
    #  层               允许写入的 AgentIdentity
    "facts":        {AgentIdentity.KNOWLEDGE},                          # 知识事实
    "observations": {AgentIdentity.READ_OPS, AgentIdentity.DIAGNOSIS}, # 工具观察
    "hypotheses":   {AgentIdentity.DIAGNOSIS},                         # 根因假设
    "plans":        {AgentIdentity.CHANGE_PLANNER},                    # MutationPlan
    "execution":    {AgentIdentity.CHANGE_EXECUTOR},                   # 执行结果
    "verification": {AgentIdentity.VERIFICATION},                      # 校验结论
})
```

InvestigatorExecutor 使用 `AgentIdentity.READ_OPS` 写 `observations` 层（与 ReadOnlyOpsExecutor 共享写权限），因此调查阶段采集的 pod_name / error_log_count 等事实可以被 DiagnosisExecutor 直接读取，无需重复工具调用。

其他垂直可以注册**完全不同的层**：

```python
# agent_csm/memory_schema.py
CSM_MEMORY_SCHEMA = MemorySchema(layers={
    "user_profile":    {"crm_reader"},
    "conversation":    {"dialogue"},
    "order_context":   {"crm_reader"},
    "escalation_plan": {"supervisor"},
})
```

Kernel 只管 `write_memory_item(layer, writer, ...)` 的 RBAC 校验，层的定义交给 Vertical。

### 5.6 Session / Memory 的实例归属

这是一个必须写清楚的边界：

- `SessionStore` 的**接口**属于 Kernel（`agent_kernel/session.py`）
- **默认实现**：`InMemorySessionStore`（测试 / 开发）和 `RedisSessionStore`（生产）都在 Kernel
  - `RedisSessionStore` 使用 key 前缀 `{prefix}:{session_id}:{messages|route|mem:{layer}|artifacts}` 隔离数据
  - 支持 TTL 管理（`_touch()` 刷新全部 session keys）和原子写入（Redis pipeline）
- 但**实例生命周期归 Vertical 装配层管理**，不能在 Kernel 放全局单例给多个 Vertical 共用

```python
# 生产环境：通过 REDIS_URL 环境变量自动选择
from agent_kernel.session_redis import create_redis_session_store

session_store = create_redis_session_store(prefix=”ops”)  # 若无 REDIS_URL 则 fallback 到内存

# agent_ops/agent.py 中注入到 OpsAgent
agent = OpsAgent(
    session_store=session_store,
    tool_registry=registry,
    audit_logger=audit_logger,
)
```

这样才能满足 §2 的”每个 Vertical 不共享运行时状态”，也避免 Supervisor / 子 Agent 之间的记忆串味。

---

## 6. 插件点（Plugin Points）

这是 Kernel 对外的所有扩展面。Vertical 就是"填这些槽位"。

| # | 插件点 | 基类 / 契约 | 说明 |
|---|--------|------------|------|
| 1 | **路由器** | `RouterBase.route(request) -> RouteDecision` | 意图识别。Vertical 可用关键词、规则、LLM |
| 2 | **执行器** | `ExecutorBase.execute(state) -> dict` | 每个 route 一个执行器 |
| 3 | **工具** | `@tool` + `ToolRegistry.register_local/_mcp` | `ToolSpec.tags / route_affinity / side_effect` |
| 4 | **MCP 服务器** | `MCPClient.register_server(name, url)` | 远程工具零代码接入 |
| 5 | **Planner 定制** | `Planner` 子类化 `_split_compound` / `_maybe_replan` | Vertical 可改拆分规则和重规划逻辑 |
| 6 | **记忆 Schema** | `MemorySchema(layers={...})` | 定义层、writer 权限 |
| 7 | **审批策略** | `ApprovalPolicy.evaluate(step, context) -> ApprovalDecision` | Ops 按 namespace，财务按金额，HR 按字段 |
| 8 | **审计扩展** | `AuditLogger.sanitize_params` / `.sinks` | 追加脱敏规则、写入 SIEM |
| 9 | **RBAC 身份** | `AgentIdentityKey` 可注册字符串契约 | Vertical 自己的 writer 身份 |
| 10 | **Executor 模式库**（可选） | `MultiHypothesisExecutor` / `ChainedReadExecutor` / `ApprovalGateExecutor` | 可选基类，给常见模式提供骨架 |

---

## 7. Supervisor 多 Agent 模式（演进方向）

### 7.1 什么时候启用 Supervisor

**不要在项目早期做 Supervisor**。先有 2–3 个稳定的 Vertical Agent，且确实出现跨域请求时再上。

典型触发场景：

```
"Q3 订单为什么下滑？"
   ├─ 数据 Agent：SQL 查询 + BI 看板
   ├─ 客服 Agent：投诉 / 退款增多的品类
   ├─ Ops Agent：有没有线上异常影响转化
   └─ Doc Agent：生成一页总结
```

### 7.2 Supervisor = "Agent 的 Planner"

Kernel 的 `Planner` / `PlanStep` **基本复用**，但 `PlanStep` 需要补一个 `execution_target` 字段来承载跨 Agent 派发语义：

```python
# 原 Vertical 内部
PlanStep.execution_target = "executor:diagnosis"   # 指向本 Agent 的 executor

# Supervisor 内部
PlanStep.execution_target = "agent:ops"            # 指向另一个 Agent
PlanStep.execution_target = "agent:data"
PlanStep.execution_target = "agent:csm"
```

这里不要继续复用 `PlanStep.route`。

- `route` 适合表示“Vertical 内部的执行器类别”
- `execution_target` 适合表示“这一步实际派发给谁”

如果继续让一个字段在不同层级承载两种语义，Planner / 审计 / 指标 / UI 都会被迫知道自己运行在哪一层。

### 7.3 结构

```
  ┌─────────────────────────────────────────┐
  │            SupervisorAgent              │
  │  (本身也是 BaseAgent 的子类化)            │
  │                                          │
  │  Router   = LLM 意图分派到某个子 Agent    │
  │  Executors = [AgentProxyExecutor × N]   │
  │              每个代理一个子 Agent          │
  │  Planner  = 跨 Agent 的编排              │
  │  Audit    = 聚合子 Agent 的 audit         │
  │                                          │
  └────────┬─────────────┬──────────┬───────┘
           ↓             ↓          ↓
    ┌──────────┐   ┌──────────┐  ┌──────────┐
    │ OpsAgent │   │ CsmAgent │  │DataAgent │
    │ (独立进程 │   │          │  │          │
    │  /独立实例)│  │          │  │          │
    └──────────┘   └──────────┘  └──────────┘
```

### 7.4 `AgentProxyExecutor`

```python
class AgentProxyExecutor(ExecutorBase):
    """把对子 Agent 的一次调用封装成一个 Step。"""
    def __init__(self, *, agent_name: str, client: AgentClient):
        self.route = f"agent:{agent_name}"
        self.client = client

    async def execute(self, state):
        goal = state["plan"].current_step().goal
        child_response = await self.client.chat(ChatRequest(
            message=goal,
            user_id=state["user_id"],
            user_role=state["user_role"],
            context=state["context"],
        ))
        return {
            "final_message": child_response.message,
            "tool_calls": child_response.tool_calls,
            "sources": child_response.sources,
        }
```

### 7.5 Supervisor 的跨 Agent 安全约束

- 子 Agent 的 `requires_approval=True` 不被上层绕过：Supervisor 转发时把 `needs_approval` 升到 Supervisor 层，并透传 / 校验该 step 对应的 `approval_receipt`
- 每个子 Agent 的 audit 独立落盘，同时 Supervisor 自己也落一条"meta audit"
- 子 Agent 的 Memory 不共享（避免串味）；Supervisor 有自己的"协同记忆层"

---

## 8. 数据契约与安全边界

### 8.1 跨 Kernel / Vertical 的核心契约

```python
# 由 Kernel 定义，所有 Vertical 共享
Plan / PlanStep / PlanDecision / PlanStepStatus
ChatRequest / ChatResponse / ToolCallEvent
RouteDecision / RiskLevel / ToolSpec / ToolSource
AgentState (TypedDict)

# 由 Kernel 定义为“可注册字符串契约”，Vertical 填内容
RouteKey           ← Ops: "knowledge" / "read_only_ops" / "diagnosis" / "mutation"
                   ← Csm: "query" / "reply" / "escalate"
MemoryLayerKey     ← Ops: "facts" / "observations" / ...
                   ← Csm: "user_profile" / "conversation" / ...
AgentIdentityKey   ← 每个 Vertical 自己的 writer 身份

# 由 Vertical 自行定义（不进 Kernel）
Hypothesis (Ops 诊断专属)
ServiceNode (Ops 拓扑专属)
OrderContext (Csm 专属)
...
```

### 8.2 安全边界：Kernel 强制 + Vertical 填配置

| 边界 | Kernel 强制 | Vertical 填什么 |
|------|-------------|------------------|
| 工具副作用隔离 | `side_effect=True` 只能在 MUTATION-like 路由 + approval | 标注每个工具的 `side_effect` |
| RBAC 身份 | `MemorySchema` 拦截非法 writer | 定义有哪些 writer、写哪些层 |
| 审批 | `ApprovalPolicy.evaluate(step, context)` 强制调用，并验证 `approval_receipt` | 实现具体判定（namespace / 风险等级）；可扩展 verification 路由的回滚预授权 |
| 审计 | 每次工具调用必落 audit | 扩展脱敏规则 |
| 迭代预算 | `Plan.max_iterations` 硬上限 | 可调节数值，不能取消 |
| FAILED fail-fast | 默认终止 | 可通过 `_maybe_replan` 显式覆写（OpsPlanner 用此追加 verification） |

### 8.3 一张图看清"谁拦谁"

```
  请求 ─┬─▶ Router           (Vertical)
        │
        ├─▶ Planner           (Kernel)
        │    │
        │    └─ max_iterations / FAILED fail-fast  (Kernel 硬约束)
        │
        ├─▶ Dispatcher        (Kernel)
        │
        ├─▶ Executor.execute  (Vertical)
        │    │
        │    ├─ _invoke_tool  (Kernel)
        │    │   ├─ side_effect 隔离       (Kernel 硬约束)
        │    │   ├─ ApprovalPolicy 查询    (Vertical 填策略)
        │    │   ├─ approval_receipt 校验  (Kernel 硬约束)
        │    │   ├─ audit + 脱敏           (Kernel 调用 + Vertical 扩展)
        │    │   └─ 工具调用（LOCAL / MCP）
        │    │
        │    └─ write_memory_item
        │        └─ MemorySchema RBAC      (Vertical 定义 + Kernel 校验)
        │
        └─▶ 回到 Planner
```

---

## 9. 当前实现状态与演进路径

### 9.1 已完成（当前代码）

架构迁移已完成，以下功能均已落地并通过 84 个测试：

| 功能 | 状态 | 位置 |
|------|------|------|
| Kernel + Vertical 分层 | ✅ 完成 | `agent_kernel/` + `agent_ops/` |
| 6 Executor 动态 wiring | ✅ 完成 | `OpsAgent.__init__` → `BaseAgent._build_graph` |
| Mutation 执行闭环 | ✅ 完成 | `mutation_plan.py` + `MutationExecutor` + `OpsPlanner._maybe_replan` |
| VerificationExecutor | ✅ 完成 | `agent_ops/executors/verification.py` |
| InvestigatorExecutor | ✅ 完成 | `agent_ops/executors/investigator.py` |
| Redis 持久化 session | ✅ 完成 | `agent_kernel/session_redis.py` |
| 完整审批状态机 | ✅ 完成 | `agent_kernel/approval.py` + `OpsApprovalPolicy` |
| 回滚预授权 | ✅ 完成 | `OpsApprovalPolicy._VERIFICATION_AUTO_APPROVED_TOOLS` |
| K8s 写操作工具（restart/scale/rollback） | ✅ 完成 | `tools/k8s_tool/` |
| 上下文感知路由 | ✅ 完成 | `IntentRouter`（ctx_has_incident / ctx_has_mutation_target） |
| MemorySchema RBAC | ✅ 完成 | `agent_kernel/memory/schema.py` + `OPS_MEMORY_SCHEMA` |
| Kernel 契约测试 | ✅ 完成 | `tests/kernel_contract/` |

### 9.2 演进方向

**下一步（框架扩展）**：

- **第二个 Vertical**：DocAgent 或 JiraAgent，验证 Kernel 真正通用（目标：只写 ~30% Ops 特化代码）
- **MCP + tool retrieval**：替换静态 16 工具列表，工具数增加不影响路由准确率
- **Router 升级为 meta-planner**：支持图内回跳、混合意图的 multi-step 规划

**远期（Supervisor）**：

只有当 ≥ 2 个 Vertical 都在稳定运行、且出现明确的跨域场景时再上 Supervisor 多 Agent 模式。不要提前做。

---

## 10. 失败路径与降级

三级降级不变，但职责归属明确：

| 级别 | 触发 | 归属 | 行为 |
|------|------|------|------|
| L1 | 单 Executor 抛异常 | Kernel `_run_step` | `PlanStepStatus.FAILED` → fail-fast FINISH |
| L2 | Executor 内部部分能力不可用（如 Ops 诊断 LLM 挂了） | Vertical（Executor 自己） | 降级输出 + 标低 confidence 写 memory |
| L3 | 审批收据非法 / 过期 / step 不匹配 | Kernel `_invoke_tool` | 拒绝执行 side-effect tool + audit（success=False） |
| L4 | 整个 `chat()` 异常 | Kernel `BaseAgent.chat` | 兜底错误响应 + audit（success=False） |

**Supervisor 额外多一级**：

| L5 | 子 Agent 不可达 / 超时 | Supervisor | AgentProxyExecutor 抛 SubAgentUnavailable → fail-fast 或降级到备选 Agent |

---

## 11. 反模式清单

迁移过程中要主动避免的诱惑：

| 反模式 | 为什么错 | 正解 |
|--------|---------|------|
| 把 `ServiceTopology` 放进 Kernel | 不是所有 Vertical 都需要拓扑 | 留在 `agent_ops/`，DiagnosisExecutor 自己引用 |
| 把 `Hypothesis` schema 进 Kernel | 客服、数据场景不是多假设诊断 | 留在 `agent_ops/schemas.py` |
| Kernel 内置一个"万能 LLM Router" | 各领域路由策略差异大，做成万能就啥都做不好 | Kernel 只给 `RouterBase`，Vertical 自己实现 |
| 共享 MemoryBackend / SessionStore 实例给多个 Vertical | 跨 Vertical 语义串味 | 每个 Vertical 自己装配自己的 SessionStore + MemoryBackend 实例 |
| Supervisor 里直接塞多个 Vertical 的工具列表 | 又退化成"全能 Agent"了 | Supervisor 的 Executor 只能是 AgentProxy，不能是具体 Tool |
| 在 Kernel 里做 Ops-flavor 的关键词 split | 中文拆分规则是 Ops 特化的（"然后重启/回滚"） | `_split_compound` 做成 Vertical 可覆写的 Planner 方法 |
| 把 `context.approved=true` 当作最终审批依据 | 不能绑定 step，无法表达审批人/有效期/范围 | Kernel 只认可校验的 `approval_receipt` |
| 让 `PlanStep.route` 同时表示“executor 类别”和“目标 Agent” | 字段语义漂移，通用代码难消费 | 拆成 `route` 与 `execution_target` 两个字段 |
| 把 `ROUTE_PROMPTS` 的文案放 Kernel | 领域腔调 | Vertical 自带 prompt 模板 |

---

## 12. 参考

- `architecture-deep-dive.md` §1–§7：上一代 Route-first 架构细节
- `architecture-deep-dive.md` §8：v1 弱点分析（本次重构的起点）
- `shared-memory-design.md`：6 层共享记忆的契约（将演化为 MemorySchema 的 Ops 实例）
- `tests/test_agent.py`：Planner / ToolRegistry / Topology / DiagnosisMemory 的单元测试（迁移后需按新包路径调整 import）

---

## 附录 A：一页速查

```
┌────────────────────────────────────────────────────────────────┐
│                                                                │
│  做什么                    放哪里           对外接口             │
│  ─────────                 ──────           ────────             │
│  Planner 推进              Kernel           Plan / advance      │
│  图编排                    Kernel           BaseAgent._build_graph│
│  工具统一注册              Kernel           ToolRegistry         │
│  MCP 接入                  Kernel           MCPClient            │
│  审计 / 脱敏               Kernel           AuditLogger          │
│  审批策略接口              Kernel           ApprovalPolicy       │
│  记忆 Schema 接口          Kernel           MemorySchema         │
│  会话存储接口              Kernel           SessionStore         │
│                                                                │
│  Ops 路由（关键词+上下文）   Vertical (Ops)   IntentRouter        │
│  K8s/Jenkins/Logs 工具     Vertical (Ops)   @tool + register    │
│  Ops 执行器 × 6            Vertical (Ops)   *Executor(ExecBase) │
│    investigation           Vertical (Ops)   InvestigatorExecutor│
│    knowledge               Vertical (Ops)   KnowledgeExecutor   │
│    read_only_ops           Vertical (Ops)   ReadOnlyOpsExecutor │
│    diagnosis               Vertical (Ops)   DiagnosisExecutor   │
│    mutation                Vertical (Ops)   MutationExecutor    │
│    verification            Vertical (Ops)   VerificationExecutor│
│  MutationPlan（变更闭环）   Vertical (Ops)   mutation_plan.py    │
│  服务拓扑                  Vertical (Ops)   ServiceTopology     │
│  Ops 提取 / 格式化          Vertical (Ops)   extractors/formatters│
│  Ops 记忆层定义            Vertical (Ops)   OPS_MEMORY_SCHEMA    │
│  Ops 审批规则 + 回滚预授权  Vertical (Ops)   OpsApprovalPolicy    │
│  Session/Memory 实例装配    Vertical (Ops)   OpsAgent.__init__   │
│                                                                │
│  跨 Agent 调度              Supervisor       MetaPlanner         │
│  子 Agent 代理              Supervisor       AgentProxyExecutor  │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

---

## 附录 B：各版本差异速查

| 维度 | 旧版 v2（设计文档初稿） | 当前实现（已落地） |
|------|------------------------|-------------------|
| 定位 | 描述一个”更强的 OpsAgent” | Kernel + Vertical 分层框架，已实现 |
| Executor 数量 | 4（knowledge / read / diagnosis / mutation） | 6（+investigation / +verification） |
| Mutation 执行 | 计划 + 审批骨架 | 完整闭环：计划→审批→执行→自动追加校验→自动回滚 |
| K8s 写操作工具 | 计划中，未实现 | 已实现：restart / scale / rollback / get_k8s_events（共 16 工具） |
| Session 持久化 | 概念描述（RedisSessionStore 示例） | 已实现：`agent_kernel/session_redis.py` |
| Approval 状态机 | 骨架 | 完整：receipt 绑定 step + 有效期 + 回滚预授权 |
| 多 Agent 模式 | Supervisor（演进方向） | 有限多 Agent（Investigator + Executor/Verifier） |
| 工具数量 | 12 | 16（+restart_deployment / +scale_deployment / +rollback_deployment / +get_k8s_events） |
| 路由信号 | 纯关键词 | 关键词 + 上下文信号（ctx_has_incident / ctx_has_mutation_target） + LLM fallback |
| `MemoryLayer` | 固定 Enum | Vertical 的 MemorySchema 定义，RBAC 校验 |
