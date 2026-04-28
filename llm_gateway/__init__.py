"""
LLM Gateway - 多模型切换层
支持 Google AI / OpenAI / Anthropic / DeepSeek / Qwen / 私有化部署
统一接口，自动 Fallback，Token 用量追踪
"""
from __future__ import annotations

from typing import Any

import structlog
from langchain_core.language_models import BaseChatModel
from config import settings, LLMProvider
from llm_gateway.observed import ObservedChatModel

logger = structlog.get_logger()


class LLMGateway:
    """LLM 网关：统一管理多个 LLM 提供商，支持路由层/分析层双模型"""

    def __init__(self, sink: Any = None):
        self._models: dict[str, BaseChatModel] = {}
        self._sink = sink
        self._token_usage: dict[str, dict] = {}  # provider -> {input_tokens, output_tokens, cost}
        self._init_models()

    def _init_models(self):
        """初始化配置的 LLM 模型"""
        # Main model (heavy, for analysis)
        self._models["main"] = self._create_model(
            provider=settings.llm_provider,
            model=settings.llm_model,
            base_url=self._resolve_base_url(settings.llm_provider, settings.openai_base_url, settings.google_api_base_url),
            temperature=settings.llm_temperature,
        )
        logger.info("main_llm_initialized", provider=settings.llm_provider.value, model=settings.llm_model)

        # Router model (lightweight, for intent classification)
        self._models["router"] = self._create_model(
            provider=settings.router_llm_provider,
            model=settings.router_llm_model,
            base_url=self._resolve_base_url(
                settings.router_llm_provider,
                settings.router_openai_base_url,
                settings.router_google_api_base_url or settings.google_api_base_url,
            ),
            temperature=0.0,
        )
        logger.info("router_llm_initialized", provider=settings.router_llm_provider.value, model=settings.router_llm_model)

    @staticmethod
    def _resolve_base_url(provider: LLMProvider, openai_base_url: str, google_base_url: str) -> str:
        if provider == LLMProvider.GOOGLE:
            return google_base_url
        return openai_base_url

    def _create_model(
        self,
        provider: LLMProvider,
        model: str,
        base_url: str = "",
        temperature: float = 0.1,
    ) -> BaseChatModel:
        """根据 provider 创建对应的 LangChain ChatModel"""

        if provider == LLMProvider.GOOGLE:
            from langchain_google_genai import ChatGoogleGenerativeAI

            client_options = {"api_endpoint": base_url} if base_url else None
            return ChatGoogleGenerativeAI(
                model=model,
                google_api_key=settings.google_api_key,
                client_options=client_options,
                temperature=temperature,
                max_output_tokens=settings.llm_max_tokens,
                convert_system_message_to_human=True,
            )

        if provider == LLMProvider.ANTHROPIC:
            from langchain_anthropic import ChatAnthropic
            return ChatAnthropic(
                model=model,
                anthropic_api_key=settings.anthropic_api_key,
                temperature=temperature,
                max_tokens=settings.llm_max_tokens,
            )

        # OpenAI-compatible: covers OpenAI, DeepSeek, Qwen, vLLM, Ollama
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model,
            api_key=settings.openai_api_key,
            base_url=base_url or settings.openai_base_url,
            temperature=temperature,
            max_tokens=settings.llm_max_tokens,
        )

    def get_main_model(self) -> ObservedChatModel:
        """获取主模型（用于复杂分析、日志诊断、Pipeline 生成）"""
        return self._observed_model("main", settings.llm_model, settings.llm_temperature)

    def get_router_model(self) -> ObservedChatModel:
        """获取路由模型（用于意图识别、参数提取）"""
        return self._observed_model("router", settings.router_llm_model, 0.0)

    def get_model(self, name: str = "main") -> ObservedChatModel:
        """按名称获取模型"""
        if name not in self._models:
            raise ValueError(f"Unknown model: {name}. Available: {list(self._models.keys())}")
        model_name = settings.llm_model if name == "main" else settings.router_llm_model if name == "router" else name
        return self._observed_model(name, model_name, settings.llm_temperature)

    def set_observability_sink(self, sink: Any) -> None:
        self._sink = sink

    def _observed_model(self, name: str, model_name: str, temperature: float) -> ObservedChatModel:
        return ObservedChatModel(
            self._models[name],
            model_name=model_name,
            purpose=name,
            sink=self._sink if settings.langfuse_llm_observation_enabled else None,
            model_parameters={
                "temperature": temperature,
                "max_tokens": settings.llm_max_tokens,
            },
        )

    def register_model(self, name: str, provider: LLMProvider, model: str, base_url: str = "", **kwargs):
        """动态注册新模型（运行时扩展）"""
        self._models[name] = self._create_model(provider, model, base_url, **kwargs)
        logger.info("model_registered", name=name, provider=provider.value, model=model)

    def get_usage_stats(self) -> dict:
        """获取 Token 用量统计"""
        return dict(self._token_usage)


# 全局单例
llm_gateway = LLMGateway()
