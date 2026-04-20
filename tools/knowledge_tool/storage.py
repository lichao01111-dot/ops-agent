from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

import psycopg
import structlog
from psycopg import sql
from psycopg.rows import dict_row

from config import settings

logger = structlog.get_logger()


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in values) + "]"


@dataclass
class StoredDocumentResult:
    document_id: str
    chunk_count: int
    replaced_existing: bool


class PostgresKnowledgeStore:
    def __init__(self, *, vector_dimensions: int | None = None) -> None:
        self._schema_ready = False
        self._vector_dimensions = int(vector_dimensions or settings.embedding_dimensions)

    @property
    def _schema_name(self) -> str:
        return settings.knowledge_pg_schema.strip() or "public"

    def _connect(self, *, autocommit: bool) -> psycopg.Connection:
        return psycopg.connect(
            settings.knowledge_pg_dsn,
            autocommit=autocommit,
            row_factory=dict_row,
        )

    def ensure_schema(self) -> None:
        if self._schema_ready:
            return

        vector_dim = max(8, self._vector_dimensions)
        schema_identifier = sql.Identifier(self._schema_name)
        with self._connect(autocommit=True) as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(schema_identifier))
            conn.execute(sql.SQL("SET search_path TO {}, public").format(schema_identifier))
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS knowledge_documents (
                    id UUID PRIMARY KEY,
                    source_name TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    original_filename TEXT,
                    content_hash TEXT NOT NULL UNIQUE,
                    raw_content TEXT NOT NULL,
                    metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS knowledge_chunks (
                    id UUID PRIMARY KEY,
                    document_id UUID NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
                    chunk_index INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    embedding VECTOR({vector_dim}) NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_document_id ON knowledge_chunks(document_id)"
            )
            try:
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_embedding "
                    "ON knowledge_chunks USING hnsw (embedding vector_cosine_ops)"
                )
            except Exception as exc:
                logger.warning("knowledge_embedding_index_skipped", error=str(exc))
        self._schema_ready = True

    def upsert_document(
        self,
        *,
        source_name: str,
        source_type: str,
        original_filename: str | None,
        content_hash: str,
        raw_content: str,
        metadata: dict[str, Any],
        chunks: list[dict[str, Any]],
    ) -> StoredDocumentResult:
        self.ensure_schema()
        with self._connect(autocommit=False) as conn:
            conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(self._schema_name)))
            existing = conn.execute(
                "SELECT id FROM knowledge_documents WHERE content_hash = %s",
                (content_hash,),
            ).fetchone()

            replaced_existing = existing is not None
            if replaced_existing:
                document_id = str(existing["id"])
                conn.execute("DELETE FROM knowledge_chunks WHERE document_id = %s::uuid", (document_id,))
                conn.execute(
                    """
                    UPDATE knowledge_documents
                    SET source_name = %s,
                        source_type = %s,
                        original_filename = %s,
                        raw_content = %s,
                        metadata = %s::jsonb,
                        updated_at = NOW()
                    WHERE id = %s::uuid
                    """,
                    (
                        source_name,
                        source_type,
                        original_filename,
                        raw_content,
                        json.dumps(metadata, ensure_ascii=False),
                        document_id,
                    ),
                )
            else:
                document_id = str(uuid.uuid4())
                conn.execute(
                    """
                    INSERT INTO knowledge_documents (
                        id, source_name, source_type, original_filename, content_hash, raw_content, metadata
                    )
                    VALUES (%s::uuid, %s, %s, %s, %s, %s, %s::jsonb)
                    """,
                    (
                        document_id,
                        source_name,
                        source_type,
                        original_filename,
                        content_hash,
                        raw_content,
                        json.dumps(metadata, ensure_ascii=False),
                    ),
                )

            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO knowledge_chunks (id, document_id, chunk_index, content, metadata, embedding)
                    VALUES (%s::uuid, %s::uuid, %s, %s, %s::jsonb, %s::vector)
                    """,
                    [
                        (
                            str(uuid.uuid4()),
                            document_id,
                            chunk["chunk_index"],
                            chunk["content"],
                            json.dumps(chunk["metadata"], ensure_ascii=False),
                            _vector_literal(chunk["embedding"]),
                        )
                        for chunk in chunks
                    ],
                )
            conn.commit()

        logger.info(
            "knowledge_document_upserted",
            document_id=document_id,
            source_name=source_name,
            source_type=source_type,
            chunk_count=len(chunks),
            replaced_existing=replaced_existing,
        )
        return StoredDocumentResult(
            document_id=document_id,
            chunk_count=len(chunks),
            replaced_existing=replaced_existing,
        )

    def search(self, query_embedding: list[float], *, k: int = 5) -> list[dict[str, Any]]:
        self.ensure_schema()
        vector = _vector_literal(query_embedding)
        with self._connect(autocommit=True) as conn:
            conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(self._schema_name)))
            rows = conn.execute(
                """
                SELECT
                    c.content,
                    c.metadata AS chunk_metadata,
                    d.source_name,
                    d.source_type,
                    d.original_filename,
                    d.metadata AS document_metadata,
                    1 - (c.embedding <=> %s::vector) AS score
                FROM knowledge_chunks c
                JOIN knowledge_documents d ON d.id = c.document_id
                ORDER BY c.embedding <=> %s::vector
                LIMIT %s
                """,
                (vector, vector, k),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_stats(self) -> dict[str, Any]:
        self.ensure_schema()
        with self._connect(autocommit=True) as conn:
            conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(self._schema_name)))
            doc_count = conn.execute("SELECT COUNT(*) AS count FROM knowledge_documents").fetchone()["count"]
            chunk_count = conn.execute("SELECT COUNT(*) AS count FROM knowledge_chunks").fetchone()["count"]
        return {
            "backend": "postgres",
            "schema": self._schema_name,
            "collection_name": settings.knowledge_collection,
            "document_count": doc_count,
            "chunk_count": chunk_count,
            "embedding_dimensions": self._vector_dimensions,
            "embedding_model": settings.embedding_model,
            "embedding_provider": settings.embedding_provider,
        }

    def list_documents(self, *, limit: int = 20) -> list[dict[str, Any]]:
        self.ensure_schema()
        with self._connect(autocommit=True) as conn:
            conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(self._schema_name)))
            rows = conn.execute(
                """
                SELECT
                    d.id,
                    d.source_name,
                    d.source_type,
                    d.original_filename,
                    d.created_at,
                    d.updated_at,
                    COUNT(c.id) AS chunk_count
                FROM knowledge_documents d
                LEFT JOIN knowledge_chunks c ON c.document_id = d.id
                GROUP BY d.id
                ORDER BY d.updated_at DESC
                LIMIT %s
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
