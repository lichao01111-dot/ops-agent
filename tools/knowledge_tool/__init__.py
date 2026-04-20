"""
知识库 RAG Tool
- PostgreSQL + pgvector 存储
- OpenAI-compatible 正式向量化接口
- 支持目录导入与上传入库 pipeline
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import uuid

import structlog
from langchain_core.tools import tool

from config import settings
from tools.knowledge_tool.embeddings import create_embedding_client
from tools.knowledge_tool.ingest import (
    IngestionSource,
    build_document_payloads,
    load_directory_sources,
    parse_raw_document,
)
from tools.knowledge_tool.storage import PostgresKnowledgeStore

logger = structlog.get_logger()


class KnowledgeBase:
    def __init__(self) -> None:
        self._embedding_client = create_embedding_client()
        self._store = PostgresKnowledgeStore(vector_dimensions=self._embedding_client.vector_dimensions)

    def ingest_sources(self, sources: list[IngestionSource], *, source_label: str) -> dict[str, Any]:
        if not sources:
            raise ValueError("没有可入库的资料。")

        prepared = build_document_payloads(sources)
        ingested_documents: list[dict[str, Any]] = []
        total_chunks = 0
        total_sources = len(prepared)
        total_prepared_chunks = sum(len(item["chunks"]) for item in prepared)

        for item in prepared:
            source = item["source"]
            chunks = item["chunks"]
            embeddings = self._embedding_client.embed_texts([chunk["content"] for chunk in chunks])
            stored = self._store.upsert_document(
                source_name=source.source_name,
                source_type=source.source_type,
                original_filename=source.original_filename,
                content_hash=item["content_hash"],
                raw_content=source.content,
                metadata=source.metadata,
                chunks=[
                    {
                        **chunk,
                        "embedding": embedding,
                    }
                    for chunk, embedding in zip(chunks, embeddings, strict=True)
                ],
            )
            total_chunks += stored.chunk_count
            ingested_documents.append(
                {
                    "document_id": stored.document_id,
                    "source_name": source.source_name,
                    "source_type": source.source_type,
                    "original_filename": source.original_filename,
                    "chunk_count": stored.chunk_count,
                    "replaced_existing": stored.replaced_existing,
                }
            )

        stats = self.get_stats()
        return {
            "status": "success",
            "source_label": source_label,
            "ingested_documents": ingested_documents,
            "document_count": len(ingested_documents),
            "chunk_count": total_chunks,
            "pipeline": [
                {
                    "stage": "read",
                    "status": "completed",
                    "source_count": total_sources,
                    "message": f"已读取 {total_sources} 份资料。",
                },
                {
                    "stage": "parse",
                    "status": "completed",
                    "source_count": total_sources,
                    "message": "已完成文本解析。",
                },
                {
                    "stage": "split",
                    "status": "completed",
                    "chunk_count": total_prepared_chunks,
                    "message": f"已切分为 {total_prepared_chunks} 个 chunk。",
                },
                {
                    "stage": "embed",
                    "status": "completed",
                    "chunk_count": total_chunks,
                    "provider": settings.embedding_provider,
                    "model": settings.embedding_model,
                    "message": f"已通过 {settings.embedding_provider} / {settings.embedding_model} 完成向量化。",
                },
                {
                    "stage": "store",
                    "status": "completed",
                    "backend": "postgres",
                    "document_count": len(ingested_documents),
                    "message": f"已写入 PostgreSQL，新增/更新 {len(ingested_documents)} 份资料。",
                },
            ],
            "stats": stats,
        }

    def ingest_directory(self, docs_dir: str) -> dict[str, Any]:
        sources = load_directory_sources(docs_dir)
        return self.ingest_sources(sources, source_label=docs_dir)

    def ingest_uploads(self, files: list[dict[str, Any]]) -> dict[str, Any]:
        upload_root = Path(settings.knowledge_upload_dir).resolve()
        upload_root.mkdir(parents=True, exist_ok=True)

        sources = [
            parse_raw_document(
                raw_bytes=item["content"],
                filename=item["filename"],
                source_type="upload",
                metadata={
                    "content_type": item.get("content_type", ""),
                    "stored_path": self._persist_upload(upload_root, item["filename"], item["content"]),
                },
            )
            for item in files
        ]
        return self.ingest_sources(sources, source_label="upload")

    def search(self, query: str, *, k: int = 5) -> list[dict[str, Any]]:
        embedding = self._embedding_client.embed_query(query)
        rows = self._store.search(embedding, k=k)
        return [
            {
                "content": row["content"],
                "source": row["source_name"],
                "source_type": row["source_type"],
                "original_filename": row["original_filename"],
                "score": float(row["score"]),
                "metadata": row.get("chunk_metadata") or {},
            }
            for row in rows
        ]

    def get_stats(self) -> dict[str, Any]:
        return self._store.get_stats()

    def list_documents(self, *, limit: int = 20) -> list[dict[str, Any]]:
        return self._store.list_documents(limit=limit)

    def health(self) -> dict[str, Any]:
        stats = self.get_stats()
        return {"status": "ok", **stats}

    def _persist_upload(self, upload_root: Path, filename: str, raw_bytes: bytes) -> str:
        target = upload_root / f"{uuid.uuid4().hex}_{Path(filename).name}"
        target.write_bytes(raw_bytes)
        return str(target)


knowledge_base = KnowledgeBase()


def load_markdown_docs(docs_dir: str) -> list[IngestionSource]:
    """兼容旧调用方；现已升级为目录资料加载，而不只限 markdown。"""
    return load_directory_sources(docs_dir)


@tool
async def query_knowledge(question: str, top_k: int = 5) -> str:
    """在 PostgreSQL + pgvector 知识库中检索与问题最相关的资料片段。"""
    results = knowledge_base.search(question, k=top_k)
    if not results:
        return json.dumps(
            {
                "answer_status": "no_results",
                "message": "知识库中未找到相关信息，建议检查资料是否已入库。",
                "results": [],
            },
            ensure_ascii=False,
        )

    return json.dumps(
        {
            "answer_status": "found",
            "question": question,
            "results": results,
        },
        ensure_ascii=False,
    )


@tool
async def index_documents(docs_directory: str) -> str:
    """将指定目录中的资料解析、分块、向量化后写入知识库。"""
    try:
        docs_path = Path(docs_directory)
        if not docs_path.exists():
            return json.dumps({"error": f"目录不存在: {docs_directory}"}, ensure_ascii=False)

        report = knowledge_base.ingest_directory(docs_directory)
        return json.dumps(report, ensure_ascii=False)
    except Exception as exc:
        logger.exception("knowledge_index_failed", directory=docs_directory, error=str(exc))
        return json.dumps({"error": f"索引失败: {str(exc)}"}, ensure_ascii=False)


knowledge_tools = [query_knowledge, index_documents]


__all__ = [
    "KnowledgeBase",
    "IngestionSource",
    "index_documents",
    "knowledge_base",
    "knowledge_tools",
    "load_markdown_docs",
    "query_knowledge",
]
