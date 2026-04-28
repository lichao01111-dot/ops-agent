# JARVIS 架构说明

> 目标读者：要读、改、扩展本项目代码的工程师。
> 读完你会知道：**是什么 / 为什么这么做 / 每个组件怎么实现 / 当前还缺什么**。
>
> 本文档经过一次外部架构评审（2026-04）后修订。评审中被指为"过度承诺"的表述已按实际代码回调；未实现的部分集中在 §10 已知短板与路线图。

---

## 1. 一句话说明

**JARVIS 是一个运维领域的 AI Agent。** 接收自然语言指令，自己选工具、执行动作、验证结果。

代码分两层：

- **`agent_kernel/`** — 通用底座（路由、计划、执行、审批、审计、记忆、工具注册）。不含运维语义。
- **`agent_ops/`** — 运维业务层（K8s / Jenkins / 日志工具 + 诊断 prompt + 审批策略）。

**定位**：骨架级生产框架，已完成主干流程；多个非功能性维度尚未落地（见 §10）。

---

## 2. 为什么这么架构

单体 ReAct Agent 跑不起来运维场景的根本原因：

| 痛点 | 单体 ReAct 的问题 | 我们的解法 |
|---|---|---|
| **副作用不可控** | LLM 一不小心就能重启生产 | **审批层**：工具声明 `side_effect=True`，必须过 `ApprovalPolicy` |
| **复合指令** | "先看日志再重启" — LLM 要么漏步要么幻觉 | **Planner**：确定性切分成 steps，一个 step 对应一个 executor |
| **路由不稳定** | 每次都让大模型判意图，贵且慢还漂移 | **规则 + 置信度 + LLM 回退**：高置信度走关键词，低置信度才调 LLM |
| **操作后不知死活** | 重启完说"完成"，其实 Pod 还在 CrashLoop | **VerificationExecutor**：MUTATION 之后自动追加轮询验证 step |
| **context 越滚越大** | 多轮对话把 pod/namespace 反复塞 prompt | **MemorySchema**：分层 + 每 item 独立 TTL |
| **不同领域要复用** | 今天运维、明天 DBA、后天 CI/CD | **Kernel / Vertical 分层**：kernel 不懂业务 |

**一句话**：用确定性代码卡住"路由 / 审批 / 验证 / 权限"这些不能出错的节点，LLM 只在它擅长的地方工作。

---

## 3. 整体分层

```
┌─────────────────────────────────────────────────────────────┐
│                      FastAPI (api/)                          │
│           /api/chat (SSE)   /api/approval   /api/knowledge   │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│             OpsAgent  (agent_ops/agent.py)                   │
│     继承 BaseAgent，装配 Router + Planner + 6 个 Executor    │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│         agent_kernel.BaseAgent  (LangGraph StateGraph)       │
│                                                              │
│   ┌────────┐   ┌─────────┐   ┌──────────────────────────┐   │
│   │ Router │──▶│ Planner │──▶│       Dispatcher         │   │
│   └────────┘   └─────────┘   └──────────┬───────────────┘   │
│                                          │                   │
│         ┌────────────┬──────────────┬────┴────┬──────────┐   │
│         ▼            ▼              ▼         ▼          ▼   │
│   [knowledge] [read_only_ops] [investigation][diagnosis]     │
│                                           [mutation][verify] │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│                  支撑设施 (agent_kernel/)                    │
│  SessionStore (InMemory；Redis 计划中)                      │
│  ApprovalPolicy  │  AuditLogger  │  MemorySchema             │
│  ToolRegistry (本地+MCP)  │  ToolInvoker (受限调用)         │
│  Middleware (timeout/retry/circuit/idem/cost/schema/metrics) │
└─────────────────────────────────────────────────────────────┘
```

---

## 4. Agent Kernel（底座）

> `agent_kernel/`，不含运维/DBA/CI 的任何概念。

### 4.1 BaseAgent — LangGraph 编排器

`agent_kernel/base_agent.py`。把路由 / 计划 / 执行器串成一张 StateGraph：

```python
class BaseAgent:
    def _build_graph(self, executors):
        g = StateGraph(AgentState)
        g.add_node("planner", self._planner_node)
        for name, ex in executors.items():
            g.add_node(name, ex.as_node())
        g.add_conditional_edges("planner", self._dispatcher, ...)
        return g.compile()
```

- `_dispatcher` 读 `state["plan"].current_step.route` 派发到对应 executor。
- 执行完回到 planner，Planner 决定"继续 / 结束"。

### 4.2 Router — 规则 + 置信度 + LLM 回退

`agent_kernel/router.py` 抽象；`agent_ops/router.py` 具体实现。

**策略**（评审后升级）：

```
keyword_rules  ─┐
                ├── confidence_score ──► 高 (≥0.6): 直接用
context_signals ┘                        低: 调 LLM 二次确认，取置信度更高者
                                         都不行: default_fallback
```

置信度打分（`IntentRouter._score_confidence`）：
- 只命中 1 个关键词类别 → `0.90`（无歧义）
- 命中 2+ 个类别 → `0.55`（触发 LLM）
- 只有 context 信号 → `0.55`（触发 LLM）
- 什么都没命中 → `0.30`（触发 LLM）

`RouteDecision` 带 `confidence: float` 和 `source: "keyword"|"llm"|"context"|"default_fallback"` 两个字段，便于调试和审计。

### 4.3 Planner — 拆分 + 自动补 step

`agent_kernel/planner.py` (kernel 抽象) + `agent_ops/planner.py` (Ops 实现)。

**总体哲学**：能用规则就用规则，LLM 只在规则不够智能时兜底。规则覆盖约 80% 常见请求，LLM 只接复杂自然语言。两条路径产出同一个 `PlanStep` schema，下游执行器看不出差异，且都受同一套**白名单 / 审批 / 自动验证** 安全约束。

#### `initial_plan(request)` 三层处理

```
                    用户消息
                       │
          ┌────────────▼────────────┐
          │  L1: 规则切分           │  agent_ops/planner.py:46-114
          │  split_compound_ops()   │
          │                         │
          │  ① 显式中文连接词正则:  │
          │     然后 / 接着 / 再 /  │
          │     并 / ,然后 / ，然后 │
          │  ② 基础设施日志双跳:    │
          │     "查 mysql 日志" →   │
          │     先查地址 + 再查日志 │
          │  上限 MAX_COMPOUND      │
          │  _SEGMENTS=3            │
          └────────────┬────────────┘
            切到 ≥2 段?
              │YES         │NO (1 段)
              ▼            ▼
        逐段调 router    ┌──────────────────────────┐
        构造 plan        │ L2: LLM 兜底             │
                         │ _initial_plan_with_llm() │ agent_ops/planner.py:138-192
                         │                          │
                         │ 触发条件白名单:          │
                         │  查出/找到/定位/根据/    │
                         │  相关/对应/地址/配置/    │
                         │  日志/异常/生产/prod/    │
                         │  mysql/redis/kafka/...   │
                         │                          │
                         │ Gemini structured output │
                         │ → PlanDraft (JSON)       │
                         │                          │
                         │ 严格校验:                │
                         │  - route 在 4 路由白名单 │
                         │  - mutation 强制 approval│
                         │  - 不一致回退 router 决策│
                         │  - <2 step 则放弃        │
                         └────────────┬─────────────┘
                            LLM 给 ≥2 step?
                       │YES                │NO / 失败
                       ▼                   ▼
                   构造 Plan       ┌──────────────────────┐
                                   │ L3: 单 step 兜底     │ agent_kernel/planner.py:93-120
                                   │ super().initial_plan │
                                   │ 走 router 单 step    │
                                   └──────────────────────┘
```

**关键约束**：LLM 输出**永不被信任**。即使 LLM 切出了 plan：
- `route` 必须在 `{knowledge, read_only_ops, diagnosis, mutation}` 白名单
- `mutation` 强制 `requires_approval=True`，LLM 没权限关审批
- LLM 选的 route 跟 router 算出来不一致 → 强制改回 router 的
- LLM 给的 prompt / version 透传到 Langfuse 便于追溯

LLM 在这里只能做**"拆分 + 排序 + 给 goal 文本"**，不能改任何安全约束。

#### `advance(plan, last_step)` — 完全规则

```python
if iterations >= max_iterations:    return FINISH   # 硬上限
if last_step.status == FAILED:      return FINISH   # 失败 fail-fast
if any step.status == PENDING:      return CONTINUE # 顺序执行
if _maybe_replan() != None:         return REPLAN   # 钩子追加 step
return FINISH
```

**没有 LLM 参与 advance 决策**。

#### `_maybe_replan` — Vertical 钩子，规则触发

`agent_ops/planner.py:249-280` 写死：mutation step **成功** + intent ∈ `{k8s_restart, k8s_scale, k8s_rollback, k8s_operate}` → 自动追加 `verification` step。

这是为什么重启 deployment 后 agent 会自动去轮询验证 —— 不是 LLM 临时决定，是写死的安全闭环。

#### 实测 4 个真实例子（2026-04 验证）

| 输入 | L1 切几段 | L2 触发? | 最终 | rationale |
|---|---|---|---|---|
| "查一下 default ns 的 pod" | 1 | ❌ | 1 step `read_only_ops` | `fast_path_keyword_routing` |
| "查 deployment，然后重启 order-service" | **2** (然后) | ❌ | 2 step: `read_only_ops` → `mutation` (approval=True) | `compound_request_split` |
| "查生产环境 mysql 的相关日志" | **2** (infra-log) | 不需要 | 2 step: `knowledge` → `read_only_ops` | `compound_request_split` |
| "找到 prod 数据库地址，并查它最近的异常日志" | 1 | ✅ | 2 step: `knowledge` → `read_only_ops` (Gemini 切的) | `llm_planner_fallback:...` |

最后一例是隐式依赖（"找到 X 并查它的 Y"），简单正则切不动 —— 必须语义理解。这是 L2 LLM 层的核心价值场景。

#### 几个故意不上 LLM 的地方

- **Router 主路径**：关键词 + 置信度阈值。命中明确关键词 → conf=0.9；多类别 → conf=0.55 触发 LLM 二次确认（但只确认意图，不改 plan 结构）
- **审批门**：纯 HMAC + receipt 校验
- **verification 轮询**：周期 / 次数都是配置常数

### 4.4 ExecutorBase

抽象基类。子类只管 `async def execute(state, event_callback) -> dict`，返回 `{"final_message", "tool_calls", "sources"}`。`as_node()` 把它接入 LangGraph。

### 4.5 SessionStore + MemorySchema + LayerPolicy

**两种实现**：
- `InMemorySessionStore`（单进程，默认，用于开发/测试）
- `RedisSessionStore`（持久化、跨进程共享，`agent_kernel/redis_session.py`）

两者接口一致，构造时可互换。Redis 后端 key 布局：
```
{ns}:{sid}:msgs            LIST[json]       对话历史
{ns}:{sid}:route           HASH             last_intent / last_route / ...
{ns}:{sid}:arts            LIST[json]       execution artifacts
{ns}:{sid}:mem:{layer}     HASH             memory items
{ns}:{sid}:mem:{layer}:exp ZSET             过期索引 (key → expire_ts)，compact O(log N)
```

**生命周期治理**（`agent_kernel/memory/lifecycle.py`，2026-04 评审后落地）：

每个 layer 有一个 `LayerPolicy`，默认值见 `DEFAULT_LAYER_POLICIES`：

| Layer | 默认 TTL | 合并策略 | 说明 |
|---|---|---|---|
| facts | 24h | REPLACE | 会话级事实 |
| observations | 1h | APPEND_LIST (max 50) | 事件流 |
| hypotheses | 2h | KEEP_HIGHER_CONFIDENCE | 同 key 保留高置信度 |
| plans | 30min | REJECT_IF_EXISTS | 一次变更只允许一个 plan |
| execution | 7d | APPEND_LIST (max 200) | 审计痕迹 |
| verification | 永久 | REPLACE | 最终结论 |

- **Dedup**：同一 `(key, value)` 在 `dedup_window_s` 内重写不更新 timestamp，避免续命。
- **Compact**：`store.compact(session_id)` 一次性清理所有过期项，Redis 侧用 ZSET 索引实现 O(log N)。
- **跨 session 隔离**：`store.clear_all_except(active_ids)` 防止陈旧 session 污染。

`MemoryLayer` 6 层 `FACTS / OBSERVATIONS / HYPOTHESES / PLANS / EXECUTION / VERIFICATION`；`MemorySchema.write_memory_item(writer, layer, ...)` 校验 `writer ∈ allowed_writers[layer]`。

### 4.6 ApprovalPolicy

`agent_kernel/approval.py` 抽象，`agent_ops/approval.py` 具体。返回 `APPROVE / REJECT / PENDING`。

- 低风险：直接 APPROVE。
- 高风险（restart/scale/rollback）：需要 `context["approval_receipt"]`（HMAC 签名），无则 PENDING。
- Pending → `ApprovalRequired` 异常 → 存 session + SSE 推 `approval_required` → 下轮带 receipt 续跑。

### 4.7 AuditLogger

`Sanitizer` 脱敏 + `Sink` 落地（Redis list / 文件），`structlog` 输出 JSON。

### 4.8 ToolRegistry + ToolInvoker + Middleware

**三层**（评审后加固）：

1. **ToolRegistry** (`agent_kernel/tools/registry.py`)：登记 `ToolSpec`。每个 ToolSpec 现在带：
   - `name / description / parameters_schema / tags / route_affinity / side_effect / source`（原有）
   - `reliability: ReliabilityPolicy`（新）— `timeout_s / retry / circuit_* / cost_ceiling_tokens / slo_p95_ms`
   - `schema_version`（新）— MCP 版本漂移检测

2. **ToolInvoker** (`agent_kernel/tools/invoker.py`，2026-04 评审后全面迁移)：每个 Executor 拿到的是**该 Executor 专属**的受限对象，不是裸 `_invoke_tool` 绑定方法。`OpsAgent.__init__` 用 `ToolInvoker.from_bound(...)` 给每个 Executor 分发独立实例，限定：
   - `caller="<executor>_executor"` — 审计标签，定位出错的 Executor
   - `allowed_routes=(...)` — 白名单（mutation_executor 只能调 `route=mutation` 的工具）
   - 拒绝未注册的 tool 名字
   - 过滤 `_ALLOWED_CALLER_KWARGS` 外的任何 kwarg（如 `bypass_approval=True`）
   - 保留 `__call__` 兼容旧签名 `await invoke_tool(name, args, event_callback, ...)`，Executor 代码零改动

   **单测**：`tests/test_tool_invoker.py`（11 用例），覆盖注册表 gate / route gate / kwarg 清洗 / 旧调用点兼容。

3. **Middleware** (`agent_kernel/tools/middleware.py`，2026-04 评审后全部落地)：调用链结构：

   ```
   Metrics → Idempotency → CostBudget → Circuit → Retry → SchemaVersion → Timeout → handler
   ```

   *Idempotency 放在 Cost / Circuit 之外，确保缓存命中不重复扣费、也不消耗熔断半开试探次数。*

   | 层 | 行为 | 关键安全规则 |
   |---|---|---|
   | **Timeout** | `asyncio.wait_for(spec.reliability.timeout_s)`，`None` 代表不限时 | 超时即 `ToolInvocationTimeout` |
   | **SchemaVersion** | 比对 `ctx.metadata["remote_schema_version"]` 与 `spec.schema_version`，**只告警不阻断** | 补丁版本漂移不影响调用 |
   | **Retry** | 指数退避 `backoff_base_s * backoff_factor^(attempt-1)` | **side_effect=True 且无 idempotency_key → 强制单次**；`retry_on_exceptions` 可白名单 |
   | **Idempotency** | 显式 key 直接用；side_effect 默认用 `sha256(sorted(args))` 派生 key；read-only 不缓存 | `(tool_name, session_id)` 作用域，不跨 session 串扰 |
   | **Circuit** | 连续 N 次失败后 open → 冷却期拒绝 → half-open 试探；成功即 reset | 进程级状态，跨 session 共享（故障通常对所有人） |
   | **CostBudget** | 从 session 账本扣 `cost_ceiling_tokens` | **只在成功时扣**，与下游 LLM 计费对齐 |
   | **Metrics** | structlog 打 `tool_invocation_metric`（含 `over_slo`、`attempt`） | 默认 in-process；子类化 `_record` 可对接 Prometheus/OTel |

   已在 `OpsAgent.__init__` 用 `build_default_chain()` 装配；`_invoke_tool` 以 `run_chain(middlewares, ctx, terminal)` 调用。Idempotency / CostBudget / Circuit 使用可拔插后端 Protocol（`IdempotencyCache` / `CostBudgetBackend` / `CircuitStateBackend`），默认进程内实现。

   **生产化后端**（`agent_kernel/tools/redis_middleware.py`）：
   - `RedisIdempotencyCache` — JSON 序列化 + namespace 隔离 + EXPIRE TTL
   - `RedisCostBudgetBackend` — `SET NX` 懒初始化 + `DECRBY` 原子扣减
   - `RedisCircuitStateBackend` — `HINCRBY` 原子计数 + `_PersistingCircuitState` 写穿 `opened_at`，跨副本共享
   - `build_redis_middleware_backends(redis_client)` 一次性返回三件套

   **可观测性**（`agent_kernel/tools/observability.py`）：`MetricsMiddleware(sink=...)` 发射 `MetricSample`，内置 sink：
   - `StructlogSink`（默认）— 打 `tool_invocation_metric` 事件
   - `MultiSink` — 扇出 + 单个 sink 失败不影响其他
   - `SloAlertSink` — `over_slo=True` 时发 `tool_slo_breach` 警告 + 可选回调
   - `PrometheusSink` — `tool_invocations_total` / `tool_invocation_duration_ms` / `tool_slo_breach_total`
   - `OTelTracingSink` — `start()/end()` 钩子写入 span 属性，失败时记 `StatusCode.ERROR`

   **单测**：`tests/test_tool_middleware.py`（27 用例）、`tests/test_redis_middleware.py`（11 用例 MiniRedis fake）、`tests/test_observability.py`（13 用例 sink 协议）。

---

## 5. JARVIS Vertical（运维业务层）

### 5.1 OpsAgent 装配

`agent_ops/agent.py` 只做拼装：

```python
class OpsAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            router=IntentRouter(),
            planner=OpsPlanner(),
            executors={
                "knowledge": KnowledgeExecutor(...),
                "read_only_ops": ReadOnlyOpsExecutor(...),
                "investigation": InvestigatorExecutor(...),
                "diagnosis": DiagnosisExecutor(...),
                "mutation": MutationExecutor(...),
                "verification": VerificationExecutor(...),
            },
            approval_policy=OpsApprovalPolicy(),
            audit=AuditLogger(...),
            session_store=InMemorySessionStore(...),  # Redis 待落地
        )
```

### 5.2 六个执行器

| Executor | 何时触发 | 动作 |
|---|---|---|
| **knowledge** | 问文档/SOP/环境 | 向量检索 + LLM 整理 |
| **read_only_ops** | 纯查询 | 直接调只读工具 + 总结 |
| **investigation** | 模糊描述 / 故障上下文 | 并发拉 K8s+日志+构建，写 OBSERVATIONS |
| **diagnosis** | "为什么/原因/排查" | symptom → hypothesis → evidence → score |
| **mutation** | 重启/扩缩容/回滚 | 审批 → 调工具 → 写 EXECUTION |
| **verification** | Planner 自动插入 | 轮询状态，失败时自动回滚 |

### 5.3 VerificationExecutor — 自动闭环

- 从 EXECUTION 层读上一步做了什么。
- `_poll_and_decide` 按 `MutationPlan.verification.max_attempts` × `poll_interval_s` 轮询。
- restart/rollback：看 Deployment `Available == Desired` 且无 Warning；默认 60s。
- scale：看实际 replicas 到目标；默认 90s。
- 失败时若工具在 `_VERIFICATION_AUTO_APPROVED_TOOLS` 中，**自带审批票**调 rollback，不弹窗。

### 5.4 工具清单（17 个）

`agent_ops/tools/`：K8s(7) / Jenkins(4) / Logs(3) / Knowledge(3)。每个注册时声明 `risk_level`，Approval 据此决策。

---

## 6. 一次请求的完整流转

`"order-service 挂了，帮我滚动重启"` 为例：

```
 1. POST /api/chat (SSE)
 2. OpsAgent.ainvoke → 加载 session → 填 state
 3. IntentRouter.route
      → 关键词只命中 RESTART_KEYWORDS → confidence=0.90
      → 直接返回 RouteDecision(route=MUTATION, source="keyword")
 4. OpsPlanner.build_initial_plan → 1 个 MUTATION step
 5. MutationExecutor
      → ApprovalPolicy.evaluate(restart_deployment, ...)
      → 无 receipt → ApprovalRequired → SSE 推 approval_required → 结束本轮
 6. 前端弹窗 → POST /api/approval → 拿 receipt
 7. 下一轮 /api/chat (带 receipt)
      → 审批通过 → ToolInvoker.invoke → TimeoutMiddleware 包住 → 调 restart_deployment
      → 写 MemoryLayer.EXECUTION → SSE 推 tool_call
 8. Planner.advance → _maybe_replan → 追加 VERIFICATION step
 9. VerificationExecutor 轮询 → 60s 内 ready → 写 VERIFICATION 层
10. final_message："已重启 order-service 并验证通过 (Ready 3/3)"
```

---

## 7. 几个关键设计决策（FAQ）

**Q1：为什么 Planner 不是 LLM？**
A：它做的是结构化决策（下一步走谁、要不要补 step）。规则够稳，只在"切分复合句"上保留 LLM 可选路径。

**Q2：为什么不用现成的 ReAct/AutoGPT？**
A：那些框架把"思考-行动-观察"包死在循环里，不给"审批必须阻塞"、"mutation 必须强制验证"这种切面钩子。LangGraph 图级别编排正好够用。

**Q3：memory 为何分层？**
A：权限 + 生命周期不同。observations 是本次调查快照（应短 TTL），facts 是会话全局（中等），verification 是最终结论（长期）。分层才能治理（§10 待补足）。

**Q4：MCP 和本地工具怎么共存？**
A：都走 `ToolRegistry → ToolInvoker → Middleware → handler`。区别只在 handler 类型。MCP 侧的非功能治理已统一收口在 `agent_kernel/tools/mcp_gateway.py`：`SecretProvider` 在 discovery / invoke 前注入 `Authorization`，token 缓存按 `REFRESH_LEAD_S=30s` 提前刷新、vault 故障时回退到缓存；`compute_schema_hash(parameters_schema, description)` 在每次 discover 时校验，漂移即打 `mcp_schema_drift_detected`；跨 server 同名工具打 `mcp_tool_name_collision`，由 14 个集成用例（`tests/test_mcp_gateway.py`，`InMemoryMCPTransport` fake）覆盖。

**Q5：要做"DBA Agent"怎么办？**
A：新建 `agent_dba/` 包，实现 `DbaRouter / DbaPlanner / 若干 DbaExecutor / DbaApprovalPolicy`，在 `agent_dba/agent.py` 拼装。`agent_kernel/` 一行不用改。**但这件事还没真做过，Kernel 通用性目前是强假设**（§10）。

**Q6：InvestigatorExecutor 能不能并入 Diagnosis？**
A：能，但会让 diagnosis prompt 又长又杂。拆出来：Investigation 并发拉事实（快），Diagnosis 只产假设（prompt 聚焦），结果复用。

---

## 8. 代码地图

| 想找… | 去看 |
|---|---|
| 图怎么编排 | `agent_kernel/base_agent.py` |
| 路由规则 + 置信度 + LLM 回退 | `agent_ops/router.py` (IntentRouter) |
| 复合指令拆分 + 自动补验证 | `agent_ops/planner.py` |
| 重启/扩容/回滚 | `agent_ops/executors/mutation.py` |
| 验证轮询 | `agent_ops/executors/verification.py` |
| 并发事实收集 | `agent_ops/executors/investigator.py` |
| 诊断四段式 | `agent_ops/executors/diagnosis.py` |
| 审批 | `agent_ops/approval.py` |
| 工具注册 | `agent_kernel/tools/registry.py` |
| 受限调用边界 | `agent_kernel/tools/invoker.py` |
| ToolInvoker 单测 | `tests/test_tool_invoker.py` |
| 超时/重试/熔断/幂等/成本/schema/metrics middleware | `agent_kernel/tools/middleware.py` |
| Middleware 单测 | `tests/test_tool_middleware.py` |
| Redis 后端（IdempotencyCache / CostBudget / Circuit） | `agent_kernel/tools/redis_middleware.py` |
| Redis 中间件单测 (MiniRedis fake) | `tests/test_redis_middleware.py` |
| Metrics sink (Structlog/Multi/SloAlert/Prometheus/OTel) | `agent_kernel/tools/observability.py` |
| Sink 单测 | `tests/test_observability.py` |
| MCP gateway (Secret/Schema-hash/Transport) | `agent_kernel/tools/mcp_gateway.py` |
| MCP 集成测 (InMemoryMCPTransport) | `tests/test_mcp_gateway.py` |
| Pydantic 模型 | `agent_kernel/schemas.py` |
| API | `api/routes.py` |
| 测试 | `tests/` (12 文件, 198 用例) |

---

## 9. 非功能性设计（NFR）

评审批评最集中的一节。以下是"每一项现状 + 未做什么"的诚实表：

| 维度 | 现状 | 未做 |
|---|---|---|
| **超时** | ✅ `TimeoutMiddleware`，按 `ToolSpec.reliability.timeout_s`（默认 30s） | 每工具默认值还没分类调 |
| **重试** | ✅ `RetryMiddleware`：指数退避 + 异常白名单 + **side_effect 无 key 时强制单次** | 需要按工具调默认 `max_attempts / backoff` |
| **熔断** | ✅ `CircuitBreakerMiddleware`：closed / open / half-open 三态，进程级状态 | 多副本部署时要换 Redis 共享状态 |
| **幂等** | ✅ `IdempotencyMiddleware`：显式 key + side_effect 派生 key，默认 5min TTL | 默认是进程内缓存，生产需 Redis 后端 |
| **成本预算** | ✅ `CostBudgetMiddleware`：session 级账本，只在成功时扣减 | 默认进程内账本，生产需 Redis |
| **SLO** | ✅ `SloAlertSink`：`over_slo=True` 时打 `tool_slo_breach` + 可选回调（webhook/PagerDuty 接入点） | 默认仍是观察+告警；自动降级/熔断未做 |
| **可观测性（工具层）** | ✅ `MetricsMiddleware(sink=...)`：默认 `StructlogSink`，可换 `PrometheusSink` / `OTelTracingSink` / `MultiSink` 扇出 | Prom/OTel 是 lazy import，部署时需装可选依赖 |
| **可观测性（请求/阶段/LLM 层）** | ⚠️ **未做**：请求级 trace、router/planner/executor 阶段 instrumentation、LLM 调用 input/output/token/cost 全部缺失 | 已出 RFC：`docs/langfuse-integration-rfc.md`（Langfuse 自托管，~9 工程日） |
| **鉴权** (MCP) | ✅ `SecretProvider` Protocol（`StaticSecretProvider` / `CallbackSecretProvider`）在 discover/invoke 前注入 `Authorization`；server 级静态 `auth_token` 作 fallback | 真实 Vault/IAM 适配器尚未编写 |
| **Token 轮转** (MCP) | ✅ `MCPClient` 缓存 secret，`REFRESH_LEAD_S=30s` 提前刷新；provider 失败时回退到缓存 | 未做后台异步刷新（懒触发） |
| **Schema 漂移** | ✅ `SchemaVersionMiddleware`（本地侧版本号）+ `compute_schema_hash` (MCP discovery 侧 SHA256)：漂移即打 `mcp_schema_drift_detected` | 仍是告警不阻断；未引入"严格模式"拒绝调用 |
| **Redis 后端** | ✅ `RedisIdempotencyCache` / `RedisCostBudgetBackend` / `RedisCircuitStateBackend` 跨副本共享状态 | 真实 Redis 集群下的 failover/重连测试未做 |
| **审计一致性** | AuditLogger 每步打点；Retry 内部失败也进 metric | **未做**：失败重试时的"归并 vs 各自打点"规则 |

---

## 10. 已知短板与路线图

| 项 | 现状 | 优先级 | 工作量 |
|---|---|---|---|
| ~~**SessionStore Redis 后端**~~ | ✅ **已实现** (`agent_kernel/redis_session.py`) | — | — |
| ~~**Memory 生命周期治理**~~ | ✅ **已实现** (`agent_kernel/memory/lifecycle.py`)：分层 TTL / dedup / 4 种合并策略 / compact / clear_all_except | — | — |
| ~~**Middleware 落地**（Retry / Idempotency / Circuit / Cost / Schema）~~ | ✅ **已实现**：7 个中间件全部落地并接入 `_invoke_tool`，27 单测 | — | — |
| ~~**ToolInvoker 全面迁移**~~ | ✅ **已完成**：`OpsAgent` 按 Executor 分发独立 invoker，绑定 `caller` + `allowed_routes`，11 单测 | — | — |
| ~~**MCP 鉴权链 / 真实集成测试**~~ | ✅ **已完成**：`SecretProvider` 注入链 + `REFRESH_LEAD_S` token 轮转 + `compute_schema_hash` 漂移检测 + `InMemoryMCPTransport` 14 集成单测（`tests/test_mcp_gateway.py`） | — | — |
| ~~**Middleware 后端生产化**（Idempotency/Cost/Circuit 切 Redis）~~ | ✅ **已实现** (`agent_kernel/tools/redis_middleware.py`)：三件套 Protocol 实现 + `build_redis_middleware_backends()` 一次性装配，11 MiniRedis 单测 | — | — |
| ~~**可观测性对接**（Prometheus/OTel sink；SLO 报警）~~ | ✅ **已实现** (`agent_kernel/tools/observability.py`)：`MetricsSink` Protocol + 5 种内置 sink（Structlog/Multi/SloAlert/Prometheus/OTel），13 单测 | — | — |
| **第二个 Vertical 验证 Kernel 通用性** | 只有 ops | P1（必做） | 1 周 |
| **Supervisor 多 Agent 协同** | 仅预留 `execution_target` 字段 | P2 | 2 周以上，需先有 2 个 vertical |
| **链路级可观测性（trace + LLM I/O + prompt mgmt）** | 工具层 sink 已就位，**请求/阶段/LLM 三层未 instrument**；prompt 散落源码 | P1 | ~9 工程日（详见 `docs/langfuse-integration-rfc.md`） |
| **真实 Redis / Vault / Prom 集成测试 / 混沌 / 并发竞态** | 当前用 MiniRedis / InMemoryMCPTransport fake，未跑真实后端；198 用例多为单测+e2e | P2 | 持续 |

**测试规模**：`tests/` 目前 12 个文件，**198 个用例**（较 2026-04 评审前 +108），涵盖：
- 内存/Redis 生命周期单测（`test_memory_lifecycle.py` 18 + `test_redis_session.py` 10）
- 工具中间件 7 层单测（`test_tool_middleware.py` 27）
- 受限调用边界单测（`test_tool_invoker.py` 11）
- Redis 中间件后端单测（`test_redis_middleware.py` 11，MiniRedis fake）
- 可观测性 sink 单测（`test_observability.py` 13）
- MCP gateway 集成测（`test_mcp_gateway.py` 14，`InMemoryMCPTransport` fake）
- 路由 / Planner / 审批 / 多假设诊断 e2e

长尾故障路径、并发竞态、真实 Redis/Vault/Prometheus 端到端压力测试仍未覆盖。

**结论**：P0（Redis 后端 + 生命周期治理）、P1（Middleware 全套 + ToolInvoker 全面迁移）、P2（MCP 鉴权治理 + Redis 后端生产化 + 可观测性对接）均已完成。离"成熟生产框架"剩：第二个 Vertical 验证 Kernel 通用性、真实后端集成压测。现状可描述为 **"非功能 envelope 全套落地（含 MCP 鉴权与可观测性）、最小权限边界上线、跨副本状态后端就绪、单 vertical 生产骨架"**。

---

## 11. 一页总结

- **两层结构**：`agent_kernel`（通用 Agent 骨架）+ `agent_ops`（运维知识）。
- **一张图**：LangGraph = Router → Planner → (6 executor) → 回到 Planner 直到 finish。
- **四个闸门**：Router（走哪条路 + 置信度 + LLM 回退）、Approval（能否动手）、Planner（强制补验证步）、Memory RBAC（能写哪层）。
- **一条调用链**：ToolRegistry → ToolInvoker（受限对象）→ Middleware（Metrics / Cost / Circuit / Idempotency / Retry / Schema / Timeout 七层）→ handler。
- **诚实边界**：Redis Session/Memory + Middleware 全套（含 Redis 跨副本后端）+ MCP 鉴权治理 + Prom/OTel sink 已落地（§4.5 / §4.8 / §9）；剩下的主要是第二个 Vertical 验证、真实后端压测（§10）。
- **扩展**：加工具 → `tools/`；加动作类型 → 新 Executor；做新领域 → 复制 `agent_ops` 为 `agent_xxx`（尚未验证）。

---

*修订说明：本版（2026-04）根据外部架构评审意见重写，重点回调 §4.5、§4.8、§9、§10 的表述与范围。版本基于 commit `6ba3307` 之后的代码。*
