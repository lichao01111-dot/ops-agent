"""
知识库 RAG Tool
- 向量检索项目文档、环境配置、架构信息
- 支持 Chroma (PoC) / Milvus (生产)
- 文档同步与索引管理
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import structlog
from langchain_core.tools import tool
from langchain_core.documents import Document

from config import settings

logger = structlog.get_logger()


class KnowledgeBase:
    """知识库管理 - 基于 Chroma 的向量检索"""

    def __init__(self):
        self._vectorstore = None
        self._embeddings = None

    def _get_embeddings(self):
        if self._embeddings is None:
            from langchain_community.embeddings import HuggingFaceEmbeddings
            self._embeddings = HuggingFaceEmbeddings(
                model_name=settings.embedding_model,
                model_kwargs={"device": "cpu"},
            )
        return self._embeddings

    def _get_vectorstore(self):
        if self._vectorstore is None:
            from langchain_chroma import Chroma
            persist_dir = settings.chroma_persist_dir
            os.makedirs(persist_dir, exist_ok=True)
            self._vectorstore = Chroma(
                collection_name="ops_knowledge",
                embedding_function=self._get_embeddings(),
                persist_directory=persist_dir,
            )
        return self._vectorstore

    def add_documents(self, documents: list[Document]) -> int:
        """添加文档到知识库"""
        vs = self._get_vectorstore()
        vs.add_documents(documents)
        logger.info("documents_added", count=len(documents))
        return len(documents)

    def search(self, query: str, k: int = 5) -> list[dict]:
        """语义搜索"""
        vs = self._get_vectorstore()
        results = vs.similarity_search_with_score(query, k=k)
        return [
            {
                "content": doc.page_content,
                "source": doc.metadata.get("source", "unknown"),
                "score": float(score),
            }
            for doc, score in results
        ]

    def get_stats(self) -> dict:
        """获取知识库统计"""
        vs = self._get_vectorstore()
        collection = vs._collection
        return {
            "total_documents": collection.count(),
            "collection_name": "ops_knowledge",
            "persist_dir": settings.chroma_persist_dir,
        }


knowledge_base = KnowledgeBase()


# ===== 文档加载工具 =====

def load_markdown_docs(docs_dir: str) -> list[Document]:
    """从目录加载 Markdown 文档"""
    from langchain_community.document_loaders import DirectoryLoader, TextLoader
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    loader = DirectoryLoader(
        docs_dir,
        glob="**/*.md",
        loader_cls=TextLoader,
        loader_kwargs={"encoding": "utf-8"},
    )
    documents = loader.load()

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200,
        separators=["\n## ", "\n### ", "\n\n", "\n", " "],
    )
    chunks = splitter.split_documents(documents)
    logger.info("docs_loaded_and_split", raw_docs=len(documents), chunks=len(chunks))
    return chunks


# ===== LangChain Tools =====

@tool
async def query_knowledge(
    question: str,
    top_k: int = 5,
) -> str:
    """查询项目知识库，回答环境信息、架构、配置等相关问题。

    Args:
        question: 用户问题，如 "测试环境的 MySQL 连接地址是什么"
        top_k: 返回最相关的文档数量
    """
    results = knowledge_base.search(question, k=top_k)

    if not results:
        return json.dumps({
            "answer_status": "no_results",
            "message": "知识库中未找到相关信息，建议检查文档是否已同步到知识库。",
            "results": [],
        }, ensure_ascii=False)

    return json.dumps({
        "answer_status": "found",
        "question": question,
        "results": results,
    }, ensure_ascii=False)


@tool
async def index_documents(
    docs_directory: str,
) -> str:
    """将指定目录的文档索引到知识库（管理员操作）。

    Args:
        docs_directory: 文档目录路径
    """
    try:
        docs_path = Path(docs_directory)
        if not docs_path.exists():
            return json.dumps({"error": f"目录不存在: {docs_directory}"})

        chunks = load_markdown_docs(docs_directory)
        count = knowledge_base.add_documents(chunks)
        stats = knowledge_base.get_stats()

        return json.dumps({
            "status": "success",
            "indexed_chunks": count,
            "total_documents": stats["total_documents"],
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"索引失败: {str(e)}"})


knowledge_tools = [query_knowledge, index_documents]
