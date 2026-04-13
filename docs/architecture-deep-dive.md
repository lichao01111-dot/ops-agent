# OpsAgent 推荐架构深度解析

本文档描述 OpsAgent 的推荐版 Agent 架构。目标不是“让所有请求都走 ReAct”，而是把不同任务映射到不同的执行范式：

- `knowledge`：RAG-first，非 ReAct
- `read_only_ops`：确定性查询执行器，非 ReAct
- `diagnosis`：受限 ReAct，限步取证
- `mutation`：计划 + 审批 + 执行 + 回读校验

Shared memory 的结构和权限矩阵见 [shared-memory-design.md](./shared-memory-design.md)。

## 一、推荐版总体控制流

```mermaid
flowchart TD
    U["User / API / IM / CLI"] --> G["Gateway Layer"]
    G --> V["Validation + Auth + RBAC"]
    V --> S["Session / Audit Context"]
    S --> R["Main Orchestrator / Router<br/>intent + risk + params + route confidence"]

    R --> C{"Route Type"}

    C -->|knowledge| K1["Knowledge Executor<br/>RAG-first, non-ReAct"]
    C -->|read_only_ops| Q1["Read-Only Executor<br/>deterministic query flow"]
    C -->|diagnosis| D1["Diagnosis Executor<br/>bounded ReAct"]
    C -->|mutation| M1["Mutation Executor<br/>plan + approval workflow"]

    K1 --> K2["Retrieve Knowledge"]
    K2 --> K3{"Evidence Enough?"}
    K3 -->|Yes| K4["Answer with Sources"]
    K3 -->|No| K5["Return No-Result / Ask Clarifying"]
    K4 --> OUT["Final Response"]
    K5 --> OUT

    Q1 --> Q2["Resolve Target + Params"]
    Q2 --> Q3["Select Read-Only Tool"]
    Q3 --> Q4["Execute Tool"]
    Q4 --> Q5{"Need One More Read?"}
    Q5 -->|No| Q6["Summarize Result"]
    Q5 -->|Yes| Q7["Execute Follow-up Read Tool"]
    Q7 --> Q6
    Q6 --> OUT

    D1 --> D2["Initial Evidence Collection"]
    D2 --> D3{"Hypothesis Possible?"}
    D3 -->|No| D4["Next Best Probe"]
    D4 --> D5["Execute Diagnostic Tool"]
    D5 --> D6{"Budget Left?"}
    D6 -->|Yes| D3
    D6 -->|No| D7["Stop Exploration"]
    D3 -->|Yes| D8["Synthesize RCA"]
    D7 --> D8
    D8 --> OUT

    M1 --> M2["Build Action Plan"]
    M2 --> M3["Policy Check"]
    M3 --> M4{"Approval Required?"}
    M4 -->|Yes| M5["Human Approval Gate"]
    M4 -->|No| M6["Execute Mutation Tool"]
    M5 -->|Approved| M6
    M5 -->|Rejected| M7["Abort + Audit"]
    M6 --> M8["Post-check Verification"]
    M8 --> M9["Execution Summary"]
    M7 --> OUT
    M9 --> OUT
```

## 二、为什么不是“主 Agent + 所有子路由都 ReAct”

这种系统最容易犯的错，是把所有问题都 agent 化。这样会带来三个问题：

1. 简单查询被复杂化。查 Pod 状态、查构建结果，本来只需要一次工具调用，却被放进多轮推理。
2. 高风险变更失控。变更类任务如果也走自由 ReAct，会把权限、审批和幂等控制交给 prompt。
3. 审计难以闭环。没有明确的路由执行契约时，很难解释“为什么走这条路、为什么用了这个工具、为什么停在这里”。

因此推荐版的关键不是“多 Agent”，而是“路由后使用专用执行器”。

## 三、四条路由的职责和范式

### 3.1 `knowledge`

范式：`RAG-first + constrained synthesis`

目标：
- 从知识库中检索事实
- 输出带来源的回答
- 不调用运维实时查询工具

执行契约：
- 优先调用 `query_knowledge`
- 检索不到就明确说未命中
- 成功标准是“回答有来源”，不是“回答得像”

### 3.2 `read_only_ops`

范式：`deterministic query executor`

目标：
- 对 Jenkins / K8s / 日志系统做只读查询
- 尽量一次命中合适的工具
- 最多做一次补充查询

执行契约：
- 先解析目标实体和参数
- 再选择只读工具
- 最终输出状态摘要

### 3.3 `diagnosis`

范式：`bounded ReAct`

目标：
- 收集证据
- 做有限步的多源排障
- 输出结论、证据、建议动作

执行契约：
- 允许多步工具探索
- 必须设置步数预算
- 必须先证据后结论

### 3.4 `mutation`

范式：`plan -> approval -> execute -> verify`

目标：
- 承载所有有副作用的动作
- 在审批门后执行
- 输出可审计的执行摘要

执行契约：
- 先生成计划
- 做 RBAC 和策略校验
- 未审批时只返回计划，不执行工具
- 执行后必须做回读验证

当前已接入的 mutation 示例：
- Jenkinsfile 生成
- 知识库文档索引

## 四、推荐版代码分层

```
gateway/
  app.py                      # API / SSE / request parsing

agent_core/
  agent.py                    # 主 orchestrator + graph wiring
  router.py                   # 路由决策
  session.py                  # session abstraction
  audit.py                    # 审计与工具轨迹
  schemas.py                  # route / risk / response types

  executors/
    knowledge.py              # RAG executor
    read_only_ops.py          # deterministic query executor
    diagnosis.py              # bounded ReAct executor
    mutation.py               # approval workflow executor
```

当前仓库尚未完全拆到 `executors/` 目录，但推荐设计就是这个方向。

## 五、函数级控制流图

下面这张图对应当前实际代码，而不是理想化概念图。每个节点都落到具体函数。

```mermaid
flowchart TD
    A["gateway.app.chat() / chat_stream()"] --> B["OpsAgent.chat()<br/>OpsAgentStreaming.chat_stream()"]
    B --> C["_build_initial_state()"]
    C --> D["graph.ainvoke() / manual streaming dispatch"]

    D --> E["_route_node()"]
    E --> F["IntentRouter.route()"]
    F --> F1["_contains_any() keyword routing"]
    F --> F2["_route_with_llm() fallback"]
    F1 --> G["_select_route_node()"]
    F2 --> G

    G --> H{"route"}
    H -->|knowledge| I["_knowledge_node()"]
    H -->|read_only_ops| J["_read_only_node()"]
    H -->|diagnosis| K["_diagnosis_node()"]
    H -->|mutation| L["_mutation_node()"]

    I --> I1["_execute_knowledge()"]
    I1 --> I2{"_is_index_request()?"}
    I2 -->|No| I3["_extract_top_k()"]
    I3 --> I4["_invoke_tool('query_knowledge')"]
    I4 --> I5["_extract_sources()"]
    I5 --> I6["_format_knowledge_result()"]

    I2 -->|Yes| I7["_extract_docs_directory()"]
    I7 --> I8["_invoke_tool('index_documents')"]
    I8 --> I9["_format_index_result()"]

    J --> J1["_execute_read_only_ops()"]
    J1 --> J2["_plan_read_only_tool()"]
    J2 --> J3["_extract_namespace() / _extract_service_name() / _extract_job_name()"]
    J2 --> J4["_extract_build_number() / _extract_time_range() / _extract_pod_name()"]
    J2 --> J5["_extract_log_level() / _extract_keyword()"]
    J3 --> J6["_invoke_tool(...)"]
    J4 --> J6
    J5 --> J6
    J6 --> J7["_format_read_only_summary()"]
    J7 --> J8["_format_single_read_only_result()"]

    K --> K1["_run_route_subgraph(route=DIAGNOSIS)"]
    K1 --> K2["_build_route_prompt()"]
    K2 --> K3["_execute_bounded_tool_loop()"]
    K3 --> K4["llm_gateway.get_main_model().bind_tools()"]
    K4 --> K5["response.tool_calls?"]
    K5 -->|Yes| K6["tool loop + ToolMessage append"]
    K6 --> K7["_extract_sources()"]
    K7 --> K3
    K5 -->|No| K8["_normalize_message_content()"]

    L --> L1["_execute_mutation()"]
    L1 --> L2{"Viewer?"}
    L2 -->|Yes| L9["return permission denial"]
    L2 -->|No| L3{"_is_index_request()?"}
    L3 -->|Yes| L4["_extract_docs_directory()"]
    L4 --> L5{"_approval_granted()?"}
    L5 -->|No| L10["return approval message"]
    L5 -->|Yes| L11["_invoke_tool('index_documents')"]
    L11 --> L12["_format_index_result()"]

    L3 -->|No| L6["_build_pipeline_plan()"]
    L6 --> L7["_extract_language() + helper extractors"]
    L7 --> L8{"_approval_granted()?"}
    L8 -->|No| L13["_format_mutation_plan()"]
    L8 -->|Yes| L14["_invoke_tool('generate_jenkinsfile')"]
    L14 --> L15["_format_mutation_execution()"]

    I6 --> Z["_persist_session_turn()"]
    I9 --> Z
    J8 --> Z
    K8 --> Z
    L9 --> Z
    L10 --> Z
    L12 --> Z
    L13 --> Z
    L15 --> Z

    Z --> AA["_audit_request()"]
    AA --> AB["audit_logger.log()"]
    AB --> AC["ChatResponse / SSE events"]
```

函数和文件对应关系：

- 主入口与图编排：`agent_core/agent.py`
- 路由策略：`agent_core/router.py`
- 会话：`agent_core/session.py`
- 审计：`agent_core/audit.py`

如果后续把执行器拆文件，推荐映射如下：

- `_execute_knowledge()` -> `agent_core/executors/knowledge.py`
- `_execute_read_only_ops()` -> `agent_core/executors/read_only_ops.py`
- `_run_route_subgraph()` 与 `_execute_bounded_tool_loop()` -> `agent_core/executors/diagnosis.py`
- `_execute_mutation()` -> `agent_core/executors/mutation.py`

## 六、每条路由的推荐状态机

### `knowledge`

```mermaid
flowchart TD
    A["Receive Request"] --> B["Classify as Knowledge QA"]
    B --> C["Build Knowledge Prompt"]
    C --> D["Call query_knowledge"]
    D --> E{"Found Results?"}
    E -->|Yes| F["Extract Sources"]
    F --> G["Summarize Answer"]
    G --> H["Return Answer + Sources"]
    E -->|No| I["Return No-Result Guidance"]
```

### `read_only_ops`

```mermaid
flowchart TD
    A["Receive Request"] --> B["Classify as Read-Only Ops"]
    B --> C["Extract Query Target<br/>service / namespace / job / time range"]
    C --> D["Select Read-Only Tool"]
    D --> E["Execute Tool"]
    E --> F{"Enough Data?"}
    F -->|Yes| G["Summarize Status / Result"]
    G --> H["Return Structured Answer"]
    F -->|No| I["Try One Follow-up Read Tool"]
    I --> J["Merge Evidence"]
    J --> G
```

### `diagnosis`

```mermaid
flowchart TD
    A["Receive Request"] --> B["Classify as Diagnosis"]
    B --> C["Build Diagnosis Prompt"]
    C --> D["Step 1: Gather Primary Evidence<br/>status / logs / build / events"]
    D --> E{"Enough to Form Hypothesis?"}
    E -->|No| F["Step 2: Choose Next Evidence Tool"]
    F --> G["Execute Follow-up Tool"]
    G --> H{"Step Budget Remaining?"}
    H -->|Yes| E
    H -->|No| I["Stop Exploration"]
    E -->|Yes| J["Form Hypothesis"]
    J --> K["Check Against Evidence"]
    K --> L["Return Conclusion + Evidence + Suggestions"]
    I --> L
```

### `mutation`

```mermaid
flowchart TD
    A["Receive Request"] --> B["Classify as Mutation"]
    B --> C["Check Role / Policy"]
    C --> D{"Role Allowed?"}
    D -->|No| E["Reject Request"]
    D -->|Yes| F["Generate Action Plan"]
    F --> G{"Approved?"}
    G -->|No| H["Return Approval Request"]
    G -->|Yes| I["Execute Mutation Tool"]
    I --> J{"Execution Success?"}
    J -->|No| K["Return Failure + Audit"]
    J -->|Yes| L["Run Post-check Verification"]
    L --> M["Return Execution Summary"]
```

## 七、实现优先级

建议按以下顺序落地：

1. 先把 `router` 固化成 orchestrator，而不是大一统 ReAct。
2. 把 `knowledge` 和 `read_only_ops` 收紧成确定性执行器。
3. 把 `diagnosis` 作为唯一保留明显 ReAct 味道的路由。
4. 把 `mutation` 做成显式审批工作流。
5. 最后再把 session / audit 持久化到 Redis / DB。
