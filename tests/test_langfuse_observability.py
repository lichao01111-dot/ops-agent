from __future__ import annotations

import sys
import types

import pytest

from agent_kernel.observability import LLMContext, LLMOutput, TraceContext
from agent_kernel.observability._context import current_observation_handle
from agent_kernel.observability.langfuse_sink import LangfuseSink
from llm_gateway.observed import ObservedChatModel


class FakeObservation:
    def __init__(self, name: str = "") -> None:
        self.name = name
        self.updates = []
        self.children = []
        self.ended = False

    def span(self, **kwargs):
        child = FakeObservation(kwargs.get("name", "span"))
        child.kwargs = kwargs
        self.children.append(child)
        return child

    def generation(self, **kwargs):
        child = FakeObservation(kwargs.get("name", "generation"))
        child.kwargs = kwargs
        self.children.append(child)
        return child

    def event(self, **kwargs):
        self.children.append(("event", kwargs))

    def start_observation(self, **kwargs):
        child = FakeObservation(kwargs.get("name", "observation"))
        child.kwargs = kwargs
        self.children.append(child)
        return child

    def update(self, **kwargs):
        self.updates.append(kwargs)

    def end(self):
        self.ended = True

    def set_trace_io(self, **kwargs):
        self.trace_io = kwargs


class FakeContextManager:
    def __init__(self, value=None):
        self.value = value or FakeObservation("cm")

    def __enter__(self):
        return self.value

    def __exit__(self, exc_type, exc, tb):
        self.value.end()


class FakeLangfuseClient:
    traces = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def start_as_current_observation(self, **kwargs):
        trace = FakeObservation("trace")
        trace.kwargs = kwargs
        self.traces.append(trace)
        return FakeContextManager(trace)


def fake_propagate_attributes(**kwargs):
    return FakeContextManager(FakeObservation("attrs"))


def test_langfuse_sink_scrubs_and_never_raises(monkeypatch):
    FakeLangfuseClient.traces = []
    monkeypatch.setitem(
        sys.modules,
        "langfuse",
        types.SimpleNamespace(Langfuse=FakeLangfuseClient, propagate_attributes=fake_propagate_attributes),
    )

    sink = LangfuseSink(public_key="pk", secret_key="sk", sample_rate=1.0)
    trace = sink.trace_start(
        TraceContext(
            trace_id="trace-1",
            name="agent_chat",
            session_id="sess",
            user_id="u@example.com",
            vertical="ops",
            input="联系我 u@example.com，token=abc",
        )
    )
    sink.trace_end(trace, {"email": "u@example.com", "api_key": "secret"}, None)

    assert FakeLangfuseClient.traces[0].kwargs["input"] == "联系我 ***EMAIL***，token=abc"
    assert FakeLangfuseClient.traces[0].updates[0]["output"]["email"] == "***EMAIL***"
    assert FakeLangfuseClient.traces[0].updates[0]["output"]["api_key"] == "***REDACTED***"


@pytest.mark.asyncio
async def test_observed_chat_model_emits_generation():
    class FakeResponse:
        content = "answer"
        usage_metadata = {"input_tokens": 3, "output_tokens": 5}
        response_metadata = {"finish_reason": "stop"}

    class FakeModel:
        async def ainvoke(self, messages, **kwargs):
            return FakeResponse()

    class FakeSink:
        def __init__(self):
            self.starts = []
            self.ends = []

        def llm_start(self, parent, ctx: LLMContext):
            self.starts.append((parent, ctx))
            return "generation-handle"

        def llm_end(self, handle, output: LLMOutput, error):
            self.ends.append((handle, output, error))

    parent = object()
    sink = FakeSink()
    token = current_observation_handle.set(parent)
    try:
        model = ObservedChatModel(FakeModel(), model_name="gemini-test", purpose="main", sink=sink)
        response = await model.ainvoke([{"role": "user", "content": "hi"}])
    finally:
        current_observation_handle.reset(token)

    assert response.content == "answer"
    assert sink.starts[0][0] is parent
    assert sink.starts[0][1].input_messages == [{"role": "user", "content": "hi"}]
    assert sink.ends[0][0] == "generation-handle"
    assert sink.ends[0][1].completion == "answer"
    assert sink.ends[0][1].input_tokens == 3
    assert sink.ends[0][1].output_tokens == 5
    assert sink.ends[0][2] is None
