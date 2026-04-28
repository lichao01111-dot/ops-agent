# RFC: 自进化 - 离线 Curator Agent + PR-style 治理（方案 A）

> **Status**: Draft for review
> **Author**: agent-kernel team
> **Date**: 2026-04-28
> **Scope**: 在不动核心代码逻辑的前提下，让 JARVIS 能从生产 trace / audit / 用户反馈里**自动产出"配置 / 知识 / Prompt / 规则"四类候选 diff**，经人工 PR review 后合入，形成日级自进化闭环。
> **Non-goals**:
> - 不做在线推理时的实时学习（影子双跑 / case-based memory 见后续 RFC）
> - 不动 Approval / Memory RBAC / ToolInvoker 白名单这三条安全红线
> - 不引入新模型训练，全部基于现有 LLM + 检索

---

## 1. 背景与动机

### 1.1 现状

现有 4 个闸门（Router / Approval / Planner / Memory RBAC）和 7 层 middleware 已经把"安全 / 副作用 / 成本"卡住，但**所有可调对象的演进完全靠人**：

| 可进化对象 | 当前演进方式 |
|---|---|
| Router 关键词、置信度阈值 | 工程师手改 `agent_ops/router.py` |
| Planner 复合句正则、infra-log 双跳规则 | 工程师手改 `agent_ops/planner.py` |
| Diagnosis / Planner-LLM / Extractor prompt | 散落源码，手改 + 重启 |
| Knowledge KB / SOP | 手工写 markdown，灌向量库 |
| Middleware 阈值（timeout / retry / circuit / cost） | `ToolSpec.reliability` 硬编码 |
| Verification 轮询参数（max_attempts / poll_interval） | `MutationPlan.verification` 硬编码 |

线上每天都在产生**真实信号**（route confidence 分布、verification 失败率、用户重发率、SLO breach、diagnosis 假设命中率），但没人去看，也没人据此回灌系统。

### 1.2 设计原则

1. **不动安全切面**：Approval / Memory RBAC / ToolInvoker 白名单是红线，自进化产物上线**也要**经过它们。
2. **不动代码逻辑**：Curator 只能写 `prompt_registry` / `config/` / `knowledge/` 三个数据/配置目录，不能改 `.py`。
3. **人是最后闸门**：所有候选以 Git PR 形式落地，人 review 合并。永远不直接 commit 到 main。
4. **可回滚**：每次合入打 tag，灰度切流，回退靠 `prompt_registry` 切版本号或 `git revert`。
5. **trace-to-diff 全链路可追溯**：每条 diff 都要附"由哪些 trace 触发、置信度多少、A/B 期望收益"。

### 1.3 与已有工作关系

| 既有组件 | 在 Curator 中的角色 |
|---|---|
| Langfuse trace（RFC 待落地）| **主输入源**：trace → span → generation 是反思 agent 的训练集 |
| `AuditLogger` + Redis sink | 备份输入源（trace 缺失时兜底） |
| `MetricsMiddleware` + `SloAlertSink` | 反思触发条件（SLO breach 自动入候选） |
| `llm_gateway/prompt_registry.py` | 候选 Prompt 的写入目标 + 版本切换闸门 |
| `tools/knowledge_tool/embeddings.py` | 候选 SOP / case 的向量化入口 |
| `MemoryLayer.VERIFICATION` | 成功结案池（"已知良好"语料源） |

Curator **复用**这些组件，不新建并行栈。

---

## 2. 整体架构

```
┌──────────────────────────────────────────────────────────────────────┐
│                         数据底座（已有 / RFC 中）                     │
│  Langfuse traces  │  AuditLogger sink  │  Prom metrics  │  user fb   │
└──────────────────────────────────┬───────────────────────────────────┘
                                   │  T+1 拉取（窗口 24h，可配）
                                   ▼
┌──────────────────────────────────────────────────────────────────────┐
│                       CuratorPipeline (新增, 离线)                    │
│                                                                       │
│  ┌────────────┐   ┌──────────────┐   ┌──────────────┐   ┌─────────┐  │
│  │  Collect   │──▶│   Analyze    │──▶│   Propose    │──▶│ Render  │  │
│  │ (datasets) │   │ (signal      │   │ (candidate   │   │  (diff  │  │
│  │            │   │  extractor)  │   │  generator)  │   │   + PR) │  │
│  └────────────┘   └──────────────┘   └──────────────┘   └────┬────┘  │
└─────────────────────────────────────────────────────────────┼────────┘
                                                              │ git PR
                                                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│                       人工治理（GitHub）                              │
│   reviewer 审 PR → label canary/full → merge                         │
└──────────────────────────────────────────────────────────────────────┘
                                                              │
                                                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│                      上线（已有路径，不变）                           │
│  prompt_registry 切版本  │  config reload  │  KB embedding rebuild   │
└──────────────────────────────────────────────────────────────────────┘
```

四个阶段都是**离线、独立、可单测**的纯函数式流水线。每一步的输入输出都用 Pydantic 模型固化，方便回放和 eval。

---

## 3. 四阶段详细设计

### 3.1 Stage 1 — Collect（采集）

**目的**：把上一窗口（默认 24h）的所有原始信号统一成 `CuratorDataset` 一个对象。

**输入源**：

| 源 | 拉取方式 | 内容 |
|---|---|---|
| Langfuse | SDK `client.get_traces(start, end)` | 完整 trace + span + generation + cost |
| AuditLogger Redis sink | `LRANGE audit:* -N` | 工具调用、approval、verification 结果 |
| Prom（可选） | PromQL 拉聚合指标 | SLO breach 计数、p95 延迟、circuit open 次数 |
| 用户反馈 | `/api/feedback` 新增端点 + DB 表 | thumbs up/down / 文本评论 / 重发标记 |

**输出**：

```python
class CuratorDataset(BaseModel):
    window_start: datetime
    window_end: datetime
    traces: list[TraceRecord]          # 一次完整请求
    audits: list[AuditRecord]          # 工具/审批维度
    metrics: MetricsSnapshot           # 聚合
    feedback: list[FeedbackRecord]
    # 派生
    sessions: list[SessionView]        # 按 session_id 聚合后的视图
```

**关键派生字段**（在 collect 阶段就算好，下游不用重算）：

- `SessionView.outcome`：枚举 `verified_ok / verified_fail / no_verify / user_repeat / user_negative`
- `SessionView.route_was_correct`：布尔，根据是否触发 LLM 回退、是否被 user_repeat 推翻
- `SessionView.plan_edit_distance`：人工修正过的 plan vs 原 plan 的编辑距离（无修正记 0）
- `SessionView.tool_failures`：失败 tool + 失败 middleware 阶段（timeout/retry-exhausted/circuit-open/...）

**实现位置**：`agent_kernel/curator/collect.py`，纯异步，不依赖 BaseAgent 运行时。

### 3.2 Stage 2 — Analyze（信号抽取）

**目的**：从 dataset 里**只用规则 + 简单聚合**抽出"哪里需要进化"，不调 LLM。

**6 类 detector**（每类对应一种可进化对象）：

| Detector | 触发条件 | 输出 |
|---|---|---|
| `RouterMissDetector` | route confidence < 0.6 + LLM 回退后选择不同 route，>= N 次 | 该意图模式下应新增/调整哪个关键词 |
| `PlannerSplitDetector` | 单 step plan 但 trace 里 user 隔轮重发不同动作 | 漏拆分的复合句样本 |
| `PromptRegressionDetector` | 同一 prompt 版本 thumbs-down 率 > 阈值 / verification 失败率 ↑ | 该 prompt 列入"待优化" |
| `ToolReliabilityDetector` | 单工具 SLO breach > N 次 / circuit open / retry exhausted 比例 ↑ | 该工具 timeout/retry/circuit 阈值候选调参 |
| `KnowledgeGapDetector` | knowledge executor "找不到" 应答率 > 阈值 + 用户重发 | 缺失的 SOP / KB 主题 |
| `VerificationFalseDetector` | verification 通过但用户 thumbs-down / 1h 内重发同 deployment | verification 判据可能误报 |

**输出**：

```python
class Signal(BaseModel):
    detector: str
    severity: Literal["info", "warn", "high"]
    target_kind: Literal["router_rule", "planner_regex", "prompt", "tool_config",
                         "kb_entry", "verification_policy"]
    target_id: str             # 例：route_keyword.RESTART / prompt.diagnosis_v3
    evidence: list[TraceRef]   # 用哪些 trace_id 支撑（≥3 才出 signal）
    confidence: float          # 0~1，根据样本量 + 信号强度
    summary: str               # 一句话人读
```

**关键约束**：

- 每个 signal 必须至少 3 个独立 trace 支撑（避免单点抖动）
- confidence < 0.5 的 signal 直接丢弃（落 audit 但不入 propose）
- 全部 detector 是纯函数 + 单测（`tests/test_curator_detectors.py`）

### 3.3 Stage 3 — Propose（候选生成）

**目的**：把 signal 转成**具体的 diff 候选**。这一步是唯一调 LLM 的阶段。

**两条路径**：

#### Path A — 规则路径（不调 LLM）

| signal 类型 | 规则生成器 |
|---|---|
| `tool_config` | 按当前阈值与实测 p99 的差距，给出 `{timeout_s: old → new}` 候选，幅度限制 ±50% |
| `verification_policy` | 同上，调 `max_attempts / poll_interval_s` |
| `router_rule`（明确关键词类） | 从 evidence trace 的 user message 里提取 1-3gram 高频词，与现有关键词去重 |

#### Path B — LLM 路径（用 Curator-LLM）

`prompt`、`planner_regex`、`kb_entry` 三类需要语义理解，走 LLM：

```python
class CuratorLLM:
    """Gemini structured output, 与现有 planner LLM 同栈"""
    async def propose(self, signal: Signal, dataset: CuratorDataset) -> ProposalDraft:
        # 输入：signal + 最多 N 条 evidence trace 的 redacted 文本
        # 输出：ProposalDraft（结构化）
```

**Curator-LLM 的护栏**（与 Planner-LLM 同款）：

- 输出永不直接生效，必须经 Render → PR → 人审
- prompt 候选必须保留原 prompt 的 input/output schema（用现有 `prompt_registry` 的 schema 校验）
- 不能修改任何安全相关字段（`requires_approval` / `allowed_routes` / `risk_level`）
- 输出附带 `expected_metric_delta`：候选预期改善哪个指标多少（人审时的判断依据）

**输出**：

```python
class Proposal(BaseModel):
    proposal_id: str           # uuid
    target_kind: str           # 同 Signal
    target_id: str
    diff: dict                 # 见 3.4 各类型的 diff schema
    rationale: str             # 为什么这么改
    expected_metric_delta: dict  # 例：{"route_correct_rate": "+3%"}
    evidence: list[TraceRef]
    rollback_hint: str         # 怎么回滚
```

### 3.4 Stage 4 — Render（落地为 Git PR）

**目的**：把 Proposal 渲染成 git diff，开 PR，附完整证据链。

#### 落地路径（按 target_kind 分）

| target_kind | 落地文件 | 上线生效方式 |
|---|---|---|
| `router_rule` | `config/router_rules.yaml`（**新增**，从 `agent_ops/router.py` 抽出） | 进程重启 / SIGHUP reload |
| `planner_regex` | `config/planner_patterns.yaml`（**新增**） | 同上 |
| `prompt` | `prompts/<name>/v<N>.md` + `prompt_registry` index 更新 | 切版本号灰度 |
| `tool_config` | `config/tool_reliability.yaml`（**新增**） | 重启 |
| `kb_entry` | `knowledge/sop/<topic>.md` | 重建 embedding |
| `verification_policy` | `config/verification.yaml`（**新增**） | 重启 |

> **前置工作**：`router.py` / `planner.py` / `ToolSpec.reliability` 等当前硬编码的对象需要先抽出到 yaml/json 配置，加 `ConfigLoader`（带 schema 校验 + hot reload）。这是本 RFC 的依赖项，单列在 §6 工程任务里。

#### PR 模板

```markdown
## [Curator] {N} candidate diffs from {window_start} → {window_end}

### Summary
- router_rule: 2 candidates
- prompt: 1 candidate (diagnosis_v3 → v4)
- tool_config: 3 candidates

### Proposal #1: router_rule.RESTART_KEYWORDS += "拉起"
**Confidence**: 0.82
**Evidence**: 7 traces ([trace_id_1](langfuse/...), ...)
**Expected delta**: route_correct_rate +2.1% on intent=k8s_restart
**Diff**:
```diff
 keywords:
   restart:
     - 重启
+    - 拉起
     - restart
```
**Rollback**: revert this commit

### ...

### Auto checks
- [x] Schema validation passed
- [x] No safety field touched
- [x] Backward compatible (additive only)
- [ ] Reviewer: route the diff to canary 10% before full
```

#### Render 实现要点

- 用 `gh pr create` 经服务账号开 PR；**不能直接 push main**
- PR 打 label：`curator-auto`、`canary-required` / `safe-to-full`
- PR 描述里 trace_id 自动渲染成 Langfuse UI 可点链接
- 同一 target_id 24h 内已有未合并 PR → 跳过（避免堆积）

---

## 4. 安全约束

| 约束 | 实现 |
|---|---|
| **永不写代码** | Render 只允许写 `config/**`、`prompts/**`、`knowledge/**` 目录；CI 设 path-based 校验拒绝 `.py` 改动 |
| **永不动安全字段** | `risk_level / requires_approval / side_effect / allowed_routes / allowed_writers` 列入 forbidden key 列表，Render 阶段静态校验 |
| **不能自动合并** | PR 必须 ≥1 human review，CODEOWNERS 强制 |
| **金丝雀** | `prompt`/`router_rule`/`planner_regex` 三类必须先 canary（基于 session_id hash 10%）跑 24h 才能全量 |
| **回滚 SLO** | 合入后 1h 内若 verification 失败率 / SLO breach / 用户负反馈率超基线 1.5×，自动开"回滚 PR"通知 oncall |
| **审计** | Curator 自身的每一步（collect/analyze/propose/render）也通过现有 `AuditLogger` + Langfuse trace 落盘，"反思过程"本身可审计 |

> Curator 是普通客户端，没有任何 ToolInvoker 特权；它没法关审批、没法绕 RBAC——这点跟"LLM 输出永不被信任"一致。

---

## 5. 数据模型与 schema

完整 Pydantic 模型放在 `agent_kernel/curator/schemas.py`，关键模型：

```python
class TraceRef(BaseModel):
    trace_id: str
    langfuse_url: str | None
    session_id: str
    ts: datetime

class CuratorDataset(BaseModel): ...    # 见 3.1
class Signal(BaseModel): ...            # 见 3.2
class Proposal(BaseModel): ...          # 见 3.3
class RenderResult(BaseModel):
    pr_url: str
    pr_number: int
    files_changed: list[str]
    proposals: list[str]
```

**diff schema 按类型固定**（拒绝 free-form），例如 `router_rule.diff`：

```python
class RouterRuleDiff(BaseModel):
    op: Literal["add_keyword", "remove_keyword", "adjust_threshold"]
    intent: str
    value: str | float
```

这样 Render 可以静态校验、PR 可以机读、回归测试能直接跑。

---

## 6. 工程任务拆解

| # | 任务 | 依赖 | 工作量 | 优先级 |
|---|---|---|---|---|
| 1 | 抽出 `router_rules / planner_patterns / tool_reliability / verification` 到 yaml + ConfigLoader（含 schema 校验、hot reload、单测） | 无 | 3d | P0 必须先做 |
| 2 | `agent_kernel/curator/schemas.py` Pydantic 模型 + 单测 | 1 | 1d | P0 |
| 3 | `curator/collect.py` Langfuse + Audit + Prom + feedback 拉取 + SessionView 派生 | 2、Langfuse RFC | 2d | P0 |
| 4 | `/api/feedback` 端点 + 反馈表 + 前端按钮 | 无 | 1d | P0 |
| 5 | 6 个 detector 的纯函数实现 + 单测（每个 detector ≥5 case） | 2、3 | 3d | P0 |
| 6 | Path A 规则候选生成器 + 单测 | 5 | 1d | P0 |
| 7 | `CuratorLLM` Path B + Gemini structured output prompt 模板 + schema 校验 | 5 | 2d | P0 |
| 8 | Render：`gh pr create` 流程 + 各类型 diff 渲染器 + PR 模板 | 6、7 | 2d | P0 |
| 9 | CronCreate / GitHub Action 调度（每日 02:00 UTC 跑一遍） | 8 | 0.5d | P0 |
| 10 | CI path-guard：拒绝 curator PR 触碰 `.py` / safety 字段 | 8 | 0.5d | P0 |
| 11 | 合入后 1h 自动监控 + 回滚 PR 触发器 | 9 | 1.5d | P1 |
| 12 | Curator 自身的 trace + 审计接入 | 3、Langfuse RFC | 0.5d | P1 |
| 13 | Eval 数据集：人工标注 200 条历史 trace 作为 ground truth，跑 detector 准确率回归 | 5 | 持续 | P1 |
| 14 | 文档：CONTRIBUTING.md 加"如何审 curator PR" | 8 | 0.5d | P1 |

**P0 合计 ~16 工程日**。可与 Langfuse RFC（~9 工程日）并行——前 3 天做 Task 1（抽配置）和 `/api/feedback`，等 Langfuse 落地完整 trace 后再接 Task 3。

---

## 7. 上线节奏与成功判据

### 7.1 三阶段上线

| Phase | 目标 | 准入 |
|---|---|---|
| **Phase 0 — 影子运行（2 周）** | Curator 跑全流程，但 PR 标 `draft`，**不真合**。人工 review 看候选质量 | 准确率 ≥ 70% 才进 Phase 1 |
| **Phase 1 — 半自动（4 周）** | 真开 PR，人工合入。每周复盘哪些类型质量高/低 | 误改率 < 5%（误改 = 合入后 24h 内回滚） |
| **Phase 2 — 治理日常化** | 进入"每日 PR review 5min"日常 oncall 任务 | 持续观察，季度复盘 |

### 7.2 北极星指标

| 指标 | 现状 baseline | 6 个月目标 |
|---|---|---|
| Route correct rate（≠ user 重发） | 待测 | +5% |
| Verification success rate | 待测 | +3% |
| 平均"工程师手改 prompt/规则"次数/月 | 估 ~10 | < 3 |
| 用户 thumbs-down 率 | 待测 | -30% |
| Curator PR 合入比例 | — | ≥ 60% |
| Curator 误改回滚率 | — | < 5% |

### 7.3 失败回退

如果 Phase 0 准确率 < 50% 持续 2 周，**整套停掉**，回到人工演进；问题大概率在 detector 信号源（trace 不够丰富 / feedback 太稀疏），先补数据再重试。

---

## 8. 与后续方案的衔接

本 RFC 是其他三套自进化方案的**数据底座**：

- **方案 B（影子双跑）**：复用 Curator 的 Proposal 作为"候选版本"输入，影子流量做更早的 A/B
- **方案 C（case-based memory）**：复用 SessionView.outcome 决定哪些会话进"成功池"
- **方案 D（DSPy 编译）**：复用 Curator dataset 直接当 DSPy 训练集

设计上保证 `CuratorDataset` 这个 Pydantic 模型稳定，让后续方案不重写采集逻辑。

---

## 9. Open Questions

1. **反馈端点**：thumbs up/down 够不够？还是要加结构化反馈（"plan 应该是 X"）？
2. **canary 切流粒度**：按 `session_id hash` 还是按 `intent` 维度？后者更细但更难控比例。
3. **Curator-LLM 用什么模型**：Gemini（与现有 planner-LLM 一致）还是单独用 Claude？后者引入新依赖。
4. **跨环境**：dev / staging / prod 的 trace 是否合并？合并能扩样本但风险是 dev 噪音污染。
5. **Knowledge 自更新**：自动从成功 diagnosis 抽 SOP 这一类，要不要先单独做一个 RFC？它跟 prompt 的工作流不太一样（KB 需要去重、合并、过期）。

---

## 10. 验收 Checklist

- [ ] Task 1-10 P0 全部完成
- [ ] `tests/test_curator_*.py` 至少 50 用例，覆盖每个 detector + render 各 diff 类型
- [ ] 影子 Phase 跑满 2 周，准确率报告归档
- [ ] CODEOWNERS 配置完成，`curator-auto` PR 必须 platform 团队 review
- [ ] CONTRIBUTING.md 更新
- [ ] 月度 metric dashboard（Grafana）上线，跟踪 §7.2 北极星指标

---

*依赖：`docs/langfuse-integration-rfc.md`（trace 数据源）、`llm_gateway/prompt_registry.py`（prompt 落地点）、`tools/knowledge_tool/embeddings.py`（KB 落地点）。*
