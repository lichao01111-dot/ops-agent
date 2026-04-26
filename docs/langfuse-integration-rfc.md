# RFC: Langfuse 接入设计

> **Status**: Draft for review
> **Author**: agent-kernel team
> **Date**: 2026-04-25
> **Scope**: 把 Langfuse self-hosted 作为 trace + LLM observability + prompt mgmt 主后端接入 ops-agent，**不替换现有 Prom/structlog/Audit 栈**。
> **Non-goals**: LangSmith feature parity 100%（playground、annotation queue 等留待 Phase 2）。

---

## 1. 背景与动机

### 1.1 当前可观测性现状

| 层 | 现状 |
|---|---|
| 工具层 | ✅ `MetricsMiddleware` + 5 种 sink (Structlog/Multi/SloAlert/Prometheus/OTel) |
| 审计层 | ✅ `AuditLogger` + Sanitizer + Redis/file sink |
| 短文本日志 | ✅ structlog 散落事件 |
| **请求层端到端** | ❌ 没有 |
| **Agent 阶段层（router/planner/executor）** | ❌ 没有 |
| **LLM 调用层（input/output/token/cost）** | ❌ 完全裸奔 |
| **Prompt 版本管理** | ❌ 散在源码 |
| **Eval / 回归数据集** | ❌ 没有 |

### 1.2 为什么选 Langfuse

- License 干净（MIT），自托管完整
- 数据模型 `trace → span → generation` 与我们 `BaseAgent → Executor → Tool/LLM` 一一对应
- 不依赖 LangChain，不会被绑死
- Prompt 管理 + eval 是真正的 LangSmith 替代级别
- 可作为新增 sink 接入，不破坏现有 Prom/OTel/structlog 栈

### 1.3 替代方案比较（决策依据）

| 选项 | License | Agent flow | Prompt mgmt | Eval | 选/否的原因 |
|---|---|---|---|---|---|
| **Langfuse** | MIT | ⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐ | ✅ 选定：综合最贴近 LangSmith |
| Phoenix | ELv2 | ⭐⭐ | ⭐ | ⭐⭐⭐ (RAG强) | M2 可作为 dev 阶段 RAG eval 工具补充 |
| Helicone | Apache | ⭐ | ⭐⭐ | ⭐ | 仅 LLM cost，agent flow 弱 |
| Lunary | Apache | ⭐⭐⭐ | ⭐⭐ | ⭐⭐ | 功能与 Langfuse 重叠，社区慢一档 |
| Laminar | Apache | ⭐⭐⭐ | ⭐⭐ | ⭐⭐ | 较新，社区小 |
| OpenLLMetry+SigNoz | Apache | ⭐⭐⭐ | — | — | 不是 LangSmith 替代品，缺 prompt/eval |

---

## 2. 设计原则（钉死再谈细节）

1. **正交，不替换**：Prom 管 metric/告警；Audit 管合规审计；structlog 管短文本日志；**Langfuse 管 trace + LLM I/O + prompt + eval**。每个东西只负责自己最擅长的事。
2. **不绑死 Langfuse**：所有接入走我们已有的 `MetricsSink` Protocol 或新增的 `StageObservabilitySink` Protocol。任何时候可以摘掉换成 Phoenix/Lunary，不用改 executor 代码。
3. **零影响降级**：Langfuse 后端宕机、网络抖动 → agent 该跑还是跑。所有 Sink 调用必须 try/except + 异步 flush。
4. **PII / 合规一等公民**：复用 `agent_kernel/audit.py` 的 `Sanitizer` 链，对 prompt + completion 做脱敏后再上送。
5. **灰度可控**：通过 `settings.langfuse.sample_rate` 控制采样比例；按 `vertical` 维度可独立开关。

---

## 3. 数据模型映射

### 3.1 Langfuse 的世界观

```
Trace             # 一次完整业务请求（顶层）
 ├── Observation  # 抽象基类，三种子类型：
 │   ├── Span        # 任意时长的逻辑段
 │   ├── Generation  # LLM 调用（input/output/usage/model）
 │   └── Event       # 时间点事件
 ├── Score        # 对 Trace 或 Observation 的评分（用户反馈/eval）
 └── Session      # 多个 Trace 的逻辑分组
```

### 3.2 我们的世界观 → Langfuse 映射表

| 我们的概念 | Langfuse 对象 | 关键字段映射 |
|---|---|---|
| 一次 `OpsAgent.ainvoke()` | `Trace` | `id=request_id`、`name="agent_chat"`、`session_id`、`user_id`、`metadata={vertical, route, intent, conversation_id}`、`input=user_message`、`output=final_message` |
| `Router.route()` | `Span` (kind=`router`) | `name="router"`、`metadata={source, confidence, matched_keywords}`、output=`RouteDecision` |
| `Planner.build_initial_plan()` / `advance()` | `Span` (kind=`planner`) | `metadata={steps_emitted, replan_reason, compound_segments}` |
| 一个 `Executor.execute()` 跑完 | `Span` (kind=`executor`) | `name="executor:{name}"`、`metadata={tool_calls_count, memory_writes}`、output=`final_message` |
| 一次 Tool 调用（中间件链全过完） | `Span` (kind=`tool`) | `name="tool:{tool_name}"`、`metadata={attempt, idempotency_key, over_slo}`、`level=ERROR` if fail |
| 一次 LLM 调用 | **`Generation`** | `model`、`model_parameters`、`input=messages`、`output=completion`、`usage={input/output/total tokens}`、`cost`（可让 Langfuse 自动算） |
| `ApprovalPolicy.evaluate()` | `Event` | `name="approval_decision"`、`metadata={decision, action, risk_level}` |
| `MemorySchema.write_memory_item()` | （**不上报**，量太大） | 转入 Prom counter；trace 太密 |
| 用户在前端点 thumbs up/down | `Score` | `name="user_feedback"`、`value=1.0/-1.0`、`comment` |

### 3.3 Trace 树的形状（最终可视化效果）

```
Trace: agent_chat  [trace_id=abc123, session=sess-42, user=u-7, vertical=ops]
 │  input:  "order-service 挂了，帮我滚动重启"
 │  output: "已重启 order-service 并验证通过 (Ready 3/3)"
 │  duration: 8.4s
 │
 ├── Span: router                                  [120ms, source=keyword, conf=0.90]
 │
 ├── Span: planner.build_initial_plan              [5ms, steps=1]
 │
 ├── Span: executor:mutation                       [3.2s]
 │   ├── Event: approval_decision                  [pending → approved via receipt]
 │   └── Span: tool:restart_deployment             [3.0s, attempt=1, ok]
 │
 ├── Span: planner.advance                         [3ms, replan=mutation_needs_verify]
 │
 └── Span: executor:verification                   [5.1s]
     └── Span: tool:get_deployment_status          [poll x3, total 5.0s, ok]
         (each poll = inner Span)
```

诊断类请求会多一层：

```
Span: executor:diagnosis
 ├── Span: tool:get_logs                        [800ms]
 ├── Span: tool:get_events                      [650ms]
 ├── Generation: hypothesis_generation          [model=claude-sonnet, in=4.2k tok, out=1.1k tok, $0.03]
 │   prompt-version: diagnosis_hypothesis_gen@v3  ← 这就是 prompt 灰度的钩子
 └── Span: hypothesis_scoring                   [120ms]
```

### 3.4 ID / 关系约定

- `trace_id = uuid4()` 在 `BaseAgent.ainvoke()` 入口生成
- 通过 `contextvars.ContextVar("trace_id")` 传递（structlog 已经用这套了，自然贯通）
- 子 span 的 `parent_observation_id` 由 Langfuse SDK 的 context manager 自动推断
- `session_id` 直接用我们已有的 SessionStore session_id
- `Generation.id`、`Span.id` 不主动指定，让 SDK 生成

---

## 4. LangfuseSink 接口设计

### 4.1 文件位置与职责

新增 `agent_kernel/observability/langfuse_sink.py`（**不放在 `tools/observability.py`**，因为它跨工具/阶段/LLM 三层职责，已经超出 tool sink 的范围）。

同时：把 `agent_kernel/tools/observability.py` 里通用的 `MetricSample`、`MetricsSink` Protocol 上移到 `agent_kernel/observability/__init__.py`，老路径保留 re-export 不破坏 backward compat。

### 4.2 增强的 sink Protocol（向后兼容扩展）

```python
# agent_kernel/observability/__init__.py
class MetricsSink(Protocol):
    def record(self, sample: MetricSample) -> None: ...

class StageObservabilitySink(Protocol):
    """Optional: sinks that understand multi-stage agent flow.

    Sinks that DON'T implement this (PrometheusSink/StructlogSink) only get
    record() at the leaf tool layer, which is fine for them.
    """
    def trace_start(self, ctx: TraceContext) -> Any: ...
    def trace_end(self, handle: Any, output: Any, error: Exception | None) -> None: ...
    def stage_start(self, parent: Any, ctx: StageContext) -> Any: ...
    def stage_end(self, handle: Any, output: Any, error: Exception | None) -> None: ...
    def llm_start(self, parent: Any, ctx: LLMContext) -> Any: ...
    def llm_end(self, handle: Any, output: LLMOutput, error: Exception | None) -> None: ...
    def event(self, parent: Any, name: str, metadata: dict) -> None: ...
```

新增 dataclass：

```python
@dataclass(frozen=True)
class TraceContext:
    trace_id: str
    name: str               # "agent_chat"
    session_id: str
    user_id: str
    vertical: str           # "ops" | "budget" | ...
    input: str              # user message
    metadata: dict[str, Any]

@dataclass(frozen=True)
class StageContext:
    stage_kind: str         # "router" | "planner" | "executor" | "tool" | ...
    name: str               # "executor:mutation"
    route: str
    metadata: dict[str, Any]

@dataclass(frozen=True)
class LLMContext:
    purpose: str            # "router_fallback" | "hypothesis_gen" | ...
    model: str              # "claude-sonnet-4-5"
    model_parameters: dict  # temperature, max_tokens, ...
    input_messages: list[dict]
    prompt_name: str | None     # 链到 Langfuse prompt registry
    prompt_version: int | None
    metadata: dict[str, Any]

@dataclass(frozen=True)
class LLMOutput:
    completion: str
    input_tokens: int
    output_tokens: int
    finish_reason: str | None
```

### 4.3 LangfuseSink 实现（骨架）

```python
# agent_kernel/observability/langfuse_sink.py
class LangfuseSink:
    """Implements StageObservabilitySink + MetricsSink."""

    def __init__(self, *, public_key, secret_key, host,
                 sample_rate: float = 1.0,
                 sanitizer: Sanitizer | None = None,
                 release: str | None = None,
                 enabled_verticals: set[str] | None = None):
        from langfuse import Langfuse  # lazy
        self._client = Langfuse(public_key=..., secret_key=..., host=...)
        self._sample_rate = sample_rate
        self._sanitizer = sanitizer or NoopSanitizer()
        self._enabled = enabled_verticals
        self._release = release  # git sha for prompt-version pinning

    def trace_start(self, ctx: TraceContext):
        if not self._sampled(ctx): return None
        return self._client.trace(
            id=ctx.trace_id,
            name=ctx.name,
            session_id=ctx.session_id,
            user_id=ctx.user_id,
            input=self._sanitizer.scrub(ctx.input),
            metadata={**ctx.metadata, "vertical": ctx.vertical},
            release=self._release,
        )

    def trace_end(self, handle, output, error):
        if handle is None: return
        try:
            handle.update(
                output=self._sanitizer.scrub(output) if output else None,
                level="ERROR" if error else "DEFAULT",
                status_message=str(error) if error else None,
            )
        except Exception as exc:
            logger.warning("langfuse_trace_end_failed", error=str(exc))

    def stage_start(self, parent, ctx: StageContext):
        if parent is None: return None
        return parent.span(name=ctx.name, metadata=ctx.metadata)

    # stage_end / llm_start / llm_end / event 同形

    def record(self, sample: MetricSample):
        # Used when the tool sink call goes through MultiSink. We DON'T
        # double-emit a span here (the stage hooks already did) — keep
        # this as a no-op or only emit "summary" event.
        return None
```

**关键决策**：

- `record()` 是 no-op。span 由 `stage_start/end` 在 middleware 层驱动，避免重复上报
- 所有 Langfuse SDK 调用都包 try/except，**永不向上抛**
- `sample_rate` < 1.0 时按 trace 整体决定（trace 内所有 span 同进退，避免半截 trace）
- `enabled_verticals = {"ops"}` 时，budget vertical 完全静默

### 4.4 与现有 sink 的并存

```python
# 装配处（agent_ops/agent.py 或新的 observability bootstrap）
sink = MultiSink(children=[
    PrometheusSink(),
    StructlogSink(),
    SloAlertSink(alert_callback=alert_to_pagerduty),
    LangfuseSink(public_key=..., secret_key=..., host=..., sample_rate=0.1),
])
```

`MultiSink` 现有逻辑就支持——`record()` 走广播；`stage_start/end` 等新方法 MultiSink 也要广播一遍（小改动 ~30 行）。

---

## 5. LLM Wrapper 设计

### 5.1 现状回顾

`llm_gateway/__init__.py` 提供 `LLMGateway` 单例，返回 LangChain `BaseChatModel`。所有 executor 拿到的就是裸 `ChatModel`。已确认两个 LLM 调用点：

- `agent_ops/router.py:384` — router LLM fallback
- `agent_ops/executors/diagnosis.py:339` — hypothesis generation

未来还会有 knowledge summarizer、planner LLM 切分等。

### 5.2 包装策略：装饰 LangChain 模型

新增 `llm_gateway/observed.py`：

```python
class ObservedChatModel:
    """Wraps a BaseChatModel and emits Langfuse Generation per call.

    Implemented via duck-typing on `.ainvoke(messages, config=None)` — we
    don't subclass BaseChatModel because LangChain's class hierarchy is
    deep and brittle. Anyone who needs the raw model still calls
    `.unwrap()`.
    """
    def __init__(self, inner: BaseChatModel, *,
                 model_name: str,
                 purpose: str,
                 sink: StageObservabilitySink | None,
                 model_pricing: ModelPricing | None = None):
        self._inner = inner
        self._model = model_name
        self._purpose = purpose
        self._sink = sink
        self._pricing = model_pricing

    async def ainvoke(self, messages, *, prompt_meta: PromptMeta | None = None, **kw):
        parent = current_observation_handle.get()  # contextvar
        ctx = LLMContext(
            purpose=self._purpose,
            model=self._model,
            model_parameters={"temperature": getattr(self._inner, "temperature", None), ...},
            input_messages=_normalise_messages(messages),
            prompt_name=prompt_meta.name if prompt_meta else None,
            prompt_version=prompt_meta.version if prompt_meta else None,
            metadata={},
        )
        handle = self._sink.llm_start(parent, ctx) if self._sink else None
        t0 = time.monotonic()
        err = None
        try:
            resp = await self._inner.ainvoke(messages, **kw)
            return resp
        except Exception as e:
            err = e
            raise
        finally:
            if handle is not None:
                output = LLMOutput(
                    completion=str(resp.content) if not err else "",
                    input_tokens=resp.usage_metadata["input_tokens"] if not err else 0,
                    output_tokens=resp.usage_metadata["output_tokens"] if not err else 0,
                    finish_reason=resp.response_metadata.get("finish_reason"),
                )
                self._sink.llm_end(handle, output, err)
            # Also emit Prometheus
            _emit_llm_metric(self._model, self._purpose, err, time.monotonic() - t0,
                             output.input_tokens if not err else 0, ...)

    def with_structured_output(self, schema):
        return ObservedChatModel(self._inner.with_structured_output(schema), ...)

    def __getattr__(self, name):
        return getattr(self._inner, name)
```

### 5.3 LLMGateway 改造

```python
class LLMGateway:
    def __init__(self, sink: StageObservabilitySink | None = None):
        self._sink = sink
        ...

    def get_main_model(self) -> ObservedChatModel:
        return ObservedChatModel(self._models["main"],
                                 model_name=settings.llm_model,
                                 purpose="main",
                                 sink=self._sink,
                                 model_pricing=PRICING.get(settings.llm_model))
```

调用方零改动（接口兼容 LangChain `ainvoke`）。

### 5.4 Prometheus 同步喂

LLM wrapper 同时打 Prom（不依赖 Langfuse）：

```
agent_llm_calls_total{model, purpose, outcome}
agent_llm_duration_ms{model, purpose}
agent_llm_input_tokens_total{model, purpose}
agent_llm_output_tokens_total{model, purpose}
agent_llm_cost_usd_total{model, purpose}
```

新增 `agent_kernel/observability/llm_pricing.py`：

```python
@dataclass(frozen=True)
class ModelPricing:
    input_per_1k: Decimal
    output_per_1k: Decimal

PRICING: dict[str, ModelPricing] = {
    "claude-sonnet-4-5": ModelPricing(Decimal("0.003"), Decimal("0.015")),
    "claude-haiku-4-5":  ModelPricing(Decimal("0.001"), Decimal("0.005")),
    "gpt-4o":            ModelPricing(Decimal("0.0025"), Decimal("0.010")),
    # ... 维护一份 hardcode 表，季度更新
}
```

**为什么不全靠 Langfuse 算成本**：Langfuse 也算，但我们的告警链路在 Prom，必须本地算一份。两边可以对账。

---

## 6. Stage Instrumentation 落地点

`BaseAgent` 和 executor 的钩子位置：

| 文件 | 位置 | 改动 |
|---|---|---|
| `agent_kernel/base_agent.py:230` `ainvoke()` | 入口 | `with sink.trace(...) as trace_handle:` 包整个 ainvoke |
| `agent_kernel/base_agent.py` `_planner_node` | planner 调用前后 | `sink.stage_start(parent, StageContext(stage_kind="planner", ...))` |
| `agent_kernel/base_agent.py` `_dispatcher` | 派发到 executor 前 | 不打点（dispatcher 是路由不是工作） |
| `agent_kernel/executor.py` `as_node()` | executor 入口 | 装饰器统一打 `executor:{name}` span |
| `agent_kernel/tools/middleware.py` MetricsMiddleware | 已有 start/end，扩成 `tool` span 的形式 | 调 `sink.stage_start(parent, StageContext(stage_kind="tool", ...))` 而非 `sink.start()` |
| `agent_ops/router.py` `IntentRouter.route()` | 入口 | 同样 `sink.stage_start(..., stage_kind="router")` |

`current_observation_handle: ContextVar` 在 `agent_kernel/observability/_context.py` 定义，stage_start 时 push、stage_end 时 pop。子 span 通过它找到 parent。

**改动量估算**：~150 行新增 + 6 处 ~5 行装饰器改动。

---

## 7. Prompt 抽取清单

### 7.1 当前所有 prompt 的位置（通过 grep 实测）

| 文件 | 行 | Prompt 名（建议） | 用途 | 当前形式 |
|---|---|---|---|---|
| `agent_ops/router.py` | ~370-385 | `ops/router/intent_classification` | 路由 LLM 回退 | f-string 拼接 |
| `agent_ops/executors/diagnosis.py` | ~321 | `ops/diagnosis/hypothesis_generation` | 假设生成 | f-string 拼接 |

只有 2 个。**这是好事**——抽取负担小，可以一次干净。

### 7.2 命名规范

```
{vertical}/{stage}/{purpose}@{version}
例：
  ops/router/intent_classification@v3
  ops/diagnosis/hypothesis_generation@v7
  budget/variance/explain@v1
```

`@version` 在 Langfuse 用 `label`（如 `production` / `staging`）做语义版本，代码里写 `label="production"` 自动取生产灰度版本。

### 7.3 抽取流程

1. **Phase 1（手动迁移）**

   - 在 Langfuse UI 或通过 SDK 把现有 2 个 prompt 录入，version=1，label=production
   - 改代码：

     ```python
     # 旧
     prompt = f"You are an ops router..."
     # 新
     prompt_obj = langfuse.get_prompt("ops/router/intent_classification", label="production")
     prompt = prompt_obj.compile(user_message=request.message)
     # 调用时把 prompt_obj.name + prompt_obj.version 传给 LLM wrapper
     resp = await llm.ainvoke(messages, prompt_meta=PromptMeta.from_obj(prompt_obj))
     ```

2. **Phase 2（fallback 机制）**

   - Langfuse 取不到 prompt 时回退到代码里的 `_FALLBACK_PROMPT` 常量
   - 这是必须的：避免 Langfuse 故障导致 agent 不可用

### 7.4 Prompt 模板的两套配置

- 代码里保留 `_FALLBACK_PROMPT` 常量 + 单测覆盖（保证逻辑兜底）
- Langfuse 里是"权威版"，可以热更新、灰度

测试里 `langfuse.get_prompt()` mock 成抛 `LangfuseUnavailable` → 走 fallback。

---

## 8. 灰度方案（最关键、最容易出事）

### 8.1 三层开关

```yaml
# config/settings.py
langfuse:
  enabled: true                     # 总开关
  host: "https://langfuse.internal"
  sample_rate: 0.10                  # 整体采样率（trace 维度）
  enabled_verticals: ["ops"]         # 哪些 vertical 上报
  prompt_management:
    enabled: false                   # ⚠️ 默认 OFF，谨慎打开
    fallback_on_error: true          # Langfuse 拿不到 prompt 时用代码里的
  llm_observation:
    enabled: true                    # LLM I/O 上报开关
    redact_input: false              # 是否脱敏 prompt 全文
    redact_output: false
```

### 8.2 灰度阶段（4 步走）

| Phase | 时长 | 启用范围 | 验收 |
|---|---|---|---|
| **P0 影子模式** | 3 天 | 仅 staging，sample_rate=1.0，prompt_management=false | Trace 树形状正确；Langfuse 后端存活；agent 性能无回归（P95 +<5ms） |
| **P1 生产采样** | 1 周 | 生产，sample_rate=0.05，仅 ops vertical | 日成本曲线和 Prom 对账误差 <2%；无 PII 泄漏抽查通过 |
| **P2 全量 trace** | 1 周 | sample_rate=1.0，仍不开 prompt_management | 存储增长符合预期；查询体验流畅 |
| **P3 Prompt 管理** | 持续 | prompt_management=true，2 个 prompt 接入 | 灰度切版本（label=staging vs production）走通 |

### 8.3 杀手开关（kill switch）

- 配置改为 `langfuse.enabled=false`，下一次请求生效（contextvars 立即失效）
- LangfuseSink 自带"连续 N 次上报失败 → 自动 disable 5 分钟"的本地熔断（复用 `CircuitBreakerMiddleware` 的状态机抽象）

### 8.4 PII 防护

- 接入 `agent_kernel/audit.py` 的 `Sanitizer` 链：**LangfuseSink 构造时强制传 sanitizer**，没有就拒绝启动
- 默认脱敏字段：
  - `password` / `token` / `api_key` / `secret` / `Authorization` 头
  - 邮箱、手机号、身份证号（regex）
  - K8s secret 类型资源的 data 字段
- 用户可在 settings 里追加自定义正则

---

## 9. 配置 / Secrets / 部署

### 9.1 Secrets

- `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` 走 K8s Secret，**不进 Git**
- 复用现有 SecretProvider 抽象（MCP gateway 已有）注入

### 9.2 Langfuse 自托管最小拓扑

- Langfuse Web (Next.js) × 1
- Postgres × 1（元数据）
- ClickHouse × 1（trace 数据，**关键性能依赖**）
- Redis × 1（队列）
- 一个独立的 namespace，存储 retention 配置 90 天默认

### 9.3 网络

- agent → Langfuse 走内网，HTTPS
- LangfuseSink 用异步队列 + batch flush（Langfuse SDK 默认就是这样的）
- 出站流量需要在 NetworkPolicy 里允许

---

## 10. 测试计划

| 测试 | 文件 | 覆盖 |
|---|---|---|
| `LangfuseSink` 单测 | `tests/test_langfuse_sink.py` | trace_start/end happy path、error 不抛、sample_rate 拒绝、sanitizer 真的脱敏 |
| `LangfuseSink` 故障注入 | 同上 | 客户端抛异常 → agent 不受影响 |
| `ObservedChatModel` 单测 | `tests/test_observed_llm.py` | input/output 上报、token 提取、prompt_meta 透传、Prom 同步打点 |
| Stage instrumentation e2e | `tests/test_observability_e2e.py` | 跑一个 mock chat → 断言 trace 树形状（用 Langfuse 的 fake server） |
| Prompt fallback | `tests/test_prompt_registry.py` | Langfuse 拿不到 prompt → 用代码 fallback；版本不存在 → 报错 |
| 中间件链回归 | 现有 27 用例 | LangfuseSink 加入 MultiSink 后，全部 27 用例仍通过 |
| 性能回归 | bench script | 加入 LangfuseSink 后，e2e P95 增加 <10ms |

---

## 11. 工作分解 + 工作量

| Task | 文件 | LOC | 工时 |
|---|---|---|---|
| T1: `agent_kernel/observability/` 模块骨架（Protocol、ContextVar、dataclass）| 新建 4 文件 | ~250 | 0.5 天 |
| T2: `LangfuseSink` 实现 | `langfuse_sink.py` | ~300 | 1 天 |
| T3: `MultiSink` 扩展支持 stage hooks | `tools/observability.py` | +50 | 0.5 天 |
| T4: `ObservedChatModel` + LLMGateway 改造 | `llm_gateway/observed.py`、`llm_gateway/__init__.py` | ~250 | 1 天 |
| T5: `llm_pricing.py` 单价表 | 新建 | ~80 | 0.5 天 |
| T6: Stage instrumentation（base_agent + executor as_node + router）| 改 5 文件 | +200 | 1.5 天 |
| T7: MetricsMiddleware 改用 stage hooks | `tools/middleware.py` | ±80 | 0.5 天 |
| T8: Prompt 抽取（2 个）+ `PromptMeta` 数据流 | router、diagnosis、新建 `prompt_registry.py` | ~200 | 1 天 |
| T9: Sanitizer 接入 + PII 测试 | 复用 audit + 新增 patterns | ~100 | 0.5 天 |
| T10: 配置 + Settings + 灰度开关 | `config/settings.py` | +60 | 0.5 天 |
| T11: 单测 + e2e 测试 | 5 个新测试文件 | ~600 | 1.5 天 |
| T12: 文档（运维手册 + observability conventions）| `docs/observability.md` | — | 0.5 天 |

**总计：~9 工程日**，1 人 2 周内能交付到 P1（生产采样）状态。P3（prompt 管理）再加 1 周观察。

---

## 12. 风险登记

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| Langfuse 上报阻塞主线程 | 中 | 高 | SDK 已是异步 batch，但要在启动时 smoke test 验证；本地熔断兜底 |
| ClickHouse 撑不住 trace 量 | 低 | 高 | sample_rate 控制；retention 早设；扩盘脚本备好 |
| Prompt 热更新引起线上行为漂移 | 中 | 中 | 必须走 label 灰度（staging → 10% → 100%）；有 review approval 流程 |
| PII 漏脱敏 | 中 | 极高 | Sanitizer 强制 + 抽样人工 review + 单测覆盖常见 pattern |
| Langfuse 版本升级破坏 SDK 兼容 | 中 | 中 | 锁定版本（pin in pyproject）；升级走 staging 一周 |
| Token 计数 LangChain 没给 | 低 | 中 | LLM wrapper 容错 + fallback 到 tiktoken 估算 |

---

## 13. Open Questions

1. **trace_id 是否暴露到 final_message？** 用户报问题时直接给 trace_id，运维一键打开 Langfuse —— 强烈建议是。
2. **多 vertical 共享一个 Langfuse 项目还是各一个？** 倾向各一个 project，同一个 Langfuse 实例。便于权限隔离 + 看板分开。
3. **Prompt review 流程谁来管？** 是不是要加一个 GitOps 化的 prompt 仓库（YAML），CI 同步到 Langfuse？还是纯靠 Langfuse UI？我倾向 GitOps，可追溯、可 code review、可 rollback。
4. **Score / 用户反馈是否 M1 做？** 需要前端配合（thumbs up/down 按钮 + 调 `/api/feedback` 端点回写 Langfuse score）。建议 M2，单独排期。
5. **Eval 怎么做？** Langfuse 的 eval 功能要"先有 dataset"。dataset 从哪来？建议从生产 trace 采样 100 条 → 人工标注 → 作为 baseline，每周用它跑一次回归。也是 M2。

---

## 14. 验收标准（Definition of Done）

- [ ] LangfuseSink 单测全过；agent 全量 198 个回归测试零回归
- [ ] Staging 跑 3 天，trace 树形状人工 review 通过
- [ ] 生产 sample_rate=0.05 跑 1 周，LLM cost 与 Prom 数据误差 <2%
- [ ] Sanitizer 抽样 100 条 trace，PII 漏出 0 条
- [ ] 文档 `docs/observability.md` 含：架构图 / 开关 / 排障手册 / PII 配置
- [ ] 杀手开关 + 本地熔断验证通过（混沌测试：stub Langfuse 返回 500，连续 1min，agent 性能不退化）

---

## 15. 一句话总结

**用 ~9 工程日把 Langfuse 接进现有的 sink/middleware 架构，得到与 LangSmith 等价的 trace + LLM 可观测性 + prompt 管理能力，不替换 Prom/Audit 任何现有职责，全程灰度可控、随时可关。**

---

## 附录 A：与现有架构的关系图

```
┌──────────────────────────────────────────────────────────┐
│  FastAPI /api/chat                                        │
│   ↓ trace_id 生成 + 注入 contextvars                      │
├──────────────────────────────────────────────────────────┤
│  OpsAgent.ainvoke()         ◄──── LangfuseSink.trace_start│
│   ├ Router.route()          ◄──── stage_start/end (router)│
│   ├ Planner.build/advance() ◄──── stage_start/end(planner)│
│   ├ Executor.execute()      ◄──── stage_start/end(executor)│
│   │   ├ ToolInvoker (per-executor)                        │
│   │   │   ↓                                               │
│   │   │  Middleware Chain                                 │
│   │   │   ├ Metrics ──────► MultiSink ──┬─► PromSink     │
│   │   │   ├ Idempotency               ├─► StructlogSink  │
│   │   │   ├ CostBudget                ├─► SloAlertSink   │
│   │   │   ├ Circuit                   ├─► OTelSink       │
│   │   │   ├ Retry                     └─► LangfuseSink ★ │
│   │   │   ├ SchemaVersion                                 │
│   │   │   └ Timeout → handler                             │
│   │   │                                                   │
│   │   └ LLMGateway.get_main_model()                       │
│   │       ↓                                               │
│   │      ObservedChatModel ★ ──► LangfuseSink.llm_start/end│
│   │                          └──► Prom (cost/token)       │
│   │                                                       │
│   └ ApprovalPolicy ──────► LangfuseSink.event             │
│                                                           │
└──────────────────────────────────────────────────────────┘
                                                ★ = 本 RFC 新增
```

## 附录 B：相关文档

- `docs/architecture-overview.md` — 总体架构（§4.8 中间件、§9 NFR、§10 路线图）
- `docs/architecture-deep-dive.md` — 工具中间件深度
- `docs/shared-memory-design.md` — 内存层设计（与本 RFC 不冲突，memory 不上报 trace）
- `agent_kernel/tools/observability.py` — 现有 sink 接口（本 RFC 扩展）
- `agent_kernel/audit.py` — Sanitizer（本 RFC 复用）
