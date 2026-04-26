"""Generate JARVIS architecture briefing — fully self-contained HTML.

No external CDN dependencies. Works in htmlpreview.github.io, local file://, anywhere.

Run:
    python scripts/build_html.py

Output: docs/index.html

View online (no download):
    https://htmlpreview.github.io/?https://raw.githubusercontent.com/lichao01111-dot/ops-agent/main/docs/index.html
"""
from __future__ import annotations
from pathlib import Path

# ── palette ──────────────────────────────────────────────────────────────────
NAVY      = "#1F3A5F"
BLUE      = "#2E5BBA"
LBLUE     = "#E8F1FA"
ACCENT    = "#F6922A"
DGRAY     = "#333333"
MGRAY     = "#666666"
LGRAY     = "#F5F5F5"
WHITE     = "#FFFFFF"
GREEN     = "#2EA06B"
RED       = "#D04545"
PURPLE    = "#7A4FB5"
CODEBG    = "#2B2B2B"
CODEFG    = "#E0E0E0"

# ── HTML helpers ─────────────────────────────────────────────────────────────

def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def code_block(lines: list[str]) -> str:
    inner = esc("\n".join(lines))
    return (f'<pre style="background:{CODEBG};color:{CODEFG};border-radius:6px;'
            f'padding:.7rem 1rem;font-size:.72em;overflow-x:auto;'
            f'white-space:pre;font-family:Menlo,Consolas,monospace;'
            f'margin:.4rem 0">{inner}</pre>')

def two_col(left: str, right: str, ratio: str = "1fr 1fr") -> str:
    return (f'<div style="display:grid;grid-template-columns:{ratio};'
            f'gap:1rem;height:calc(100% - 5rem)">'
            f'<div>{left}</div><div>{right}</div></div>')

def card(content: str, bg: str = LBLUE, border: str = BLUE) -> str:
    return (f'<div style="background:{bg};border-left:4px solid {border};'
            f'border-radius:8px;padding:.8rem;height:100%;box-sizing:border-box">'
            f'{content}</div>')

def hdr(text: str, color: str = NAVY) -> str:
    return f'<div style="font-weight:700;color:{color};margin-bottom:.4rem">{text}</div>'

def badge(text: str, bg: str = BLUE, fg: str = WHITE) -> str:
    return (f'<span style="background:{bg};color:{fg};border-radius:4px;'
            f'padding:1px 8px;font-size:.8em;font-weight:700">{text}</span>')

def ul(*items, color: str = DGRAY, size: str = ".85em") -> str:
    lis = "".join(f'<li style="color:{color};font-size:{size};margin:.2rem 0">{i}</li>'
                  for i in items)
    return f'<ul style="margin:.3rem 0;padding-left:1.3em">{lis}</ul>'

def bottom_bar(text: str) -> str:
    return (f'<div style="background:{NAVY};color:{WHITE};border-radius:6px;'
            f'padding:.5rem 1rem;text-align:center;font-weight:700;'
            f'font-size:.88em;margin-top:.6rem">{text}</div>')

def table(headers: list[str], rows: list[list[str]],
          col_colors: list[str] | None = None) -> str:
    ths = "".join(f'<th style="background:{NAVY};color:{WHITE};padding:.35rem .6rem;'
                  f'text-align:left">{h}</th>' for h in headers)
    trs = ""
    for i, row in enumerate(rows):
        bg = WHITE if i % 2 == 0 else LGRAY
        cells = ""
        for j, cell in enumerate(row):
            color = (col_colors[j] if col_colors else DGRAY) if j < len(row) else DGRAY
            cells += (f'<td style="padding:.3rem .6rem;border:1px solid #ddd;'
                      f'color:{color};font-size:.83em">{cell}</td>')
        trs += f'<tr style="background:{bg}">{cells}</tr>'
    return (f'<table style="width:100%;border-collapse:collapse;font-size:.85em">'
            f'<thead><tr>{ths}</tr></thead><tbody>{trs}</tbody></table>')

def flow_row(num: str, title: str, body: str, color: str = ACCENT) -> str:
    return (f'<div style="display:grid;grid-template-columns:1.6rem 110px 1fr;'
            f'gap:.5rem;align-items:center;background:{LGRAY};'
            f'border-radius:4px;padding:.3rem .5rem;margin:.2rem 0">'
            f'<div style="background:{color};color:{WHITE};border-radius:50%;'
            f'width:1.4rem;height:1.4rem;display:flex;align-items:center;'
            f'justify-content:center;font-weight:700;font-size:.78em">{num}</div>'
            f'<strong style="color:{NAVY};font-size:.82em">{title}</strong>'
            f'<span style="font-family:Menlo,Consolas,monospace;font-size:.78em;'
            f'color:{DGRAY}">{body}</span></div>')

def section(chapter: str, title: str, subtitle: str, body: str) -> str:
    ch_html = (f'<div style="font-size:.68em;color:{ACCENT};font-weight:700;'
               f'letter-spacing:.06em;margin-bottom:.15rem">{chapter}</div>'
               if chapter else "")
    sub_html = (f'<p style="font-size:.82em;color:{MGRAY};margin:.1rem 0 .5rem">'
                f'{subtitle}</p>' if subtitle else "")
    return f"""\
<div class="slide">
  <div class="slide-inner">
    {ch_html}
    <h2 style="color:{NAVY};margin:0 0 .15rem;font-size:1.35em">{title}</h2>
    {sub_html}
    <div style="height:3px;background:{BLUE};border-radius:2px;margin:.3rem 0 .6rem"></div>
    {body}
  </div>
</div>"""

# ── slides ────────────────────────────────────────────────────────────────────

def s01():
    return f"""\
<div class="slide" style="background:{NAVY}">
  <div style="display:flex;height:100%;align-items:center;padding:0 5%">
    <div style="border-left:6px solid {ACCENT};padding-left:2rem">
      <h1 style="color:{WHITE};font-size:2.6em;margin:0">JARVIS 架构详解</h1>
      <p style="color:#CCDDEE;font-size:1.35em;margin:.6rem 0">
        Agent Kernel + Vertical Agent</p>
      <p style="color:#9AAACC;font-size:.95em;margin:0">
        从设计原则到实现细节 · 全栈技术汇报</p>
      <p style="color:#667788;font-size:.8em;margin-top:2rem">2026 · 技术评审</p>
    </div>
  </div>
</div>"""


def s02():
    chapters = [
        ("Part 1", "背景与问题",          "为什么要重构",                    "p.3–5",  BLUE),
        ("Part 2", "整体分层",            "Kernel / Vertical / Supervisor",  "p.6–8",  GREEN),
        ("Part 3", "Agent Kernel 深度讲解","每个组件的职责与接口",            "p.9–18", PURPLE),
        ("Part 4", "JARVIS 垂直实现",   "作为第一个 Vertical 样例",        "p.19–22",ACCENT),
        ("Part 5", "关键执行流程",         "审批 / 诊断 / 复合请求",          "p.23–26",BLUE),
        ("Part 6", "插件化与扩展",         "10 个插件点 + 新 Vertical 清单",  "p.27–28",GREEN),
        ("Part 7", "演进方向与测试",       "降级 / 测试 / Supervisor / 路线图","p.29–32",PURPLE),
    ]
    rows = ""
    for tag, name, desc, pages, color in chapters:
        rows += (f'<div style="display:grid;grid-template-columns:75px 1fr 55px;'
                 f'gap:.5rem;align-items:center;padding:.35rem .5rem;'
                 f'background:{LGRAY};border-radius:5px;margin:.25rem 0">'
                 f'<span style="background:{color};color:{WHITE};border-radius:4px;'
                 f'padding:1px 6px;font-size:.78em;font-weight:700;text-align:center">{tag}</span>'
                 f'<div><strong style="color:{NAVY};font-size:.9em">{name}</strong> '
                 f'<span style="color:{MGRAY};font-size:.8em">— {desc}</span></div>'
                 f'<span style="color:{MGRAY};font-size:.78em;text-align:right">{pages}</span>'
                 f'</div>')
    return section("", "目录", "Table of Contents", rows)


def s03():
    pains = [
        ("🔀", "路由退化", RED,
         "多领域关键词冲突，\"重启\"既是运维又是客服。单 Agent 关键词路由准确率断崖下跌。"),
        ("🧨", "安全边界失守", ACCENT,
         "不同业务审批流差异巨大：重启 Pod/发起转账/修改薪资，审批人和阈值完全不同。"),
        ("🧠", "工具选错", PURPLE,
         "LLM 面对 &gt;20 个工具开始犯糊涂，工具检索准确率明显退化 — 模型能力天花板。"),
        ("🧴", "记忆串味", BLUE,
         "FACTS/HYPOTHESES 只适合诊断场景，到了客服、数据领域强行复用会污染语义。"),
    ]
    cards = "".join(
        f'<div style="background:{LGRAY};border-left:4px solid {c};'
        f'border-radius:6px;padding:.5rem .7rem">'
        f'<div style="font-size:1.3em">{e} '
        f'<strong style="color:{c}">{t}</strong></div>'
        f'<p style="margin:.2rem 0 0;font-size:.82em;color:{DGRAY}">{b}</p></div>'
        for e, t, c, b in pains
    )
    grid = f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:.6rem">{cards}</div>'
    return section("PART 1  背景与问题", "旧架构的根本问题",
                   "所有逻辑塞进一个 Agent，最终会崩塌在四个方向",
                   grid + bottom_bar("结论：窄而深的垂直 Agent 是护城河；一个 Agent 吃天下是陷阱"))


def s04():
    def col_items(items, color, mark):
        return "".join(
            f'<div style="font-size:.8em;color:{color};margin:.18rem 0">{mark} {i}</div>'
            for i in items
        )
    left_items = ["Planner / PlanStep / advance", "LangGraph StateGraph 编排",
                  "ToolRegistry + MCP Gateway", "DiagnosisExecutor 多假设模式",
                  "MemorySchema + RBAC 分层", "ApprovalPolicy + AuditLogger",
                  "双入口 chat / chat_stream", "三级降级（L1 / L2 / L3）"]
    right_items = ["tools/* K8s/Jenkins/Logs 12 个", "config/topology.yaml 服务拓扑",
                   "router.py 运维关键词映射", "extract_namespace/pod/service",
                   "_plan_read_only_tool 只读工具", "_build_pipeline_plan 发布流程",
                   "_format_single_read_only_result", "_update_memory_from_tool_output"]
    left  = card(hdr("70% 可复用骨架（领域无关）", BLUE) + col_items(left_items, NAVY, "✓"),  LBLUE, BLUE)
    right = card(hdr("30% 业务逻辑（Ops 特有）",  ACCENT) + col_items(right_items, DGRAY, "●"), "#fff2e6", ACCENT)
    return section("PART 1  背景与问题", "核心判断：70% 骨架无关领域，30% 才是业务",
                   "这是整个架构重构的出发点",
                   two_col(left, right) + bottom_bar("把 70% 抽成 Agent Kernel → 新垂直 Agent 只写 30%"))


def s05():
    ps = [("1","Kernel 零领域知识","任何关键词、工具名、业务 Schema 都不许进 Kernel。"),
          ("2","插件点显式声明","Kernel 通过抽象基类 + 依赖注入暴露扩展点，不通过猜测或反射。"),
          ("3","契约先于实现","Pydantic 定义 Plan / PlanStep / ToolSpec / MemoryItem，跨 Vertical 共享。"),
          ("4","安全边界不可绕过","RBAC、Approval、side_effect 由 Kernel 强制执行，Vertical 只能填配置。"),
          ("5","每个 Vertical 独立实例","各自的工具、路由、记忆语义，不共享运行时状态。"),
          ("6","Supervisor 是 Agent 的 Planner","上层调度不关心下层怎么干，只发 sub-plan 给具名子 Agent。")]
    rows = "".join(
        f'<div style="display:flex;gap:.7rem;align-items:flex-start;'
        f'padding:.4rem;border-bottom:1px solid #eee">'
        f'<div style="background:{BLUE};color:{WHITE};border-radius:50%;'
        f'width:1.6rem;height:1.6rem;display:flex;align-items:center;'
        f'justify-content:center;font-weight:700;flex-shrink:0;font-size:.85em">{n}</div>'
        f'<div><strong style="color:{NAVY};font-size:.88em">{t}</strong>'
        f'<p style="margin:.1rem 0 0;font-size:.78em;color:{DGRAY}">{b}</p></div></div>'
        for n, t, b in ps
    )
    return section("PART 1  背景与问题", "六条设计原则", "Kernel 和 Vertical 分界线的裁判", rows)


def s06():
    verts = [("JARVIS","运维（已落地）",GREEN),("CsmAgent","客服（未来）",MGRAY),
             ("DataAgent","数据（未来）",MGRAY),("DocAgent","文档（未来）",MGRAY)]
    vcards = "".join(
        f'<div style="background:{c};color:{WHITE};border-radius:6px;'
        f'padding:.4rem;text-align:center;font-weight:700;font-size:.82em">'
        f'{n}<br/><span style="font-weight:400;font-size:.85em">{s}</span></div>'
        for n, s, c in verts
    )
    comps = ["Planner","Router","Executor","ToolRegistry","MCPClient",
             "MemorySchema","MemoryBackend","Approval","Audit","Session"]
    kboxes = "".join(
        f'<div style="background:{WHITE};border:1px solid {BLUE};border-radius:4px;'
        f'padding:.2rem .4rem;font-size:.72em;color:{NAVY};font-weight:600">{c}</div>'
        for c in comps
    )
    body = f"""
<div style="background:{LGRAY};border:2px dashed {MGRAY};border-radius:8px;
            padding:.5rem;text-align:center;color:{MGRAY};font-size:.82em;margin-bottom:.4rem">
  🔮 <strong>Supervisor（演进方向）</strong> — MetaPlanner · PlanStep.execution_target · 跨 Agent 联合审批
</div>
<div style="text-align:center;color:{MGRAY};font-size:1.2em">↓ 派发任务</div>
<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:.4rem;margin:.2rem 0">{vcards}</div>
<div style="text-align:center;color:{MGRAY};font-size:1.2em">↓ 调用 Kernel</div>
<div style="background:{LBLUE};border:2px solid {BLUE};border-radius:8px;padding:.5rem">
  <strong style="color:{NAVY}">🧱 Agent Kernel（领域无关骨架）</strong>
  <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:.25rem;margin-top:.3rem">{kboxes}</div>
</div>"""
    return section("PART 2  整体分层", "三层总图：Supervisor / Vertical / Kernel",
                   "从下向上是依赖关系，从上向下是调度关系", body)


def s07():
    def mk(items):
        return "".join(
            f'<div style="font-size:.8em;margin:.2rem 0">'
            f'<span style="color:{GREEN if s=="✅" else RED};font-weight:700">{s}</span> '
            f'<span style="color:{DGRAY}">{t}</span>'
            f'{"<br/><span style=&quot;color:"+MGRAY+";font-size:.85em&quot;>"+sub+"</span>" if sub else ""}'
            f'</div>'
            for s, t, sub in items
        )
    kernel = [("✅","编排/执行/审计/脱敏/降级","通用能力"),
              ("✅","抽象基类 ExecutorBase / RouterBase","插件契约"),
              ("✅","Pydantic Schema：Plan / PlanStep / ToolSpec","数据契约"),
              ("✅","RouteKey / MemoryLayerKey 等可注册字符串","开放契约"),
              ("❌","不许出现 pod / jenkins / 重启 等业务词",""),
              ("❌","不许硬编码路由、工具名、关键词",""),
              ("❌","不许导入 agent_ops 任何模块","")]
    vertical = [("✅","业务工具（K8s / Jenkins / CRM / SQL）","调用链"),
                ("✅","业务关键词路由（\"重启\" → mutation）","Router 子类"),
                ("✅","风险审批规则（生产 + mutation = 必审批）","ApprovalPolicy"),
                ("✅","记忆层定义（observations / hypotheses）","MemorySchema"),
                ("✅","业务 Prompt 和诊断打分启发式","_heuristic_score"),
                ("✅","业务格式化输出（formatters.py）",""),
                ("✅","Planner 子类覆写复合拆分规则","OpsPlanner")]
    left  = card(hdr("🧱 Agent Kernel 必须是什么", BLUE) + mk(kernel), LBLUE, BLUE)
    right = card(hdr("🎯 Vertical Agent 才能是什么", ACCENT) + mk(vertical), "#fff2e6", ACCENT)
    return section("PART 2  整体分层", "Kernel vs Vertical：什么归谁？",
                   "这条边界是架构评审的准绳 — 违反它就是反模式",
                   two_col(left, right))


def s08():
    def tree(lines, color):
        return "".join(
            f'<div style="font-family:Menlo,Consolas,monospace;font-size:.72em;'
            f'color:{color};white-space:pre;margin:.05rem 0">{l}</div>'
            for l in lines
        )
    kernel_tree = ["base_agent.py        # BaseAgent + LangGraph 图",
                   "planner.py           # Planner + advance/replan",
                   "router.py            # RouterBase",
                   "executor.py          # ExecutorBase/FunctionExecutor",
                   "schemas.py           # Plan/PlanStep/ToolSpec...",
                   "approval.py          # ApprovalPolicy+ApprovalDecision",
                   "audit.py             # AuditLogger+sanitizer/sink",
                   "session.py           # SessionStore ABC+InMemory",
                   "memory/schema.py     # MemorySchema (RBAC)",
                   "memory/backend.py    # MemoryBackend ABC",
                   "tools/registry.py   # ToolRegistry",
                   "tools/mcp_gateway.py # MCP Client",
                   "patterns/            # 可选基类库",
                   "  multi_hypothesis.py",
                   "  approval_gate.py"]
    ops_tree = ["agent.py             # JARVIS 装配（代码类名仍为 OpsAgent）",
                "router.py            # IntentRouter (关键词)",
                "planner.py           # OpsPlanner (中文拆分)",
                "risk_policy.py       # OpsApprovalPolicy",
                "memory_schema.py     # OPS_MEMORY_SCHEMA 6层",
                "extractors.py        # namespace/pod/service",
                "formatters.py        # 结果格式化",
                "memory_hooks.py      # tool→memory 规则",
                "topology.py          # ServiceTopology",
                "tool_setup.py        # 12个 Ops 工具注册",
                "executors/knowledge.py",
                "executors/read_only.py",
                "executors/diagnosis.py  # 多假设",
                "executors/mutation.py"]
    left  = card(hdr("📁 agent_kernel/", BLUE)  + tree(kernel_tree, NAVY),   LBLUE, BLUE)
    right = card(hdr("📁 agent_ops/",    ACCENT) + tree(ops_tree,   DGRAY),   "#fff2e6", ACCENT)
    return section("PART 2  整体分层",
                   "代码组织：agent_kernel/ 和 agent_ops/ 两棵树",
                   "每个目录都对应架构图上的一个盒子", two_col(left, right))


def s09():
    rows = [["BaseAgent",      "装配 Planner+Executors+Session，暴露 chat/chat_stream",    "base_agent.py"],
            ["Planner",        "生成 Plan、advance/replan、max_iterations 预算",            "planner.py"],
            ["RouterBase",     "把自然语言请求 → RouteDecision",                            "router.py · route()"],
            ["ExecutorBase",   "单个路由的执行器抽象基类",                                   "executor.py · execute()"],
            ["ToolRegistry",   "本地 + MCP 工具的统一 ToolSpec + retrieve",                 "tools/registry.py"],
            ["MCPClient",      "MCP 服务器网关，零代码接入远程工具",                          "tools/mcp_gateway.py"],
            ["MemorySchema",   "记忆层定义 + RBAC 白名单",                                  "memory/schema.py"],
            ["MemoryBackend",  "共享记忆存储接口（InMemory / Redis / DB）",                  "memory/backend.py"],
            ["ApprovalPolicy", "审批策略接口 + ApprovalReceipt 校验",                       "approval.py"],
            ["AuditLogger",    "审计日志 + sanitizer/sink 扩展钩子",                        "audit.py"],
            ["SessionStore",   "会话 + 消息历史 + 记忆条目存储",                              "session.py"]]
    ths = ["组件", "职责", "关键接口 / 文件"]
    trs = ""
    for i, row in enumerate(rows):
        bg = WHITE if i % 2 == 0 else LGRAY
        trs += (f'<tr style="background:{bg}">'
                f'<td style="padding:.3rem .5rem;border:1px solid #ddd;'
                f'font-family:Menlo,Consolas,monospace;font-weight:700;'
                f'color:{NAVY};font-size:.8em">{row[0]}</td>'
                f'<td style="padding:.3rem .5rem;border:1px solid #ddd;'
                f'font-size:.8em;color:{DGRAY}">{row[1]}</td>'
                f'<td style="padding:.3rem .5rem;border:1px solid #ddd;'
                f'font-family:Menlo,Consolas,monospace;font-size:.75em;'
                f'color:{MGRAY}">{row[2]}</td></tr>')
    body = (f'<table style="width:100%;border-collapse:collapse">'
            f'<thead><tr>'
            + "".join(f'<th style="background:{NAVY};color:{WHITE};padding:.35rem .5rem;'
                      f'text-align:left;font-size:.82em">{h}</th>' for h in ths)
            + f'</tr></thead><tbody>{trs}</tbody></table>')
    return section("PART 3  Agent Kernel 深度讲解",
                   "Kernel 的 11 个核心组件",
                   "每个组件都有明确职责边界和可测试的接口", body)


def s10():
    body = f"""
<div style="text-align:center;padding:.5rem 0">
  <div style="display:inline-block;background:{MGRAY};color:{WHITE};
              border-radius:5px;padding:.25rem .8rem;font-size:.82em">▶ entry_point</div>
  <div style="color:{MGRAY};font-size:1.3em">↓</div>
  <div style="display:inline-block;background:{NAVY};color:{WHITE};
              border-radius:8px;padding:.45rem 2rem;font-weight:700;
              font-family:Menlo,Consolas,monospace">planner — _planner_node</div>
  <div style="color:{MGRAY};font-size:1.3em">↓ conditional edges</div>
  <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:.5rem;
              max-width:680px;margin:.3rem auto">
    <div style="background:{GREEN};color:{WHITE};border-radius:6px;padding:.45rem;
                text-align:center;font-family:Menlo,Consolas,monospace;font-size:.82em">knowledge</div>
    <div style="background:{BLUE};color:{WHITE};border-radius:6px;padding:.45rem;
                text-align:center;font-family:Menlo,Consolas,monospace;font-size:.82em">read_only_ops</div>
    <div style="background:{PURPLE};color:{WHITE};border-radius:6px;padding:.45rem;
                text-align:center;font-family:Menlo,Consolas,monospace;font-size:.82em">diagnosis</div>
    <div style="background:{ACCENT};color:{WHITE};border-radius:6px;padding:.45rem;
                text-align:center;font-family:Menlo,Consolas,monospace;font-size:.82em">mutation</div>
  </div>
  <div style="color:{MGRAY};font-size:1.3em">↓ advance()</div>
  <div style="display:inline-block;background:{RED};color:{WHITE};
              border-radius:5px;padding:.25rem 1.2rem;
              font-family:Menlo,Consolas,monospace">finish — END</div>
</div>
<div style="background:{LBLUE};border-radius:8px;padding:.5rem .8rem;margin-top:.5rem">
  <strong style="color:{NAVY}">🔑 关键点</strong>
  {ul("执行器节点不是写死的 — 由 Vertical 传入的 executors 列表决定，BaseAgent 动态 add_node",
      "每个 executor 执行完必须回到 planner，由 advance() 决定 CONTINUE / REPLAN / FINISH",
      "execution_target 字段优先于 route，为未来 Supervisor 跨 Agent 派发预留语义")}
</div>"""
    return section("PART 3  Agent Kernel 深度讲解",
                   "BaseAgent 的核心图（LangGraph StateGraph）",
                   "planner 节点是中心，executors 是动态扇出", body)


def s11():
    left  = card(hdr("initial_plan(request) → Plan", BLUE) + code_block([
        "1. self._split_compound(message)",
        "   → Kernel 默认返回 [message]",
        "   → OpsPlanner 覆写中文拆分",
        "",
        "2. for each segment:",
        "     decision = await router.route()",
        "     step = PlanStep(route, intent, goal)",
        "",
        "3. 串成 Plan，带 depends_on 关系",
        "",
        "# 扩展点",
        "_split_compound(msg) -> list[str]",
        "_dedupe_segments(segs, limit=3)",
    ]), LBLUE, BLUE)
    right = card(hdr("advance(plan, last_step) → Decision", GREEN) + code_block([
        "1. iterations ≥ max_iterations",
        "   → FINISH（防 AI 死循环，默认 6）",
        "",
        "2. last_step.status == FAILED",
        "   → FINISH（fail-fast）",
        "",
        "3. 还有 PENDING 步骤",
        "   → CONTINUE",
        "",
        "4. _maybe_replan(plan, last)",
        "   → REPLAN（Vertical 覆写追加步骤）",
        "",
        "5. 否则 → FINISH",
    ]), "#e5f4e9", GREEN)
    return section("PART 3  Agent Kernel 深度讲解",
                   "Planner — 计划生成 + 前进决策",
                   "两大职责：initial_plan() 和 advance()", two_col(left, right))


def s12():
    left  = card(hdr("契约：RouterBase (ABC)", BLUE) + code_block([
        "class RouterBase(ABC):",
        "    @abstractmethod",
        "    async def route(",
        "        self, request: ChatRequest",
        "    ) -> RouteDecision: ...",
        "",
        "# RouteDecision 字段",
        "intent:            IntentTypeKey",
        "route:             RouteKey",
        "risk_level:        LOW/MEDIUM/HIGH/CRITICAL",
        "requires_approval: bool",
        "rationale:         str",
    ]), LBLUE, BLUE)
    right = card(hdr("Ops 实现：IntentRouter 关键词路由", ACCENT) + code_block([
        '"MySQL / 地址 / 密码"',
        "  → knowledge / LOW / no-approval",
        "",
        '"pod / 日志 / 状态 / 查"',
        "  → read_only_ops / LOW",
        "",
        '"为什么 / 根因 / 故障"',
        "  → diagnosis / MEDIUM",
        "",
        '"重启 / 部署 / 回滚"',
        "  → mutation / HIGH / 必审批",
        "",
        "# 进阶可换成 LLM-based Router",
        "# 只要继承 RouterBase 即可",
    ]), "#fff2e6", ACCENT)
    return section("PART 3  Agent Kernel 深度讲解",
                   "Router — 意图识别与路由决策",
                   "从自然语言到 RouteDecision", two_col(left, right))


def s13():
    patterns = [
        ("MultiHypothesisExecutor", PURPLE,
         "多假设并行诊断 · 5-stage pipeline",
         ["_collect_symptoms", "_generate_hypotheses",
          "_evidence_args_for", "_score_and_summarize", "_persist"]),
        ("ApprovalGateExecutor", ACCENT,
         "审批闸门 · 先验 receipt 再放行",
         ["_execute_approved  (receipt 通过后才调用)",
          "_denial_message    (可覆写拒绝话术)"]),
    ]
    pcards = ""
    for name, color, desc, hooks in patterns:
        hooks_html = "".join(
            f'<div style="font-family:Menlo,Consolas,monospace;font-size:.78em;'
            f'color:{DGRAY};margin:.12rem 0">✓ {esc(h)}</div>' for h in hooks
        )
        pcards += (f'<div style="background:{LGRAY};border-radius:8px;overflow:hidden">'
                   f'<div style="background:{color};color:{WHITE};padding:.3rem .6rem;'
                   f'font-family:Menlo,Consolas,monospace;font-weight:700;font-size:.82em">{name}</div>'
                   f'<div style="padding:.45rem .6rem">'
                   f'<p style="font-size:.8em;color:{DGRAY};margin:.15rem 0">{desc}</p>'
                   f'{hooks_html}</div></div>')
    body = (code_block([
        "class ExecutorBase(ABC):",
        "    def __init__(self, *, node_name: str, route_name: str):",
        "        self.node_name  = node_name   # LangGraph 节点名",
        "        self.route_name = route_name  # 逻辑路由名",
        "",
        "    @abstractmethod",
        "    async def execute(self, state: dict, event_callback=None) -> dict:",
        '        """返回 {final_message, tool_calls, sources, ...}"""',
    ])
    + f'<p style="font-weight:700;color:{NAVY};margin:.4rem 0 .25rem">'
      f'Kernel 内置可选基类（agent_kernel/patterns/）</p>'
    + f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:.5rem">{pcards}</div>')
    return section("PART 3  Agent Kernel 深度讲解",
                   "ExecutorBase — 执行器抽象基类",
                   "每个 route 对应一个 Executor；Vertical 自由扩展", body)


def s14():
    left  = card(hdr("ToolSpec：工具元数据契约", BLUE) + code_block([
        "class ToolSpec(BaseModel):",
        "    name:              str",
        "    description:       str",
        "    tags:              list[str] = []",
        "    route_affinity:    list[str] = []",
        "    side_effect:       bool = False",
        "    source:            ToolSource  # LOCAL/MCP",
        "    parameters_schema: dict = {}",
    ]) + ul("side_effect=True → Kernel 自动要求 approval",
            "route_affinity → retrieve 时优先匹配该路由工具",
            "source=MCP → 远端工具，通过 MCPClient 转发"), LBLUE, BLUE)
    right = card(hdr("MCP Gateway：远程工具零代码接入", ACCENT) + code_block([
        "# 典型调用链",
        "1. MCPClient.register_server(name, url)",
        "2. await client.load_tools(name)",
        "3. for spec in discovered:",
        "       registry.register_mcp(spec, handler)",
        "",
        "4. registry.retrieve(goal, route, top_k)",
        "   → 本地 + MCP 工具一起返回",
        "",
        "5. handler.ainvoke(args)",
        "   → 本地直接执行 / MCP 转发远端",
        "",
        "# 效果：不写一行代码接入",
        "# 任意 MCP-compatible 工具服务",
    ]), "#fff2e6", ACCENT)
    return section("PART 3  Agent Kernel 深度讲解",
                   "ToolRegistry + MCP Gateway",
                   "本地工具 + 远程 MCP 工具统一 ToolSpec，统一 retrieve",
                   two_col(left, right))


def s15():
    left  = card(hdr("MemorySchema — RBAC 层定义", BLUE) + code_block([
        'schema = MemorySchema(layers={',
        '    "facts":        {"knowledge"},',
        '    "observations": {"read_ops"},',
        '    "hypotheses":   {"diagnosis"},',
        '    "plans":        {"planner"},',
        '})',
        '',
        '# 关键 API',
        'schema.assert_can_write(layer, writer)',
        '# 非白名单 → PermissionError',
        '',
        'session_store.write_memory_item(...)',
        '# 每次写入自动调 assert_can_write',
    ]), LBLUE, BLUE)
    right = card(hdr("MemoryBackend — 存储可替换", GREEN) + code_block([
        "class MemoryBackend(ABC):",
        "    def get(session, key): ...",
        "    def put(session, key, val): ...",
        "    def list(session, prefix): ...",
        "",
        "# 已有实现",
        "InMemoryMemoryBackend  (默认)",
        "",
        "# 未来可替换",
        "RedisMemoryBackend",
        "PostgresMemoryBackend",
        "VectorMemoryBackend",
        "",
        "# §5.5：每个 Vertical 持有独立实例",
        "#       不共享运行时状态",
    ]), "#e5f4e9", GREEN)
    return section("PART 3  Agent Kernel 深度讲解",
                   "Memory — 分层记忆 + RBAC + 后端抽象",
                   "Schema 管谁能写什么层；Backend 管怎么存", two_col(left, right))


def s16():
    gates = [("①","step 存在且 requires_approval=True","否则拒绝"),
             ("②","context 里有合法的 approval_receipt dict","否则拒绝"),
             ("③","receipt.step_id == 当前 step.step_id","防止凭据复用"),
             ("④","receipt.expires_at > 现在","过期即失效"),
             ("⑤","Vertical 可覆写 validate_receipt 额外校验","金额/namespace")]
    gates_html = "".join(
        f'<div style="display:flex;gap:.5rem;align-items:center;margin:.28rem 0">'
        f'<span style="background:{ACCENT};color:{WHITE};border-radius:4px;'
        f'padding:1px 7px;font-weight:700;flex-shrink:0">{n}</span>'
        f'<div style="background:{LGRAY};padding:.28rem .5rem;border-radius:4px;'
        f'flex:1;font-size:.83em"><strong>{g}</strong> '
        f'<span style="color:{MGRAY}">→ 失败: {note}</span></div></div>'
        for n, g, note in gates
    )
    body = (f'<div style="background:{LGRAY};border-radius:8px;'
            f'padding:.6rem .8rem;margin-bottom:.5rem">'
            f'<strong style="color:{NAVY}">ApprovalReceipt 的 5 个关键字段</strong>'
            + code_block([
                "receipt_id   — 唯一凭据 ID，可追溯",
                "step_id      — 绑定到具体 PlanStep（换步骤就失效）",
                "approved_by  — 谁批的",
                "scope        — 生效范围（某个 namespace / 某笔金额）",
                "expires_at   — 过期时间（默认几分钟）",
            ]) + '</div>'
            + f'<p style="font-weight:700;color:{NAVY};margin:.3rem 0 .2rem">'
              f'ApprovalPolicy.evaluate() 验证流程</p>'
            + gates_html)
    return section("PART 3  Agent Kernel 深度讲解",
                   "Approval — 审批凭据绑定步骤",
                   "只认 ApprovalReceipt，不认 context.approved=true", body)


def s17():
    left  = card(hdr("AuditEntry 字段", BLUE) + code_block([
        "timestamp       datetime",
        "user_id         str",
        "session_id      str",
        "intent          Optional[IntentTypeKey]",
        "route           Optional[RouteKey]",
        "risk_level      Optional[RiskLevel]",
        "needs_approval  bool",
        "tool_name       Optional[str]",
        "tool_calls      list[str]",
        "params          dict  # ← sanitize 目标",
        "result_summary  str",
        "success         bool",
        "duration_ms     int",
    ]), LBLUE, BLUE)
    right = card(hdr("扩展钩子：sanitizer / sink", GREEN) + code_block([
        "logger.add_sanitizer(lambda params: {",
        "    **params,",
        "    'password': '***',",
        "    'token':    '***',",
        "})",
        "",
        "logger.add_sink(siem_sink)    # 写 SIEM",
        "logger.add_sink(metrics_sink) # 上报指标",
    ]) + ul("Sanitizers 顺序执行，失败不影响下游",
            "Sinks 独立异常隔离（一个挂了不传染）",
            "默认已覆盖 password / token / ak / sk",
            "所有 tool call 必过 log() — 自动审计所有 Vertical"),
    "#e5f4e9", GREEN)
    return section("PART 3  Agent Kernel 深度讲解",
                   "Audit — 全量可审计 + 脱敏 + SIEM 推送",
                   "每次工具调用产生一条 AuditEntry；支持 sanitizer / sink 扩展",
                   two_col(left, right))


def s18():
    rules = [
        ("#1", RED,     "side_effect 工具必须 receipt",
         "side_effect=True 的工具只能被 requires_approval=True 的 step 调用，且必须携带已绑定、未过期的凭据",
         "E2E B01-B04"),
        ("#2", ACCENT,  "所有工具调用走 _invoke_tool",
         "必审计、必脱敏；业务代码不能越过 Kernel 直接调 handler",
         "E2E B08"),
        ("#3", PURPLE,  "记忆写入必须过 RBAC",
         "write_memory_item 先 assert_can_write；非法 writer → PermissionError",
         "E2E B05"),
        ("#4", BLUE,    "max_iterations 是硬预算",
         "超过立即 FINISH，防止 AI 死循环烧 Token（默认 6 步）",
         "E2E B06"),
        ("#5", GREEN,   "FAILED 默认 fail-fast",
         "一步失败就停车；Vertical 可通过 _maybe_replan 覆写，但必须显式",
         "E2E B07"),
    ]
    rows = "".join(
        f'<div style="display:flex;gap:.7rem;align-items:flex-start;'
        f'background:{LGRAY};border-left:4px solid {c};border-radius:6px;'
        f'padding:.4rem .6rem;margin:.28rem 0">'
        f'<div style="color:{c};font-weight:700;font-size:1em;flex-shrink:0">{tag}</div>'
        f'<div style="flex:1"><strong style="color:{NAVY};font-size:.87em">{title}</strong>'
        f'<p style="font-size:.78em;color:{DGRAY};margin:.1rem 0">{body}</p></div>'
        f'<span style="color:{c};font-size:.73em;font-weight:700;'
        f'flex-shrink:0;align-self:center">{test}</span></div>'
        for tag, c, title, body, test in rules
    )
    return section("PART 3  Agent Kernel 深度讲解",
                   "Kernel 的 5 条不变量（§4.2）",
                   "任何 Vertical 都不能违反 — 业务代码绕不过去", rows)


def s19():
    body = code_block([
        "def create_ops_agent() -> OpsAgent:",
        "    registry      = create_tool_registry()",
        "    register_ops_builtins(registry)              # 12 个 Ops 工具注册",
        "",
        "    audit_logger  = create_audit_logger()        # 默认脱敏已内置",
        "    session_store = create_session_store(",
        "        memory_schema=OPS_MEMORY_SCHEMA,         # 6 层 Ops 语义",
        "    )",
        "    mcp_client    = create_mcp_client(registry=registry)",
        "",
        "    return OpsAgent(",
        "        session_store   = session_store,",
        "        tool_registry   = registry,",
        "        audit_logger    = audit_logger,",
        "        mcp_client      = mcp_client,",
        "    )",
        "",
        "# OpsAgent.__init__ 内部：",
        "#   router          = IntentRouter()             # 关键词路由",
        "#   planner         = OpsPlanner(router=router)  # 中文复合拆分",
        "#   approval_policy = OpsApprovalPolicy()        # 生产 namespace 必审批",
        "#   executors = [Knowledge, ReadOnly, Diagnosis, Mutation]",
    ]) + bottom_bar("想做新 Vertical？复制这个文件，替换 4 个 Ops 专有组件即可")
    return section("PART 4  JARVIS 垂直实现",
                   "JARVIS 组成：装配就是套壳",
                   "create_ops_agent() 把 Kernel 组件拼起来", body)


def s20():
    execs = [
        ("KnowledgeExecutor",  GREEN,     "knowledge",     "回答 MySQL 地址、联系人等静态知识",    "query_knowledge",                        "facts 层"),
        ("ReadOnlyOpsExecutor",BLUE,      "read_only_ops", "查 pod/日志/部署状态/Jenkins 构建",   "get_pod_status, get_pod_logs, ...",       "observations 层"),
        ("DiagnosisExecutor ★",PURPLE,    "diagnosis",     "多假设并行 + 证据 + 打分 + 根因",     "diagnose_pod, search_logs, ...",          "hypotheses 层"),
        ("MutationExecutor",   ACCENT,    "mutation",      "重启/发布/回滚，必带 receipt",        "restart_pod, trigger_jenkins_build, ...", "plans+execution 层"),
    ]
    cards = "".join(
        f'<div style="border-radius:8px;overflow:hidden;border:1px solid #ddd">'
        f'<div style="background:{c};color:{WHITE};padding:.35rem .5rem;'
        f'font-family:Menlo,Consolas,monospace;font-weight:700;font-size:.78em">{name}</div>'
        f'<div style="padding:.4rem .5rem;font-size:.76em">'
        f'<div style="color:{c};font-family:Menlo,Consolas,monospace;margin:.1rem 0">{route}</div>'
        f'<p style="margin:.2rem 0"><strong>职责：</strong>{desc}</p>'
        f'<p style="margin:.2rem 0"><strong>工具：</strong>'
        f'<span style="font-family:Menlo,Consolas,monospace">{tools}</span></p>'
        f'<p style="margin:.1rem 0"><strong>记忆写入：</strong>'
        f'<span style="color:{c};font-weight:700">{mem}</span></p>'
        f'</div></div>'
        for name, c, route, desc, tools, mem in execs
    )
    return section("PART 4  JARVIS 垂直实现",
                   "JARVIS 的 4 个 Executor",
                   "每个路由都有明确的职责、工具范围、记忆写入权限",
                   f'<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:.5rem">{cards}</div>')


def s21():
    body = f"""
<div style="background:{LBLUE};border:2px solid {BLUE};border-radius:8px;
            padding:.5rem;text-align:center;font-weight:700;color:{BLUE};font-size:1em">
  「先查一下 staging pod 状态，然后帮我重启 order-service」
</div>
<div style="text-align:center;color:{MGRAY};font-size:1.2em;margin:.2rem 0">
  ↓ OpsPlanner._split_compound()
</div>
{code_block([
    "_OPS_SPLIT_PATTERNS = [",
    '    re.compile(r"\\s*然后\\s*"),',
    '    re.compile(r"\\s*接着\\s*"),',
    '    re.compile(r"\\s*再\\s*(?=(?:帮|把|重|触|回))"),',
    "]",
])}
<div style="text-align:center;color:{MGRAY};font-size:1.2em;margin:.2rem 0">
  ↓ 拆出 2 个 PlanStep（带依赖关系）
</div>
<div style="display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:.5rem">
  <div style="background:{BLUE};color:{WHITE};border-radius:8px;padding:.6rem;text-align:center;font-size:.88em">
    <strong>Step 1</strong><br/>查 staging pod 状态<br/>
    <span style="font-size:.85em">route = read_only_ops · LOW</span>
  </div>
  <div style="text-align:center;color:{ACCENT};font-size:1.4em">
    →<br/><span style="font-size:.4em;color:{MGRAY}">depends_on</span>
  </div>
  <div style="background:{ACCENT};color:{WHITE};border-radius:8px;padding:.6rem;text-align:center;font-size:.88em">
    <strong>Step 2</strong><br/>重启 order-service<br/>
    <span style="font-size:.85em">route = mutation · HIGH · 必审批</span>
  </div>
</div>"""
    return section("PART 4  JARVIS 垂直实现",
                   "OpsPlanner — 复合请求的中文拆分",
                   "_split_compound 覆写示例：把一句话拆成多步", body)


def s22():
    matrix = [("default/staging","read_only_ops","✓ 放行",    GREEN),
              ("default/staging","diagnosis",    "✓ 放行",    GREEN),
              ("default/staging","mutation",     "⚠ 需 receipt",ACCENT),
              ("production",     "read_only_ops","✓ 放行",    GREEN),
              ("production",     "mutation",     "🛑 必须审批", RED),
              ("production",     "delete*",      "🛑 双人复核", RED)]
    mat_rows = "".join(
        f'<tr><td style="padding:.28rem .4rem;border:1px solid #ddd;font-size:.78em">{ns}</td>'
        f'<td style="padding:.28rem .4rem;border:1px solid #ddd;font-size:.78em;'
        f'font-family:Menlo,Consolas,monospace">{rt}</td>'
        f'<td style="padding:.28rem .4rem;border:1px solid #ddd;font-size:.78em;'
        f'color:{c};font-weight:700">{d}</td></tr>'
        for ns, rt, d, c in matrix
    )
    layers = [("facts","事实层：服务地址、联系人","knowledge"),
              ("observations","观察层：pod 状态、日志","read_ops"),
              ("hypotheses","假设层：诊断假设 + 评分","diagnosis"),
              ("plans","计划层：变更计划","change_planner"),
              ("execution","执行层：已执行动作","change_executor"),
              ("verification","验证层：验证结果","verifier")]
    layer_rows = "".join(
        f'<div style="display:grid;grid-template-columns:110px 1fr 90px;'
        f'gap:.3rem;align-items:center;margin:.2rem 0">'
        f'<div style="background:{BLUE};color:{WHITE};border-radius:4px;'
        f'padding:2px 5px;font-family:Menlo,Consolas,monospace;font-size:.75em">{lyr}</div>'
        f'<div style="font-size:.8em;color:{DGRAY}">{desc}</div>'
        f'<div style="font-family:Menlo,Consolas,monospace;font-size:.75em;'
        f'color:{ACCENT};font-weight:700">{w}</div></div>'
        for lyr, desc, w in layers
    )
    left = card(hdr("OpsApprovalPolicy（risk_policy.py）", ACCENT)
                + '<table style="width:100%;border-collapse:collapse;margin-top:.3rem">'
                + f'<tr><th style="background:{NAVY};color:{WHITE};padding:.25rem .4rem;font-size:.78em">namespace</th>'
                + f'<th style="background:{NAVY};color:{WHITE};padding:.25rem .4rem;font-size:.78em">route</th>'
                + f'<th style="background:{NAVY};color:{WHITE};padding:.25rem .4rem;font-size:.78em">决定</th></tr>'
                + mat_rows + '</table>', "#fff2e6", ACCENT)
    right = card(hdr("OPS_MEMORY_SCHEMA（memory_schema.py）", BLUE)
                 + f'<p style="font-size:.82em;margin:.3rem 0"><strong>6 层记忆 + 对应 writer：</strong></p>'
                 + layer_rows, LBLUE, BLUE)
    return section("PART 4  JARVIS 垂直实现",
                   "OpsApprovalPolicy + OPS_MEMORY_SCHEMA",
                   "Ops 填好的两个安全相关插件槽", two_col(left, right))


def s23():
    steps = [("①","API 入口",      "ChatRequest(message, user_id, session_id)"),
             ("②","状态构建",      "_build_initial_state 读取最近 6 条消息"),
             ("③","Planner 节点",  "initial_plan → 拆分 → 第一个 PlanStep"),
             ("④","Dispatcher",    "按 execution_target 或 route → 挑选节点"),
             ("⑤","Executor 执行", "执行器调 _invoke_tool 执行工具"),
             ("⑥","Approval 闸门", "side_effect 工具必须过 ApprovalPolicy.evaluate"),
             ("⑦","_invoke_tool",  "调用 handler.ainvoke + Audit 落盘"),
             ("⑧","Memory 写入",   "按 Schema RBAC 写入对应层"),
             ("⑨","回到 Planner",  "advance → CONTINUE / REPLAN / FINISH")]
    rows = "".join(flow_row(n, t, b) for n, t, b in steps)
    body = rows + (f'<div style="background:{LBLUE};border-radius:6px;'
                   f'padding:.4rem .8rem;margin-top:.4rem;text-align:center;'
                   f'font-weight:700;color:{BLUE};font-size:.88em">'
                   f'🔁 ③–⑨ 循环直到 FINISH 或 max_iterations</div>')
    return section("PART 5  关键执行流程",
                   "一次 chat() 请求的完整流水线",
                   "从 HTTP 入口到 Audit 落盘的 9 个阶段", body)


def s24():
    body = f"""
<div style="display:flex;align-items:center;gap:.4rem;flex-wrap:wrap;
            font-size:.82em;margin-bottom:.5rem">
  <div style="background:{BLUE};color:{WHITE};border-radius:5px;
              padding:.25rem .7rem;font-family:Menlo,Consolas,monospace">Executor.execute()</div>
  <span style="color:{MGRAY}">→</span>
  <div style="background:{NAVY};color:{WHITE};border-radius:5px;
              padding:.25rem .7rem;font-family:Menlo,Consolas,monospace">_invoke_tool()</div>
  <span style="color:{MGRAY}">→</span>
  <div style="background:{LGRAY};border:1px solid #ccc;border-radius:5px;
              padding:.25rem .7rem;font-family:Menlo,Consolas,monospace">spec.side_effect ?</div>
</div>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:.8rem;margin-bottom:.5rem">
  <div style="background:#ffe8e0;border:2px solid {RED};border-radius:8px;padding:.7rem">
    <strong style="color:{RED}">Yes（有副作用）</strong>
    <ul style="font-size:.82em;margin:.3rem 0;padding-left:1.3em">
      <li>→ approval_policy.evaluate()</li>
      <li>未批准：返回 error: "需要审批..."</li>
      <li>Audit 照样记录 FAILED 条目</li>
    </ul>
  </div>
  <div style="background:#e5f4e9;border:2px solid {GREEN};border-radius:8px;padding:.7rem">
    <strong style="color:{GREEN}">No（无副作用）或已批准</strong>
    <ul style="font-size:.82em;margin:.3rem 0;padding-left:1.3em">
      <li>→ handler.ainvoke(args)</li>
      <li>status = SUCCESS</li>
      <li>Memory 写入 + Audit 落盘</li>
    </ul>
  </div>
</div>
<div style="background:{LBLUE};border-radius:8px;padding:.5rem .8rem">
  <strong style="color:{NAVY}">💡 关键</strong>：这条路径由 Kernel 在 _invoke_tool 里
  <strong>强制执行</strong>。Vertical 想绕过？唯一办法是不走 _invoke_tool —
  但那就没有审计了，两难。
</div>"""
    return section("PART 5  关键执行流程",
                   "审批闸门的工作原理",
                   "side_effect 工具从 _invoke_tool 到 handler 之间必经这个门", body)


def s25():
    stages = [
        ("1", "症状采集",   "_collect_symptoms",
         "diagnose_pod\nget_pod_status\nsearch_logs", LBLUE),
        ("2", "假设生成",   "_generate_hypotheses",
         "LLM + 拓扑\n→ 至多 4 条\nHypothesis", "#e5f4e9"),
        ("3", "并行取证",   "_collect_evidence_parallel",
         "asyncio.gather\n每假设 ≤2 个\n证据工具", "#fff2e6"),
        ("4", "打分合成",   "_score_and_synthesize",
         "error/oom → +1.8\n疑点匹配 → +0.5", "#f0e6ff"),
        ("5", "写入记忆",   "_write_memory",
         "每条 hypothesis\ntop_hypothesis_id\ndiagnosis_summary", LBLUE),
    ]
    cards = "".join(
        f'<div style="background:{bg};border-radius:8px;padding:.4rem;text-align:center">'
        f'<div style="background:{ACCENT};color:{WHITE};border-radius:50%;'
        f'width:1.4rem;height:1.4rem;display:inline-flex;align-items:center;'
        f'justify-content:center;font-weight:700;font-size:.8em;margin-bottom:.2rem">{n}</div>'
        f'<div style="font-weight:700;color:{NAVY};font-size:.82em">{title}</div>'
        f'<div style="font-family:Menlo,Consolas,monospace;font-size:.68em;'
        f'color:{BLUE};margin:.15rem 0">{esc(func)}</div>'
        f'<div style="font-family:Menlo,Consolas,monospace;font-size:.7em;'
        f'color:{DGRAY};white-space:pre-line">{body}</div></div>'
        for n, title, func, body, bg in stages
    )
    body = (f'<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:.35rem;margin-bottom:.4rem">{cards}</div>'
            f'<div style="background:#ffe8d8;border-radius:6px;padding:.35rem .7rem;'
            f'font-size:.8em;color:{ACCENT};font-weight:700;margin-bottom:.3rem">'
            f'🛟 降级路径：假设生成失败 / LLM 不可用 → _fallback_single_chain 仍然返回已收集症状</div>'
            f'<div style="background:{LBLUE};border-radius:6px;padding:.4rem .7rem;font-size:.8em">'
            f'✨ 这个 5-stage pipeline 已被抽成 Kernel 的 <strong>MultiHypothesisExecutor</strong> 基类，'
            f'未来客服 Agent 同样可继承。</div>')
    return section("PART 5  关键执行流程",
                   "DiagnosisExecutor — 多假设并行诊断",
                   "「多个假设 · 并行取证 · 启发式打分 · 归纳结论」", body)


def s26():
    events = [
        ("t=0ms",  NAVY,      "用户",        "「先查一下 staging pod 状态，然后帮我重启 order-service」"),
        ("t=5ms",  BLUE,      "Planner",     "OpsPlanner._split_compound 分出 2 段 Step"),
        ("t=8ms",  GREEN,     "Router×2",    "Step1→read_only_ops/LOW · Step2→mutation/HIGH/必审批"),
        ("t=30ms", BLUE,      "Step 1 执行", "ReadOnlyOps → get_pod_status(staging) → Audit + Memory.observations"),
        ("t=45ms", MGRAY,     "advance()",   "Plan cursor → Step 2；返回 CONTINUE"),
        ("t=48ms", RED,       "Step 2 进入", "MutationExecutor → restart_deployment → side_effect=True → 拒绝（无 receipt）"),
        ("t=50ms", ACCENT,    "返回用户",    "\"此操作需要审批\" · Audit 记录 FAILED 条目"),
    ]
    rows = "".join(
        f'<div style="display:grid;grid-template-columns:55px 85px 1fr;gap:.4rem;'
        f'align-items:center;margin:.22rem 0">'
        f'<span style="font-family:Menlo,Consolas,monospace;font-size:.72em;color:{MGRAY}">{t}</span>'
        f'<span style="background:{c};color:{WHITE};border-radius:4px;padding:1px 5px;'
        f'font-size:.75em;font-weight:700;text-align:center">{phase}</span>'
        f'<span style="font-size:.8em;color:{DGRAY}">{desc}</span></div>'
        for t, c, phase, desc in events
    )
    return section("PART 5  关键执行流程",
                   "实战例子：复合请求的全链路时序",
                   "「先查 staging pod，然后重启 order-service」", rows)


def s27():
    rows_data = [
        ("1", "路由器",       "RouterBase.route() → RouteDecision",           "IntentRouter 关键词映射"),
        ("2", "执行器",       "ExecutorBase.execute(state) → dict",           "Knowledge/ReadOnly/Diagnosis/Mutation"),
        ("3", "工具",         "@tool + ToolRegistry.register_local/_mcp",     "K8s/Jenkins/Logs/Knowledge 共 12 个"),
        ("4", "MCP 服务器",   "MCPClient.register_server(name, url)",         "可接入任意 MCP-compatible 远端工具"),
        ("5", "Planner 定制", "Planner 子类 _split_compound/_maybe_replan",   "OpsPlanner 中文复合拆分"),
        ("6", "记忆 Schema",  "MemorySchema(layers={...})",                   "OPS_MEMORY_SCHEMA 6 层"),
        ("7", "审批策略",     "ApprovalPolicy.evaluate(step, context)",       "OpsApprovalPolicy 风险矩阵"),
        ("8", "审计扩展",     "AuditLogger.add_sanitizer / add_sink",         "Ops 级脱敏 + SIEM 可扩展"),
        ("9", "RBAC 身份",    "AgentIdentityKey 可注册字符串",                 "knowledge/read_ops/diagnosis 等"),
        ("10","Executor 模式","MultiHypothesisExecutor/ApprovalGateExecutor", "Ops DiagnosisExecutor 可继承"),
    ]
    trs = "".join(
        f'<tr style="background:{"#fff" if int(n)%2==0 else LGRAY}">'
        f'<td style="padding:.28rem .4rem;border:1px solid #ddd;text-align:center;'
        f'color:{ACCENT};font-weight:700;font-size:.8em">{n}</td>'
        f'<td style="padding:.28rem .4rem;border:1px solid #ddd;font-weight:700;'
        f'color:{NAVY};font-size:.8em">{name}</td>'
        f'<td style="padding:.28rem .4rem;border:1px solid #ddd;'
        f'font-family:Menlo,Consolas,monospace;font-size:.72em">{c}</td>'
        f'<td style="padding:.28rem .4rem;border:1px solid #ddd;'
        f'font-size:.78em;color:{MGRAY}">{ops}</td></tr>'
        for n, name, c, ops in rows_data
    )
    body = (f'<table style="width:100%;border-collapse:collapse">'
            f'<thead><tr>'
            + "".join(f'<th style="background:{NAVY};color:{WHITE};padding:.3rem .4rem;'
                      f'text-align:left;font-size:.8em">{h}</th>'
                      for h in ["#", "插件点", "基类 / 契约", "Ops 填了什么"])
            + f'</tr></thead><tbody>{trs}</tbody></table>')
    return section("PART 6  插件化与扩展",
                   "10 个插件点：Kernel 对外的所有扩展面",
                   "Vertical 就是「填这些槽位」", body)


def s28():
    steps = [
        ("1","定义记忆 Schema","agent_csm/memory_schema.py",
         ["CSM_MEMORY_SCHEMA = MemorySchema(layers={",
          "    'user_profile':    {'crm_reader'},",
          "    'conversation':    {'dialogue'},",
          "    'order_context':   {'crm_reader'},",
          "})"]),
        ("2","定义风险策略","agent_csm/risk_policy.py",
         ["class CsmApprovalPolicy(ApprovalPolicy):",
          "    def validate_receipt(...):",
          "        # 退款 > 1000 元 → 需要主管 receipt"]),
        ("3","定义路由器","agent_csm/router.py",
         ["class CsmKeywordRouter(RouterBase):",
          "    async def route(request):",
          "        if '退款' in msg: return RouteDecision(..., 'refund', HIGH)"]),
        ("4","实现执行器 + 工具","agent_csm/executors/ 和 tools/",
         ["RefundExecutor / TrackingExecutor / EscalationExecutor",
          "接入 CRM / 订单系统 / 工单系统"]),
        ("5","装配入口","agent_csm/__init__.py",
         ["# 复制 create_ops_agent() → 改 4 个专有组件",
          "audit_logger/session_store/mcp_client 继续用 Kernel factory"]),
    ]
    rows = "".join(
        f'<div style="display:flex;gap:.5rem;align-items:flex-start;margin:.28rem 0">'
        f'<div style="background:{ACCENT};color:{WHITE};border-radius:50%;'
        f'width:1.4rem;height:1.4rem;display:flex;align-items:center;'
        f'justify-content:center;font-weight:700;flex-shrink:0;font-size:.78em">{n}</div>'
        f'<div style="flex:1">'
        f'<div style="font-weight:700;color:{NAVY};font-size:.84em">{title}</div>'
        f'<div style="font-family:Menlo,Consolas,monospace;font-size:.74em;'
        f'color:{ACCENT};margin:.1rem 0">{esc(filepath)}</div>'
        f'<div style="background:{CODEBG};border-radius:4px;padding:.3rem .5rem">'
        + "".join(f'<div style="font-family:Menlo,Consolas,monospace;font-size:.7em;'
                  f'color:{CODEFG}">{esc(l)}</div>' for l in code)
        + '</div></div></div>'
        for n, title, filepath, code in steps
    )
    return section("PART 6  插件化与扩展",
                   "做一个新 Vertical 需要几步？",
                   "以假想的「CsmAgent 客服」为例 · ～ 1–2 周工作量", rows)


def s29():
    levels = [
        ("L1", BLUE,   "Executor 级降级",
         "触发：单个 executor 抛异常",
         "处理：PlanStepStatus=FAILED → fail-fast → 返回错误说明",
         "用户看到：\"步骤 X 执行失败\"；其他步骤不继续"),
        ("L2", ACCENT, "Planner 级降级",
         "触发：max_iterations 耗尽 / Planner 生成空 Plan",
         "处理：fallback_plan 兜底 → 单 knowledge step",
         "用户看到：AI 进入「普通问答」模式"),
        ("L3", RED,    "Kernel 级降级",
         "触发：Receipt 失败 / 记忆 Backend 故障 / graph 崩",
         "处理：chat() 外层 try/except → ChatResponse 不崩",
         "用户看到：\"系统暂时不可用\"；Audit 仍然落盘错误条目"),
    ]
    cards = "".join(
        f'<div style="display:flex;gap:.7rem;background:{LGRAY};'
        f'border-radius:8px;padding:.6rem;margin:.35rem 0">'
        f'<div style="background:{c};color:{WHITE};border-radius:6px;'
        f'padding:.3rem .7rem;font-size:1.6em;font-weight:700;'
        f'display:flex;align-items:center;flex-shrink:0">{lv}</div>'
        f'<div><strong style="color:{c};font-size:.9em">{title}</strong>'
        f'<ul style="margin:.25rem 0;font-size:.8em;padding-left:1.2em">'
        f'<li>{trigger}</li><li>{handle}</li><li>{ux}</li></ul></div></div>'
        for lv, c, title, trigger, handle, ux in levels
    )
    return section("PART 7  演进方向与测试",
                   "三级降级路径（§10）",
                   "每一级都有明确触发条件和用户感知", cards)


def s30():
    groups = [("A","Happy Path",5,GREEN),("B","Kernel 不变量",8,RED),
              ("C","插件点",7,PURPLE),("D","Vertical 隔离",2,BLUE),
              ("E","降级路径",2,ACCENT),("F","反模式回归",3,MGRAY)]
    badges = "".join(
        f'<div style="background:{c};color:{WHITE};border-radius:6px;'
        f'padding:.3rem .5rem;font-size:.8em;font-weight:700;text-align:center">'
        f'{code} {name}<br/><span style="font-size:.9em">{cnt} 个</span></div>'
        for code, name, cnt, c in groups
    )
    body = f"""
<div style="display:flex;flex-direction:column;align-items:center;gap:.3rem;margin:.4rem 0">
  <div style="background:{PURPLE};color:{WHITE};border-radius:6px;
              padding:.35rem 2rem;font-weight:700;font-size:.88em">
    L2 · E2E · 27 个
  </div>
  <div style="background:{BLUE};color:{WHITE};border-radius:6px;
              padding:.35rem 4rem;font-weight:700;font-size:.88em">
    L1 · Kernel 契约 · 4 个（保证 Kernel 不与 Ops 耦合）
  </div>
  <div style="background:{GREEN};color:{WHITE};border-radius:6px;
              padding:.35rem 5rem;font-weight:700;font-size:.88em;text-align:center">
    L0 · 单元测试 · 53 个 — Planner/Registry/Topology/Memory/Patterns ...
  </div>
</div>
<p style="font-weight:700;color:{NAVY};margin:.4rem 0 .2rem">E2E 测试矩阵（27 个用例分 6 组）</p>
<div style="display:grid;grid-template-columns:repeat(6,1fr);gap:.35rem">{badges}</div>"""
    return section("PART 7  演进方向与测试",
                   "测试金字塔与目前覆盖情况",
                   "L0 单元 / L1 契约 / L2 E2E — 共 84 个自动化测试用例", body)


def s31():
    agents = [("DataAgent","查销售曲线\nSQL+BI",GREEN),
              ("CsmAgent","投诉分类\n退款/延迟占比",BLUE),
              ("JARVIS","线上异常排查\n影响转化故障",ACCENT),
              ("DocAgent","生成一页\n摘要报告",PURPLE)]
    acards = "".join(
        f'<div style="background:{c};color:{WHITE};border-radius:8px;'
        f'padding:.5rem;text-align:center;font-weight:700;font-size:.82em">'
        f'{n}<br/><span style="font-weight:400;font-size:.82em;white-space:pre-line">{b}</span></div>'
        for n, b, c in agents
    )
    body = f"""
<div style="background:{NAVY};color:{WHITE};border-radius:6px;padding:.45rem;
            text-align:center;font-weight:700">
  用户：「Q3 订单为什么下滑？」
</div>
<div style="text-align:center;color:{MGRAY};font-size:1.2em">↓</div>
<div style="background:{ACCENT};color:{WHITE};border-radius:8px;padding:.4rem;
            text-align:center;font-weight:700;margin-bottom:.2rem">
  Supervisor（MetaPlanner + AgentProxyExecutor）
</div>
<div style="text-align:center;color:{MGRAY};font-size:1.2em">↓ 派发子任务</div>
<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:.35rem;margin:.2rem 0">
  {acards}
</div>
<div style="text-align:center;color:{MGRAY};font-size:1.2em">↓ 聚合</div>
<div style="background:{NAVY};color:{WHITE};border-radius:6px;padding:.4rem;
            text-align:center;font-weight:700;font-size:.88em">
  Supervisor 聚合：联合审批 / 跨 Agent Audit / 统一 ChatResponse
</div>
<div style="background:{LBLUE};border-radius:6px;padding:.4rem .7rem;
            margin-top:.4rem;font-size:.8em">
  🔑 关键机制：<code>PlanStep.execution_target = "agent:data" / "agent:csm" / ...</code><br/>
  已在 PlanStep Schema 预留字段 — 不需要重构 Kernel 就能落地 Supervisor
</div>"""
    return section("PART 7  演进方向与测试",
                   "未来演进：Supervisor 多 Agent 协同",
                   "跨域问题拆给多个 Vertical，最后汇总 — §7 演进方向", body)


def s32():
    roadmap_items = [
        ("近期 Q2",  ["DiagnosisExecutor 接入 MultiHypothesisExecutor 基类",
                      "接入真实 MCP 服务器（k8s-mcp / jenkins-mcp）",
                      "补齐 RedisSessionStore / MemoryBackend"]),
        ("中期 Q3-Q4",["落地第二个 Vertical（客服 / 数据 任选）",
                       "Kernel 跨域通用性验证",
                       "Agent 的灰度 / 回滚 / 版本管理"]),
        ("远期 1年+", ["Supervisor 多 Agent 协同落地",
                       "跨域请求自动拆解",
                       "统一人机交互与观测平台"]),
    ]
    rmap = "".join(
        f'<div style="margin:.2rem 0">'
        f'<span style="background:{ACCENT};color:{WHITE};border-radius:4px;'
        f'padding:1px 8px;font-size:.8em;font-weight:700">{stage}</span>'
        + ul(*items, size=".8em")
        + '</div>'
        for stage, items in roadmap_items
    )
    summary = ["Kernel 11 个组件 + 5 条不变量",
               "10 个插件点 + 新 Vertical 五步清单",
               "JARVIS 4 个 Executor + 中文拆分",
               "审批凭据 / 多假设诊断详细流程",
               "三级降级 + 84 个自动化测试覆盖",
               "Supervisor 演进方向已预留字段"]
    left  = card(hdr("✅ 本次讲解覆盖", BLUE)
                 + ul(*summary)
                 + f'<div style="background:{NAVY};border-radius:6px;padding:.5rem;margin-top:.4rem">'
                   f'<strong style="color:{ACCENT}">核心价值</strong>'
                   f'<p style="color:{WHITE};font-size:.82em;margin:.2rem 0">'
                   f'JARVIS 只是起点 — 我们在造一个可以孵化任意业务 Agent 的基建平台</p>'
                   f'</div>', LBLUE, BLUE)
    right = card(hdr("🛣️ 路线图", ACCENT) + rmap, "#fff2e6", ACCENT)
    return section("PART 7  演进方向与测试",
                   "总结 · 路线图 · Q&amp;A", "",
                   two_col(left, right))


# ── CSS + JS shell ────────────────────────────────────────────────────────────

CSS = f"""\
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
html, body {{ width: 100%; height: 100%; overflow: hidden;
  font-family: "PingFang SC","Noto Sans SC","Microsoft YaHei",
               "Hiragino Sans GB",Arial,sans-serif;
  background: #222; color: {DGRAY}; }}
#deck {{ width: 100%; height: 100%; position: relative; }}
.slide {{ display: none; width: 100%; height: 100%;
  position: absolute; top: 0; left: 0;
  background: {WHITE}; overflow: hidden; }}
.slide.active {{ display: flex; flex-direction: column; }}
.slide-inner {{ flex: 1; overflow-y: auto; padding: 1.2rem 2rem; }}
/* nav bar */
#nav {{ position: fixed; bottom: 0; left: 0; right: 0; height: 36px;
  background: {NAVY}; display: flex; align-items: center;
  justify-content: center; gap: 1rem; z-index: 99; }}
#nav button {{ background: none; border: 1px solid #ffffff44; color: {WHITE};
  border-radius: 4px; padding: 2px 14px; cursor: pointer; font-size: .82em; }}
#nav button:hover {{ background: #ffffff22; }}
#counter {{ color: #aabbcc; font-size: .82em; min-width: 60px; text-align: center; }}
/* scrollbar */
.slide-inner::-webkit-scrollbar {{ width: 4px; }}
.slide-inner::-webkit-scrollbar-thumb {{ background: #ccc; border-radius: 2px; }}
/* lists & code */
ul {{ padding-left: 1.3em; }}
ul li {{ margin: .2rem 0; font-size: .88em; }}
pre {{ margin: .4rem 0 !important; }}
code {{ font-family: Menlo,Consolas,monospace; }}
h2 {{ font-size: 1.35em; }}
"""

JS = """\
(function(){
  var slides = document.querySelectorAll('.slide');
  var n = slides.length;
  var cur = 0;
  var counter = document.getElementById('counter');
  function show(i){
    slides[cur].classList.remove('active');
    cur = Math.max(0, Math.min(i, n-1));
    slides[cur].classList.add('active');
    counter.textContent = (cur+1) + ' / ' + n;
    slides[cur].querySelector('.slide-inner').scrollTop = 0;
  }
  show(0);
  document.getElementById('btn-prev').onclick = function(){ show(cur-1); };
  document.getElementById('btn-next').onclick = function(){ show(cur+1); };
  document.addEventListener('keydown', function(e){
    if(e.key==='ArrowRight'||e.key==='ArrowDown'||e.key===' ') show(cur+1);
    else if(e.key==='ArrowLeft'||e.key==='ArrowUp') show(cur-1);
  });
})();
"""

SHELL = """\
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>JARVIS 架构详解</title>
<style>{css}</style>
</head>
<body>
<div id="deck">
{slides}
</div>
<div id="nav">
  <button id="btn-prev">◀ 上一页</button>
  <span id="counter">1 / 32</span>
  <button id="btn-next">下一页 ▶</button>
</div>
<script>{js}</script>
</body>
</html>
"""

# ── assemble ──────────────────────────────────────────────────────────────────

SLIDE_FUNCS = [
    s01, s02,
    s03, s04, s05,
    s06, s07, s08,
    s09, s10, s11, s12, s13, s14, s15, s16, s17, s18,
    s19, s20, s21, s22,
    s23, s24, s25, s26,
    s27, s28,
    s29, s30, s31, s32,
]


def main():
    slides_html = "\n".join(fn() for fn in SLIDE_FUNCS)
    html = SHELL.format(css=CSS, slides=slides_html, js=JS)
    out = Path("docs/index.html")
    out.parent.mkdir(exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"✅ Generated: {out.resolve()}")
    print(f"   Slides:    {len(SLIDE_FUNCS)}")
    print()
    print("🌐 在线预览（推送后）：")
    repo = "lichao01111-dot/ops-agent"
    print(f"   https://htmlpreview.github.io/?https://raw.githubusercontent.com/{repo}/main/docs/index.html")


if __name__ == "__main__":
    main()
