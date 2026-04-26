# JARVIS

基于 LangChain + LangGraph 构建的 DevOps AI Agent。采用「Agent Kernel + Vertical Agent」分层架构：通用编排骨架在 `agent_kernel/`，Ops 垂直逻辑在 `agent_ops/`，覆盖知识问答、只读运维查询、Stage-0 事件聚合、故障诊断、受审批约束的变更执行和变更后自动校验。

说明：产品名现统一为 `JARVIS`；当前代码实现中的核心类名、工厂方法和包路径仍保持 `OpsAgent` / `create_ops_agent()` / `agent_ops/`，以与现有代码一致。

## 当前状态

功能完整的 MVP，具备生产可用的核心骨架。已实现：

- `knowledge`：RAG-first 知识问答
- `read_only_ops`：K8s / Jenkins / 日志只读查询
- `investigation`：Stage-0 并行取证（5 工具同时采集，写入 OBSERVATIONS 层）
- `diagnosis`：多假设并行验证，5 层症状采集 + 拓扑驱动
- `mutation`：计划 + 审批门 + 执行，支持重启 / 扩缩容 / 回滚 / Jenkinsfile 生成 / 知识库索引
- `verification`：变更后自动轮询校验，失败自动回滚或升级告警
- `shared memory`：6 层分层共享记忆，带 RBAC 写入权限控制
- `session persistence`：Redis 持久化 session（7 天 TTL，可配置）
- `approval state machine`：完整审批状态机，approval_receipt 绑定 step + 签发人 + 有效期
- `audit`：全链路工具调用审计，含参数脱敏
- `SSE`：流式输出 route / tool_call / tool_result / message / done 事件
- `knowledge admin`：知识库管理 API + Web 页面（统计 / 文档列表 / 上传 / 向量检索）

## 架构

系统由六个 executor 组成完整闭环：

```mermaid
flowchart TD
    U["User / API / IM / CLI"] --> G["Gateway Layer"]
    G --> O["JARVIS"]
    O --> K["Agent Kernel\n(Planner + StateGraph + ToolRegistry + Memory + Approval)"]
    O --> R{"Router\n意图识别"}
    R -->|investigation| IV["InvestigatorExecutor\n5工具并行取证"]
    R -->|knowledge| KX["KnowledgeExecutor\nRAG-first"]
    R -->|read_only_ops| RO["ReadOnlyOpsExecutor\n确定性查询"]
    R -->|diagnosis| DX["DiagnosisExecutor\n多假设验证"]
    R -->|mutation| MX["MutationExecutor\n计划+审批+执行"]
    MX -->|变更成功后自动追加| VX["VerificationExecutor\n轮询校验+自动回滚"]

    IV -->|写 OBSERVATIONS| MEM["SessionStore\n(Redis / In-Memory)"]
    DX -->|读 OBSERVATIONS| MEM
    MX -->|写 PLANS/EXECUTION| MEM
    VX -->|写 VERIFICATION| MEM
```

主架构文档见 [docs/architecture-v2.md](./docs/architecture-v2.md)。  
Route-first 演进历史见 [docs/architecture-deep-dive.md](./docs/architecture-deep-dive.md)。  
Shared memory schema 和权限矩阵见 [docs/shared-memory-design.md](./docs/shared-memory-design.md)。

## 核心能力

| 路由 | 触发时机 | 工具 | 说明 |
|------|----------|------|------|
| `investigation` | 上下文有活跃告警 + 短消息 / `force_investigate=True` | `get_pod_status`, `get_deployment_status`, `get_k8s_events`, `search_logs`, `query_jenkins_build` | asyncio.gather 并行，写 OBSERVATIONS |
| `knowledge` | 环境信息 / SOP / 文档问答 | `query_knowledge` | RAG-first，非 ReAct |
| `read_only_ops` | 查询 Pod / Deployment / Jenkins / 日志 | `get_pod_status`, `get_deployment_status`, `get_service_info`, `get_pod_logs`, `query_jenkins_build`, `get_jenkins_build_log`, `search_logs`, `get_error_statistics` | 确定性查询，非 ReAct |
| `diagnosis` | 故障原因分析 / 排查 / 根因 | `diagnose_pod`, `get_pod_logs`, `get_k8s_events`, `query_jenkins_build`, `query_knowledge` | 多假设并行打分，5 层症状采集 |
| `mutation` | 重启 / 扩缩容 / 回滚 / 生成 / 索引 | `restart_deployment`, `scale_deployment`, `rollback_deployment`, `generate_jenkinsfile`, `index_documents` | 需审批，成功后自动追加 verification |
| `verification` | Planner 自动追加（mutation 成功后） | `get_deployment_status`, `rollback_deployment` | 轮询校验，超时自动回滚 |

## 17 个工具

| 类别 | 工具 | 有副作用 |
|------|------|---------|
| **K8s 只读** | `get_pod_status`, `get_deployment_status`, `get_service_info`, `get_pod_logs`, `get_k8s_events`, `diagnose_pod` | — |
| **K8s 写操作** | `restart_deployment`, `scale_deployment`, `rollback_deployment` | ✅ |
| **Jenkins** | `query_jenkins_build`, `get_jenkins_build_log`, `generate_jenkinsfile` | `generate_jenkinsfile` ✅ |
| **日志** | `search_logs`, `get_error_statistics` | — |
| **知识库** | `query_knowledge`, `index_documents` | `index_documents` ✅ |

## Mutation 执行闭环

```
用户请求 → MutationExecutor
    ├─ 提取目标、参数（正则 + 上下文补全）
    ├─ 构建 MutationPlan（action / VerificationCriteria / RollbackSpec）
    ├─ 检查 approval_receipt（绑定 step_id）
    ├─ 执行工具
    └─ 写 PLANS + EXECUTION 层
            │
            ▼ OpsPlanner._maybe_replan() 自动追加
            │
    VerificationExecutor
    ├─ 读 PLANS 层的 MutationPlan
    ├─ 轮询 get_deployment_status
    │   重启: 10s × 6 次 = 最长 60s
    │   扩容: 10s × 9 次 = 最长 90s
    │   回滚: 10s × 6 次 = 最长 60s
    ├─ 成功 → 写 VERIFICATION，返回确认
    └─ 失败 → rollback_deployment（预授权，无需二次审批）
              └─ 回滚失败 → 升级告警消息
```

## 两阶段告警调查（有限多 Agent）

```
模糊问题 / 活跃告警（ctx_has_incident + 消息 ≤ 6 词）
        │
        ▼
InvestigatorExecutor  ← Stage-0
  asyncio.gather 并行：
    ┌─ get_pod_status
    ├─ get_deployment_status
    ├─ get_k8s_events (Warning events)
    ├─ search_logs (ERROR, 最近 1h)
    └─ query_jenkins_build
  写 OBSERVATIONS 层
  返回结构化告警摘要 + 建议下一步
        │
        ▼
DiagnosisExecutor  ← 读 OBSERVATIONS，跳过重复采集
  多假设生成 + 并行取证 + 打分 → 根因输出
```

## Shared Memory

| 层 | 写入者 | 内容 |
|----|--------|------|
| `facts` | `knowledge_agent` | 环境事实（namespace / service / env） |
| `observations` | `read_ops_agent` | 工具观察（pod_status / error_summary / k8s_warning_events） |
| `hypotheses` | `diagnosis_agent` | 根因假设和置信分 |
| `plans` | `change_planner` | MutationPlan（含 VerificationCriteria / RollbackSpec） |
| `execution` | `change_executor` | 工具执行结果 |
| `verification` | `verification_agent` | 校验结论 / 回滚状态 / 升级信息 |

## 测试

84 个测试，全部通过：

```bash
python3 -m pytest -q
```

| 测试文件 | 内容 |
|----------|------|
| `tests/test_agent.py` | Ops vertical 功能回归（路由、工具、诊断、mutation、记忆） |
| `tests/test_patterns_approval_gate.py` | 审批门模式测试 |
| `tests/test_patterns_multi_hypothesis.py` | 多假设模式测试 |
| `tests/kernel_contract/test_kernel_contract.py` | Kernel 契约（自定义 route / memory / 审批 / 实例隔离） |
| `tests/e2e/test_architecture_v2_e2e.py` | 端到端场景测试 |

## 快速开始

### 1. 环境准备

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

要求：Python `>= 3.11`

### 2. 配置

```bash
cp .env.example .env
```

**LLM（至少配置一组）：**

```bash
# OpenAI
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o
OPENAI_API_KEY=sk-xxx
ROUTER_LLM_MODEL=gpt-4o-mini    # Router 用轻量模型

# DeepSeek
LLM_PROVIDER=deepseek
LLM_MODEL=deepseek-chat
OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=https://api.deepseek.com/v1

# Anthropic
LLM_PROVIDER=anthropic
LLM_MODEL=claude-sonnet-4-20250514
ANTHROPIC_API_KEY=sk-ant-xxx
```

**K8s：**

```bash
KUBECONFIG_PATH=/path/to/kubeconfig          # 可选，默认用集群内 ServiceAccount
K8S_ALLOWED_NAMESPACES=dev,staging,default   # 允许写操作的 namespace
K8S_READONLY_NAMESPACES=prod,production      # 只读 namespace
```

**Jenkins：**

```bash
JENKINS_URL=http://jenkins.internal
JENKINS_USER=admin
JENKINS_TOKEN=xxx
```

**日志系统（二选一）：**

```bash
# Elasticsearch（默认）
LOG_PROVIDER=elasticsearch
ELASTICSEARCH_URL=http://localhost:9200

# Loki
LOG_PROVIDER=loki
LOKI_URL=http://localhost:3100
```

**知识库：**

```bash
KNOWLEDGE_PG_DSN=postgresql://user:pass@localhost:5433/opsdb
EMBEDDING_PROVIDER=openai_compatible
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_API_KEY=sk-xxx
```

**Redis session 持久化：**

```bash
REDIS_URL=redis://localhost:6379/0
REDIS_SESSION_TTL_SECONDS=604800    # 7 天（默认）
```

**服务器：**

```bash
SERVER_HOST=0.0.0.0
SERVER_PORT=8000
```

### 3. 索引知识库

```bash
python main.py --index ./docs
```

### 4. 启动服务

```bash
python main.py
# 或
docker compose up -d
```

### 5. 交互式调试

```bash
python main.py --chat
```

## API

### 对话接口

```bash
# 非流式
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "查一下 staging 的 order-service pod 状态",
    "user_id": "dev@company.com",
    "user_role": "viewer",
    "context": {}
  }'

# 流式（SSE）
curl -N http://localhost:8000/api/chat/stream \
  -H "Content-Type: application/json" \
  -d '{
    "message": "帮我分析 staging 的 order-service 为什么报错",
    "user_id": "dev@company.com",
    "user_role": "operator"
  }'

# 触发告警调查
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "看看 payment-service 的情况",
    "user_id": "oncall@company.com",
    "user_role": "operator",
    "context": {"force_investigate": true}
  }'
```

SSE 事件类型：`route` / `tool_call` / `tool_result` / `message` / `sources` / `done`

### 知识库管理

```bash
GET  /api/knowledge/stats              # 统计信息
GET  /api/knowledge/documents          # 文档列表（分页）
GET  /api/knowledge/search?q=xxx       # 向量检索
POST /api/knowledge/index-directory    # 索引指定目录
POST /api/knowledge/upload             # 上传并索引文件
```

Web 管理页面：`http://localhost:8000/knowledge-admin`

### 其他

```bash
GET /health          # 健康检查
GET /api/tools       # 已注册工具列表
GET /api/audit       # 审计日志（支持 user_id 过滤）
```

## 多轮示例

```
1. "order-service 在哪个环境？"
   → knowledge：把 service/env/namespace 写入 facts 层

2. "帮我看看它的 pod 有没有报错"
   → read_only_ops：从 facts 层补全参数，查 pod + logs

3. "分析一下怎么处理"
   → diagnosis：读 facts + observations，多假设排查

4. "帮我重启一下"
   → mutation：构建 MutationPlan，等待 approval_receipt

5. [用户确认审批]
   → 执行 restart_deployment，Planner 追加 verification 步骤

6. [VerificationExecutor 自动]
   → 轮询 get_deployment_status，60s 内确认副本 Running
```

## 安全边界

- `Viewer` 角色不能走 mutation 路由
- 所有带副作用的工具需携带 `approval_receipt`，receipt 绑定 step_id + 有效期，不可跨步骤复用
- `rollback_deployment` 在 VerificationExecutor 内预授权（原 mutation 审批覆盖补偿动作）
- 所有工具调用经 `_invoke_tool` 统一审计，敏感参数脱敏处理
- K8s 写操作受 `K8S_ALLOWED_NAMESPACES` 约束，`K8S_READONLY_NAMESPACES` 中的 namespace 只读

## 项目结构

```text
ops-agent/
├── main.py
├── pyproject.toml
├── .env.example
├── Dockerfile
├── docker-compose.yml
├── config/
│   └── settings.py                  ← 所有环境变量配置
├── agent_kernel/                    ← 通用编排骨架（零 Ops 知识）
│   ├── base_agent.py                ← BaseAgent：LangGraph 图编排 + chat/chat_stream
│   ├── approval.py                  ← ApprovalPolicy 抽象 + approval_receipt 校验
│   ├── audit.py                     ← AuditLogger + 参数脱敏
│   ├── executor.py                  ← ExecutorBase 抽象
│   ├── planner.py                   ← Planner：Plan 生成 / advance / replan
│   ├── router.py                    ← RouterBase 抽象
│   ├── schemas.py                   ← Plan / PlanStep / ChatRequest / ToolCallEvent …
│   ├── session.py                   ← SessionStore（内存实现）
│   ├── session_redis.py             ← RedisSessionStore（7 天 TTL）
│   ├── memory/
│   │   ├── backend.py               ← MemoryBackend 接口
│   │   └── schema.py                ← MemorySchema + RBAC 校验
│   ├── patterns/
│   │   ├── approval_gate.py         ← 审批门可复用模式
│   │   └── multi_hypothesis.py      ← 多假设并行可复用模式
│   └── tools/
│       ├── mcp_gateway.py           ← MCPClient：MCP 服务器注册 + 工具加载
│       └── registry.py              ← ToolRegistry：本地 + MCP 工具统一注册
├── agent_ops/                       ← Ops 垂直
│   ├── agent.py                     ← JARVIS 装配入口（代码类名仍为 OpsAgent）
│   ├── planner.py                   ← OpsPlanner：_maybe_replan（自动追加 verification）
│   ├── router.py                    ← IntentRouter：关键词 + 上下文信号 + LLM fallback
│   ├── schemas.py                   ← AgentRoute / IntentType / Hypothesis / ServiceNode
│   ├── memory_schema.py             ← OPS_MEMORY_SCHEMA（6 层 RBAC）
│   ├── memory_hooks.py              ← store/load_mutation_plan, write_verification_memory
│   ├── mutation_plan.py             ← MutationPlan / VerificationCriteria / RollbackSpec
│   ├── risk_policy.py               ← OpsApprovalPolicy（namespace + 回滚预授权）
│   ├── extractors.py                ← extract_namespace / service_name / pod_name
│   ├── formatters.py                ← 各路由响应格式化
│   ├── tool_setup.py                ← 17 个工具注册
│   ├── topology.py                  ← ServiceTopology（服务依赖图）
│   └── executors/
│       ├── knowledge.py             ← KnowledgeExecutor
│       ├── read_only.py             ← ReadOnlyOpsExecutor
│       ├── investigator.py          ← InvestigatorExecutor（Stage-0 并行取证）
│       ├── diagnosis.py             ← DiagnosisExecutor（多假设）
│       ├── mutation.py              ← MutationExecutor
│       └── verification.py         ← VerificationExecutor（轮询 + 自动回滚）
├── gateway/
│   ├── app.py                       ← FastAPI（chat / knowledge / audit / tools）
│   └── adapters/
│       └── im_adapter.py            ← IM 适配骨架
├── llm_gateway/
│   └── __init__.py                  ← LLM 提供商抽象（OpenAI / DeepSeek / Anthropic）
├── tools/
│   ├── k8s_tool/                    ← 9 个 K8s 工具（含 3 个写操作）
│   ├── jenkins_tool/                ← 3 个 Jenkins 工具
│   ├── log_tool/                    ← 2 个日志工具
│   └── knowledge_tool/             ← 2 个知识库工具（含 index）
├── docs/
│   ├── architecture-v2.md
│   ├── architecture-deep-dive.md
│   └── shared-memory-design.md
└── tests/
    ├── test_agent.py
    ├── test_patterns_approval_gate.py
    ├── test_patterns_multi_hypothesis.py
    ├── kernel_contract/
    │   └── test_kernel_contract.py
    └── e2e/
        └── test_architecture_v2_e2e.py
```

## 已知限制

- `main.py` 和 `gateway/app.py` 使用 `reload=True`，生产部署需去掉
- `RedisSessionStore` 使用同步 redis 客户端，高并发场景建议换 `redis.asyncio`
- IM adapter 目前是骨架，未对接真实 IM 平台（钉钉 / 飞书 / Slack）
- 工具层需真实 K8s / Jenkins 环境，本地无环境时返回错误响应
- `agent_core/` 目录保留（历史残余，不再使用）

## 演进方向

- 第二个 Vertical（DocAgent / JiraAgent），验证 Kernel 真正通用
- 接入 MCP + tool retrieval，替换静态 17 工具列表
- Router 升级为支持图内回跳的 meta-planner
- `RedisSessionStore` 切换为 `redis.asyncio` 异步客户端
- 对接真实 IM 平台
- 建立 eval harness + incident case 反向沉淀

完整差距分析见 [docs/architecture-deep-dive.md §8](./docs/architecture-deep-dive.md)。
