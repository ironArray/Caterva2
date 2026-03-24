from __future__ import annotations

import json
import uuid

from caterva2.services import settings

from . import config, providers, sessions, tools
from .schemas import AssistantPayload, ChatResponse, TracePayload, TraceToolCall, UsagePayload


def owner_for_user(user) -> str:
    return str(user.id) if user else "anonymous"


def create_session(*, user, metadata: dict | None = None):
    return sessions.registry.create(
        owner=owner_for_user(user),
        model=config.get_model_name(),
        ttl_seconds=settings.llm_session_ttl_seconds,
        system_prompt=config.SYSTEM_PROMPT,
        max_sessions=settings.llm_max_concurrent_sessions,
        metadata=metadata,
    )


def get_session(session_id: str, *, user):
    return sessions.registry.get(session_id, owner_for_user(user), settings.llm_session_ttl_seconds)


def reset_session(session_id: str, *, user):
    return sessions.registry.reset(session_id, owner_for_user(user), settings.llm_session_ttl_seconds)


def delete_session(session_id: str, *, user):
    return sessions.registry.delete(session_id, owner_for_user(user))


def _trim_messages(messages: list[dict]) -> list[dict]:
    if not messages:
        return []
    return [messages[0]] + messages[1:][-settings.llm_max_history_messages :]


def run_chat_turn(*, session_id: str, user, message: str) -> ChatResponse:
    if len(message) > settings.llm_max_input_chars:
        raise ValueError(f"Input too long: max {settings.llm_max_input_chars} chars")

    session = get_session(session_id, user=user)
    with session.lock:
        if session.total_tokens_used > settings.llm_max_total_tokens:
            raise RuntimeError("Token budget exceeded for this session; reset it before continuing")

        session.messages.append({"role": "user", "content": message})
        provider = providers.get_provider()
        request_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        trace_tool_calls: list[TraceToolCall] = []

        for iteration in range(1, settings.llm_max_iterations + 1):
            response = provider.complete(
                messages=_trim_messages(session.messages),
                tools=tools.TOOLS,
                tool_choice="auto",
                temperature=0.2,
                max_tokens=1024,
            )
            for key in request_usage:
                request_usage[key] += int(response.usage.get(key, 0) or 0)
            session.total_tokens_used += int(response.usage.get("total_tokens", 0) or 0)

            if not response.tool_calls:
                assistant_text = response.content or "[No response from provider]"
                session.messages.append({"role": "assistant", "content": assistant_text})
                return ChatResponse(
                    session_id=session.session_id,
                    message_id=str(uuid.uuid4()),
                    assistant=AssistantPayload(text=assistant_text, artifacts=[]),
                    usage=UsagePayload(
                        provider=config.get_provider_name(),
                        model=session.model,
                        prompt_tokens=request_usage["prompt_tokens"],
                        completion_tokens=request_usage["completion_tokens"],
                        total_tokens=request_usage["total_tokens"],
                        session_total_tokens=session.total_tokens_used,
                    ),
                    trace=TracePayload(iterations=iteration, tool_calls=trace_tool_calls),
                )

            session.messages.append(
                {
                    "role": "assistant",
                    "content": response.content,
                    "tool_calls": [tool_call.as_api_dict() for tool_call in response.tool_calls],
                }
            )

            for tool_call in response.tool_calls:
                result = tools.execute_tool(tool_call.name, tool_call.arguments, user=user)
                trace_tool_calls.append(
                    TraceToolCall(name=tool_call.name, arguments=tool_call.arguments, ok=result["ok"])
                )
                session.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_call.name,
                        "content": json.dumps(result),
                    }
                )

            if session.total_tokens_used > settings.llm_max_total_tokens:
                raise RuntimeError("Token budget exceeded for this session; reset it before continuing")

        raise RuntimeError("Max iterations reached; please rephrase your request")
