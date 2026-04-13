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
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel

from agent_core import OpsAgent, OpsAgentStreaming, ChatRequest, ChatResponse
from agent_core.audit import audit_logger
from agent_core.schemas import UserRole
from config import settings
from tools import ALL_TOOLS

logger = structlog.get_logger()

# ===== Lifespan =====

agent: OpsAgentStreaming | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent
    logger.info("starting_ops_agent", llm_provider=settings.llm_provider.value, llm_model=settings.llm_model)
    agent = OpsAgentStreaming()
    logger.info("ops_agent_ready", tools=[t.name for t in ALL_TOOLS])
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
    return {
        "tools": [
            {"name": t.name, "description": t.description}
            for t in ALL_TOOLS
        ]
    }


@app.get("/api/audit")
async def get_audit_logs(user_id: str = "", limit: int = 50):
    """查询审计日志"""
    if user_id:
        entries = audit_logger.get_by_user(user_id, limit)
    else:
        entries = audit_logger.get_recent(limit)
    return {"entries": [e.model_dump() for e in entries]}


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
