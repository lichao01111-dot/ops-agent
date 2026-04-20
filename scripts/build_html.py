"""Generate OpsAgent architecture briefing as a reveal.js HTML presentation.

Run:
    python scripts/build_html.py

Output: docs/index.html

View online (no download):
    https://htmlpreview.github.io/?https://raw.githubusercontent.com/lichao01111-dot/ops-agent/main/docs/index.html
"""
from __future__ import annotations
from pathlib import Path

# ─── color palette (matches build_ppt.py) ───────────────────────────────────
NAVY        = "#1F3A5F"
DEEP_BLUE   = "#2E5BBA"
LIGHT_BLUE  = "#E8F1FA"
ACCENT      = "#F6922A"
DARK_GRAY   = "#333333"
MED_GRAY    = "#666666"
LIGHT_GRAY  = "#F5F5F5"
WHITE       = "#FFFFFF"
GREEN       = "#2EA06B"
RED         = "#D04545"
PURPLE      = "#7A4FB5"
CODE_BG     = "#2B2B2B"

# ─── helpers ────────────────────────────────────────────────────────────────

def badge(text, color=DEEP_BLUE, fg=WHITE, extra=""):
    return (f'<span style="background:{color};color:{fg};'
            f'border-radius:4px;padding:2px 10px;font-size:.85em;{extra}">'
            f'{text}</span>')

def row(*cells, header=False):
    tag = "th" if header else "td"
    return "<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in cells) + "</tr>"

def code_block(lines, lang="python"):
    code = "\n".join(lines)
    return f'<pre><code class="language-{lang}">{code}</code></pre>'

def two_col(left_html, right_html, ratio="1fr 1fr", gap="1.5rem"):
    return (f'<div style="display:grid;grid-template-columns:{ratio};'
            f'gap:{gap};height:100%">'
            f'<div>{left_html}</div><div>{right_html}</div></div>')

def box(content, bg=LIGHT_BLUE, border=DEEP_BLUE, padding="1rem", radius="8px"):
    return (f'<div style="background:{bg};border-left:4px solid {border};'
            f'border-radius:{radius};padding:{padding};height:100%;">'
            f'{content}</div>')

def ul(*items, color=DARK_GRAY, mono=False):
    font = "font-family:monospace;" if mono else ""
    lis = "".join(f'<li style="color:{color};{font}">{i}</li>' for i in items)
    return f"<ul>{lis}</ul>"

def tag_row(tags: list[tuple]):
    """tags = [(label, text, color), ...]"""
    parts = []
    for label, text, color in tags:
        parts.append(
            f'<div style="display:flex;align-items:center;gap:.5rem;margin:.3rem 0">'
            f'<span style="background:{color};color:#fff;border-radius:4px;'
            f'padding:1px 8px;font-size:.8em;white-space:nowrap">{label}</span>'
            f'<span style="color:{DARK_GRAY}">{text}</span></div>'
        )
    return "".join(parts)

def flow_step(num, title, body, color=ACCENT):
    return (f'<div style="display:flex;align-items:flex-start;gap:.7rem;'
            f'margin:.35rem 0">'
            f'<div style="background:{color};color:#fff;border-radius:50%;'
            f'width:1.5rem;height:1.5rem;display:flex;align-items:center;'
            f'justify-content:center;font-weight:bold;flex-shrink:0;font-size:.85em">'
            f'{num}</div>'
            f'<div><strong style="color:{NAVY}">{title}</strong> '
            f'<span style="color:{DARK_GRAY};font-size:.9em">{body}</span></div></div>')

# ─── CSS & HTML shell ────────────────────────────────────────────────────────

CSS = f"""
:root {{
  --navy:      {NAVY};
  --blue:      {DEEP_BLUE};
  --lblue:     {LIGHT_BLUE};
  --accent:    {ACCENT};
  --gray:      {DARK_GRAY};
  --mgray:     {MED_GRAY};
  --lgray:     {LIGHT_GRAY};
  --green:     {GREEN};
  --red:       {RED};
  --purple:    {PURPLE};
  --codebg:    {CODE_BG};
}}
.reveal {{
  font-family: "PingFang SC", "Noto Sans SC", "Microsoft YaHei", sans-serif;
  color: {DARK_GRAY};
}}
.reveal h1,.reveal h2,.reveal h3,.reveal h4 {{
  color: {NAVY};
  font-family: "PingFang SC","Noto Sans SC","Microsoft YaHei",sans-serif;
  text-transform: none;
}}
.reveal section {{ text-align: left; font-size: 1rem; }}
.reveal .chapter-label {{
  font-size:.7em; color:{ACCENT}; font-weight:700; letter-spacing:.08em;
  text-transform:uppercase; margin-bottom:.3rem;
}}
.reveal .slide-subtitle {{
  font-size:.85em; color:{MED_GRAY}; margin-top:-.3rem; margin-bottom:.8rem;
}}
.reveal .rule-bar {{
  height:4px; background:{DEEP_BLUE}; margin:0 0 .8rem 0; border-radius:2px;
}}
.reveal pre {{
  background:{CODE_BG}; border-radius:8px; padding:1rem; overflow-x:auto;
  font-size:.72em; box-shadow:none; width:100%;
}}
.reveal pre code {{ color:#e0e0e0; background:transparent; }}
.reveal table {{ width:100%; border-collapse:collapse; font-size:.85em; }}
.reveal table th {{
  background:{NAVY}; color:{WHITE}; padding:.4rem .6rem; text-align:center;
}}
.reveal table td {{ padding:.35rem .6rem; border:1px solid #ddd; }}
.reveal table tr:nth-child(even) td {{ background:{LIGHT_GRAY}; }}
.reveal ul {{ margin:0; padding-left:1.3em; }}
.reveal ul li {{ margin:.25rem 0; color:{DARK_GRAY}; }}
.reveal .two-col {{ display:grid; gap:1.5rem; }}
.reveal .card {{
  border-radius:8px; padding:.8rem 1rem; height:100%;
}}
.reveal .card-navy {{ background:{LIGHT_BLUE}; border-left:4px solid {DEEP_BLUE}; }}
.reveal .card-orange {{ background:#fff2e6; border-left:4px solid {ACCENT}; }}
.reveal .card-green {{ background:#e5f4e9; border-left:4px solid {GREEN}; }}
.reveal .card-purple {{ background:#f0e6ff; border-left:4px solid {PURPLE}; }}
.reveal .hdr {{
  font-size:1.05em; font-weight:700; color:{NAVY}; margin-bottom:.4rem;
}}
.reveal .badge {{
  display:inline-block; border-radius:4px; padding:1px 8px; font-size:.8em;
  font-weight:700; white-space:nowrap;
}}
.reveal .tag-blue  {{ background:{DEEP_BLUE}; color:#fff; }}
.reveal .tag-green {{ background:{GREEN};     color:#fff; }}
.reveal .tag-red   {{ background:{RED};       color:#fff; }}
.reveal .tag-accent {{ background:{ACCENT};   color:#fff; }}
.reveal .tag-purple {{ background:{PURPLE};   color:#fff; }}
.reveal .tag-gray  {{ background:{MED_GRAY};  color:#fff; }}
.reveal .mono {{ font-family:Menlo,Consolas,monospace; }}
.reveal .step-row {{
  display:flex; align-items:flex-start; gap:.7rem; margin:.3rem 0;
}}
.reveal .step-num {{
  background:{ACCENT}; color:#fff; border-radius:50%;
  width:1.5rem; height:1.5rem; display:flex; align-items:center;
  justify-content:center; font-weight:bold; flex-shrink:0; font-size:.82em;
}}
.reveal .bottom-bar {{
  background:{NAVY}; color:{WHITE}; padding:.5rem 1rem; border-radius:6px;
  text-align:center; font-weight:700; margin-top:.8rem; font-size:.9em;
}}
.reveal .cover-bg {{ background:{NAVY}; }}
"""

SHELL = """\
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OpsAgent 架构详解</title>
<link rel="stylesheet"
  href="https://cdn.jsdelivr.net/npm/reveal.js@5.1.0/dist/reset.css"/>
<link rel="stylesheet"
  href="https://cdn.jsdelivr.net/npm/reveal.js@5.1.0/dist/reveal.css"/>
<link rel="stylesheet"
  href="https://cdn.jsdelivr.net/npm/reveal.js@5.1.0/dist/theme/white.css"/>
<link rel="stylesheet"
  href="https://cdn.jsdelivr.net/npm/highlight.js@11.9.0/styles/atom-one-dark.min.css"/>
<style>
{css}
</style>
</head>
<body>
<div class="reveal">
<div class="slides">
{slides}
</div>
</div>
<script src="https://cdn.jsdelivr.net/npm/reveal.js@5.1.0/dist/reveal.js"></script>
<script src="https://cdn.jsdelivr.net/npm/reveal.js@5.1.0/plugin/highlight/highlight.js"></script>
<script src="https://cdn.jsdelivr.net/npm/reveal.js@5.1.0/plugin/notes/notes.js"></script>
<script>
Reveal.initialize({{
  hash: true,
  slideNumber: 'c/t',
  transition: 'fade',
  plugins: [RevealHighlight, RevealNotes],
}});
</script>
</body>
</html>
"""

# ─── slides ─────────────────────────────────────────────────────────────────

def s01_cover():
    return f"""\
<section data-background="{NAVY}">
  <div style="text-align:center;padding:3rem 0">
    <div style="width:6px;height:120px;background:{ACCENT};display:inline-block;
                margin-right:1.5rem;vertical-align:middle"></div>
    <div style="display:inline-block;vertical-align:middle;text-align:left">
      <h1 style="color:{WHITE};font-size:2.8em;margin:0">OpsAgent 架构详解</h1>
      <p style="color:#CCDDEE;font-size:1.4em;margin:.5rem 0">
        Agent Kernel + Vertical Agent</p>
      <p style="color:#9AAACC;font-size:1em">从设计原则到实现细节 · 全栈技术汇报</p>
    </div>
    <p style="color:#8899AA;font-size:.8em;margin-top:3rem">2026 · 技术评审</p>
  </div>
</section>"""


def s02_toc():
    chapters = [
        ("Part 1", "背景与问题",          "为什么要重构",                    "p.3–5",  DEEP_BLUE),
        ("Part 2", "整体分层",            "Kernel / Vertical / Supervisor",   "p.6–8",  GREEN),
        ("Part 3", "Agent Kernel 深度讲解","每个组件的职责与接口",             "p.9–18", PURPLE),
        ("Part 4", "OpsAgent 垂直实现",   "作为第一个 Vertical 样例",         "p.19–22",ACCENT),
        ("Part 5", "关键执行流程",         "审批 / 诊断 / 复合请求",           "p.23–26",DEEP_BLUE),
        ("Part 6", "插件化与扩展",         "10 个插件点 + 新 Vertical 清单",   "p.27–28",GREEN),
        ("Part 7", "演进方向与测试",       "降级 / 测试 / Supervisor / 路线图","p.29–32",PURPLE),
    ]
    rows = ""
    for tag, name, desc, pages, color in chapters:
        rows += (f'<div style="display:grid;grid-template-columns:80px 1fr 60px;'
                 f'align-items:center;gap:.5rem;margin:.3rem 0;padding:.4rem .6rem;'
                 f'background:{LIGHT_GRAY};border-radius:6px">'
                 f'<span style="background:{color};color:#fff;border-radius:4px;'
                 f'padding:2px 6px;font-size:.8em;font-weight:700;text-align:center">{tag}</span>'
                 f'<div><strong style="color:{NAVY}">{name}</strong> '
                 f'<span style="color:{MED_GRAY};font-size:.85em">— {desc}</span></div>'
                 f'<span style="color:{MED_GRAY};font-size:.8em;text-align:right">{pages}</span>'
                 f'</div>')
    return f"""\
<section>
  <div class="chapter-label">TABLE OF CONTENTS</div>
  <h2>目录</h2>
  <div class="rule-bar"></div>
  {rows}
</section>"""


# ── Part 1 ───────────────────────────────────────────────────────────────────

def s03_problem():
    pains = [
        ("🔀", "路由退化", RED,
         "多领域关键词冲突，\"重启\"既是运维又是客服。单 Agent 关键词路由准确率断崖下跌。"),
        ("🧨", "安全边界失守", ACCENT,
         "不同业务审批流差异巨大：重启 Pod / 发起转账 / 修改薪资，审批人和阈值完全不同。"),
        ("🧠", "工具选错", PURPLE,
         "LLM 面对 >20 个工具开始犯糊涂，工具检索准确率明显退化 — 模型能力天花板。"),
        ("🧴", "记忆串味", DEEP_BLUE,
         "FACTS/HYPOTHESES 只适合诊断场景，到了客服、数据领域强行复用会污染语义。"),
    ]
    cards = ""
    for emoji, title, color, body in pains:
        cards += (f'<div style="background:{LIGHT_GRAY};border-left:5px solid {color};'
                  f'border-radius:6px;padding:.6rem .8rem">'
                  f'<div style="font-size:1.5em">{emoji} '
                  f'<strong style="color:{color}">{title}</strong></div>'
                  f'<p style="margin:.3rem 0 0;font-size:.85em;color:{DARK_GRAY}">{body}</p>'
                  f'</div>')
    grid = (f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:.7rem">'
            f'{cards}</div>')
    conclusion = (f'<div class="bottom-bar">'
                  f'结论：窄而深的垂直 Agent 是护城河；一个 Agent 吃天下是陷阱'
                  f'</div>')
    return f"""\
<section>
  <div class="chapter-label">PART 1  背景与问题</div>
  <h2>旧架构的根本问题</h2>
  <p class="slide-subtitle">所有逻辑塞进一个 Agent，最终会崩塌在四个方向</p>
  <div class="rule-bar"></div>
  {grid}
  {conclusion}
</section>"""


def s04_insight():
    left_items = [
        "Planner / PlanStep / advance",
        "LangGraph StateGraph 编排",
        "ToolRegistry + MCP Gateway",
        "DiagnosisExecutor 多假设模式",
        "MemorySchema + RBAC 分层",
        "ApprovalPolicy + AuditLogger",
        "双入口 chat / chat_stream",
        "三级降级（L1 / L2 / L3）",
    ]
    right_items = [
        "tools/* K8s / Jenkins / Logs 12 个",
        "config/topology.yaml  服务拓扑",
        "router.py  运维关键词映射",
        "extract_namespace / pod / service",
        "_plan_read_only_tool  只读工具编排",
        "_build_pipeline_plan  发布流程",
        "_format_single_read_only_result",
        "_update_memory_from_tool_output",
    ]
    def make_col(items, color, check):
        return "".join(
            f'<div style="font-size:.82em;color:{color};margin:.2rem 0">'
            f'{check} {i}</div>' for i in items
        )
    left = (f'<div class="card card-navy">'
            f'<div class="hdr" style="color:{DEEP_BLUE}">70%  可复用骨架（领域无关）</div>'
            f'{make_col(left_items, NAVY, "✓")}</div>')
    right = (f'<div class="card card-orange">'
             f'<div class="hdr" style="color:{ACCENT}">30%  业务逻辑（Ops 特有）</div>'
             f'{make_col(right_items, DARK_GRAY, "●")}</div>')
    return f"""\
<section>
  <div class="chapter-label">PART 1  背景与问题</div>
  <h2>核心判断：70% 骨架无关领域，30% 才是业务</h2>
  <p class="slide-subtitle">这是整个架构重构的出发点</p>
  <div class="rule-bar"></div>
  <div class="two-col" style="grid-template-columns:1fr 1fr">
    {left}{right}
  </div>
  <div class="bottom-bar">把 70% 抽成 Agent Kernel → 新垂直 Agent 只写 30%</div>
</section>"""


def s05_principles():
    ps = [
        ("1", "Kernel 零领域知识",
         "任何关键词、工具名、业务 Schema、格式化函数都不许进 Kernel。"),
        ("2", "插件点显式声明",
         "Kernel 通过抽象基类 + 依赖注入暴露扩展点，不通过猜测或反射。"),
        ("3", "契约先于实现",
         "Pydantic / TypedDict 定义 Plan / PlanStep / ToolSpec / MemoryItem，跨 Vertical 共享。"),
        ("4", "安全边界可配置但不可绕过",
         "RBAC、Approval、side_effect 由 Kernel 强制执行，Vertical 只能填配置不能削弱。"),
        ("5", "每个 Vertical 独立实例",
         "各自的工具、路由、记忆语义，不共享运行时状态。"),
        ("6", "Supervisor 是 Agent 的 Planner",
         "上层调度不关心下层怎么干，只发 sub-plan 给具名的子 Agent。"),
    ]
    rows = ""
    for num, title, body in ps:
        rows += (f'<div style="display:flex;gap:.8rem;align-items:flex-start;'
                 f'padding:.5rem;border-bottom:1px solid #eee">'
                 f'<div style="background:{DEEP_BLUE};color:#fff;border-radius:50%;'
                 f'width:1.8rem;height:1.8rem;display:flex;align-items:center;'
                 f'justify-content:center;font-weight:700;flex-shrink:0">{num}</div>'
                 f'<div><strong style="color:{NAVY}">{title}</strong>'
                 f'<p style="margin:.15rem 0 0;font-size:.85em;color:{DARK_GRAY}">{body}</p>'
                 f'</div></div>')
    return f"""\
<section>
  <div class="chapter-label">PART 1  背景与问题</div>
  <h2>六条设计原则</h2>
  <p class="slide-subtitle">Kernel 和 Vertical 分界线的裁判</p>
  <div class="rule-bar"></div>
  {rows}
</section>"""


# ── Part 2 ───────────────────────────────────────────────────────────────────

def s06_layers():
    verticals = [
        ("OpsAgent", "运维（已落地）", GREEN),
        ("CsmAgent", "客服（未来）",   MED_GRAY),
        ("DataAgent", "数据（未来）",  MED_GRAY),
        ("DocAgent", "文档（未来）",   MED_GRAY),
    ]
    vcards = "".join(
        f'<div style="background:{c};color:#fff;border-radius:8px;'
        f'padding:.5rem;text-align:center;font-weight:700;font-size:.85em">'
        f'{n}<br/><span style="font-weight:400;font-size:.85em">{s}</span></div>'
        for n, s, c in verticals
    )
    kernel_comps = ["Planner", "Router", "Executor", "ToolRegistry", "MCPClient",
                    "MemorySchema", "MemoryBackend", "Approval", "Audit", "Session"]
    kboxes = "".join(
        f'<div style="background:{WHITE};border:1px solid {DEEP_BLUE};'
        f'border-radius:4px;padding:.2rem .5rem;font-size:.75em;color:{NAVY};'
        f'font-weight:600">{c}</div>' for c in kernel_comps
    )
    return f"""\
<section>
  <div class="chapter-label">PART 2  整体分层</div>
  <h2>三层总图：Supervisor / Vertical / Kernel</h2>
  <p class="slide-subtitle">从下向上是依赖关系，从上向下是调度关系</p>
  <div class="rule-bar"></div>

  <div style="background:{LIGHT_GRAY};border:2px dashed {MED_GRAY};
              border-radius:8px;padding:.6rem;text-align:center;
              color:{MED_GRAY};font-size:.85em;margin-bottom:.5rem">
    🔮 <strong>Supervisor（演进方向）</strong> — MetaPlanner · PlanStep.execution_target · 跨 Agent 联合审批
  </div>
  <div style="text-align:center;font-size:1.2em;color:{MED_GRAY}">↓ 派发任务</div>
  <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:.5rem;margin:.3rem 0">
    {vcards}
  </div>
  <div style="text-align:center;font-size:1.2em;color:{MED_GRAY}">↓ 调用 Kernel</div>
  <div style="background:{LIGHT_BLUE};border:2px solid {DEEP_BLUE};
              border-radius:8px;padding:.6rem">
    <strong style="color:{NAVY}">🧱 Agent Kernel（领域无关骨架）</strong>
    <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:.3rem;margin-top:.4rem">
      {kboxes}
    </div>
  </div>
</section>"""


def s07_boundary():
    kernel = [
        ("✅", GREEN, "编排 / 执行 / 审计 / 脱敏 / 降级", "通用能力"),
        ("✅", GREEN, "抽象基类 ExecutorBase / RouterBase", "插件契约"),
        ("✅", GREEN, "Pydantic Schema：Plan / PlanStep / ToolSpec", "数据契约"),
        ("✅", GREEN, "RouteKey / MemoryLayerKey 等可注册字符串", "开放契约"),
        ("❌", RED,   "不许出现 \"pod\" \"jenkins\" \"重启\" 等业务词", ""),
        ("❌", RED,   "不许硬编码路由、工具名、关键词", ""),
        ("❌", RED,   "不许导入 agent_ops 任何模块", ""),
    ]
    vertical = [
        ("✅", GREEN, "业务工具（K8s / Jenkins / CRM / SQL）", "调用链"),
        ("✅", GREEN, "业务关键词路由（\"重启\" → mutation）", "Router 子类"),
        ("✅", GREEN, "风险审批规则（生产 + mutation = 必审批）", "ApprovalPolicy"),
        ("✅", GREEN, "记忆层定义（observations / hypotheses ...）", "MemorySchema"),
        ("✅", GREEN, "业务 Prompt 和诊断打分启发式", "_heuristic_score"),
        ("✅", GREEN, "业务格式化输出（formatters.py）", ""),
        ("✅", GREEN, "Planner 子类覆写复合拆分规则", "OpsPlanner"),
    ]
    def make_list(items):
        return "".join(
            f'<div style="font-size:.82em;margin:.2rem 0">'
            f'<span style="color:{c};font-weight:700">{s}</span> '
            f'<span style="color:{DARK_GRAY}">{t}</span>'
            f'{"<br/><span style=\'color:"+MED_GRAY+";font-size:.85em\'>"+sub+"</span>" if sub else ""}'
            f'</div>'
            for s, c, t, sub in items
        )
    left = (f'<div class="card card-navy">'
            f'<div class="hdr" style="color:{DEEP_BLUE}">🧱 Agent Kernel 必须是什么</div>'
            f'{make_list(kernel)}</div>')
    right = (f'<div class="card card-orange">'
             f'<div class="hdr" style="color:{ACCENT}">🎯 Vertical Agent 才能是什么</div>'
             f'{make_list(vertical)}</div>')
    return f"""\
<section>
  <div class="chapter-label">PART 2  整体分层</div>
  <h2>Kernel vs Vertical：什么归谁？</h2>
  <p class="slide-subtitle">这条边界是架构评审的准绳 — 违反它就是反模式</p>
  <div class="rule-bar"></div>
  <div class="two-col" style="grid-template-columns:1fr 1fr">
    {left}{right}
  </div>
</section>"""


def s08_directory():
    kernel_tree = [
        "base_agent.py        # BaseAgent + LangGraph 图",
        "planner.py           # Planner + advance / replan",
        "router.py            # RouterBase",
        "executor.py          # ExecutorBase / FunctionExecutor",
        "schemas.py           # Plan / PlanStep / ToolSpec ...",
        "approval.py          # ApprovalPolicy + ApprovalDecision",
        "audit.py             # AuditLogger + sanitizer / sink",
        "session.py           # SessionStore ABC + InMemory",
        "memory/schema.py     # MemorySchema (RBAC)",
        "memory/backend.py    # MemoryBackend ABC",
        "tools/registry.py    # ToolRegistry",
        "tools/mcp_gateway.py # MCP Client",
        "patterns/            # 可选基类库",
        "  multi_hypothesis.py",
        "  approval_gate.py",
    ]
    ops_tree = [
        "agent.py             # OpsAgent 装配",
        "router.py            # IntentRouter (关键词)",
        "planner.py           # OpsPlanner (中文拆分)",
        "risk_policy.py       # OpsApprovalPolicy",
        "memory_schema.py     # OPS_MEMORY_SCHEMA 6 层",
        "extractors.py        # namespace / pod / service",
        "formatters.py        # 结果格式化",
        "memory_hooks.py      # tool → memory 规则",
        "topology.py          # ServiceTopology",
        "tool_setup.py        # 12 个 Ops 工具注册",
        "executors/knowledge.py",
        "executors/read_only.py",
        "executors/diagnosis.py  # 多假设",
        "executors/mutation.py",
    ]
    def tree_html(lines, color):
        return "".join(
            f'<div style="font-family:monospace;font-size:.75em;'
            f'color:{color};white-space:pre;margin:.05rem 0">{l}</div>'
            for l in lines
        )
    left = (f'<div class="card card-navy">'
            f'<div class="hdr mono" style="color:{DEEP_BLUE}">📁 agent_kernel/</div>'
            f'{tree_html(kernel_tree, NAVY)}</div>')
    right = (f'<div class="card card-orange">'
             f'<div class="hdr mono" style="color:{ACCENT}">📁 agent_ops/</div>'
             f'{tree_html(ops_tree, DARK_GRAY)}</div>')
    return f"""\
<section>
  <div class="chapter-label">PART 2  整体分层</div>
  <h2>代码组织：agent_kernel/ 和 agent_ops/ 两棵树</h2>
  <p class="slide-subtitle">每个目录都对应架构图上的一个盒子</p>
  <div class="rule-bar"></div>
  <div class="two-col" style="grid-template-columns:1fr 1fr">
    {left}{right}
  </div>
</section>"""


# ── Part 3 ───────────────────────────────────────────────────────────────────

def s09_components():
    rows_data = [
        ("BaseAgent",      "装配 Planner + Executors + Session，暴露 chat / chat_stream",       "base_agent.py"),
        ("Planner",        "生成 Plan、advance / replan、max_iterations 预算",                  "planner.py"),
        ("RouterBase",     "把自然语言请求 → RouteDecision",                                    "router.py · async route()"),
        ("ExecutorBase",   "单个路由的执行器抽象基类",                                           "executor.py · async execute(state)"),
        ("ToolRegistry",   "本地 + MCP 工具的统一 ToolSpec + retrieve",                         "tools/registry.py"),
        ("MCPClient",      "MCP 服务器网关，零代码接入远程工具",                                  "tools/mcp_gateway.py"),
        ("MemorySchema",   "记忆层定义 + RBAC 白名单",                                          "memory/schema.py"),
        ("MemoryBackend",  "共享记忆存储接口（InMemory / Redis / DB）",                         "memory/backend.py"),
        ("ApprovalPolicy", "审批策略接口 + ApprovalReceipt 校验",                               "approval.py · evaluate"),
        ("AuditLogger",    "审计日志 + sanitizer / sink 扩展钩子",                              "audit.py · log / add_sanitizer"),
        ("SessionStore",   "会话 + 消息历史 + 记忆条目存储",                                     "session.py (ABC + InMemory)"),
    ]
    trs = "".join(
        f'<tr><td style="font-family:monospace;font-weight:700;color:{NAVY}">{a}</td>'
        f'<td>{b}</td>'
        f'<td style="font-family:monospace;font-size:.85em;color:{MED_GRAY}">{c}</td></tr>'
        for a, b, c in rows_data
    )
    return f"""\
<section>
  <div class="chapter-label">PART 3  Agent Kernel 深度讲解</div>
  <h2>Kernel 的 11 个核心组件</h2>
  <p class="slide-subtitle">每个组件都有明确职责边界和可测试的接口</p>
  <div class="rule-bar"></div>
  <table>
    <thead><tr><th>组件</th><th>职责</th><th>关键接口 / 文件</th></tr></thead>
    <tbody>{trs}</tbody>
  </table>
</section>"""


def s10_graph():
    return f"""\
<section>
  <div class="chapter-label">PART 3  Agent Kernel 深度讲解</div>
  <h2>BaseAgent 的核心图（LangGraph StateGraph）</h2>
  <p class="slide-subtitle">一图读懂：planner 节点是中心，executors 是动态扇出</p>
  <div class="rule-bar"></div>

  <div style="text-align:center">
    <!-- entry -->
    <div style="display:inline-block;background:{MED_GRAY};color:#fff;
                border-radius:6px;padding:.3rem 1rem;font-size:.85em">▶ entry_point</div>
    <div style="font-size:1.5em;color:{MED_GRAY}">↓</div>

    <!-- planner -->
    <div style="display:inline-block;background:{NAVY};color:#fff;
                border-radius:8px;padding:.5rem 2rem;font-weight:700;
                font-family:monospace">planner — _planner_node</div>
    <div style="font-size:1.5em;color:{MED_GRAY}">↓ conditional edges</div>

    <!-- executors -->
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:.5rem;
                max-width:700px;margin:0 auto">
      <div style="background:{GREEN};color:#fff;border-radius:6px;padding:.5rem;
                  text-align:center;font-family:monospace;font-size:.85em">knowledge</div>
      <div style="background:{DEEP_BLUE};color:#fff;border-radius:6px;padding:.5rem;
                  text-align:center;font-family:monospace;font-size:.85em">read_only_ops</div>
      <div style="background:{PURPLE};color:#fff;border-radius:6px;padding:.5rem;
                  text-align:center;font-family:monospace;font-size:.85em">diagnosis</div>
      <div style="background:{ACCENT};color:#fff;border-radius:6px;padding:.5rem;
                  text-align:center;font-family:monospace;font-size:.85em">mutation</div>
    </div>
    <div style="font-size:1.5em;color:{MED_GRAY}">↓ advance()</div>

    <div style="display:inline-block;background:{RED};color:#fff;
                border-radius:6px;padding:.3rem 1.5rem;font-family:monospace">finish — END</div>
  </div>

  <div style="background:{LIGHT_BLUE};border-radius:8px;padding:.6rem 1rem;margin-top:.8rem">
    <strong style="color:{NAVY}">🔑 关键点</strong>
    <ul style="margin:.3rem 0;font-size:.85em">
      <li>执行器节点不是写死的 — 由 Vertical 传入的 executors 列表决定，BaseAgent 动态 add_node</li>
      <li>每个 executor 执行完必须回到 planner，由 advance() 决定 CONTINUE / REPLAN / FINISH</li>
      <li>execution_target 字段优先于 route，为未来 Supervisor 跨 Agent 派发预留语义</li>
    </ul>
  </div>
</section>"""


def s11_planner():
    left_code = code_block([
        "# initial_plan(request) → Plan",
        "1. self._split_compound(message)",
        "   → Kernel 默认返回 [message]",
        "   → OpsPlanner 覆写中文拆分",
        "",
        "2. for each segment:",
        "     decision = await router.route()",
        "     step = PlanStep(route, intent,",
        "                     goal, ...)",
        "",
        "3. 串成 Plan，带 depends_on 关系",
        "",
        "# 扩展点",
        "_split_compound(message) -> list[str]",
        "_dedupe_segments(segments, limit=3)",
    ])
    right_code = code_block([
        "# advance(plan, last_step) → Decision",
        "",
        "1. iterations ≥ max_iterations",
        "   → FINISH（防 AI 死循环）",
        "",
        "2. last_step.status == FAILED",
        "   → FINISH（fail-fast）",
        "",
        "3. 还有 PENDING 步骤",
        "   → CONTINUE",
        "",
        "4. _maybe_replan(plan, last)",
        "   → REPLAN（Vertical 覆写）",
        "",
        "5. 否则 → FINISH",
    ])
    left = (f'<div class="card card-navy">'
            f'<div class="hdr mono" style="color:{DEEP_BLUE}">initial_plan(request) → Plan</div>'
            f'{left_code}</div>')
    right = (f'<div class="card card-green">'
             f'<div class="hdr mono" style="color:{GREEN}">advance(plan, last_step) → Decision</div>'
             f'{right_code}</div>')
    return f"""\
<section>
  <div class="chapter-label">PART 3  Agent Kernel 深度讲解</div>
  <h2>Planner — 计划生成 + 前进决策</h2>
  <p class="slide-subtitle">两大职责：initial_plan() 和 advance()</p>
  <div class="rule-bar"></div>
  <div class="two-col" style="grid-template-columns:1fr 1fr">
    {left}{right}
  </div>
</section>"""


def s12_router():
    left = (f'<div class="card card-navy">'
            f'<div class="hdr mono" style="color:{DEEP_BLUE}">契约：RouterBase (ABC)</div>'
            + code_block([
                "class RouterBase(ABC):",
                "    @abstractmethod",
                "    async def route(",
                "        self, request: ChatRequest",
                "    ) -> RouteDecision:",
                "        ...",
            ])
            + f'<p style="font-size:.85em;margin:.5rem 0"><strong>RouteDecision 字段：</strong></p>'
            + code_block([
                "intent:           IntentTypeKey",
                "route:            RouteKey",
                "risk_level:       LOW/MEDIUM/HIGH/CRITICAL",
                "requires_approval: bool",
                "rationale:        str",
            ])
            + '</div>')
    right = (f'<div class="card card-orange">'
             f'<div class="hdr mono" style="color:{ACCENT}">Ops 实现：IntentRouter 关键词路由</div>'
             + code_block([
                 "\"MySQL / 地址 / 密码\"",
                 "  → knowledge / LOW / no-approval",
                 "",
                 "\"pod / 日志 / 状态 / 查\"",
                 "  → read_only_ops / LOW",
                 "",
                 "\"为什么 / 根因 / 故障\"",
                 "  → diagnosis / MEDIUM",
                 "",
                 "\"重启 / 部署 / 回滚\"",
                 "  → mutation / HIGH / 必审批",
                 "",
                 "# 进阶：换成 LLM-based Router",
                 "# 只要继承 RouterBase 即可",
             ])
             + '</div>')
    return f"""\
<section>
  <div class="chapter-label">PART 3  Agent Kernel 深度讲解</div>
  <h2>Router — 意图识别与路由决策</h2>
  <p class="slide-subtitle">从自然语言到 RouteDecision</p>
  <div class="rule-bar"></div>
  <div class="two-col" style="grid-template-columns:1fr 1fr">
    {left}{right}
  </div>
</section>"""


def s13_executor():
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
            f'<div style="font-family:monospace;font-size:.8em;color:{DARK_GRAY};'
            f'margin:.15rem 0">✓ {h}</div>' for h in hooks
        )
        pcards += (f'<div style="background:{LIGHT_GRAY};border-radius:8px;'
                   f'border-top:4px solid {color}">'
                   f'<div style="background:{color};color:#fff;border-radius:4px 4px 0 0;'
                   f'padding:.3rem .6rem;font-family:monospace;font-weight:700">{name}</div>'
                   f'<div style="padding:.5rem .6rem">'
                   f'<p style="font-size:.82em;color:{DARK_GRAY};margin:.2rem 0">{desc}</p>'
                   f'{hooks_html}</div></div>')
    return f"""\
<section>
  <div class="chapter-label">PART 3  Agent Kernel 深度讲解</div>
  <h2>ExecutorBase — 执行器抽象基类</h2>
  <p class="slide-subtitle">每个 route 对应一个 Executor；Vertical 自由扩展</p>
  <div class="rule-bar"></div>
  {code_block([
      "class ExecutorBase(ABC):",
      "    def __init__(self, *, node_name: str, route_name: str):",
      "        self.node_name  = node_name   # LangGraph 节点名",
      "        self.route_name = route_name  # 逻辑路由名",
      "",
      "    @abstractmethod",
      "    async def execute(self, state: dict, event_callback=None) -> dict:",
      '        """返回 {final_message, tool_calls, sources, ...}"""',
  ])}
  <p style="font-weight:700;color:{NAVY};margin:.5rem 0">
    Kernel 内置可选基类（agent_kernel/patterns/）</p>
  <div class="two-col" style="grid-template-columns:1fr 1fr">
    {pcards}
  </div>
</section>"""


def s14_tools():
    left = (f'<div class="card card-navy">'
            f'<div class="hdr mono" style="color:{DEEP_BLUE}">ToolSpec：工具元数据契约</div>'
            + code_block([
                "class ToolSpec(BaseModel):",
                "    name:              str",
                "    description:       str",
                "    tags:              list[str] = []",
                "    route_affinity:    list[str] = []",
                "    side_effect:       bool = False",
                "    source:            ToolSource  # LOCAL/MCP",
                "    parameters_schema: dict = {}",
            ])
            + f'<ul style="font-size:.82em;margin-top:.4rem">'
              f'<li><strong>side_effect=True</strong> → Kernel 自动要求 approval</li>'
              f'<li><strong>route_affinity</strong> → retrieve 优先匹配该路由工具</li>'
              f'<li><strong>source=MCP</strong> → 远端工具，通过 MCPClient 转发</li>'
              f'</ul></div>')
    right = (f'<div class="card card-orange">'
             f'<div class="hdr mono" style="color:{ACCENT}">MCP Gateway：远程工具零代码接入</div>'
             + code_block([
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
                 "   → 本地直接执行",
                 "   → MCP 转发到远端服务器",
                 "",
                 "# 效果：不写一行代码接入",
                 "# 任意 MCP-compatible 工具服务",
             ])
             + '</div>')
    return f"""\
<section>
  <div class="chapter-label">PART 3  Agent Kernel 深度讲解</div>
  <h2>ToolRegistry + MCP Gateway</h2>
  <p class="slide-subtitle">本地工具 + 远程 MCP 工具统一 ToolSpec，统一 retrieve</p>
  <div class="rule-bar"></div>
  <div class="two-col" style="grid-template-columns:1fr 1fr">
    {left}{right}
  </div>
</section>"""


def s15_memory():
    left = (f'<div class="card card-navy">'
            f'<div class="hdr mono" style="color:{DEEP_BLUE}">MemorySchema — RBAC 层定义</div>'
            + code_block([
                'schema = MemorySchema(layers={',
                '    "facts":        {"knowledge"},',
                '    "observations": {"read_ops"},',
                '    "hypotheses":   {"diagnosis"},',
                '    "plans":        {"planner"},',
                '})',
            ])
            + code_block([
                "# 关键 API",
                "schema.assert_can_write(layer, writer)",
                "# 非白名单 → PermissionError",
                "",
                "session_store.write_memory_item(...)",
                "# 每次写入自动调 assert_can_write",
                "",
                "session_store.resolve_memory_value(...)",
                "# 按优先顺序读多层",
            ])
            + '</div>')
    right = (f'<div class="card card-green">'
             f'<div class="hdr mono" style="color:{GREEN}">MemoryBackend — 存储可替换</div>'
             + code_block([
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
             ])
             + '</div>')
    return f"""\
<section>
  <div class="chapter-label">PART 3  Agent Kernel 深度讲解</div>
  <h2>Memory — 分层记忆 + RBAC + 后端抽象</h2>
  <p class="slide-subtitle">Schema 管谁能写什么层；Backend 管怎么存</p>
  <div class="rule-bar"></div>
  <div class="two-col" style="grid-template-columns:1fr 1fr">
    {left}{right}
  </div>
</section>"""


def s16_approval():
    gates = [
        ("①", "step 存在且 requires_approval=True", "否则拒绝"),
        ("②", "context 里有合法的 approval_receipt dict", "否则拒绝"),
        ("③", "receipt.step_id == 当前 step.step_id", "防止凭据复用"),
        ("④", "receipt.expires_at > 现在", "过期即失效"),
        ("⑤", "Vertical 可覆写 validate_receipt 额外校验", "金额 / namespace"),
    ]
    gates_html = "".join(
        f'<div style="display:flex;gap:.5rem;align-items:center;margin:.3rem 0">'
        f'<span style="background:{ACCENT};color:#fff;border-radius:4px;'
        f'padding:2px 8px;font-weight:700;flex-shrink:0">{n}</span>'
        f'<div style="background:{LIGHT_GRAY};padding:.3rem .6rem;'
        f'border-radius:4px;flex:1;font-size:.88em"><strong>{g}</strong>'
        f' <span style="color:{MED_GRAY}">→ 失败: {note}</span></div></div>'
        for n, g, note in gates
    )
    return f"""\
<section>
  <div class="chapter-label">PART 3  Agent Kernel 深度讲解</div>
  <h2>Approval — 审批凭据绑定步骤</h2>
  <p class="slide-subtitle">只认 ApprovalReceipt，不认 context.approved=true</p>
  <div class="rule-bar"></div>

  <div style="background:{LIGHT_GRAY};border-radius:8px;padding:.7rem 1rem;
              margin-bottom:.6rem">
    <strong style="color:{NAVY}">ApprovalReceipt 的 5 个关键字段</strong>
    {code_block([
        "receipt_id   — 唯一凭据 ID，可追溯",
        "step_id      — 绑定到具体 PlanStep（换步骤就失效）",
        "approved_by  — 谁批的",
        "scope        — 生效范围（某个 namespace / 某笔金额）",
        "expires_at   — 过期时间（默认几分钟）",
    ])}
  </div>

  <p style="font-weight:700;color:{NAVY}">ApprovalPolicy.evaluate() 验证流程</p>
  {gates_html}
</section>"""


def s17_audit():
    left = (f'<div class="card card-navy">'
            f'<div class="hdr mono" style="color:{DEEP_BLUE}">AuditEntry 字段</div>'
            + code_block([
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
            ])
            + '</div>')
    right = (f'<div class="card card-green">'
             f'<div class="hdr mono" style="color:{GREEN}">扩展钩子：sanitizer / sink</div>'
             + code_block([
                 "logger.add_sanitizer(lambda params: {",
                 "    **params,",
                 "    'password': '***',",
                 "    'token':    '***',",
                 "})",
                 "",
                 "logger.add_sink(siem_sink)    # 写 SIEM",
                 "logger.add_sink(metrics_sink) # 上报指标",
             ])
             + f'<ul style="font-size:.82em;margin-top:.4rem">'
               f'<li>Sanitizers 顺序执行，失败不影响下游</li>'
               f'<li>Sinks 独立异常隔离（一个挂了不传染）</li>'
               f'<li>默认已覆盖 password / token / ak / sk</li>'
               f'<li>所有 tool call 必过 log() — 自动审计所有 Vertical</li>'
               f'</ul></div>')
    return f"""\
<section>
  <div class="chapter-label">PART 3  Agent Kernel 深度讲解</div>
  <h2>Audit — 全量可审计 + 脱敏 + SIEM 推送</h2>
  <p class="slide-subtitle">每次工具调用产生一条 AuditEntry；支持 sanitizer / sink 扩展</p>
  <div class="rule-bar"></div>
  <div class="two-col" style="grid-template-columns:1fr 1fr">
    {left}{right}
  </div>
</section>"""


def s18_invariants():
    rules = [
        ("#1", RED,     "side_effect 工具必须 receipt",
         "side_effect=True 的工具只能被 requires_approval=True 的 step 调用，且必须携带已绑定、未过期的 approval_receipt",
         "E2E B01-B04"),
        ("#2", ACCENT,  "所有工具调用走 _invoke_tool",
         "必审计、必脱敏；业务代码不能越过 Kernel 直接调 handler",
         "E2E B08"),
        ("#3", PURPLE,  "记忆写入必须过 RBAC",
         "write_memory_item 先 assert_can_write；非法 writer → PermissionError",
         "E2E B05"),
        ("#4", DEEP_BLUE, "max_iterations 是硬预算",
         "超过立即 FINISH，防止 AI 死循环烧 Token",
         "E2E B06"),
        ("#5", GREEN,   "FAILED 默认 fail-fast",
         "一步失败就停车；Vertical 可通过 _maybe_replan 覆写，但必须显式",
         "E2E B07"),
    ]
    rows = ""
    for tag, color, title, body, test in rules:
        rows += (f'<div style="display:flex;gap:.8rem;align-items:flex-start;'
                 f'background:{LIGHT_GRAY};border-left:5px solid {color};'
                 f'border-radius:6px;padding:.5rem .7rem;margin:.3rem 0">'
                 f'<div style="color:{color};font-weight:700;font-size:1.1em;'
                 f'flex-shrink:0">{tag}</div>'
                 f'<div style="flex:1"><strong style="color:{NAVY}">{title}</strong>'
                 f'<p style="font-size:.82em;color:{DARK_GRAY};margin:.1rem 0">{body}</p></div>'
                 f'<span style="color:{color};font-size:.75em;font-weight:700;'
                 f'flex-shrink:0;align-self:center">{test}</span></div>')
    return f"""\
<section>
  <div class="chapter-label">PART 3  Agent Kernel 深度讲解</div>
  <h2>Kernel 的 5 条不变量（§4.2）</h2>
  <p class="slide-subtitle">任何 Vertical 都不能违反 — 业务代码绕不过去</p>
  <div class="rule-bar"></div>
  {rows}
</section>"""


# ── Part 4 ───────────────────────────────────────────────────────────────────

def s19_ops_composition():
    return f"""\
<section>
  <div class="chapter-label">PART 4  OpsAgent 垂直实现</div>
  <h2>OpsAgent 组成：装配就是套壳</h2>
  <p class="slide-subtitle">create_ops_agent() 把 Kernel 组件拼起来</p>
  <div class="rule-bar"></div>
  {code_block([
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
  ])}
  <div class="bottom-bar">想做新 Vertical？复制这个文件，替换 4 个 Ops 专有组件即可</div>
</section>"""


def s20_executors():
    execs = [
        ("KnowledgeExecutor", GREEN,
         "route = knowledge",
         "回答 MySQL 地址、联系人等静态知识",
         "query_knowledge",
         "facts 层"),
        ("ReadOnlyOpsExecutor", DEEP_BLUE,
         "route = read_only_ops",
         "查 pod / 日志 / 部署状态 / Jenkins 构建",
         "get_pod_status, get_pod_logs,\nsearch_logs, query_jenkins_build ...",
         "observations 层"),
        ("DiagnosisExecutor ★", PURPLE,
         "route = diagnosis",
         "多假设并行 + 证据收集 + 打分 + 根因总结",
         "diagnose_pod, get_pod_status,\nsearch_logs, get_error_statistics ...",
         "hypotheses 层"),
        ("MutationExecutor", ACCENT,
         "route = mutation",
         "变更：重启 / 发布 / 回滚，必带 receipt",
         "restart_pod, restart_deployment,\ntrigger_jenkins_build ...",
         "plans + execution 层"),
    ]
    cards = ""
    for name, color, route, desc, tools, mem in execs:
        cards += (f'<div style="border-radius:8px;overflow:hidden;'
                  f'border:1px solid #ddd">'
                  f'<div style="background:{color};color:#fff;padding:.4rem .6rem;'
                  f'font-family:monospace;font-weight:700;font-size:.85em">{name}</div>'
                  f'<div style="padding:.4rem .6rem;font-size:.78em">'
                  f'<div style="color:{color};font-family:monospace">{route}</div>'
                  f'<p style="margin:.3rem 0"><strong>职责：</strong>{desc}</p>'
                  f'<p style="margin:.3rem 0"><strong>典型工具：</strong>'
                  f'<span style="font-family:monospace">{tools}</span></p>'
                  f'<p style="margin:.2rem 0"><strong>记忆写入：</strong>'
                  f'<span style="color:{color};font-weight:700">{mem}</span></p>'
                  f'</div></div>')
    return f"""\
<section>
  <div class="chapter-label">PART 4  OpsAgent 垂直实现</div>
  <h2>OpsAgent 的 4 个 Executor</h2>
  <p class="slide-subtitle">每个路由都有明确的职责、工具范围、记忆写入权限</p>
  <div class="rule-bar"></div>
  <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:.5rem">
    {cards}
  </div>
</section>"""


def s21_planner():
    return f"""\
<section>
  <div class="chapter-label">PART 4  OpsAgent 垂直实现</div>
  <h2>OpsPlanner — 复合请求的中文拆分</h2>
  <p class="slide-subtitle">_split_compound 覆写示例：把一句话拆成多步</p>
  <div class="rule-bar"></div>

  <div style="background:{LIGHT_BLUE};border:2px solid {DEEP_BLUE};border-radius:8px;
              padding:.6rem;text-align:center;font-weight:700;color:{DEEP_BLUE};
              font-size:1.05em">
    「先查一下 staging pod 状态，然后帮我重启 order-service」
  </div>
  <div style="text-align:center;font-size:1.5em;color:{MED_GRAY}">↓ OpsPlanner._split_compound()</div>

  {code_block([
      "_OPS_SPLIT_PATTERNS = [",
      '    re.compile(r"\\s*然后\\s*"),',
      '    re.compile(r"\\s*接着\\s*"),',
      '    re.compile(r"\\s*再\\s*(?=(?:帮|把|重|触|回|生|执))"),',
      '    re.compile(r"\\s*,\\s*然后\\s*"),  # 中英逗号两种',
      "]",
  ])}
  <div style="text-align:center;font-size:1.5em;color:{MED_GRAY}">↓ 拆出 2 个 PlanStep</div>

  <div style="display:grid;grid-template-columns:1fr auto 1fr;
              align-items:center;gap:.5rem">
    <div style="background:{DEEP_BLUE};color:#fff;border-radius:8px;
                padding:.7rem;text-align:center;font-size:.9em">
      <strong>Step 1</strong><br/>
      查 staging pod 状态<br/>
      <span style="font-size:.85em">route = read_only_ops · LOW</span>
    </div>
    <div style="font-size:1.5em;color:{ACCENT}">→<br/>
      <span style="font-size:.5em;color:{MED_GRAY}">depends_on</span>
    </div>
    <div style="background:{ACCENT};color:#fff;border-radius:8px;
                padding:.7rem;text-align:center;font-size:.9em">
      <strong>Step 2</strong><br/>
      重启 order-service<br/>
      <span style="font-size:.85em">route = mutation · HIGH · 必审批</span>
    </div>
  </div>
</section>"""


def s22_approval_memory():
    matrix = [
        ("default / staging", "read_only_ops", "✓ 放行",    GREEN),
        ("default / staging", "diagnosis",     "✓ 放行",    GREEN),
        ("default / staging", "mutation",      "⚠ 需 receipt", ACCENT),
        ("production",        "read_only_ops", "✓ 放行",    GREEN),
        ("production",        "mutation",      "🛑 必须审批", RED),
        ("production",        "delete*",       "🛑 双人复核", RED),
    ]
    mat_rows = "".join(
        f'<tr><td>{ns}</td><td style="font-family:monospace">{rt}</td>'
        f'<td style="color:{c};font-weight:700">{d}</td></tr>'
        for ns, rt, d, c in matrix
    )
    layers = [
        ("facts",        "事实层：服务地址、联系人",   "knowledge"),
        ("observations", "观察层：pod 状态、日志",    "read_ops"),
        ("hypotheses",   "假设层：诊断假设 + 评分",   "diagnosis"),
        ("plans",        "计划层：变更计划",          "change_planner"),
        ("execution",    "执行层：已执行动作",         "change_executor"),
        ("verification", "验证层：验证结果",           "verifier"),
    ]
    layer_rows = "".join(
        f'<div style="display:grid;grid-template-columns:120px 1fr 100px;'
        f'gap:.3rem;align-items:center;margin:.2rem 0">'
        f'<div style="background:{DEEP_BLUE};color:#fff;border-radius:4px;'
        f'padding:2px 6px;font-family:monospace;font-size:.8em;text-align:center">{lyr}</div>'
        f'<div style="font-size:.82em;color:{DARK_GRAY}">{desc}</div>'
        f'<div style="font-family:monospace;font-size:.78em;color:{ACCENT};'
        f'font-weight:700">{writer}</div></div>'
        for lyr, desc, writer in layers
    )
    left = (f'<div class="card card-orange">'
            f'<div class="hdr mono" style="color:{ACCENT}">OpsApprovalPolicy（risk_policy.py）</div>'
            f'<p style="font-size:.85em;margin:.3rem 0"><strong>风险矩阵：</strong></p>'
            f'<table><thead><tr><th>namespace</th><th>route</th><th>决定</th></tr></thead>'
            f'<tbody>{mat_rows}</tbody></table></div>')
    right = (f'<div class="card card-navy">'
             f'<div class="hdr mono" style="color:{DEEP_BLUE}">OPS_MEMORY_SCHEMA（memory_schema.py）</div>'
             f'<p style="font-size:.85em;margin:.3rem 0"><strong>6 层记忆 + 对应 writer：</strong></p>'
             f'{layer_rows}</div>')
    return f"""\
<section>
  <div class="chapter-label">PART 4  OpsAgent 垂直实现</div>
  <h2>OpsApprovalPolicy + OPS_MEMORY_SCHEMA</h2>
  <p class="slide-subtitle">Ops 填好的两个安全相关插件槽</p>
  <div class="rule-bar"></div>
  <div class="two-col" style="grid-template-columns:1fr 1fr">
    {left}{right}
  </div>
</section>"""


# ── Part 5 ───────────────────────────────────────────────────────────────────

def s23_chat_flow():
    steps = [
        ("①", "API 入口",     "ChatRequest(message, user_id, session_id)"),
        ("②", "状态构建",     "_build_initial_state 读取最近 6 条消息"),
        ("③", "Planner 节点", "initial_plan → 拆分 → 第一个 PlanStep"),
        ("④", "Dispatcher",   "按 execution_target 或 route → 挑选节点"),
        ("⑤", "Executor 执行","执行器调 _invoke_tool 执行工具"),
        ("⑥", "Approval 闸门","side_effect 工具必须过 ApprovalPolicy.evaluate"),
        ("⑦", "_invoke_tool", "调用 handler.ainvoke + Audit 落盘"),
        ("⑧", "Memory 写入",  "按 Schema RBAC 写入对应层"),
        ("⑨", "回到 Planner", "advance → CONTINUE / REPLAN / FINISH"),
    ]
    rows = ""
    for num, title, body in steps:
        rows += (f'<div style="display:grid;grid-template-columns:2rem 100px 1fr;'
                 f'gap:.5rem;align-items:center;background:{LIGHT_GRAY};'
                 f'border-radius:4px;padding:.3rem .5rem;margin:.2rem 0">'
                 f'<div style="background:{ACCENT};color:#fff;border-radius:50%;'
                 f'width:1.5rem;height:1.5rem;display:flex;align-items:center;'
                 f'justify-content:center;font-weight:700;font-size:.8em">{num}</div>'
                 f'<strong style="color:{NAVY};font-size:.88em">{title}</strong>'
                 f'<span style="font-family:monospace;font-size:.8em;color:{DARK_GRAY}">{body}</span>'
                 f'</div>')
    return f"""\
<section>
  <div class="chapter-label">PART 5  关键执行流程</div>
  <h2>一次 chat() 请求的完整流水线</h2>
  <p class="slide-subtitle">从 HTTP 入口到 Audit 落盘的 9 个阶段</p>
  <div class="rule-bar"></div>
  {rows}
  <div style="background:{LIGHT_BLUE};border-radius:6px;padding:.4rem .8rem;
              margin-top:.5rem;text-align:center;font-weight:700;color:{DEEP_BLUE}">
    🔁 ③–⑨ 循环直到 FINISH 或 max_iterations
  </div>
</section>"""


def s24_approval_gate():
    return f"""\
<section>
  <div class="chapter-label">PART 5  关键执行流程</div>
  <h2>审批闸门的工作原理</h2>
  <p class="slide-subtitle">side_effect 工具从 _invoke_tool 到 handler 之间必经这个门</p>
  <div class="rule-bar"></div>

  <!-- flow diagram -->
  <div style="display:flex;align-items:center;gap:.4rem;flex-wrap:wrap;
              font-size:.82em;margin-bottom:.6rem">
    <div style="background:{DEEP_BLUE};color:#fff;border-radius:6px;
                padding:.3rem .7rem;font-family:monospace">Executor.execute()</div>
    <span style="color:{MED_GRAY}">→</span>
    <div style="background:{NAVY};color:#fff;border-radius:6px;
                padding:.3rem .7rem;font-family:monospace">_invoke_tool()</div>
    <span style="color:{MED_GRAY}">→</span>
    <div style="background:{LIGHT_GRAY};border:1px solid #ccc;border-radius:6px;
                padding:.3rem .7rem;font-family:monospace">spec.side_effect?</div>
  </div>

  <div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem">
    <div style="background:#ffe8e0;border:2px solid {RED};border-radius:8px;
                padding:.8rem">
      <strong style="color:{RED}">Yes（有副作用）</strong>
      <div style="font-size:.85em;margin:.4rem 0">
        → approval_policy.evaluate()<br/>
        → 未批准：<code>{{error: "需要审批..."}}</code><br/>
        → Audit 照样记录 FAILED 条目
      </div>
    </div>
    <div style="background:#e5f4e9;border:2px solid {GREEN};border-radius:8px;
                padding:.8rem">
      <strong style="color:{GREEN}">No（无副作用）或已批准</strong>
      <div style="font-size:.85em;margin:.4rem 0">
        → handler.ainvoke(args)<br/>
        → status = SUCCESS<br/>
        → Memory 写入 + Audit 落盘
      </div>
    </div>
  </div>

  <div style="background:{LIGHT_BLUE};border-radius:8px;padding:.6rem 1rem;
              margin-top:.6rem">
    <strong style="color:{NAVY}">💡 关键</strong>：这条路径由 Kernel 在 _invoke_tool 里
    <strong>强制执行</strong>。Vertical 想绕过？唯一办法是不走 _invoke_tool —
    但那就没有审计了，两难。
  </div>
</section>"""


def s25_diagnosis():
    stages = [
        ("1", "症状采集",   "_collect_symptoms",           "diagnose_pod\nget_pod_status\nsearch_logs",            LIGHT_BLUE),
        ("2", "假设生成",   "_generate_hypotheses",         "LLM + 拓扑 + 候选工具\n→ 至多 4 条 Hypothesis",         "#e5f4e9"),
        ("3", "并行取证",   "_collect_evidence_parallel",   "asyncio.gather\n每假设 ≤ 2 个证据工具",                 "#fff2e6"),
        ("4", "打分合成",   "_score_and_synthesize",        "error/oom/crashloop → +1.8\n疑点对象匹配 → +0.5",        "#f0e6ff"),
        ("5", "写入记忆",   "_write_memory",                "每条 hypothesis 一条\ntop_hypothesis_id\ndiagnosis_summary","#e8f1fa"),
    ]
    cards = "".join(
        f'<div style="background:{bg};border-radius:8px;padding:.5rem;text-align:center">'
        f'<div style="background:{ACCENT};color:#fff;border-radius:50%;width:1.5rem;'
        f'height:1.5rem;display:inline-flex;align-items:center;justify-content:center;'
        f'font-weight:700;margin-bottom:.3rem">{n}</div>'
        f'<div style="font-weight:700;color:{NAVY};font-size:.88em">{title}</div>'
        f'<div style="font-family:monospace;font-size:.72em;color:{DEEP_BLUE};'
        f'margin:.2rem 0">{func}</div>'
        f'<div style="font-family:monospace;font-size:.72em;color:{DARK_GRAY};'
        f'white-space:pre-line">{body}</div></div>'
        for n, title, func, body, bg in stages
    )
    return f"""\
<section>
  <div class="chapter-label">PART 5  关键执行流程</div>
  <h2>DiagnosisExecutor — 多假设并行诊断</h2>
  <p class="slide-subtitle">「多个假设 · 并行取证 · 启发式打分 · 归纳结论」</p>
  <div class="rule-bar"></div>

  <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:.4rem;margin-bottom:.5rem">
    {cards}
  </div>

  <div style="background:#ffe8d8;border-radius:6px;padding:.4rem .8rem;
              font-size:.82em;color:{ACCENT};font-weight:700">
    🛟 降级路径：假设生成失败 / LLM 不可用 → _fallback_single_chain 仍然返回已收集症状
  </div>
  <div style="background:{LIGHT_BLUE};border-radius:6px;padding:.5rem .8rem;
              margin-top:.4rem;font-size:.82em">
    ✨ 这个 5-stage pipeline 已被抽成 Kernel 的 <strong>MultiHypothesisExecutor</strong> 基类。
    未来客服 Agent 也可继承：「退款 / 漏发 / 延迟」三条假设并行查 → 打分归纳根因。
  </div>
</section>"""


def s26_compound():
    events = [
        ("t=0ms",  NAVY,      "用户",         "「先查一下 staging pod 状态，然后帮我重启 order-service」"),
        ("t=5ms",  DEEP_BLUE, "Planner",      "OpsPlanner._split_compound 分出 2 段 Step"),
        ("t=8ms",  GREEN,     "Router × 2",   "Step1 → read_only_ops / LOW · Step2 → mutation / HIGH / 必审批"),
        ("t=30ms", DEEP_BLUE, "Step 1 执行",  "ReadOnlyOpsExecutor → get_pod_status(staging) → Audit + Memory.observations"),
        ("t=45ms", MED_GRAY,  "advance()",    "Plan cursor → Step 2；返回 CONTINUE"),
        ("t=48ms", RED,       "Step 2 进入",  "MutationExecutor → restart_deployment → side_effect=True → ApprovalPolicy.evaluate 拒绝"),
        ("t=50ms", ACCENT,    "返回用户",     "\"此操作需要审批\" · Audit 记录一条 FAILED 条目"),
    ]
    rows = "".join(
        f'<div style="display:grid;grid-template-columns:55px 100px 1fr;gap:.4rem;'
        f'align-items:center;margin:.25rem 0">'
        f'<span style="font-family:monospace;font-size:.75em;color:{MED_GRAY}">{t}</span>'
        f'<span style="background:{c};color:#fff;border-radius:4px;padding:1px 6px;'
        f'font-size:.78em;font-weight:700;text-align:center">{phase}</span>'
        f'<span style="font-size:.82em;color:{DARK_GRAY}">{desc}</span></div>'
        for t, c, phase, desc in events
    )
    return f"""\
<section>
  <div class="chapter-label">PART 5  关键执行流程</div>
  <h2>实战例子：复合请求的全链路时序</h2>
  <p class="slide-subtitle">「先查 staging pod，然后重启 order-service」</p>
  <div class="rule-bar"></div>
  {rows}
</section>"""


# ── Part 6 ───────────────────────────────────────────────────────────────────

def s27_plugins():
    rows_data = [
        ("1",  "路由器",       "RouterBase.route() -> RouteDecision",           "IntentRouter 关键词映射"),
        ("2",  "执行器",       "ExecutorBase.execute(state) -> dict",           "Knowledge/ReadOnly/Diagnosis/Mutation"),
        ("3",  "工具",         "@tool + ToolRegistry.register_local/_mcp",      "K8s/Jenkins/Logs/Knowledge 共 12 个"),
        ("4",  "MCP 服务器",   "MCPClient.register_server(name, url)",          "可接入任意 MCP-compatible 远端工具"),
        ("5",  "Planner 定制", "Planner 子类 _split_compound / _maybe_replan", "OpsPlanner 中文复合拆分"),
        ("6",  "记忆 Schema",  "MemorySchema(layers={...})",                    "OPS_MEMORY_SCHEMA 6 层"),
        ("7",  "审批策略",     "ApprovalPolicy.evaluate(step, context)",        "OpsApprovalPolicy 风险矩阵"),
        ("8",  "审计扩展",     "AuditLogger.add_sanitizer / add_sink",          "Ops 级脱敏 + SIEM 可扩展"),
        ("9",  "RBAC 身份",    "AgentIdentityKey 可注册字符串",                  "knowledge/read_ops/diagnosis 等"),
        ("10", "Executor 模式","MultiHypothesisExecutor/ApprovalGateExecutor",  "Ops DiagnosisExecutor 可继承"),
    ]
    trs = "".join(
        f'<tr>'
        f'<td style="text-align:center;color:{ACCENT};font-weight:700">{n}</td>'
        f'<td style="font-weight:700;color:{NAVY}">{name}</td>'
        f'<td style="font-family:monospace;font-size:.8em">{contract}</td>'
        f'<td style="font-size:.85em;color:{MED_GRAY}">{ops}</td>'
        f'</tr>'
        for n, name, contract, ops in rows_data
    )
    return f"""\
<section>
  <div class="chapter-label">PART 6  插件化与扩展</div>
  <h2>10 个插件点：Kernel 对外的所有扩展面</h2>
  <p class="slide-subtitle">Vertical 就是「填这些槽位」</p>
  <div class="rule-bar"></div>
  <table style="font-size:.82em">
    <thead>
      <tr><th>#</th><th>插件点</th><th>基类 / 契约</th><th>Ops 填了什么</th></tr>
    </thead>
    <tbody>{trs}</tbody>
  </table>
</section>"""


def s28_new_vertical():
    steps = [
        ("1", "定义记忆 Schema",     "agent_csm/memory_schema.py",
         ["CSM_MEMORY_SCHEMA = MemorySchema(layers={",
          "    'user_profile':    {'crm_reader'},",
          "    'conversation':    {'dialogue'},",
          "    'order_context':   {'crm_reader'},",
          "    'escalation_plan': {'supervisor'},",
          "})"]),
        ("2", "定义风险策略",         "agent_csm/risk_policy.py",
         ["class CsmApprovalPolicy(ApprovalPolicy):",
          "    def validate_receipt(...):",
          "        # 退款 > 1000 元 → 需要主管 receipt"]),
        ("3", "定义路由器",           "agent_csm/router.py",
         ["class CsmKeywordRouter(RouterBase):",
          "    async def route(request):",
          "        if '退款' in msg: return RouteDecision(..., 'refund', HIGH)",
          "        if '物流' in msg: return RouteDecision(..., 'tracking', LOW)"]),
        ("4", "实现执行器 + 注册工具", "agent_csm/executors/ 和 tools/",
         ["RefundExecutor / TrackingExecutor / EscalationExecutor",
          "接入 CRM / 订单系统 / 工单系统"]),
        ("5", "装配入口",             "agent_csm/__init__.py",
         ["# 复制 create_ops_agent() → 改 4 个专有组件",
          "audit_logger / session_store / mcp_client 继续用 Kernel factory"]),
    ]
    rows = ""
    for num, title, filepath, code in steps:
        code_html = "".join(
            f'<div style="font-family:monospace;font-size:.72em;color:#e0e0e0;'
            f'white-space:pre">{l}</div>' for l in code
        )
        rows += (f'<div style="display:flex;gap:.6rem;align-items:flex-start;'
                 f'margin:.3rem 0">'
                 f'<div style="background:{ACCENT};color:#fff;border-radius:50%;'
                 f'width:1.5rem;height:1.5rem;display:flex;align-items:center;'
                 f'justify-content:center;font-weight:700;flex-shrink:0;'
                 f'font-size:.82em">{num}</div>'
                 f'<div style="flex:1">'
                 f'<div style="font-weight:700;color:{NAVY};font-size:.88em">{title}</div>'
                 f'<div style="font-family:monospace;font-size:.78em;color:{ACCENT};'
                 f'margin:.1rem 0">{filepath}</div>'
                 f'<div style="background:{CODE_BG};border-radius:4px;padding:.3rem .5rem">'
                 f'{code_html}</div></div></div>')
    return f"""\
<section>
  <div class="chapter-label">PART 6  插件化与扩展</div>
  <h2>做一个新 Vertical 需要几步？</h2>
  <p class="slide-subtitle">以假想的「CsmAgent 客服」为例 · ～ 1–2 周工作量</p>
  <div class="rule-bar"></div>
  {rows}
</section>"""


# ── Part 7 ───────────────────────────────────────────────────────────────────

def s29_degradation():
    levels = [
        ("L1", DEEP_BLUE, "Executor 级降级",
         "触发：单个 executor 抛异常",
         "处理：PlanStepStatus=FAILED → fail-fast → 返回错误说明",
         "用户看到：\"步骤 X 执行失败\"；其他步骤不继续"),
        ("L2", ACCENT,    "Planner 级降级",
         "触发：max_iterations 耗尽 / Planner 生成空 Plan",
         "处理：fallback_plan 兜底 → 单 knowledge step",
         "用户看到：AI 进入「普通问答」模式"),
        ("L3", RED,       "Kernel 级降级",
         "触发：Receipt 失败 / 记忆 Backend 故障 / 整个 graph 崩",
         "处理：chat() 外层 try/except → ChatResponse 不崩",
         "用户看到：\"系统暂时不可用\"；Audit 仍然落盘错误条目"),
    ]
    cards = "".join(
        f'<div style="display:flex;gap:.8rem;background:{LIGHT_GRAY};'
        f'border-radius:8px;padding:.7rem;margin:.4rem 0">'
        f'<div style="background:{c};color:#fff;border-radius:6px;'
        f'padding:.3rem .8rem;font-size:1.8em;font-weight:700;'
        f'display:flex;align-items:center;flex-shrink:0">{lv}</div>'
        f'<div><strong style="color:{c};font-size:1em">{title}</strong>'
        f'<ul style="margin:.3rem 0;font-size:.83em">'
        f'<li>{trigger}</li><li>{handle}</li><li>{ux}</li></ul></div></div>'
        for lv, c, title, trigger, handle, ux in levels
    )
    return f"""\
<section>
  <div class="chapter-label">PART 7  演进方向与测试</div>
  <h2>三级降级路径（§10）</h2>
  <p class="slide-subtitle">每一级都有明确触发条件和用户感知</p>
  <div class="rule-bar"></div>
  {cards}
</section>"""


def s30_testing():
    groups = [
        ("A", "Happy Path",     5, GREEN),
        ("B", "Kernel 不变量",   8, RED),
        ("C", "插件点",          7, PURPLE),
        ("D", "Vertical 隔离",   2, DEEP_BLUE),
        ("E", "降级路径",         2, ACCENT),
        ("F", "反模式回归",       3, MED_GRAY),
    ]
    badges = "".join(
        f'<div style="background:{c};color:#fff;border-radius:6px;'
        f'padding:.3rem .6rem;font-size:.82em;font-weight:700;text-align:center">'
        f'{code}&nbsp;{name}<br/><span style="font-size:.9em">{cnt} 个</span></div>'
        for code, name, cnt, c in groups
    )
    return f"""\
<section>
  <div class="chapter-label">PART 7  演进方向与测试</div>
  <h2>测试金字塔与目前覆盖情况</h2>
  <p class="slide-subtitle">L0 单元 / L1 契约 / L2 E2E — 共 84 个自动化测试用例</p>
  <div class="rule-bar"></div>

  <!-- pyramid -->
  <div style="display:flex;flex-direction:column;align-items:center;gap:.3rem;
              margin:.5rem 0">
    <div style="background:{PURPLE};color:#fff;border-radius:6px;
                padding:.4rem 2rem;font-weight:700;font-size:.9em">
      L2 · E2E · 27 个
    </div>
    <div style="background:{DEEP_BLUE};color:#fff;border-radius:6px;
                padding:.4rem 4rem;font-weight:700;font-size:.9em">
      L1 · Kernel 契约 · 4 个（保证 Kernel 不与 Ops 耦合）
    </div>
    <div style="background:{GREEN};color:#fff;border-radius:6px;
                padding:.4rem 6rem;font-weight:700;font-size:.9em;text-align:center">
      L0 · 单元测试 · 53 个<br/>
      <span style="font-size:.85em">Planner / Registry / Topology / Memory / Patterns ...</span>
    </div>
  </div>

  <p style="font-weight:700;color:{NAVY};margin:.5rem 0">E2E 测试矩阵（27 个用例分 6 组）</p>
  <div style="display:grid;grid-template-columns:repeat(6,1fr);gap:.4rem">
    {badges}
  </div>
</section>"""


def s31_supervisor():
    agents = [
        ("DataAgent",  "查销售曲线\nSQL + BI",       GREEN),
        ("CsmAgent",   "投诉分类\n退款/延迟占比",     DEEP_BLUE),
        ("OpsAgent",   "线上异常排查\n影响转化故障",  ACCENT),
        ("DocAgent",   "生成一页\n摘要报告",          PURPLE),
    ]
    acards = "".join(
        f'<div style="background:{c};color:#fff;border-radius:8px;'
        f'padding:.6rem;text-align:center;font-weight:700;font-size:.85em">'
        f'{n}<br/><span style="font-weight:400;font-size:.82em;white-space:pre-line">{b}</span></div>'
        for n, b, c in agents
    )
    return f"""\
<section>
  <div class="chapter-label">PART 7  演进方向与测试</div>
  <h2>未来演进：Supervisor 多 Agent 协同</h2>
  <p class="slide-subtitle">跨域问题拆给多个 Vertical，最后汇总 — §7 演进方向</p>
  <div class="rule-bar"></div>

  <div style="background:{NAVY};color:#fff;border-radius:6px;padding:.5rem;
              text-align:center;font-weight:700;font-size:1.05em">
    用户：「Q3 订单为什么下滑？」
  </div>
  <div style="text-align:center;font-size:1.3em;color:{MED_GRAY}">↓</div>
  <div style="background:{ACCENT};color:#fff;border-radius:8px;padding:.5rem;
              text-align:center;font-weight:700;margin-bottom:.3rem">
    Supervisor（MetaPlanner + AgentProxyExecutor）
  </div>
  <div style="text-align:center;font-size:1.3em;color:{MED_GRAY}">↓ 派发子任务</div>
  <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:.4rem;margin:.3rem 0">
    {acards}
  </div>
  <div style="text-align:center;font-size:1.3em;color:{MED_GRAY}">↓ 聚合</div>
  <div style="background:{NAVY};color:#fff;border-radius:6px;padding:.4rem;
              text-align:center;font-weight:700;font-size:.9em">
    Supervisor 聚合：联合审批 / 跨 Agent Audit / 统一 ChatResponse
  </div>

  <div style="background:{LIGHT_BLUE};border-radius:6px;padding:.4rem .8rem;
              margin-top:.5rem;font-size:.82em">
    🔑 关键机制：<code>PlanStep.execution_target = "agent:data" / "agent:csm" / ...</code><br/>
    已在 PlanStep Schema 预留字段 — 不需要重构 Kernel 就能落地 Supervisor
  </div>
</section>"""


def s32_roadmap():
    roadmap = [
        ("近期 Q2",  ["DiagnosisExecutor 接入 MultiHypothesisExecutor 基类",
                      "接入真实 MCP 服务器（k8s-mcp / jenkins-mcp）",
                      "补齐 RedisSessionStore / MemoryBackend"]),
        ("中期 Q3-Q4", ["落地第二个 Vertical（客服 / 数据 任选）",
                        "Kernel 跨域通用性验证",
                        "Agent 的灰度 / 回滚 / 版本管理"]),
        ("远期 1年+",  ["Supervisor 多 Agent 协同落地",
                        "跨域请求自动拆解",
                        "统一人机交互与观测平台"]),
    ]
    rmap = "".join(
        f'<div style="margin:.3rem 0">'
        f'<div style="display:inline-block;background:{ACCENT};color:#fff;'
        f'border-radius:4px;padding:1px 8px;font-size:.82em;font-weight:700">{stage}</div>'
        f'<ul style="margin:.2rem 0 .2rem 1.3em;font-size:.82em">'
        + "".join(f'<li>{i}</li>' for i in items)
        + '</ul></div>'
        for stage, items in roadmap
    )
    summary_items = [
        "Kernel 11 个组件 + 5 条不变量",
        "10 个插件点 + 新 Vertical 五步清单",
        "OpsAgent 4 个 Executor + 中文拆分",
        "审批凭据 / 多假设诊断详细流程",
        "三级降级 + 84 个自动化测试覆盖",
        "Supervisor 演进方向已预留字段",
    ]
    summary_html = "".join(
        f'<li style="font-size:.85em">{i}</li>' for i in summary_items
    )
    left = (f'<div class="card card-navy">'
            f'<div class="hdr" style="color:{DEEP_BLUE}">✅ 本次讲解覆盖</div>'
            f'<ul>{summary_html}</ul>'
            f'<div style="background:{NAVY};border-radius:6px;padding:.6rem;'
            f'margin-top:.5rem">'
            f'<strong style="color:{ACCENT}">核心价值</strong>'
            f'<p style="color:#fff;font-size:.82em;margin:.3rem 0">'
            f'OpsAgent 只是起点 — 我们在造一个<br/>'
            f'可以孵化任意业务 Agent 的基建平台</p></div>'
            f'</div>')
    right = (f'<div class="card card-orange">'
             f'<div class="hdr" style="color:{ACCENT}">🛣️ 路线图</div>'
             f'{rmap}</div>')
    return f"""\
<section>
  <div class="chapter-label">PART 7  演进方向与测试</div>
  <h2>总结 · 路线图 · Q&amp;A</h2>
  <div class="rule-bar"></div>
  <div class="two-col" style="grid-template-columns:1fr 1fr">
    {left}{right}
  </div>
</section>"""


# ─── assemble ────────────────────────────────────────────────────────────────

SLIDE_FUNCS = [
    s01_cover, s02_toc,
    s03_problem, s04_insight, s05_principles,
    s06_layers, s07_boundary, s08_directory,
    s09_components, s10_graph, s11_planner, s12_router,
    s13_executor, s14_tools, s15_memory,
    s16_approval, s17_audit, s18_invariants,
    s19_ops_composition, s20_executors, s21_planner, s22_approval_memory,
    s23_chat_flow, s24_approval_gate, s25_diagnosis, s26_compound,
    s27_plugins, s28_new_vertical,
    s29_degradation, s30_testing, s31_supervisor, s32_roadmap,
]


def main():
    slides_html = "\n\n".join(fn() for fn in SLIDE_FUNCS)
    html = SHELL.format(css=CSS, slides=slides_html)
    out = Path("docs/index.html")
    out.parent.mkdir(exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"✅ Generated: {out.resolve()}")
    print(f"   Slides:    {len(SLIDE_FUNCS)}")
    print()
    print("🌐 在线预览（推送后）：")
    print("   https://htmlpreview.github.io/?https://raw.githubusercontent.com/"
          "lichao01111-dot/ops-agent/main/docs/index.html")


if __name__ == "__main__":
    main()
