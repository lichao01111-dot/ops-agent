# Observability

OpsAgent keeps the existing observability stack and adds Langfuse as an optional trace backend.

## What Gets Reported

- `Trace`: one `agent_chat` per `BaseAgent.chat()` request.
- `Span`: planner, executor, and tool stages.
- `Generation`: LLM calls wrapped by `ObservedChatModel`.
- `MetricSample`: existing tool metrics still go through `StructlogSink`, `PrometheusSink`, `SloAlertSink`, or `MultiSink`.

## Configuration

Langfuse is off by default. Enable it with:

```bash
LANGFUSE_ENABLED=true
LANGFUSE_PUBLIC_KEY=pk-lf-xxx
LANGFUSE_SECRET_KEY=sk-lf-xxx
LANGFUSE_HOST=https://langfuse.internal
LANGFUSE_SAMPLE_RATE=0.1
LANGFUSE_ENABLED_VERTICALS=ops
LANGFUSE_PROMPT_MANAGEMENT_ENABLED=false
LANGFUSE_PROMPT_LABEL=production
LANGFUSE_PROMPT_FALLBACK_ON_ERROR=true
```

`LANGFUSE_SAMPLE_RATE` is trace-level sampling. A sampled trace includes its child spans and LLM generations together.

## Safety

- Langfuse SDK calls are isolated with `try/except`; failed reporting does not break agent execution.
- `LangfuseSink` applies the default audit sanitizer and masks common email/phone patterns before upload.
- Tool metrics keep using the existing sink path, with Langfuse added through `MultiSink` only when enabled.

## Current Scope

Implemented:

- Trace/span/generation skeleton.
- Langfuse sink with sampling and defensive failure handling.
- LLM gateway wrapper preserving the existing `.ainvoke()` and `.with_structured_output()` call surface.
- Basic planner/executor/tool stage instrumentation.
- Prompt registry fallback for router, planner, diagnosis, and knowledge prompts.

Pending from the RFC:

- User feedback scores.
- Eval datasets and regression jobs.
- Local Prometheus LLM cost counters.
