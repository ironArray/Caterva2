from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from datetime import datetime


class CreateSessionRequest(BaseModel):
    name: str | None = None
    root_hint: str | None = None
    notebook_path: str | None = None


class SessionMetadataResponse(BaseModel):
    session_id: str
    created_at: datetime
    expires_at: datetime
    model: str
    owner: str
    message_count: int


class CreateSessionResponse(SessionMetadataResponse):
    pass


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    context: dict[str, Any] | None = None


class ArtifactPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str


class AssistantPayload(BaseModel):
    text: str
    artifacts: list[ArtifactPayload] = Field(default_factory=list)


class UsagePayload(BaseModel):
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    session_total_tokens: int = 0


class TraceToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    ok: bool = True


class TracePayload(BaseModel):
    iterations: int
    tool_calls: list[TraceToolCall] = Field(default_factory=list)


class ChatResponse(BaseModel):
    session_id: str
    message_id: str
    assistant: AssistantPayload
    usage: UsagePayload
    trace: TracePayload


class ResetSessionResponse(BaseModel):
    session_id: str
    reset: bool
    message_count: int


class DeleteSessionResponse(BaseModel):
    session_id: str
    deleted: bool


SessionMetadataResponse.model_rebuild(_types_namespace={"datetime": __import__("datetime").datetime})
CreateSessionResponse.model_rebuild(_types_namespace={"datetime": __import__("datetime").datetime})
