from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass

import httpx

from . import config


@dataclass
class NormalizedToolCall:
    id: str
    name: str
    arguments: dict

    def as_api_dict(self) -> dict:
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.arguments),
            },
        }


@dataclass
class NormalizedResponse:
    content: str
    tool_calls: list[NormalizedToolCall]
    usage: dict


def _extract_path(text: str) -> str | None:
    match = re.search(r"(@(?:public|shared|personal)(?:/[^\s,;]+)?)", text)
    return match.group(1) if match else None


class MockProvider:
    name = "mock"

    def complete(self, *, messages, tools, tool_choice, temperature, max_tokens) -> NormalizedResponse:
        last_message = messages[-1]
        if last_message["role"] == "tool":
            tool_messages = []
            for message in reversed(messages):
                if message["role"] != "tool":
                    break
                tool_messages.append(message["content"])
            tool_messages.reverse()
            return NormalizedResponse(
                content="\n".join(tool_messages),
                tool_calls=[],
                usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            )

        user_text = last_message.get("content", "")
        lower = user_text.lower()
        path = _extract_path(user_text)
        tool_calls = []
        if "roots" in lower:
            tool_calls.append(NormalizedToolCall(str(uuid.uuid4()), "list_roots", {}))
        elif "stats" in lower and path:
            tool_calls.append(NormalizedToolCall(str(uuid.uuid4()), "get_dataset_stats", {"path": path}))
        elif any(keyword in lower for keyword in ("info", "metadata")) and path:
            tool_calls.append(NormalizedToolCall(str(uuid.uuid4()), "get_dataset_info", {"path": path}))
        elif path:
            tool_calls.append(NormalizedToolCall(str(uuid.uuid4()), "list_datasets", {"path": path}))
        elif any(keyword in lower for keyword in ("list", "datasets")):
            tool_calls.append(NormalizedToolCall(str(uuid.uuid4()), "list_roots", {}))

        if tool_calls:
            return NormalizedResponse(
                content="",
                tool_calls=tool_calls,
                usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            )

        return NormalizedResponse(
            content="No tool call was needed for that request.",
            tool_calls=[],
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        )


class GroqProvider:
    name = "groq"
    base_url = "https://api.groq.com/openai/v1/chat/completions"

    def complete(self, *, messages, tools, tool_choice, temperature, max_tokens) -> NormalizedResponse:
        api_key = config.get_api_key()
        if not api_key:
            raise RuntimeError("Missing provider API key for configured LLM provider")

        payload = {
            "model": config.get_model_name(),
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=config.get_timeout()) as client:
            response = client.post(self.base_url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()

        choice = data["choices"][0]["message"]
        tool_calls = []
        for item in choice.get("tool_calls") or []:
            function = item.get("function") or {}
            arguments = function.get("arguments") or "{}"
            if isinstance(arguments, str):
                arguments = json.loads(arguments or "{}")
            tool_calls.append(
                NormalizedToolCall(
                    id=item.get("id", str(uuid.uuid4())),
                    name=function.get("name", ""),
                    arguments=arguments,
                )
            )

        return NormalizedResponse(
            content=choice.get("content") or "",
            tool_calls=tool_calls,
            usage=data.get("usage") or {},
        )


def get_provider():
    provider = config.get_provider_name().lower()
    if provider == "mock":
        return MockProvider()
    if provider == "groq":
        return GroqProvider()
    raise RuntimeError(f"Unsupported LLM provider: {provider}")
