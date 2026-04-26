# JARVIS 端到端测试计划 (Architecture v2)

> 说明：`JARVIS` 是系统名；测试代码中的真实类名、工厂方法仍是 `OpsAgent` / `create_ops_agent()`。

> 本文档按 `docs/architecture-v2.md` 的每一条不变量、插件点和安全边界，设计端到端（E2E）测试用例。
> 所谓"端到端" = 驱动 `agent.chat(ChatRequest)` 或 `agent._invoke_tool(...)` 的完整调用链，
> 覆盖 Planner → Dispatcher → Executor → _invoke_tool → Audit → Memory 的整条路径。
>
> 实现见 `tests/e2e/test_architecture_v2_e2e.py`。

---

## 1. 测试金字塔中的位置

| 层 | 位置 | 关注点 |
|---|---|---|
| L0 单元 | `tests/test_agent.py` | Planner / Registry / Topology / DiagnosisMemory 的内部逻辑 |
| L1 契约 | `tests/kernel_contract/test_kernel_contract.py` | Kernel 不与 Ops 耦合（基线契约） |
| **L2 端到端（本文档）** | `tests/e2e/test_architecture_v2_e2e.py` | **整条 chat() 调用链 + 架构不变量** |
| L3 回归 | 未来：真实 K8s / Jenkins 场景录屏回放 | 外部依赖 |

E2E 不测"K8s 是否返回正确 Pod 列表"（那是集成测），而是验证：**给定骨架 + 可替换工具句柄，架构约束是否被强制执行**。

## 2. 测试命名规则

`test_E2E_<group><nn>_<slug>`

| 分组 | 含义 |
|---|---|
| A | Happy Path 主流程 |
| B | Kernel 不变量（§4.2） |
| C | 插件点（§6） |
| D | 垂直隔离（§5.5） |
| E | 降级路径（§10） |
| F | 反模式回归（§11） |

## 3. 共用 Fixture 与辅助

| Fixture / Helper | 作用 |
|---|---|
| `make_dummy_agent(schema=None, executors=None, router=None)` | 基于 `BaseAgent` 装配最小垂直 |
| `make_ops_agent_stubbed()` | `create_ops_agent()` 后把 12 个 Ops 工具句柄替换为确定性 stub |
| `make_valid_receipt(step, approved_by, expires_in_seconds=300)` | 给定 `PlanStep` 产出合法 `ApprovalReceipt` |
| `DummyHandler(payload)` | 实现 `async ainvoke(args)` 返回固定 JSON |

## 4. 用例矩阵

### A — Happy Path 主流程

| ID | 用例 | 架构引用 | Pass 判据 |
|---|---|---|---|
| **A01** | Dummy Vertical 通过 `chat()` 跑通 1 步 | §4.3 / §5.5 | `response.route == "dummy_route"` 且 `response.sources` 非空 |
| **A02** | JARVIS "查询 MySQL 地址" 走 knowledge 路由 | §5.1 | `response.route == "knowledge"`，stub 的 `query_knowledge` 被调用 1 次 |
| **A03** | JARVIS mutation 带合法 receipt → 工具成功执行 | §4.2 #1 / §8.2 | `ToolCallEvent.status == SUCCESS`，输出不含 `error` |
| **A04** | 复合请求"先查 pod 状态，然后重启 order-service"产生 2 步 plan 并按序执行 | §5.1 / §11 | `len(plan.steps) == 2`，两步按顺序 SUCCEEDED |
| **A05** | `PlanStep.execution_target="executor:foo"` 显式指定时，覆盖 `route` 的派发 | §7.2 | 请求被派发给 `node_name="foo"` 的 executor，即使 `route="bar"` |

### B — Kernel 不变量 (§4.2)

| ID | 用例 | 架构引用 | Pass 判据 |
|---|---|---|---|
| **B01** | side_effect 工具无 receipt → 调用 FAILED | §4.2 #1 | `event.status == FAILED`，`output` 包含 "approval_receipt" 字样 |
| **B02** | receipt.step_id 与当前 step 不匹配 → FAILED | §4.2 #1 | 同上 |
| **B03** | receipt.expires_at 已过期 → FAILED | §4.2 #1 | 同上 |
| **B04** | 仅填 `context={"approved": True}` 无 receipt → FAILED | §4.2 注解 | 裸 `approved` 不被 Kernel 认为合法 |
| **B05** | Unauthorized writer 写 memory → `PermissionError` | §4.2 #3 | `pytest.raises(PermissionError)` |
| **B06** | `plan.max_iterations=1` 后仍有 pending step → 立即 FINISH | §4.2 #4 | `response.message` 来自已执行的步骤，未再推进 |
| **B07** | Executor 抛异常 → `PlanStepStatus.FAILED` + FINISH | §4.2 #5 | 下一步不被执行；`chat()` 正常返回错误说明 |
| **B08** | 每次 `chat()` 恰好产生 1 条 audit 条目 | §4.2 #2 | `audit_logger.get_recent(10)` 新增计数 == 1 |

### C — 插件点 (§6)

| ID | 用例 | 架构引用 | Pass 判据 |
|---|---|---|---|
| **C01** | 自定义 `RouterBase` 决定路由 | §6 #1 | 响应的 `route` 来自自定义 Router |
| **C02** | 自定义 `ApprovalPolicy` 被 Kernel 调用 | §6 #7 | 自定义策略的 `evaluate` 被 invoke，且否决时 side-effect FAILED |
| **C03** | 自定义 `MemorySchema(layers=...)` 生效 | §6 #6 | 合法 writer 写入成功，非法 writer 抛 `PermissionError` |
| **C04** | `AuditLogger.add_sanitizer` 注册的脱敏钩子生效 | §6 #8 | audit entry 的 `params` 中敏感字段被替换 |
| **C05** | `AuditLogger.add_sink` 注册的 sink 收到 entry | §6 #8 | sink 捕获的 entry 数 == chat 次数 |
| **C06** | `OpsPlanner` 中文拆分在 `create_ops_agent()` 真实路径中生效 | §6 #5 / §11 | 复合请求 → 2 步 plan（通过 chat 响应间接验证） |
| **C07** | `Planner._maybe_replan` 覆写能在 FINISH 前追加步骤 | §6 #5 | Vertical 子类返回新 step，执行器被调用 2 次，最终响应来自新 step |

### D — 垂直隔离 (§5.5)

| ID | 用例 | 架构引用 | Pass 判据 |
|---|---|---|---|
| **D01** | 两个独立 JARVIS 实例共享 session_id，数据互不可见 | §5.5 | A 写入的记忆，B 读取为 `None` |
| **D02** | 两个 MemorySchema 即使层名同名，SessionStore 实例互不影响 | §5.5 / §11 | Agent B 无法读到 Agent A 的相同 key |

### E — 降级路径 (§10)

| ID | 用例 | 架构引用 | Pass 判据 |
|---|---|---|---|
| **E01** | L1：executor 抛异常 → `chat()` 返回 ChatResponse 不崩溃，含错误说明 | §10 L1 | 响应消息提示"步骤执行失败"，`response.message` 非空 |
| **E02** | L3：receipt 非法 → side-effect 被拒但 chat 正常结束 | §10 L3 | response 正常返回；该 ToolCallEvent.status == FAILED |

### F — 反模式回归 (§11)

| ID | 用例 | 架构引用 | Pass 判据 |
|---|---|---|---|
| **F01** | `agent_kernel` 任意模块不导入 `IntentType/AgentRoute/MemoryLayer/AgentIdentity` Ops 枚举 | §11 #2 / §4.4 | 静态 grep 断言 |
| **F02** | `agent_kernel` 导入不到 `Hypothesis` / `ServiceTopology` | §11 #1 / #2 | 尝试 `from agent_kernel.xxx import Hypothesis` 预期 `ImportError` |
| **F03** | `agent_kernel.planner` 无模块级 `_split_compound`（已挪到 `OpsPlanner` 方法） | §11 #6 | `hasattr(agent_kernel.planner, "_split_compound") is False` |

## 5. 断言风格

1. **只测架构行为，不测业务计算**：不断言"Pod 是否在 Running 状态"，只断言"side_effect 工具是否被拦下"。
2. **一测一事**：每个 case 聚焦一条不变量；不做 end-of-story 万能断言。
3. **使用 stub 工具句柄**：通过 `registry.register_mcp(ToolSpec, DummyHandler)` 注入确定性响应；不启动真实 K8s / LLM。
4. **断言要能区分"架构错"与"业务错"**：例如 B01 不是"工具执行失败"，而是"`output` 明确提到 approval_receipt"。

## 6. 运行

```bash
pytest tests/e2e/ -v
pytest tests/e2e/test_architecture_v2_e2e.py::TestB01_KernelInvariants -v
```

## 7. 不在本计划覆盖的场景（故意排除）

| 场景 | 原因 |
|---|---|
| 真实 LLM 调用 / DiagnosisExecutor 多假设 LLM 推理 | 属于 Ops 业务能力集成测，不是框架不变量 |
| Supervisor / AgentProxyExecutor (§7) | v2 为演进方向，未实现 |
| Redis / DB SessionStore | `SessionStore` 是 ABC，接口已在 L1 契约测试中覆盖 |
| 性能 / 并发压力 | 属于性能测试套件 |

## 8. 维护守则

- 新增一条 architecture-v2 不变量 → 同步加一个 E2E-x 用例
- 用例重命名/删除 → 本文档与代码同步更新
- 若修复某个 case，先补复现断言再改代码（TDD 闭环）
