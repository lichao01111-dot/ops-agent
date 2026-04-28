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

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent_kernel.schemas import ChatRequest, ChatResponse, UserRole
from agent_ops import OpsAgentStreaming, create_ops_agent_streaming
from config import settings
from gateway.approvals import ApprovalRegistry, sign_receipt
from gateway.auth import (
    AuthIdentity,
    authenticate,
    display_name_for,
    issue_token,
    verify_token,
)
from gateway.conversations import ConversationIndex
from tools.knowledge_tool import knowledge_base

logger = structlog.get_logger()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PLAYGROUND_PATH = PROJECT_ROOT / "docs" / "playground.html"
KNOWLEDGE_ADMIN_PATH = PROJECT_ROOT / "docs" / "knowledge_admin.html"
FRONTEND_DIR = PROJECT_ROOT / "frontend"

# Routing table: agent_id (frontend) → backend handler.
# M1 only IT-Ops is real; the other three are honest stubs that say so.
SUPPORTED_AGENT_IDS = {"it-ops", "risk", "finance", "service"}
REAL_AGENT_IDS = {"it-ops"}


def _agent_stub_response(agent_id: str, session_id: str, message: str) -> dict:
    name_map = {
        "risk": "风控顾问 / Risk Advisor",
        "finance": "财务分析师 / Finance Analyst",
        "service": "客服专员 / Customer Service",
    }
    label = name_map.get(agent_id, agent_id)
    return {
        "session_id": session_id,
        "message": (
            f"[Stub] **{label}** 还在建设中。\n\n"
            f"目前只有 **IT 运维助手** 是真实接入的。请在左侧切换到 IT 运维助手再试。\n\n"
            f"（你输入的内容：{message[:120]}）"
        ),
        "intent": None,
        "route": None,
        "risk_level": "low",
        "needs_approval": False,
        "tool_calls": [],
        "sources": [],
        "tokens_used": 0,
    }

# ===== Lifespan =====

agent: OpsAgentStreaming | None = None
agent_registry = None
agent_audit_logger = None
conversation_index = ConversationIndex()
approval_registry = ApprovalRegistry()


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


class LoginInput(BaseModel):
    username: str
    password: str


class ApprovalDecisionInput(BaseModel):
    request_id: str
    decision: str  # "approve" | "reject"
    comment: str = ""


class ConversationCreateInput(BaseModel):
    title: str = "新建对话"
    agent_id: str = "it-ops"


class ConversationPatchInput(BaseModel):
    title: str


# ===== Auth dependency =====

def get_optional_identity(authorization: str | None = Header(default=None)) -> AuthIdentity | None:
    """Resolve Bearer token without raising. Returns None for anon."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    return verify_token(authorization.split(" ", 1)[1].strip())


def get_required_identity(
    identity: AuthIdentity | None = Depends(get_optional_identity),
) -> AuthIdentity:
    if identity is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return identity


# ===== Routes =====

@app.get("/health")
async def health():
    return {"status": "healthy", "agent": agent is not None}


@app.get("/")
async def index():
    """Default landing → /app/ so relative asset URLs resolve under /app/."""
    from fastapi.responses import RedirectResponse
    if (FRONTEND_DIR / "JARVIS.html").exists():
        return RedirectResponse(url="/app/JARVIS.html", status_code=307)
    if PLAYGROUND_PATH.exists():
        return FileResponse(PLAYGROUND_PATH)
    raise HTTPException(status_code=404, detail="No landing page available")


@app.get("/playground")
async def playground_alias():
    if not PLAYGROUND_PATH.exists():
        raise HTTPException(status_code=404, detail="Playground page not found")
    return FileResponse(PLAYGROUND_PATH)


@app.get("/knowledge-admin")
async def knowledge_admin():
    if not KNOWLEDGE_ADMIN_PATH.exists():
        raise HTTPException(status_code=404, detail="Knowledge admin page not found")
    return FileResponse(KNOWLEDGE_ADMIN_PATH)


# ===== Auth =====

@app.post("/api/auth/login")
async def auth_login(payload: LoginInput):
    identity = authenticate(payload.username, payload.password)
    if identity is None:
        raise HTTPException(status_code=401, detail="invalid credentials")
    token = issue_token(identity)
    return {
        "token": token,
        "user": {
            "user_id": identity.user_id,
            "username": identity.username,
            "display_name": display_name_for(identity.username),
            "role": identity.role,
        },
    }


@app.get("/api/auth/me")
async def auth_me(identity: AuthIdentity = Depends(get_required_identity)):
    return {
        "user_id": identity.user_id,
        "username": identity.username,
        "display_name": display_name_for(identity.username),
        "role": identity.role,
    }


@app.post("/api/auth/logout")
async def auth_logout():
    # Stateless tokens — logout is client-side (drop the token).
    return {"ok": True}


# ===== Chat =====

def _resolve_chat_user(
    input_data: ChatInput,
    identity: AuthIdentity | None,
) -> tuple[str, UserRole]:
    """Token wins if present; falls back to body for legacy/anon usage."""
    if identity is not None:
        return identity.user_id, UserRole(identity.role)
    return input_data.user_id or "anonymous", UserRole(input_data.user_role)


@app.post("/api/chat")
async def chat(
    input_data: ChatInput,
    identity: AuthIdentity | None = Depends(get_optional_identity),
):
    """非流式对话接口"""
    if not agent:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    agent_id = (input_data.context or {}).get("agent_id", "it-ops")
    if agent_id not in SUPPORTED_AGENT_IDS:
        raise HTTPException(status_code=400, detail=f"unknown agent_id: {agent_id}")

    user_id, user_role = _resolve_chat_user(input_data, identity)
    session_id = input_data.session_id or str(uuid.uuid4())

    if agent_id not in REAL_AGENT_IDS:
        conversation_index.touch(
            user_id=user_id,
            session_id=session_id,
            agent_id=agent_id,
            preview=input_data.message,
            title_hint=input_data.message,
        )
        return _agent_stub_response(agent_id, session_id, input_data.message)

    request = ChatRequest(
        message=input_data.message,
        session_id=session_id,
        user_id=user_id,
        user_role=user_role,
        context=input_data.context or {},
    )

    response = await agent.chat(request)
    conversation_index.touch(
        user_id=user_id,
        session_id=session_id,
        agent_id=agent_id,
        preview=response.message,
        title_hint=input_data.message,
    )
    return response.model_dump()


@app.post("/api/chat/stream")
async def chat_stream(
    input_data: ChatInput,
    identity: AuthIdentity | None = Depends(get_optional_identity),
):
    """SSE 流式对话接口"""
    if not agent:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    agent_id = (input_data.context or {}).get("agent_id", "it-ops")
    if agent_id not in SUPPORTED_AGENT_IDS:
        raise HTTPException(status_code=400, detail=f"unknown agent_id: {agent_id}")

    user_id, user_role = _resolve_chat_user(input_data, identity)
    session_id = input_data.session_id or str(uuid.uuid4())

    # Stub agents reply via a single synthetic SSE stream so the frontend
    # event-handling code path stays uniform.
    if agent_id not in REAL_AGENT_IDS:
        stub = _agent_stub_response(agent_id, session_id, input_data.message)
        conversation_index.touch(
            user_id=user_id,
            session_id=session_id,
            agent_id=agent_id,
            preview=input_data.message,
            title_hint=input_data.message,
        )

        async def stub_stream():
            yield f"event: start\ndata: {json.dumps({'session_id': session_id})}\n\n"
            yield f"event: final\ndata: {json.dumps(stub, ensure_ascii=False, default=str)}\n\n"

        return StreamingResponse(stub_stream(), media_type="text/event-stream")

    request = ChatRequest(
        message=input_data.message,
        session_id=session_id,
        user_id=user_id,
        user_role=user_role,
        context=input_data.context or {},
    )

    async def event_generator():
        last_message = ""
        async for event in agent.chat_stream(request):
            event_type = event.get("event", "message")
            data = event.get("data", {})
            if event_type == "final" and isinstance(data, dict):
                last_message = data.get("message", "") or last_message
                if data.get("needs_approval"):
                    # Issue a pending approval entry so the frontend can
                    # POST a decision against a server-side request_id.
                    pending = approval_registry.issue(
                        session_id=session_id,
                        user_id=user_id,
                        step_id=data.get("step_id", ""),
                        action=data.get("action", "unknown"),
                        risk_level=data.get("risk_level", "high"),
                        payload=data,
                    )
                    yield (
                        "event: approval_required\n"
                        f"data: {json.dumps({'request_id': pending.request_id, 'action': pending.action, 'risk_level': pending.risk_level, 'payload': data}, ensure_ascii=False, default=str)}\n\n"
                    )
            payload = json.dumps(data, ensure_ascii=False, default=str)
            yield f"event: {event_type}\ndata: {payload}\n\n"

        conversation_index.touch(
            user_id=user_id,
            session_id=session_id,
            agent_id=agent_id,
            preview=last_message or input_data.message,
            title_hint=input_data.message,
        )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ===== Approval =====

@app.post("/api/approval/decision")
async def approval_decision(
    payload: ApprovalDecisionInput,
    identity: AuthIdentity = Depends(get_required_identity),
):
    pending = approval_registry.get(payload.request_id)
    if pending is None:
        raise HTTPException(status_code=404, detail="approval request not found or expired")
    if pending.user_id and pending.user_id != identity.user_id and identity.role != "admin":
        raise HTTPException(status_code=403, detail="not allowed to decide on this request")
    if pending.decided:
        raise HTTPException(status_code=409, detail="already decided")

    if payload.decision == "reject":
        approval_registry.mark_decided(payload.request_id)
        return {
            "ok": True,
            "decision": "reject",
            "session_id": pending.session_id,
            "receipt": None,
        }
    if payload.decision != "approve":
        raise HTTPException(status_code=400, detail="decision must be 'approve' or 'reject'")

    receipt = sign_receipt(
        step_id=pending.step_id,
        approved_by=identity.username,
        scope=pending.action,
    )
    approval_registry.mark_decided(payload.request_id)
    return {
        "ok": True,
        "decision": "approve",
        "session_id": pending.session_id,
        "receipt": receipt,  # frontend passes back via context.approval_receipt
    }


# ===== Conversations =====

@app.get("/api/conversations")
async def list_conversations(
    limit: int = 50,
    identity: AuthIdentity = Depends(get_required_identity),
):
    items = conversation_index.list(identity.user_id, limit=limit)
    return {
        "conversations": [
            {
                "session_id": m.session_id,
                "title": m.title,
                "agent_id": m.agent_id,
                "preview": m.preview,
                "updated_at": m.updated_at,
                "created_at": m.created_at,
            }
            for m in items
        ]
    }


@app.post("/api/conversations")
async def create_conversation(
    payload: ConversationCreateInput,
    identity: AuthIdentity = Depends(get_required_identity),
):
    if payload.agent_id not in SUPPORTED_AGENT_IDS:
        raise HTTPException(status_code=400, detail=f"unknown agent_id: {payload.agent_id}")
    meta = conversation_index.create(
        user_id=identity.user_id,
        agent_id=payload.agent_id,
        title=payload.title,
    )
    return {
        "session_id": meta.session_id,
        "title": meta.title,
        "agent_id": meta.agent_id,
        "created_at": meta.created_at,
        "updated_at": meta.updated_at,
        "preview": meta.preview,
    }


@app.patch("/api/conversations/{session_id}")
async def patch_conversation(
    session_id: str,
    payload: ConversationPatchInput,
    identity: AuthIdentity = Depends(get_required_identity),
):
    meta = conversation_index.get(session_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    if meta.user_id != identity.user_id and identity.role != "admin":
        raise HTTPException(status_code=403, detail="not allowed")
    updated = conversation_index.rename(session_id, title=payload.title)
    return {
        "session_id": updated.session_id,
        "title": updated.title,
        "agent_id": updated.agent_id,
        "updated_at": updated.updated_at,
    }


@app.delete("/api/conversations/{session_id}")
async def delete_conversation(
    session_id: str,
    identity: AuthIdentity = Depends(get_required_identity),
):
    meta = conversation_index.get(session_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    if meta.user_id != identity.user_id and identity.role != "admin":
        raise HTTPException(status_code=403, detail="not allowed")
    conversation_index.delete(session_id)
    return {"ok": True}


@app.get("/api/conversations/{session_id}/messages")
async def get_conversation_messages(
    session_id: str,
    limit: int = 50,
    identity: AuthIdentity = Depends(get_required_identity),
):
    if not agent:
        raise HTTPException(status_code=503, detail="Agent not initialized")
    meta = conversation_index.get(session_id)
    if meta and meta.user_id != identity.user_id and identity.role != "admin":
        raise HTTPException(status_code=403, detail="not allowed")
    try:
        messages = agent.session_store.get_recent_messages(session_id, limit=limit)
    except Exception as exc:
        logger.warning("conv_messages_failed", session=session_id, error=str(exc))
        messages = []
    serialised = []
    for m in messages:
        role = getattr(m, "type", "ai")
        if role == "human":
            role = "user"
        elif role == "ai":
            role = "assistant"
        serialised.append({"role": role, "content": getattr(m, "content", str(m))})
    return {"session_id": session_id, "messages": serialised}


# ===== Agents catalog =====

@app.get("/api/agents")
async def list_agents():
    return {
        "agents": [
            {"id": "it-ops",  "name": "IT运维助手",   "name_en": "IT Ops",          "role": "IT Operations",     "icon": "💻", "available": True},
            {"id": "risk",    "name": "风控顾问",     "name_en": "Risk Advisor",    "role": "Risk & Compliance", "icon": "🛡️", "available": False},
            {"id": "finance", "name": "财务分析师",   "name_en": "Finance Analyst", "role": "Financial Analysis","icon": "📊", "available": False},
            {"id": "service", "name": "客服专员",     "name_en": "Customer Service","role": "Customer Support",  "icon": "🎧", "available": False},
        ]
    }


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


# ===== Static frontend =====

# Mounted last so /api/* and named routes win.
if FRONTEND_DIR.exists():
    app.mount("/app", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


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
