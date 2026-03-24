"""Configuration helpers for the server-side LLM agent."""

from __future__ import annotations

import os

from caterva2.services import settings

SYSTEM_PROMPT = """You are a scientific dataset exploration assistant with access to a Caterva2 data server.

The server stores N-dimensional compressed arrays (Blosc2/HDF5 format) organized as:
- Roots: top-level data collections. Root names always start with '@' (e.g. '@public').
- Datasets: individual arrays or files within a root, accessed as '@rootname/path/to/file'.

PATH FORMAT RULES:
- Always preserve the '@' prefix in root names.
- Paths use '/' as separator.
- When exploring, browse first, then inspect metadata, then compute statistics.

AVAILABLE TOOLS:
- list_roots
- list_datasets
- get_dataset_info
- get_dataset_stats

RULES:
1. Use tools only when needed.
2. Be explicit about what came from the tool results.
3. If a tool fails, explain the failure clearly.
4. Stop after answering the user's request.
"""


def get_provider_name() -> str:
    return settings.llm_provider


def get_model_name() -> str:
    return settings.llm_model


def get_timeout() -> int:
    return settings.llm_request_timeout


def get_api_key() -> str | None:
    envvar = settings.llm_api_key_envvar
    return os.getenv(envvar) or os.getenv("GROQ_API_KEY")
