from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import httpx
import structlog

from config import settings

logger = structlog.get_logger()


def _batched(values: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield values[index:index + size]


class EmbeddingClient:
    vector_dimensions: int

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def embed_query(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]


@dataclass
class OpenAICompatibleEmbeddingClient(EmbeddingClient):
    api_key: str
    base_url: str
    model: str
    batch_size: int
    vector_dimensions: int

    def _endpoint(self) -> str:
        return f"{self.base_url.rstrip('/')}/embeddings"

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if not self.api_key:
            raise RuntimeError("embedding_api_key 未配置，无法调用正式向量化接口。")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        embeddings: list[list[float]] = []
        with httpx.Client(timeout=90.0) as client:
            for batch in _batched(texts, max(1, self.batch_size)):
                response = client.post(
                    self._endpoint(),
                    headers=headers,
                    json={"model": self.model, "input": batch},
                )
                response.raise_for_status()
                payload = response.json()
                data = payload.get("data") or []
                data.sort(key=lambda item: item.get("index", 0))
                embeddings.extend(item["embedding"] for item in data)
        logger.info("embedding_batch_complete", provider="openai_compatible", count=len(embeddings), model=self.model)
        return embeddings


@dataclass
class LocalSentenceTransformerEmbeddingClient(EmbeddingClient):
    model_name: str

    def __post_init__(self) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(self.model_name)
        dimension_getter = getattr(self._model, "get_embedding_dimension", None)
        if callable(dimension_getter):
            self.vector_dimensions = int(dimension_getter())
        else:
            self.vector_dimensions = int(self._model.get_sentence_embedding_dimension())

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._model.encode(texts, normalize_embeddings=True)
        logger.info("embedding_batch_complete", provider="local_sentence_transformer", count=len(texts), model=self.model_name)
        return [vector.tolist() for vector in vectors]


def create_embedding_client() -> EmbeddingClient:
    provider = settings.embedding_provider.lower().strip()
    if provider == "openai_compatible":
        api_key = settings.embedding_api_key or settings.openai_api_key
        base_url = settings.embedding_base_url or settings.openai_base_url
        return OpenAICompatibleEmbeddingClient(
            api_key=api_key,
            base_url=base_url,
            model=settings.embedding_model,
            batch_size=settings.embedding_batch_size,
            vector_dimensions=settings.embedding_dimensions,
        )
    if provider == "huggingface_local":
        return LocalSentenceTransformerEmbeddingClient(settings.local_embedding_model)
    raise RuntimeError(f"不支持的 embedding_provider: {settings.embedding_provider}")
