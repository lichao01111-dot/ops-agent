from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader

from config import settings

logger = structlog.get_logger()


@dataclass
class IngestionSource:
    source_name: str
    source_type: str
    original_filename: str | None
    content: str
    metadata: dict[str, Any]


def _clean_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _split_text(source: IngestionSource) -> list[dict[str, Any]]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.knowledge_chunk_size,
        chunk_overlap=settings.knowledge_chunk_overlap,
        separators=["\n## ", "\n### ", "\n\n", "\n", " ", ""],
    )
    pieces = splitter.split_text(source.content)
    return [
        {
            "chunk_index": index,
            "content": piece,
            "metadata": {
                "source": source.source_name,
                "source_type": source.source_type,
                "original_filename": source.original_filename,
                **source.metadata,
            },
        }
        for index, piece in enumerate(pieces)
        if piece.strip()
    ]


def _parse_pdf(raw_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(raw_bytes))
    texts = [(page.extract_text() or "").strip() for page in reader.pages]
    return "\n\n".join(text for text in texts if text)


def _parse_csv(raw_bytes: bytes) -> str:
    rows = list(csv.reader(io.StringIO(raw_bytes.decode("utf-8", errors="ignore"))))
    return "\n".join(" | ".join(cell.strip() for cell in row) for row in rows if any(cell.strip() for cell in row))


def _parse_json(raw_bytes: bytes) -> str:
    payload = json.loads(raw_bytes.decode("utf-8", errors="ignore"))
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _parse_html(raw_bytes: bytes) -> str:
    text = raw_bytes.decode("utf-8", errors="ignore")
    text = re.sub(r"<script.*?>.*?</script>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<style.*?>.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return text


def _parse_plain_text(raw_bytes: bytes) -> str:
    return raw_bytes.decode("utf-8", errors="ignore")


def parse_raw_document(
    *,
    raw_bytes: bytes,
    filename: str,
    source_type: str,
    metadata: dict[str, Any] | None = None,
) -> IngestionSource:
    suffix = Path(filename).suffix.lower()
    if suffix in {".md", ".txt", ".py", ".yaml", ".yml", ".log"}:
        content = _parse_plain_text(raw_bytes)
    elif suffix == ".pdf":
        content = _parse_pdf(raw_bytes)
    elif suffix == ".json":
        content = _parse_json(raw_bytes)
    elif suffix in {".html", ".htm"}:
        content = _parse_html(raw_bytes)
    elif suffix == ".csv":
        content = _parse_csv(raw_bytes)
    else:
        raise ValueError(f"暂不支持的文件类型: {suffix or 'unknown'}")

    cleaned = _clean_text(content)
    if not cleaned:
        raise ValueError("文件内容为空，无法入库。")

    return IngestionSource(
        source_name=filename,
        source_type=source_type,
        original_filename=filename,
        content=cleaned,
        metadata=metadata or {},
    )


def load_directory_sources(docs_dir: str) -> list[IngestionSource]:
    base = Path(docs_dir)
    supported = {".md", ".txt", ".pdf", ".json", ".html", ".htm", ".csv", ".py", ".yaml", ".yml", ".log"}
    sources: list[IngestionSource] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in supported:
            continue
        raw_bytes = path.read_bytes()
        relative_name = str(path.relative_to(base))
        source = parse_raw_document(
            raw_bytes=raw_bytes,
            filename=relative_name,
            source_type="directory",
            metadata={"directory": str(base.resolve())},
        )
        sources.append(source)
    logger.info("docs_loaded_and_prepared", raw_sources=len(sources), directory=str(base))
    return sources


def build_document_payloads(sources: list[IngestionSource]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for source in sources:
        chunks = _split_text(source)
        payloads.append(
            {
                "source": source,
                "content_hash": hashlib.sha256(source.content.encode("utf-8")).hexdigest(),
                "chunks": chunks,
            }
        )
    logger.info(
        "sources_split_into_chunks",
        source_count=len(payloads),
        chunk_count=sum(len(item["chunks"]) for item in payloads),
    )
    return payloads
