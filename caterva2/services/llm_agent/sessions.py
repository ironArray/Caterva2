from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta


@dataclass
class AgentSession:
    session_id: str
    owner: str
    model: str
    created_at: datetime
    expires_at: datetime
    messages: list[dict]
    total_tokens_used: int = 0
    metadata: dict = field(default_factory=dict)
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)


class SessionRegistry:
    def __init__(self):
        self._lock = threading.RLock()
        self._sessions: dict[str, AgentSession] = {}

    def _cleanup(self) -> None:
        now = datetime.now(UTC)
        expired = [sid for sid, session in self._sessions.items() if session.expires_at <= now]
        for sid in expired:
            self._sessions.pop(sid, None)

    def create(
        self,
        *,
        owner: str,
        model: str,
        ttl_seconds: int,
        system_prompt: str,
        max_sessions: int,
        metadata: dict | None = None,
    ) -> AgentSession:
        with self._lock:
            self._cleanup()
            active_sessions = sum(1 for session in self._sessions.values() if session.owner == owner)
            if active_sessions >= max_sessions:
                raise RuntimeError("Maximum concurrent agent sessions reached")

            now = datetime.now(UTC)
            session_id = str(uuid.uuid4())
            session = AgentSession(
                session_id=session_id,
                owner=owner,
                model=model,
                created_at=now,
                expires_at=now + timedelta(seconds=ttl_seconds),
                messages=[{"role": "system", "content": system_prompt}],
                metadata=metadata or {},
            )
            self._sessions[session_id] = session
            return session

    def get(self, session_id: str, owner: str, ttl_seconds: int) -> AgentSession:
        with self._lock:
            self._cleanup()
            session = self._sessions.get(session_id)
            if session is None:
                raise KeyError(session_id)
            if session.owner != owner:
                raise PermissionError(session_id)
            session.expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
            return session

    def reset(self, session_id: str, owner: str, ttl_seconds: int) -> AgentSession:
        with self._lock:
            session = self.get(session_id, owner, ttl_seconds)
            system_prompt = session.messages[0]
            session.messages = [system_prompt]
            session.total_tokens_used = 0
            return session

    def delete(self, session_id: str, owner: str) -> bool:
        with self._lock:
            self._cleanup()
            session = self._sessions.get(session_id)
            if session is None:
                raise KeyError(session_id)
            if session.owner != owner:
                raise PermissionError(session_id)
            del self._sessions[session_id]
            return True


registry = SessionRegistry()
