"""
OpsAgent 全局配置
使用 pydantic-settings 管理，支持 .env 文件和环境变量
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMProvider(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    DEEPSEEK = "deepseek"
    QWEN = "qwen"


class LogProvider(str, Enum):
    ELASTICSEARCH = "elasticsearch"
    LOKI = "loki"
    SLS = "sls"
    CUSTOM = "custom"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Main LLM ---
    llm_provider: LLMProvider = LLMProvider.OPENAI
    llm_model: str = "gpt-4o"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    anthropic_api_key: str = ""
    llm_temperature: float = 0.1
    llm_max_tokens: int = 4096

    # --- Router LLM (lightweight) ---
    router_llm_provider: LLMProvider = LLMProvider.OPENAI
    router_llm_model: str = "gpt-4o-mini"
    router_openai_base_url: str = "https://api.openai.com/v1"

    # --- Jenkins ---
    jenkins_url: str = ""
    jenkins_user: str = ""
    jenkins_token: str = ""

    # --- Kubernetes ---
    kubeconfig_path: Optional[str] = None
    k8s_allowed_namespaces: str = "dev,staging,default"
    k8s_readonly_namespaces: str = "prod,production"

    # --- Log System ---
    log_provider: LogProvider = LogProvider.ELASTICSEARCH
    elasticsearch_url: str = "http://localhost:9200"
    loki_url: str = "http://localhost:3100"

    # --- Knowledge / Embeddings ---
    knowledge_backend: str = "postgres"
    knowledge_pg_dsn: str = "postgresql://ops_agent:ops_agent@localhost:5432/ops_agent"
    knowledge_pg_schema: str = "public"
    knowledge_collection: str = "ops_knowledge"
    embedding_provider: str = "openai_compatible"
    embedding_model: str = "text-embedding-3-small"
    embedding_api_key: str = ""
    embedding_base_url: str = ""
    embedding_dimensions: int = 1536
    embedding_batch_size: int = 32
    local_embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    knowledge_upload_dir: str = "./data/uploads"
    knowledge_chunk_size: int = 1000
    knowledge_chunk_overlap: int = 200

    # --- Redis ---
    redis_url: str = "redis://localhost:6379/0"

    # --- Server ---
    server_host: str = "0.0.0.0"
    server_port: int = 8000

    # --- Security ---
    secret_key: str = "change-me"
    admin_users: str = ""

    @property
    def allowed_namespaces(self) -> list[str]:
        return [ns.strip() for ns in self.k8s_allowed_namespaces.split(",") if ns.strip()]

    @property
    def readonly_namespaces(self) -> list[str]:
        return [ns.strip() for ns in self.k8s_readonly_namespaces.split(",") if ns.strip()]

    @property
    def admin_user_list(self) -> list[str]:
        return [u.strip() for u in self.admin_users.split(",") if u.strip()]


settings = Settings()
