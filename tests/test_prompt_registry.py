from __future__ import annotations

import sys
import types

import pytest

from llm_gateway.prompt_registry import PromptRegistry


class FakePrompt:
    version = 7

    def compile(self, **variables):
        return f"hello {variables['name']}"


class FakeLangfuse:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def get_prompt(self, name, label):
        assert name == "ops/test"
        assert label == "staging"
        return FakePrompt()


def test_prompt_registry_uses_langfuse_prompt(monkeypatch):
    monkeypatch.setitem(sys.modules, "langfuse", types.SimpleNamespace(Langfuse=FakeLangfuse))
    registry = PromptRegistry(
        enabled=True,
        public_key="pk",
        secret_key="sk",
        host="http://langfuse",
        label="staging",
    )

    prompt = registry.get_prompt("ops/test", "fallback {name}", name="world")

    assert prompt.text == "hello world"
    assert prompt.meta.name == "ops/test"
    assert prompt.meta.version == 7
    assert prompt.source == "langfuse"


def test_prompt_registry_falls_back_when_disabled():
    registry = PromptRegistry(enabled=False)

    prompt = registry.get_prompt("ops/test", "fallback {name}", name="world")

    assert prompt.text == "fallback world"
    assert prompt.meta.name == "ops/test"
    assert prompt.meta.version is None
    assert prompt.source == "fallback"


def test_prompt_registry_falls_back_when_langfuse_errors(monkeypatch):
    class BrokenLangfuse(FakeLangfuse):
        def get_prompt(self, name, label):
            raise RuntimeError("down")

    monkeypatch.setitem(sys.modules, "langfuse", types.SimpleNamespace(Langfuse=BrokenLangfuse))
    registry = PromptRegistry(enabled=True, public_key="pk", secret_key="sk", fallback_on_error=True)

    prompt = registry.get_prompt("ops/test", "fallback {name}", name="world")

    assert prompt.text == "fallback world"
    assert prompt.source == "fallback"


def test_prompt_registry_can_raise_when_fallback_disabled(monkeypatch):
    class BrokenLangfuse(FakeLangfuse):
        def get_prompt(self, name, label):
            raise RuntimeError("down")

    monkeypatch.setitem(sys.modules, "langfuse", types.SimpleNamespace(Langfuse=BrokenLangfuse))
    registry = PromptRegistry(enabled=True, public_key="pk", secret_key="sk", fallback_on_error=False)

    with pytest.raises(RuntimeError, match="down"):
        registry.get_prompt("ops/test", "fallback {name}", name="world")
