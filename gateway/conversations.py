"""
In-process conversation index for the JARVIS gateway.

Maps user_id → ordered list of session_ids, with lightweight metadata
(title, agent_id, last activity). Persistence is intentionally minimal —
the conversation *content* lives in the agent SessionStore. This index
just enables the sidebar to enumerate "what conversations does this user
have", which the SessionStore protocol does not currently support.

When SessionStore grows a list_sessions(user_id) method, replace this
with a thin adapter and delete the in-process state.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ConversationMeta:
    session_id: str
    user_id: str
    title: str = "新建对话"
    agent_id: str = "it-ops"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    preview: str = ""

    def touch(self, *, preview: Optional[str] = None) -> None:
        self.updated_at = time.time()
        if preview is not None:
            self.preview = preview[:120]


class ConversationIndex:
    def __init__(self) -> None:
        self._by_id: dict[str, ConversationMeta] = {}
        self._by_user: dict[str, list[str]] = {}
        self._lock = threading.RLock()

    def create(
        self,
        *,
        user_id: str,
        agent_id: str = "it-ops",
        title: str = "新建对话",
    ) -> ConversationMeta:
        sid = str(uuid.uuid4())
        meta = ConversationMeta(
            session_id=sid,
            user_id=user_id,
            title=title,
            agent_id=agent_id,
        )
        with self._lock:
            self._by_id[sid] = meta
            self._by_user.setdefault(user_id, []).insert(0, sid)
        return meta

    def get(self, session_id: str) -> Optional[ConversationMeta]:
        with self._lock:
            return self._by_id.get(session_id)

    def list(self, user_id: str, *, limit: int = 50) -> list[ConversationMeta]:
        with self._lock:
            ids = list(self._by_user.get(user_id, []))[:limit]
            return [self._by_id[sid] for sid in ids if sid in self._by_id]

    def rename(self, session_id: str, *, title: str) -> Optional[ConversationMeta]:
        with self._lock:
            meta = self._by_id.get(session_id)
            if meta is None:
                return None
            meta.title = title[:80]
            meta.touch()
            return meta

    def delete(self, session_id: str) -> bool:
        with self._lock:
            meta = self._by_id.pop(session_id, None)
            if meta is None:
                return False
            user_list = self._by_user.get(meta.user_id, [])
            if session_id in user_list:
                user_list.remove(session_id)
            return True

    def touch(
        self,
        *,
        user_id: str,
        session_id: str,
        agent_id: Optional[str] = None,
        preview: Optional[str] = None,
        title_hint: Optional[str] = None,
    ) -> ConversationMeta:
        """Idempotently ensure the conversation exists and bump its mtime.

        Used after every chat turn so that conversations that were
        created elsewhere (legacy clients, direct API hits) still appear
        in the sidebar.
        """
        with self._lock:
            meta = self._by_id.get(session_id)
            if meta is None:
                meta = ConversationMeta(
                    session_id=session_id,
                    user_id=user_id,
                    agent_id=agent_id or "it-ops",
                    title=(title_hint or "新建对话")[:80],
                )
                self._by_id[session_id] = meta
                self._by_user.setdefault(user_id, []).insert(0, session_id)
            else:
                # Move-to-front so most-recent is on top.
                bucket = self._by_user.setdefault(user_id, [])
                if session_id in bucket:
                    bucket.remove(session_id)
                bucket.insert(0, session_id)
                if agent_id:
                    meta.agent_id = agent_id
                if title_hint and meta.title == "新建对话":
                    meta.title = title_hint[:80]
            meta.touch(preview=preview)
            return meta
