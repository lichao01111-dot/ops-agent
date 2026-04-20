"""
OpsAgent API Gateway
- REST API + SSE 流式输出
- 健康检查
- 审计日志查询
"""
from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager

import structlog
import uvicorn
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from agent_kernel.schemas import ChatRequest, ChatResponse, UserRole
from agent_ops import OpsAgentStreaming, create_ops_agent_streaming
from config import settings
from tools.knowledge_tool import knowledge_base

logger = structlog.get_logger()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PLAYGROUND_PATH = PROJECT_ROOT / "docs" / "playground.html"
KNOWLEDGE_ADMIN_PATH = PROJECT_ROOT / "docs" / "knowledge_admin.html"

# ===== Lifespan =====

agent: OpsAgentStreaming | None = None
agent_registry = None
agent_audit_logger = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent, agent_registry, agent_audit_logger
    logger.info("starting_ops_agent", llm_provider=settings.llm_provider.value, llm_model=settings.llm_model)
    agent = create_ops_agent_streaming()
    agent_registry = agent.tool_registry
    agent_audit_logger = agent.audit_logger
    logger.info("ops_agent_ready", tools=[spec.name for spec in agent_registry.all_specs()])
    yield
    logger.info("shutting_down_ops_agent")


# ===== App =====

app = FastAPI(
    title="OpsAgent API",
    description="DevOps AI Agent - 智能运维助手",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: 生产环境限制域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ===== Request Models =====

class ChatInput(BaseModel):
    message: str
    session_id: str = ""
    user_id: str = "anonymous"
    user_role: str = "viewer"
    context: dict = {}


# ===== Routes =====

@app.get("/health")
async def health():
    return {"status": "healthy", "agent": agent is not None}


@app.get("/")
async def playground():
    if not PLAYGROUND_PATH.exists():
        raise HTTPException(status_code=404, detail="Playground page not found")
    return FileResponse(PLAYGROUND_PATH)


@app.get("/playground")
async def playground_alias():
    return await playground()


@app.get("/knowledge-admin")
async def knowledge_admin():
    if not KNOWLEDGE_ADMIN_PATH.exists():
        raise HTTPException(status_code=404, detail="Knowledge admin page not found")
    return FileResponse(KNOWLEDGE_ADMIN_PATH)


@app.post("/api/chat")
async def chat(input_data: ChatInput):
    """非流式对话接口"""
    if not agent:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    request = ChatRequest(
        message=input_data.message,
        session_id=input_data.session_id or str(uuid.uuid4()),
        user_id=input_data.user_id,
        user_role=UserRole(input_data.user_role),
        context=input_data.context,
    )

    response = await agent.chat(request)
    return response.model_dump()


@app.post("/api/chat/stream")
async def chat_stream(input_data: ChatInput):
    """SSE 流式对话接口"""
    if not agent:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    request = ChatRequest(
        message=input_data.message,
        session_id=input_data.session_id or str(uuid.uuid4()),
        user_id=input_data.user_id,
        user_role=UserRole(input_data.user_role),
        context=input_data.context,
    )

    async def event_generator():
        async for event in agent.chat_stream(request):
            event_type = event.get("event", "message")
            data = json.dumps(event.get("data", {}), ensure_ascii=False)
            yield f"event: {event_type}\ndata: {data}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/tools")
async def list_tools():
    """获取已注册的 Tool 列表"""
    if agent_registry is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")
    return {
        "tools": [
            {"name": spec.name, "description": spec.description, "source": spec.source}
            for spec in agent_registry.all_specs()
        ]
    }


@app.get("/api/audit")
async def get_audit_logs(user_id: str = "", limit: int = 50):
    """查询审计日志"""
    if agent_audit_logger is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")
    if user_id:
        entries = agent_audit_logger.get_by_user(user_id, limit)
    else:
        entries = agent_audit_logger.get_recent(limit)
    return {"entries": [e.model_dump() for e in entries]}


@app.get("/api/knowledge/stats")
async def knowledge_stats():
    try:
        return knowledge_base.get_stats()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"knowledge stats failed: {exc}")


@app.get("/api/knowledge/documents")
async def knowledge_documents(limit: int = 20):
    try:
        return {"documents": knowledge_base.list_documents(limit=limit)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"knowledge documents failed: {exc}")


@app.get("/api/knowledge/search")
async def knowledge_search(query: str, top_k: int = 5):
    try:
        return {"query": query, "results": knowledge_base.search(query, k=top_k)}
    except Exception as exc:
        logger.exception("knowledge_search_failed", query=query, top_k=top_k, error=str(exc))
        raise HTTPException(status_code=500, detail=f"knowledge search failed: {exc}")


@app.post("/api/knowledge/index-directory")
async def knowledge_index_directory(
    docs_directory: str = Form(...),
):
    try:
        return knowledge_base.ingest_directory(docs_directory)
    except Exception as exc:
        logger.exception("knowledge_index_directory_failed", directory=docs_directory, error=str(exc))
        raise HTTPException(status_code=500, detail=f"knowledge index failed: {exc}")


@app.post("/api/knowledge/upload")
async def knowledge_upload(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    payloads: list[dict] = []
    for uploaded in files:
        raw = await uploaded.read()
        payloads.append(
            {
                "filename": uploaded.filename or "unnamed",
                "content_type": uploaded.content_type or "",
                "content": raw,
            }
        )

    try:
        return knowledge_base.ingest_uploads(payloads)
    except Exception as exc:
        logger.exception("knowledge_upload_failed", file_count=len(files), error=str(exc))
        raise HTTPException(status_code=500, detail=f"knowledge upload failed: {exc}")


# ===== Entry Point =====

def main():
    uvicorn.run(
        "gateway.app:app",
        host=settings.server_host,
        port=settings.server_port,
        reload=True,
        log_level="info",
    )


if __name__ == "__main__":
    main()
