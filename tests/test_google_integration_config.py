from __future__ import annotations

import sys
import types

from config import LLMProvider
from config.settings import Settings
from llm_gateway.observed import ObservedChatModel


def test_llm_gateway_google_provider(monkeypatch):
    inits: list[dict[str, object]] = []

    class FakeChatGoogleGenerativeAI:
        def __init__(self, **kwargs):
            inits.append(kwargs)

    fake_module = types.SimpleNamespace(ChatGoogleGenerativeAI=FakeChatGoogleGenerativeAI)
    monkeypatch.setitem(sys.modules, "langchain_google_genai", fake_module)

    import llm_gateway as gateway_module

    fake_settings = Settings(
        llm_provider=LLMProvider.GOOGLE,
        llm_model="gemini-2.0-flash",
        google_api_key="google-key",
        google_api_base_url="custom.googleapis.com",
        router_llm_provider=LLMProvider.GOOGLE,
        router_llm_model="gemini-2.0-flash-lite",
        router_google_api_base_url="router.googleapis.com",
        llm_max_tokens=2048,
    )
    monkeypatch.setattr(gateway_module, "settings", fake_settings)

    gateway = gateway_module.LLMGateway()
    main_model = gateway.get_main_model()
    router_model = gateway.get_router_model()

    assert isinstance(main_model, ObservedChatModel)
    assert isinstance(router_model, ObservedChatModel)
    assert isinstance(main_model.unwrap(), FakeChatGoogleGenerativeAI)
    assert isinstance(router_model.unwrap(), FakeChatGoogleGenerativeAI)
    assert inits[0]["model"] == "gemini-2.0-flash"
    assert inits[0]["google_api_key"] == "google-key"
    assert inits[0]["max_output_tokens"] == 2048
    assert inits[0]["convert_system_message_to_human"] is True
    assert inits[0]["client_options"] == {"api_endpoint": "custom.googleapis.com"}
    assert inits[1]["model"] == "gemini-2.0-flash-lite"
    assert inits[1]["client_options"] == {"api_endpoint": "router.googleapis.com"}


def test_create_embedding_client_google(monkeypatch):
    calls: list[dict[str, object]] = []

    class FakeGoogleGenerativeAIEmbeddings:
        def __init__(self, **kwargs):
            calls.append({"init": kwargs})

        def embed_documents(self, texts, **kwargs):
            calls.append({"documents": {"texts": texts, **kwargs}})
            return [[0.1, 0.2] for _ in texts]

        def embed_query(self, text, **kwargs):
            calls.append({"query": {"text": text, **kwargs}})
            return [0.3, 0.4]

    fake_module = types.SimpleNamespace(GoogleGenerativeAIEmbeddings=FakeGoogleGenerativeAIEmbeddings)
    monkeypatch.setitem(sys.modules, "langchain_google_genai", fake_module)

    import tools.knowledge_tool.embeddings as embeddings_module

    fake_settings = Settings(
        embedding_provider="google",
        embedding_model="gemini-embedding-001",
        embedding_api_key="embed-key",
        embedding_base_url="embed.googleapis.com",
        embedding_dimensions=768,
        embedding_batch_size=16,
        google_api_key="fallback-key",
    )
    monkeypatch.setattr(embeddings_module, "settings", fake_settings)

    client = embeddings_module.create_embedding_client()
    assert client.embed_texts(["a", "b"]) == [[0.1, 0.2], [0.1, 0.2]]
    assert client.embed_query("q") == [0.3, 0.4]

    init_calls = [call["init"] for call in calls if "init" in call]
    assert init_calls[-1] == {
        "model": "gemini-embedding-001",
        "google_api_key": "embed-key",
        "client_options": {"api_endpoint": "embed.googleapis.com"},
    }
    document_calls = [call["documents"] for call in calls if "documents" in call]
    query_calls = [call["query"] for call in calls if "query" in call]
    assert document_calls[-1]["task_type"] == "RETRIEVAL_DOCUMENT"
    assert document_calls[-1]["output_dimensionality"] == 768
    assert document_calls[-1]["batch_size"] == 16
    assert query_calls[-1]["task_type"] == "RETRIEVAL_QUERY"
    assert query_calls[-1]["output_dimensionality"] == 768
